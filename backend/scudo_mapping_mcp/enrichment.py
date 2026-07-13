"""
M10 conceptual-enrichment agent (Section 10c).

Runs strictly AFTER a mapping is AUTO_MAPPED/APPROVED/OVERRIDDEN — never inside
the cost ladder (invariant I5: the ladder's decision surface doesn't grow a
new LLM vote; this agent only proposes additive metadata that hangs off an
already-decided ``Concept``).

Two entry points, mirroring the existing Opus seams:

    extract_field_structure(ref, concept_iri) -> ConceptualGraph
        Proposes a Field/FieldGroup/BusinessDataElement breakdown of a
        vendor row. Same system-prompt-forces-single-JSON-object discipline
        as ``opus_dense._opus_invoke_score``/``_parse_score_json``.
        Malformed output -> empty ``ConceptualGraph`` (abstain), never
        partially-validated data onto the graph.

    classify_business_concept(ref, concept_iri, candidates) -> Optional[ConceptualNode]
        Re-scores ``candidates`` (already in the store) and returns its
        single best pick, or ``None`` on abstain. Off-list-rejection
        enforcement (fail-closed, confidence caps) happens at the CALL SITE
        per house convention — mirrors how ``specialist.py`` proposes and
        ``matching.py`` enforces, not folded into this function.

FEATURE FLAG — ``SCUDO_ENRICHMENT_BACKEND`` (``config.Settings.enrichment_backend``):
    "off" (default): both functions abstain immediately with no AWS call —
        ``extract_field_structure`` returns an empty ``ConceptualGraph``,
        ``classify_business_concept`` returns ``None``. Smoke gates stay
        green without Bedrock creds.
    "opus": real Bedrock invoke_model call on Claude Opus 4.8, same
        cross-region inference profile as ``opus_dense.py``.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Optional

from .config import settings
from .models import (
    ConceptualEdge,
    ConceptualEdgeKind,
    ConceptualGraph,
    ConceptualNode,
    ConceptualNodeKind,
    VendorProductRef,
    conceptual_iri,
)
from .opus_dense import DEFAULT_BEDROCK_MODEL_ID

# Hard ceiling for the synchronous classify/extract calls (seconds) — mirrors
# specialist.py's SCUDO_SPECIALIST_TIMEOUT_S guard so an enrichment call
# can't hang past the route's request lifetime.
_TIMEOUT_S = float(os.getenv("SCUDO_ENRICHMENT_TIMEOUT_S", "20"))

# data_type values the extractor is allowed to emit — anything else from the
# model is coerced to "string" rather than let arbitrary text into the graph.
_ALLOWED_DATA_TYPES = frozenset({"string", "date", "decimal", "integer", "boolean"})


def _call_with_timeout(fn, *args):
    """Run ``fn(*args)`` in a single-worker executor with a hard wall-clock
    timeout, returning ``None`` on timeout or any exception. Manual
    ``ThreadPoolExecutor`` management (not ``with``) — specialist.py's exact
    discipline: ``__exit__`` would call ``shutdown(wait=True)`` and re-block
    on a hung Bedrock call right after the timeout fired.
    """
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        fut = ex.submit(fn, *args)
        result = fut.result(timeout=_TIMEOUT_S)
        ex.shutdown(wait=False)
        return result
    except FutureTimeout:
        ex.shutdown(wait=False, cancel_futures=True)
        return None
    except Exception:  # noqa: BLE001 — abstain on any failure
        ex.shutdown(wait=False, cancel_futures=True)
        return None


# ──────────────────────────────────────────────────────────────────────────
# extract_field_structure
# ──────────────────────────────────────────────────────────────────────────
_EXTRACT_SYSTEM_PROMPT = (
    "You are the SCUDO conceptual-enrichment extractor. Given a vendor "
    "product's raw row data, you propose a Field/FieldGroup breakdown of "
    "its structure as a SINGLE JSON object.\n"
    "\n"
    "RULES (load-bearing):\n"
    "  1. Output MUST be a single JSON object with exactly one key: "
    '     "fields" — a list of objects, each with keys "vendor_field_name" '
    '     (string, must be an actual key from the raw row), "label" '
    '     (short human label), "data_type" (one of "string", "date", '
    '     "decimal", "integer", "boolean"), and "primary_key" (bool). '
    "     No prose around it, no markdown fences.\n"
    "  2. Only propose fields that are ACTUALLY PRESENT in the raw row "
    "     given below. Never invent a field name that isn't a key in it.\n"
    '  3. If the row has no fields worth structuring, return {"fields": []}.\n'
)


def extract_field_structure(ref: VendorProductRef, concept_iri: str) -> ConceptualGraph:
    """Propose a Field/FieldGroup breakdown of ``ref.raw`` for ``concept_iri``.

    Returns an empty ``ConceptualGraph`` (abstain) when the backend is
    "off", the Bedrock call fails, or the model's output doesn't parse into
    well-formed fields — never partially-validated data onto the graph.
    """
    empty = ConceptualGraph(root_concept_iri=concept_iri)
    if settings.enrichment_backend != "opus":
        return empty

    fields = _call_with_timeout(_extract_invoke, ref)
    if not fields:
        # None (timeout/error/abstain) or an all-off-list response that
        # filtered down to nothing — either way, no group-with-zero-fields
        # node should land on the graph.
        return empty

    nodes: list[ConceptualNode] = []
    edges: list[ConceptualEdge] = []
    group_local_id = f"field-group-{ref.vendor}-{ref.product_id}"
    group_iri = conceptual_iri(
        concept_iri, ConceptualNodeKind.FIELD_GROUP, group_local_id
    )
    nodes.append(
        ConceptualNode(
            iri=group_iri,
            kind=ConceptualNodeKind.FIELD_GROUP,
            label=ref.name or ref.product_id,
            attaches_to_concept_iri=concept_iri,
        )
    )
    for f in fields:
        field_local_id = f"field-{ref.vendor}-{ref.product_id}-{f['vendor_field_name']}"
        field_iri = conceptual_iri(
            concept_iri, ConceptualNodeKind.FIELD, field_local_id
        )
        nodes.append(
            ConceptualNode(
                iri=field_iri,
                kind=ConceptualNodeKind.FIELD,
                label=f["label"],
                attaches_to_concept_iri=concept_iri,
                vendor_field_name=f["vendor_field_name"],
                data_type=f["data_type"],
                primary_key=f["primary_key"],
                nullable=f.get("nullable"),
                sequence_number=f.get("sequence_number"),
            )
        )
        edges.append(
            ConceptualEdge(
                from_iri=group_iri,
                to_iri=field_iri,
                kind=ConceptualEdgeKind.CONTAINS,
                label="contains",
            )
        )

    return ConceptualGraph(root_concept_iri=concept_iri, nodes=nodes, edges=edges)


def _extract_invoke(ref: VendorProductRef) -> list[dict]:
    """Call Bedrock invoke_model on Claude Opus 4.8, parse and validate the
    proposed field list against ``ref.raw``'s actual keys. Raises on any
    malformed/off-list output — the caller treats any exception as abstain.
    """
    import boto3  # type: ignore

    model_id = os.getenv("SCUDO_BEDROCK_MODEL_ID") or DEFAULT_BEDROCK_MODEL_ID
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "eu-west-2"
    client = boto3.client("bedrock-runtime", region_name=region)

    raw_keys = sorted(ref.raw.keys())
    user_message = (
        f"VENDOR PRODUCT:\n"
        f"  name:        {ref.name}\n"
        f"  description: {ref.description}\n"
        f"  raw row keys: {raw_keys}\n"
        f"  raw row:     {json.dumps(ref.raw)}\n"
        "\n"
        "Return the JSON object now."
    )
    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "system": _EXTRACT_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_message}],
        }
    )
    response = client.invoke_model(
        modelId=model_id,
        body=body,
        contentType="application/json",
        accept="application/json",
    )
    payload = json.loads(response["body"].read())

    text = ""
    for block in payload.get("content", []):
        if block.get("type") == "text":
            text = block.get("text", "")
            break
    if not text:
        raise RuntimeError(
            f"Opus returned no text content; payload keys={list(payload)}"
        )

    parsed = _parse_extract_json(text)
    fields = parsed.get("fields")
    if not isinstance(fields, list):
        raise RuntimeError(f"Opus 'fields' is not a list: {parsed!r}")

    # String-normalize the raw row's keys before comparing — ref.raw is a
    # plain dict, and comparing a non-string proposed name against
    # non-string raw keys risks Python equality oddities (True == 1).
    return _validated_fields(fields, valid_keys={str(k) for k in ref.raw.keys()})


def _validated_fields(fields: list, *, valid_keys: set[str]) -> list[dict]:
    """Validate model-proposed field dicts against the allowed-keys contract.

    Phase C extension (spec "extractor asymmetry", acceptance-side only):
    ``nullable`` and ``sequence_number`` are now accepted — the fields exist
    on ``ConceptualNode`` and conceptual_layer.json fixtures carry them. The
    live prompt (``_EXTRACT_SYSTEM_PROMPT``) is deliberately UNCHANGED: the
    model is not asked for the new keys; when it volunteers them they are
    type-checked (bool / int, coerced to None otherwise — fail-closed like
    the data_type coercion) instead of silently dropped.
    """
    out: list[dict] = []
    for f in fields:
        if not isinstance(f, dict):
            continue
        raw_name = f.get("vendor_field_name")
        if raw_name is None:
            continue
        vendor_field_name = str(raw_name)
        # Off-list rejection: never fabricate a field name absent from the
        # actual raw row — mirrors the specialist's anchor-to-what's-offered
        # contract.
        if vendor_field_name not in valid_keys:
            continue
        data_type = str(f.get("data_type") or "string").strip().lower()
        if data_type not in _ALLOWED_DATA_TYPES:
            data_type = "string"
        primary_key_raw = f.get("primary_key", False)
        primary_key = primary_key_raw is True
        nullable_raw = f.get("nullable")
        nullable = nullable_raw if isinstance(nullable_raw, bool) else None
        seq_raw = f.get("sequence_number")
        sequence_number = (
            seq_raw
            if isinstance(seq_raw, int) and not isinstance(seq_raw, bool)
            else None
        )
        out.append(
            {
                "vendor_field_name": vendor_field_name,
                "label": str(f.get("label") or vendor_field_name),
                "data_type": data_type,
                "primary_key": primary_key,
                "nullable": nullable,
                "sequence_number": sequence_number,
            }
        )
    return out


def _parse_extract_json(text: str) -> dict:
    """Parse the Opus output. Tolerates markdown fences and trailing prose
    on a best-effort basis, same discipline as opus_dense._parse_score_json."""
    s = text.strip()
    if s.startswith("```"):
        lines = [ln for ln in s.splitlines() if not ln.startswith("```")]
        s = "\n".join(lines).strip()
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise RuntimeError(f"Opus output not JSON: {text!r}")
    return json.loads(s[start : end + 1])


# ──────────────────────────────────────────────────────────────────────────
# classify_business_concept
# ──────────────────────────────────────────────────────────────────────────
def classify_business_concept(
    ref: VendorProductRef,
    concept_iri: str,
    candidates: list[ConceptualNode],
) -> Optional[ConceptualNode]:
    """Re-score ``candidates`` and return the single best pick, or ``None``
    on abstain (backend off, timeout, error, or empty candidate list).

    Anchored to ``candidates`` already in the store — this function never
    returns a node outside what it's offered. Broader fail-closed policy
    (confidence caps / review routing) remains the call site's job, mirroring
    how matching.py enforces the rung-4 specialist's contract rather than
    specialist.py itself.
    """
    if settings.enrichment_backend != "opus" or not candidates:
        return None
    pick = _call_with_timeout(_classify_best_pick, ref, concept_iri, candidates)
    if pick is None:
        return None
    candidate_iris = {c.iri for c in candidates}
    if pick.iri not in candidate_iris:
        return None
    return pick


def _classify_best_pick(
    ref: VendorProductRef,
    concept_iri: str,
    candidates: list[ConceptualNode],
) -> Optional[ConceptualNode]:
    """Re-score each candidate with opus_dense_score and return the top one.
    Anchored strictly to ``candidates`` — never returns a node not in the
    list it was given."""
    from .opus_dense import opus_dense_score

    best: Optional[ConceptualNode] = None
    best_score = -1.0
    for node in candidates:
        score = opus_dense_score(
            query_label=ref.name or ref.product_id,
            query_desc=ref.description or "",
            candidate_label=node.label,
            candidate_desc="",
        )
        if score > best_score:
            best_score = score
            best = node
    return best
