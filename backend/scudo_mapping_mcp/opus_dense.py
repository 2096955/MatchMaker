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
no rerank, no fusion, no boost. The 0.80 confidence floor is calibrated
against this raw quantity (arb-review-pack §5.2 invariant: similarity
equals raw dense score).

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


# ──────────────────────────────────────────────────────────────────────────
# Bedrock model id — mirrors agent.py so the two paths can't drift.
# ──────────────────────────────────────────────────────────────────────────
DEFAULT_BEDROCK_MODEL_ID = "eu.anthropic.claude-opus-4-8"


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

    # Opus path — guarded by the explicit fallback env var.
    try:
        return _clamp01(
            _opus_invoke_score(
                query_label,
                query_desc,
                candidate_label,
                candidate_desc,
            )
        )
    except Exception as e:  # noqa: BLE001
        if _fallback_enabled():
            return _clamp01(
                _jaro_winkler_score(
                    query_label,
                    query_desc,
                    candidate_label,
                    candidate_desc,
                )
            )
        raise RuntimeError(
            f"opus_dense_score failed and SCUDO_DENSE_FALLBACK is off: {e}"
        ) from e


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

    client = boto3.client("bedrock-runtime", region_name=region)

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
        out: list[Candidate] = []
        for c in survivors:
            score = opus_dense_score(
                query_label=query,
                query_desc=query_desc,
                candidate_label=c.node.label,
                candidate_desc=taxonomy_candidate_desc(c.node),
            )
            out.append(Candidate(node=c.node, similarity=score))
        return out

    return scorer
