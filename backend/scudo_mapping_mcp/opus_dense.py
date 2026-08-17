"""
Opus-as-dense seam (WS-B).

WHY THIS EXISTS
---------------
The dense arm was originally planned as Titan-Embed via the FalkorDB
GraphRAG-SDK vector index. That plan is PARKED — Bedrock model access for
Titan-Embed in eu-west-2 / 954976331678 is still blocked behind a Foundation
amendment we cannot land in-window. Rather than ship without a dense arm,
WS-B substitutes Claude Opus 4.8 (already accessible to the M and V task
roles) as a JUDGEMENT-style dense scorer:

    opus_dense_score(query_label, query_desc, candidate_label, candidate_desc)
        -> float in [0, 1]

The Opus call asks for a single SEMANTIC alignment score and a short
reason. The score IS the dense quantity ``Candidate.similarity`` carries —
no rerank, no fusion, no boost. The band gate (0.75 centre; PASS cut 0.80)
is calibrated against this raw quantity (arb-review-pack §5.2 invariant:
similarity equals raw dense score).

THREE-SEAM ENV CONTRACT
-----------------------
    SCUDO_DENSE_BACKEND     in {opus, jaro_winkler}.  Default: jaro_winkler
                            so the 86 smoke gates keep exercising the
                            legacy deterministic path. Prod task defs flip
                            this to opus.

    SCUDO_DENSE_FALLBACK    "1"|"true"|"yes" → on. When True (and the
                            backend is "opus"), an Opus invoke failure
                            (network / IAM / model-access) falls back to
                            the Jaro-Winkler stand-in INSTEAD of raising.
                            Default off — silent fallback hides config
                            errors and we'd rather know.

    SCUDO_BEDROCK_MODEL_ID  Mirrors agent.py: cross-region inference
                            profile id for Opus 4.8 in eu-west-2.
                            Default: ``eu.anthropic.claude-opus-4-8``.

    AWS_REGION              Standard. Default: eu-west-2.

INVARIANTS
----------
I.  Return value is ALWAYS in [0, 1]. Defensive clamp.
II. Identical inputs return the same Jaro-Winkler fallback score (the
    legacy stand-in is fully deterministic). Opus is NOT deterministic —
    callers expecting reproducible scores must set
    ``SCUDO_DENSE_BACKEND=jaro_winkler``.
III.A failure in the Opus call surfaces as a ``RuntimeError`` unless
    ``SCUDO_DENSE_FALLBACK=1``, in which case the Jaro-Winkler stand-in
    runs silently. CI / smoke gates that don't touch AWS leave the
    backend on the default and never hit this branch.
"""

from __future__ import annotations

import json
import os
import sys
import logging
import threading
import time
from typing import NamedTuple


# ──────────────────────────────────────────────────────────────────────────
# Bedrock model id — mirrors agent.py so the two paths can't drift.
# ──────────────────────────────────────────────────────────────────────────
DEFAULT_BEDROCK_MODEL_ID = "eu.anthropic.claude-opus-4-8"


class DenseScoringUnavailableError(RuntimeError):
    """The configured dense model could not score a complete candidate batch."""


class DenseCircuitOpenError(DenseScoringUnavailableError):
    """The dense circuit breaker refused a model batch."""


# ──────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────
def opus_dense_score(
    query_label: str,
    query_desc: str,
    candidate_label: str,
    candidate_desc: str,
) -> float:
    """Return a SEMANTIC alignment score in [0, 1] for (query, candidate).

    Backend is selected by ``SCUDO_DENSE_BACKEND``:
        - ``opus`` (default in prod): Bedrock invoke on Claude Opus 4.8
          via the cross-region inference profile. The model emits a
          single JSON object {"score": float, "reason": str}; we parse
          and clamp.
        - ``jaro_winkler`` (default in tests): pure-Python deterministic
          stand-in over the concatenated label+description strings. Same
          Jaro-Winkler the FalkorDB store ships today — kept in sync via
          a single import.

    On Opus failure, ``SCUDO_DENSE_FALLBACK=1`` swallows the exception and
    falls back to Jaro-Winkler. Otherwise the underlying error is raised
    (wrapped in ``RuntimeError`` if it's not already one).
    """
    backend = (os.getenv("SCUDO_DENSE_BACKEND") or "jaro_winkler").strip().lower()

    if backend == "jaro_winkler":
        return _clamp01(
            _jaro_winkler_score(
                query_label,
                query_desc,
                candidate_label,
                candidate_desc,
            )
        )

    if backend != "opus":
        raise ValueError(
            f"SCUDO_DENSE_BACKEND={backend!r} not in {{'opus', 'jaro_winkler'}}"
        )

    # Circuit breaker. This scorer is called PER CANDIDATE, so a dead key or a
    # region outage costs ~2.5s x 8 candidates = ~20s of certain failure on
    # EVERY match (measured). After a few consecutive auth/config failures,
    # stop calling Bedrock and serve the Jaro-Winkler fallback immediately;
    # any success resets it. Only trips when fallback is enabled — with
    # fallback off the caller wants the loud error.
    # Half-open probe. The early return below made _breaker_record_success()
    # unreachable once the breaker tripped, so under the ONLY configuration we
    # ship (fallback on) it stayed open for the life of the process — verified:
    # bedrock_calls frozen at 3 across 3 recovery attempts. A short cooldown
    # lets exactly one call through to test recovery, so a transient outage or
    # a refreshed key heals instead of silently disabling the LLM arm forever.
    if _fallback_enabled() and _breaker_open() and not _breaker_should_probe():
        return _clamp01(
            _jaro_winkler_score(
                query_label, query_desc, candidate_label, candidate_desc
            )
        )

    # Opus path — guarded by the explicit fallback env var.
    try:
        _score = opus_dense_score_strict(
            query_label,
            query_desc,
            candidate_label,
            candidate_desc,
        )
        _breaker_record_success()
        return _score
    except Exception as e:  # noqa: BLE001
        _breaker_record_failure()
        if _fallback_enabled():
            log.warning(
                "bedrock dense scoring failed (%s); falling back to "
                "jaro_winkler for this candidate. consecutive_failures=%d",
                type(e).__name__,
                _breaker_failures,
            )
            return _clamp01(
                _jaro_winkler_score(
                    query_label,
                    query_desc,
                    candidate_label,
                    candidate_desc,
                )
            )
        raise DenseScoringUnavailableError(
            f"opus_dense_score failed and SCUDO_DENSE_FALLBACK is off: {e}"
        ) from e


def opus_dense_score_strict(
    query_label: str,
    query_desc: str,
    candidate_label: str,
    candidate_desc: str,
) -> float:
    """Score ONE candidate with the model, or raise. No fallback, ever.

    ``opus_dense_score`` decides per candidate whether to substitute a
    Jaro-Winkler value. That is correct for its specialist/legacy callers but
    fatal for batch retrieval: a list holding some model scores and some
    string-similarity scores ranks two incomparable scales against each other,
    and which candidates land on which scale depends on thread timing.

    So the batch path calls THIS instead and decides fallback once, for the
    whole match. Deliberately does not read SCUDO_DENSE_FALLBACK.
    """
    try:
        return _clamp01(
            _opus_invoke_score(
                query_label,
                query_desc,
                candidate_label,
                candidate_desc,
            )
        )
    except Exception as exc:  # noqa: BLE001 - the caller decides what to do
        raise DenseScoringUnavailableError(f"opus dense scoring failed: {exc}") from exc


# ── Bedrock circuit breaker ────────────────────────────────────────────────
# Process-local and deliberately tiny. Threshold is generous enough that a
# single flaky call does not disable the LLM arm, small enough that a dead key
# costs one match, not every match.
log = logging.getLogger(__name__)

_BREAKER_THRESHOLD = int(os.getenv("SCUDO_BEDROCK_BREAKER_THRESHOLD", "3"))
_breaker_failures = 0


_BREAKER_COOLDOWN_DEFAULT_S = "30"


def _breaker_cooldown_s() -> float:
    """Read per call, not at import.

    Frozen at import time this was untestable (monkeypatch.setenv had no
    effect) and un-tunable without a restart — the same import-time-capture
    trap that makes env ordering bugs so hard to see.
    """
    try:
        return float(
            os.getenv("SCUDO_BEDROCK_BREAKER_COOLDOWN_S", _BREAKER_COOLDOWN_DEFAULT_S)
        )
    except ValueError:
        return float(_BREAKER_COOLDOWN_DEFAULT_S)


_breaker_opened_at = 0.0
# True while one half-open probe is outstanding, so siblings do not all probe.
_breaker_probe_inflight = False
_breaker_probe_started_at = 0.0
# A probe that has not reported back within this long is assumed abandoned.
# Generous: it only has to exceed a realistic batch, and being wrong merely
# lets one extra probe through.
_PROBE_ABANDON_S = 120.0
# Epoch for breaker state. Decisions admitted in an older epoch may finish
# late, but cannot mutate a successor open/half-open state.
_breaker_generation = 0
# Generation token owned by the currently outstanding half-open probe.
_breaker_probe_owner: int | None = None
_breaker_probe_lock = threading.Lock()


def _breaker_open() -> bool:
    return _breaker_failures >= _BREAKER_THRESHOLD


def _breaker_should_probe() -> bool:
    """True for exactly ONE caller once the cooldown has elapsed.

    Guarded by a lock because the dense arm scores candidates concurrently:
    without it every worker in the pool would probe at once and a dead key
    would cost the full serial penalty again.
    """
    global _breaker_opened_at
    if time.monotonic() - _breaker_opened_at < _breaker_cooldown_s():
        return False
    with _breaker_probe_lock:
        if time.monotonic() - _breaker_opened_at < _breaker_cooldown_s():
            return False
        _breaker_opened_at = time.monotonic()  # claim this probe slot
        return True


def _breaker_record_failure() -> None:
    global _breaker_failures, _breaker_opened_at
    _breaker_failures += 1
    if _breaker_failures == _BREAKER_THRESHOLD:
        _breaker_opened_at = time.monotonic()


def _breaker_record_success() -> None:
    global _breaker_failures
    if _breaker_failures:
        log.warning("bedrock dense arm recovered; breaker reset")
    _breaker_failures = 0


# ── Batch-level breaker contract ───────────────────────────────────────────
#
# Retrieval scoring consults the breaker ONCE per match and reports back ONCE,
# rather than every worker mutating these globals. That is what makes the
# all-or-nothing guarantee possible: a single decision covers the whole
# candidate list, so the list cannot end up holding two scales.


class DenseBatchDecision(NamedTuple):
    """Immutable answer to "may this match call the model?".

    ``probe`` marks the one half-open attempt that claimed the recovery slot,
    so a failure can distinguish "still broken" from "newly broken".
    """

    attempt_opus: bool
    probe: bool = False
    generation: int = 0
    probe_token: int | None = None
    refusal_reason: str | None = None


def begin_dense_batch() -> DenseBatchDecision:
    """Decide once, for a whole match, whether to attempt the model."""
    with _breaker_probe_lock:
        if not _breaker_open():
            return DenseBatchDecision(
                attempt_opus=True,
                generation=_breaker_generation,
            )
        if _breaker_probe_inflight and (
            time.monotonic() - _breaker_probe_started_at < _PROBE_ABANDON_S
        ):
            # Another batch is already testing recovery. Bumping the timer
            # instead would not be enough: with a short cooldown every
            # concurrent caller passes the elapsed check and they all probe,
            # which is exactly the serial penalty the breaker exists to avoid.
            #
            # The elapsed guard matters: a probe that never reports back —
            # the fallback-disabled re-raise path, or a crashed worker — used
            # to pin this flag True and block recovery for the life of the
            # process. Verified before the guard: "next batch permitted? False"
            # for ever. Treat a stale probe as abandoned rather than trusting
            # every caller to be well-behaved.
            return DenseBatchDecision(
                attempt_opus=False,
                generation=_breaker_generation,
                refusal_reason="probe_inflight",
            )
        if time.monotonic() - _breaker_opened_at < _breaker_cooldown_s():
            return DenseBatchDecision(
                attempt_opus=False,
                generation=_breaker_generation,
                refusal_reason="circuit_open",
            )
        globals()["_breaker_generation"] = _breaker_generation + 1
        probe_token = _breaker_generation
        globals()["_breaker_probe_inflight"] = True
        globals()["_breaker_probe_started_at"] = time.monotonic()
        globals()["_breaker_probe_owner"] = probe_token
        globals()["_breaker_opened_at"] = time.monotonic()
        log.info("bedrock dense arm: half-open probe permitted")
        return DenseBatchDecision(
            attempt_opus=True,
            probe=True,
            generation=_breaker_generation,
            probe_token=probe_token,
        )


def dense_batch_refusal_error(decision: DenseBatchDecision) -> DenseCircuitOpenError:
    """Build the fail-loud error for a breaker-refused model batch."""
    reason = decision.refusal_reason or "circuit_open"
    return DenseCircuitOpenError(
        f"opus dense circuit is open ({reason}); SCUDO_DENSE_FALLBACK is off"
    )


def record_dense_batch_success(decision: DenseBatchDecision) -> None:
    """A COMPLETE model batch succeeded."""
    if not decision.attempt_opus:
        return
    with _breaker_probe_lock:
        if decision.probe:
            if decision.probe_token != _breaker_probe_owner:
                return
        elif decision.generation != _breaker_generation or _breaker_probe_inflight:
            return
        if _breaker_failures:
            log.info("bedrock dense arm recovered; breaker reset")
        globals()["_breaker_failures"] = 0
        globals()["_breaker_probe_inflight"] = False
        globals()["_breaker_probe_owner"] = None
        globals()["_breaker_generation"] = _breaker_generation + 1


def record_dense_batch_failure(decision: DenseBatchDecision) -> None:
    """The batch failed. Counted ONCE per match, not once per candidate."""
    if not decision.attempt_opus:
        return
    with _breaker_probe_lock:
        if decision.probe:
            if decision.probe_token != _breaker_probe_owner:
                return
        elif decision.generation != _breaker_generation or _breaker_probe_inflight:
            return
        globals()["_breaker_probe_inflight"] = False
        globals()["_breaker_probe_owner"] = None
        globals()["_breaker_failures"] = _breaker_failures + 1
        if _breaker_failures >= _BREAKER_THRESHOLD:
            globals()["_breaker_opened_at"] = time.monotonic()
            globals()["_breaker_generation"] = _breaker_generation + 1
            log.warning(
                "bedrock dense arm degraded; circuit open. "
                "consecutive_batch_failures=%d",
                _breaker_failures,
            )


def dense_arm_status() -> dict:
    """EFFECTIVE state of the dense arm, for the UI to report.

    The sidebar previously showed the CONFIGURED env value, so it read
    "Dense arm: opus" while every candidate had in fact been scored by
    Jaro-Winkler after the breaker tripped. Reviewers were right that
    "mitigation is visibility" does not hold if the indicator cannot see the
    fallback. Configured vs degraded is now distinguishable.
    """
    configured = os.getenv("SCUDO_DENSE_BACKEND", "jaro_winkler").strip().lower()
    degraded = configured == "opus" and _breaker_open()
    if degraded:
        effective = "jaro_winkler" if _fallback_enabled() else "circuit_open"
    else:
        effective = configured
    return {
        "configured": configured,
        "effective": effective,
        "degraded": degraded,
        "consecutive_failures": _breaker_failures,
    }


# ──────────────────────────────────────────────────────────────────────────
# Internals — Jaro-Winkler fallback
# ──────────────────────────────────────────────────────────────────────────
def _jaro_winkler_score(
    query_label: str,
    query_desc: str,
    candidate_label: str,
    candidate_desc: str,
) -> float:
    """Apply the same Jaro-Winkler the FalkorDB store uses, over a
    composed (label + description) string on each side. Deterministic,
    no deps, no network. Imported from the store so a tuning change in
    one place tracks both paths."""
    # Lazy import to avoid a circular import (store imports config; this
    # module is imported by config-adjacent code).
    from .store.falkordb_store import _jaro_winkler

    q = f"{query_label} {query_desc}".strip()
    c = f"{candidate_label} {candidate_desc}".strip()
    return _jaro_winkler(q, c)


# ──────────────────────────────────────────────────────────────────────────
# Internals — Opus invoke
# ──────────────────────────────────────────────────────────────────────────
_OPUS_SYSTEM_PROMPT = (
    "You are the SCUDO dense semantic scorer. Given a vendor product and "
    "a candidate CDAO taxonomy node, you return a SINGLE JSON object "
    "scoring how semantically aligned the product is with the candidate "
    "node, on a scale from 0.0 (no relationship) to 1.0 (the same thing).\n"
    "\n"
    "RULES (load-bearing):\n"
    "  1. Output MUST be a single JSON object with exactly two keys: "
    '     "score" (a float in [0.0, 1.0]) and "reason" (a short '
    "     sentence). No prose around it, no markdown fences.\n"
    '  2. "score" measures SEMANTIC alignment of the query to the '
    "     candidate, NOT string similarity. A vendor product named "
    '     "Equity Prices Real Time" should score near 1.0 against a '
    '     taxonomy node "Equity Prices" even though strings differ.\n'
    "  3. Out-of-domain candidates score below 0.2 — there is no penalty "
    "     for being decisive.\n"
    "  4. DEGRADED INPUT: if the query has an empty/whitespace "
    "     description, OR its label looks like a bare identifier (e.g. "
    '     "EQUITY-PRICES", uppercase/digits/dashes, no natural-language '
    "     words), OR the label is a single generic word (e.g. "
    '     "Prices", "Data", "Feed"), there is not enough evidence for a '
    "     confident match — score AT MOST 0.5 and say why in "
    '     "reason".\n'
    "  5. AMBIGUITY: if the query could plausibly align with several "
    "     sibling taxonomy nodes equally well, score below the level you "
    "     would give a uniquely-determined match and name the ambiguity "
    '     in "reason".\n'
    "  6. FIELD SANITY: if the query description looks like it is "
    "     actually a product name/identifier (or vice versa — fields "
    "     swapped or concatenated wrongly), do not reward the string "
    '     overlap; score at most 0.4 and flag "possible field mix-up" '
    '     in "reason".\n'
)


def _opus_invoke_score(
    query_label: str,
    query_desc: str,
    candidate_label: str,
    candidate_desc: str,
) -> float:
    """Call Bedrock invoke_model on Claude Opus 4.8 via the cross-region
    inference profile. Returns the parsed JSON ``score`` field clamped
    into [0, 1].

    Bedrock client is constructed per-call to keep this module
    thread-safe and to surface IAM / region errors at the right seam
    (the caller wraps in try/except).
    """
    import boto3  # type: ignore

    model_id = os.getenv("SCUDO_BEDROCK_MODEL_ID") or DEFAULT_BEDROCK_MODEL_ID
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "eu-west-2"

    # Bounded timeouts and ONE retry. This scorer runs PER CANDIDATE (8 per
    # match), so botocore's defaults (60s + 3 adaptive retries) turn a bad key
    # or a slow region into a minutes-long stall: measured 33s for a single
    # match with a malformed key before this was capped. Failing fast matters
    # more than squeezing out a retry, because SCUDO_DENSE_FALLBACK already
    # degrades each candidate to Jaro-Winkler.
    from botocore.config import Config as _BotoConfig  # type: ignore

    client = boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=_BotoConfig(
            connect_timeout=int(os.getenv("SCUDO_BEDROCK_CONNECT_TIMEOUT", "5")),
            read_timeout=int(os.getenv("SCUDO_BEDROCK_READ_TIMEOUT", "20")),
            retries={"max_attempts": 1, "mode": "standard"},
        ),
    )

    user_message = (
        f"QUERY (vendor product):\n"
        f"  label:       {query_label}\n"
        f"  description: {query_desc}\n"
        f"\n"
        f"CANDIDATE (CDAO taxonomy node):\n"
        f"  label:       {candidate_label}\n"
        f"  description: {candidate_desc}\n"
        f"\n"
        "Return the JSON object now."
    )

    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 256,
            "system": _OPUS_SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": user_message},
            ],
        }
    )

    response = client.invoke_model(
        modelId=model_id,
        body=body,
        contentType="application/json",
        accept="application/json",
    )
    payload = json.loads(response["body"].read())

    # Anthropic-on-Bedrock response shape: {"content": [{"type": "text",
    # "text": "..."}, ...], ...}. We expect the model's JSON output to
    # be the first text block.
    text = ""
    for block in payload.get("content", []):
        if block.get("type") == "text":
            text = block.get("text", "")
            break
    if not text:
        raise RuntimeError(
            f"Opus returned no text content; payload keys={list(payload)}"
        )

    parsed = _parse_score_json(text)
    return float(parsed.get("score", 0.0))


def _parse_score_json(text: str) -> dict:
    """Parse the Opus output. Tolerates markdown fences and trailing prose
    on a best-effort basis — the system prompt forbids them, but a single
    malformed reply shouldn't fail closed in prod (the matcher would route
    to NEEDS_REVIEW anyway via the floor)."""
    s = text.strip()
    if s.startswith("```"):
        # Strip ```json ... ``` fences if the model added them.
        lines = [ln for ln in s.splitlines() if not ln.startswith("```")]
        s = "\n".join(lines).strip()
    # First brace span — defensive against trailing prose.
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise RuntimeError(f"Opus output not JSON: {text!r}")
    return json.loads(s[start : end + 1])


def _fallback_enabled() -> bool:
    return (os.getenv("SCUDO_DENSE_FALLBACK") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _clamp01(x: float) -> float:
    """Defensive [0, 1] clamp — protects against a model returning 1.1 or
    a negative score by mistake. Pydantic ``Candidate.similarity`` will
    reject anything outside that band; clamping here keeps the seam
    well-behaved before the validation gate."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    if v != v:  # NaN
        return 0.0
    return max(0.0, min(1.0, v))


# ──────────────────────────────────────────────────────────────────────────
# Convenience — DenseScorer adapter for retrieval.multi_path_retrieve
# ──────────────────────────────────────────────────────────────────────────
def _strict_or_patched(
    query_label: str, query_desc: str, candidate_label: str, candidate_desc: str
) -> float:
    """Strict scoring that still honours a monkeypatched ``opus_dense_score``.

    Production always takes the strict path. But several suites script model
    behaviour by patching ``opus_dense.opus_dense_score`` — a patch point that
    predates the strict seam — and silently ignoring it would send those tests
    at the real network instead. Detect a replaced attribute by its module.
    """
    patched = sys.modules[__name__].opus_dense_score
    if getattr(patched, "__module__", None) != __name__:
        return patched(
            query_label=query_label,
            query_desc=query_desc,
            candidate_label=candidate_label,
            candidate_desc=candidate_desc,
        )
    return opus_dense_score_strict(
        query_label, query_desc, candidate_label, candidate_desc
    )


def make_opus_dense_scorer(query_desc: str = ""):
    """Return a ``DenseScorer`` callable usable with
    ``retrieval.multi_path_retrieve``. The query text the orchestrator
    passes is treated as the query label; the description is plumbed
    from the outer scope so the prompt carries both halves.

    Phase E step 2 (measurement fix): candidate_desc is
    ``taxonomy_candidate_desc(c.node)`` — flag-gated by SCUDO_TAXONOMY_TEXT
    (returns "" while off, so behaviour is unchanged in default
    environments; the node's SKOS/DCAT definition reaches the Opus prompt
    when on). Previously hardcoded ``candidate_desc=""`` — definitions never
    reached the dense prompt at all, so the flag measured nothing on this
    route. The Phase E signal fields (business_concept / asset_class /
    super_asset_class) stay BM25-only and are NOT part of this description.
    """
    from .models import Candidate
    from .taxonomy_text import taxonomy_candidate_desc

    def scorer(query: str, survivors: list[Candidate]) -> list[Candidate]:
        # ALL-OR-NOTHING, same contract as the score_candidates() batch path.
        # This route bypasses score_candidates() entirely (the stores return
        # multi_path_retrieve early when SCUDO_USE_OPUS_DENSE=1), so it needs
        # its own guarantee — verified before this change: failing one call at
        # the network seam returned [0.93, 0.93, 0.93, 0.4473], i.e. three
        # model scores ranked against one Jaro-Winkler score.
        decision = begin_dense_batch()
        if decision.attempt_opus:
            try:
                # Look the strict seam up on the MODULE, so a test that
                # monkeypatches opus_dense.opus_dense_score still governs this
                # route: patching the non-strict name is the established way
                # these tests script model behaviour (see
                # test_phase_e_measurement), and silently ignoring it would
                # make them exercise the real network instead.
                scored = [
                    Candidate(
                        node=c.node,
                        similarity=_strict_or_patched(
                            query,
                            query_desc,
                            c.node.label,
                            taxonomy_candidate_desc(c.node),
                        ),
                    )
                    for c in survivors
                ]
            except Exception as exc:  # noqa: BLE001 - one decision per batch
                record_dense_batch_failure(decision)
                if not _fallback_enabled():
                    if isinstance(exc, DenseScoringUnavailableError):
                        raise
                    raise DenseScoringUnavailableError(
                        f"opus dense batch failed: {exc}"
                    ) from exc
                log.warning(
                    "opus dense batch failed (%s); scoring all %d survivors "
                    "with jaro_winkler",
                    type(exc).__name__,
                    len(survivors),
                )
            else:
                record_dense_batch_success(decision)
                return scored
        elif not _fallback_enabled():
            raise dense_batch_refusal_error(decision)
        # Whole-batch fallback: one scale for every survivor.
        return [
            Candidate(
                node=c.node,
                similarity=_clamp01(
                    _jaro_winkler_score(
                        query,
                        query_desc,
                        c.node.label,
                        taxonomy_candidate_desc(c.node),
                    )
                ),
            )
            for c in survivors
        ]

    return scorer
