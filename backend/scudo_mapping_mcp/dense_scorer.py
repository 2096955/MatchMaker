"""
Opus-as-dense-scorer surface.

⚠️  DEPRECATED — NOT WIRED. See ``opus_dense.py`` for the canonical
    Opus dense-scoring surface that is actually wired into the matcher
    today (both via ``store/falkordb_store.py`` flag-on path and the
    legacy ``SCUDO_DENSE_BACKEND=opus`` path).

    This module ships a BATCH scoring shape
    (``score_candidates(query_text, candidates) -> list[Candidate]``)
    that takes a candidate list in one Opus call. ``opus_dense.py``
    ships a PER-PAIR shape that's already integrated end-to-end and
    backed by the smoke gates the matcher actually exercises.

    Closes ARB finding A2 (two parallel Opus surfaces). The dead twin
    is preserved (not deleted) only because its smoke gates still
    encode useful constraints for the batch shape — when we revisit
    a batched scorer for cost optimisation, those tests will be the
    starting point.

WHY THIS MODULE EXISTS (historical — pre-deprecation)
-----------------------------------------------------
Titan embeddings are PARKED for the demo build. The matching ladder still
needs a dense-similarity signal richer than the Jaro-Winkler stand-in
shipped in ``store/falkordb_store.py``. Claude Opus 4.8 (already wired
into the package via the BedrockMappingAgent in ``agent.py``) can act as
the dense scorer DIRECTLY: it reads the vendor product text, looks at
the candidate CDAO nodes, and returns a similarity score in [0, 1] per
candidate. The matcher can then sort by that score the same way it would
sort by a cosine-on-Titan score.

This is a STAND-IN, not a replacement: when Titan unparks, the contract
below is the seam where embeddings re-enter — same input shape, same
output shape, swap the body.

CONTRACT — pinned
-----------------
``score_candidates(query_text, candidates, *, model_client=None,
max_candidates=25) -> list[Candidate]``

  * Input ordering is PRESERVED on the returned list — callers that
    pre-filtered to top-K can re-sort by the new ``.similarity``
    themselves; this function does NOT re-sort. Stability is what lets
    the matcher inject a deterministic tie-break.
  * ``.similarity`` on the returned candidates is the Opus-derived
    score in [0, 1]. Every other field on the Candidate (the node and
    its label / parent / children) is COPIED FROM THE INPUT — Opus is
    NOT trusted to invent taxonomy structure (I5 cap on LLM outputs).
  * Input cap: ``len(candidates) > max_candidates`` raises
    ``ValueError``. We do NOT truncate silently — the matcher should
    pre-filter to the top-K it cares about. A silent truncation would
    let a regression in the retrieval stage drop legitimate candidates
    on the floor without surfacing the drop in any test.
  * Response validation: every input IRI must appear exactly once in
    the Opus response; scores must be in [0, 1]; abstain otherwise by
    raising ``DenseScoreError``. The matcher's borderline branch is the
    natural recipient — it can fall back to the deterministic floor
    check when this surface abstains, the same path it takes when no
    specialist is passed at all.

DI / TESTABILITY
----------------
``model_client`` is injectable so the smoke tests can stub Opus without
calling Bedrock. ``None`` means construct a default Strands + Bedrock
client lazily on first use (module-singleton), the same pattern
``agent.BedrockMappingAgent._build_agent`` follows. A test that mocks
this client never reaches boto3.

THREAD-SAFETY
-------------
The module-singleton client is read-only after construction. The seam
itself is a pure function — no module state besides the cached client.
The matcher's per-call specialist seam is the surface that USES this
function, and that seam is explicitly per-call (matching.py §SpecialistScorer)
so concurrent matcher calls do not poison each other.

PROMPT SHAPE
------------
Committed to the module as constants so the tunables are visible in one
place and the smoke gate can pin them. Temperature pinned LOW (0.1) so a
replay against the same prompt yields the same scores within a small
window — matters for the demo's reproducibility story.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from .models import Candidate, TaxonomyNode


# ──────────────────────────────────────────────────────────────────────────
# Public error type
# ──────────────────────────────────────────────────────────────────────────
class DenseScoreError(RuntimeError):
    """Raised when Opus fails or returns an unparseable / invalid response.

    The matcher catches this at the specialist-seam boundary and falls back
    to the deterministic floor check — same path as when no specialist is
    passed at all. This keeps the LLM from poisoning a borderline decision
    when it misbehaves; abstain-on-uncertainty is the rule (I5).
    """


# ──────────────────────────────────────────────────────────────────────────
# Prompt shape — committed; tune in one place
# ──────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "You score how well each CDAO taxonomy node matches a vendor product. "
    "For each candidate node, output a similarity score in [0, 1] reflecting "
    "the strength of the semantic match between the vendor product and the "
    "node's label.\n\n"
    "Scoring rubric — apply consistently:\n"
    "  - 0.9 or higher: clear semantic match (the vendor product is "
    "obviously the same thing the node describes).\n"
    "  - 0.5 to 0.8:    partial fit (related domain, but a different facet "
    "or granularity).\n"
    "  - Below 0.5:     poor fit (the node is in a different area entirely).\n\n"
    "Be calibrated, not generous. The downstream matcher applies a 0.80 "
    "confidence floor, so inflated scores cause incorrect auto-mappings — "
    "an error mode the deterministic retrieval cannot recover."
)

# Pinned low for replay-safety. The demo's reproducibility story relies on
# the same product + same candidates producing the same band decision; high
# temperature would let the borderline band flip on every run.
SCORING_TEMPERATURE = 0.1

# Pin the model id to the same cross-region inference profile the agent uses.
# Single source of truth: ``agent.DEFAULT_BEDROCK_MODEL_ID``. Imported lazily
# to avoid loading agent (and its store dependencies) at module-import time.
def _default_model_id() -> str:
    # SCUDO_BEDROCK_MODEL_ID is the same env var the agent honours, so a
    # single override switches BOTH the agent and this dense scorer.
    from .agent import DEFAULT_BEDROCK_MODEL_ID
    return os.getenv("SCUDO_BEDROCK_MODEL_ID") or DEFAULT_BEDROCK_MODEL_ID


def _default_region() -> str:
    # Same precedence chain as the agent and the rest of the package.
    return (
        os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or "eu-west-2"
    )


# ──────────────────────────────────────────────────────────────────────────
# Module-singleton client — avoid per-call construction cost
# ──────────────────────────────────────────────────────────────────────────
# Strands' BedrockModel wraps boto3; constructing it on every borderline
# call would add noticeable latency to the cost-ladder Rung 4 path. The
# matcher invokes this on EVERY borderline case, so the savings compound.
# Lazily-initialised so importing this module never forces a Bedrock dep
# (the package still has to load on dev machines without boto3 configured).
_DEFAULT_CLIENT: Any = None


def _get_default_client() -> Any:
    """Lazily build the default Strands + Bedrock client.

    Mirrors ``agent.BedrockMappingAgent._build_agent``. Tests should pass
    a stub via the ``model_client`` kwarg and never reach this code path.
    """
    global _DEFAULT_CLIENT
    if _DEFAULT_CLIENT is not None:
        return _DEFAULT_CLIENT
    try:
        from strands.models import BedrockModel  # type: ignore
    except ImportError as e:
        raise DenseScoreError(
            "strands-agents is required to use the default Opus dense "
            "scorer. Install with: pip install strands-agents>=0.1, or "
            "pass a stubbed model_client kwarg for tests."
        ) from e
    _DEFAULT_CLIENT = BedrockModel(
        model_id=_default_model_id(),
        region_name=_default_region(),
    )
    return _DEFAULT_CLIENT


# ──────────────────────────────────────────────────────────────────────────
# Public scoring function — the contract
# ──────────────────────────────────────────────────────────────────────────
def score_candidates(
    query_text: str,
    candidates: list[Candidate],
    *,
    model_client: Optional[Any] = None,
    max_candidates: int = 25,
) -> list[Candidate]:
    """Score each candidate's similarity to ``query_text`` using Opus.

    Returns a new list of ``Candidate`` objects in the SAME ORDER as the
    input, with ``.similarity`` replaced by the Opus-derived score in
    [0, 1]. The underlying ``TaxonomyNode`` is copied unchanged — Opus is
    not trusted to invent or rewrite taxonomy structure.

    Raises:
        ValueError: if ``len(candidates) > max_candidates``. Truncation
            is intentionally NOT silent — the matcher should pre-filter.
        DenseScoreError: on Opus failure, malformed JSON, missing or
            extra IRIs in the response, or out-of-range scores. The
            matcher treats this the same as "no specialist passed".
    """
    if len(candidates) > max_candidates:
        raise ValueError(
            f"score_candidates received {len(candidates)} candidates; "
            f"max_candidates={max_candidates}. Pre-filter at the matcher "
            f"before calling — silent truncation would hide retrieval "
            f"regressions."
        )

    # Empty input — nothing to score. Don't invoke Opus.
    if not candidates:
        return []

    client = model_client if model_client is not None else _get_default_client()

    user_prompt = _build_user_prompt(query_text, candidates)
    raw_response = _invoke_model(client, user_prompt)
    scores_by_iri = _parse_response(raw_response, candidates)

    # Re-emit candidates in input order with the new similarity score.
    out: list[Candidate] = []
    for c in candidates:
        score = scores_by_iri[c.node.iri]
        out.append(Candidate(
            node=TaxonomyNode(
                iri=c.node.iri,
                label=c.node.label,
                parent_iri=c.node.parent_iri,
                children_iris=list(c.node.children_iris),
            ),
            similarity=score,
        ))
    return out


# ──────────────────────────────────────────────────────────────────────────
# Internal: prompt construction
# ──────────────────────────────────────────────────────────────────────────
def _build_user_prompt(query_text: str, candidates: list[Candidate]) -> str:
    """Build the user-facing prompt with the rendered candidate list.

    Stable, line-oriented format — easy for Opus to follow and for a
    reviewer to read in the smoke output.
    """
    lines = ["Vendor product:", query_text.strip() or "(no text supplied)", ""]
    lines.append("Candidates:")
    for i, c in enumerate(candidates, start=1):
        # IRI first so the model's JSON key matches what we expect on parse.
        lines.append(f"{i}. {c.node.iri}: {c.node.label}")
    lines.append("")
    lines.append(
        "Return JSON only, no prose: "
        "{\"scores\": [{\"iri\": \"<iri>\", \"score\": <float in [0,1]>}, ...]}"
    )
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────
# Internal: model invocation (kept tiny — easy to stub in tests)
# ──────────────────────────────────────────────────────────────────────────
def _invoke_model(client: Any, user_prompt: str) -> str:
    """Invoke Opus and return the raw text response.

    The client surface accepted here is whatever Strands' BedrockModel
    exposes today (``.converse`` / ``.generate`` / ``.complete``), or any
    duck-typed stub that returns a string. The shim tries the most
    common shapes and falls back to ``str(client(...))`` so smoke tests
    can pass a plain callable.
    """
    try:
        # 1. Plain callable — what the smoke tests pass (a function that
        #    takes (system, user, temperature) and returns a string).
        if callable(client) and not hasattr(client, "converse") \
                and not hasattr(client, "generate"):
            return str(client(
                system=SYSTEM_PROMPT,
                user=user_prompt,
                temperature=SCORING_TEMPERATURE,
            ))
        # 2. Strands BedrockModel (or any object with .converse / .generate).
        for attr in ("converse", "generate", "complete", "invoke"):
            fn = getattr(client, attr, None)
            if fn is None:
                continue
            response = fn(
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
                temperature=SCORING_TEMPERATURE,
            )
            # Strands' converse returns an object; extract text best-effort.
            if isinstance(response, str):
                return response
            if isinstance(response, dict):
                # Anthropic-shape: {"content": [{"text": "..."}]}
                if "content" in response and isinstance(response["content"], list):
                    parts = [
                        p.get("text", "") for p in response["content"]
                        if isinstance(p, dict)
                    ]
                    return "".join(parts)
                # OpenAI-ish fallback: {"text": "..."}
                if "text" in response:
                    return str(response["text"])
            # Object with .text or .content attribute.
            text_attr = getattr(response, "text", None) \
                or getattr(response, "content", None)
            if text_attr is not None:
                return str(text_attr)
            return str(response)
        # 3. Last resort — call the object itself.
        return str(client(user_prompt))
    except DenseScoreError:
        raise
    except Exception as e:  # noqa: BLE001
        raise DenseScoreError(
            f"Opus invoke failed: {type(e).__name__}: {e}"
        ) from e


# ──────────────────────────────────────────────────────────────────────────
# Internal: response parsing + validation
# ──────────────────────────────────────────────────────────────────────────
def _parse_response(
    raw: str,
    candidates: list[Candidate],
) -> dict[str, float]:
    """Parse Opus' JSON response. Raise DenseScoreError on any anomaly.

    Validates:
      * The response is valid JSON.
      * It contains a ``scores`` list.
      * Every input IRI appears exactly once in the response.
      * No extra IRIs (Opus didn't hallucinate a new taxonomy node).
      * Every score is a number in [0, 1].
    """
    if not raw or not raw.strip():
        raise DenseScoreError("Opus returned an empty response.")

    # Some models wrap JSON in code fences or prose. Best-effort extract.
    text = _extract_json_block(raw)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise DenseScoreError(
            f"Opus response is not valid JSON: {e.msg}; raw={raw[:200]!r}"
        ) from e

    if not isinstance(payload, dict) or "scores" not in payload:
        raise DenseScoreError(
            f"Opus response missing 'scores' key; payload={payload!r}"
        )
    scores_list = payload["scores"]
    if not isinstance(scores_list, list):
        raise DenseScoreError(
            f"Opus 'scores' must be a list; got {type(scores_list).__name__}"
        )

    expected_iris = {c.node.iri for c in candidates}
    seen: dict[str, float] = {}
    for entry in scores_list:
        if not isinstance(entry, dict):
            raise DenseScoreError(
                f"Each score entry must be a JSON object; got {entry!r}"
            )
        iri = entry.get("iri")
        score = entry.get("score")
        if not isinstance(iri, str) or not iri:
            raise DenseScoreError(
                f"Score entry missing valid 'iri': {entry!r}"
            )
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise DenseScoreError(
                f"Score for {iri!r} must be numeric; got {score!r}"
            )
        if score < 0.0 or score > 1.0:
            raise DenseScoreError(
                f"Score for {iri!r} out of [0,1]: {score}"
            )
        if iri not in expected_iris:
            raise DenseScoreError(
                f"Opus invented an unknown candidate IRI: {iri!r}"
            )
        if iri in seen:
            raise DenseScoreError(
                f"Opus scored {iri!r} more than once."
            )
        seen[iri] = float(score)

    missing = expected_iris - seen.keys()
    if missing:
        raise DenseScoreError(
            f"Opus omitted scores for: {sorted(missing)!r}"
        )
    return seen


def _extract_json_block(raw: str) -> str:
    """Pull the first JSON object out of raw text.

    Opus normally returns clean JSON when asked, but sometimes wraps it in
    ```json ... ``` fences or adds a sentence before / after. This helper
    tolerates that without being so loose it accepts garbage.
    """
    s = raw.strip()
    # Strip triple-backtick fences if present.
    if s.startswith("```"):
        # Drop opening fence (with optional language tag) and closing fence.
        s = s.split("```", 2)
        # s is now ["", "json\n{...}\n", ""] or similar.
        if len(s) >= 2:
            inner = s[1]
            # Drop a leading "json" language tag if present.
            if "\n" in inner:
                first, _, rest = inner.partition("\n")
                if first.strip().lower() in {"json", ""}:
                    s = rest
                else:
                    s = inner
            else:
                s = inner
    s = s.strip()
    # If there's leading prose, find the first '{' and matching close.
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return s[start:end + 1]
    return s
