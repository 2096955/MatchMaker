"""Capone-shaped mapping façade for the Understand-Anything matching dashboard.

Dashboard client (`packages/dashboard/src/api/mapping.ts`) expects:
  GET  /api/mapping/vendors
  POST /api/mapping/ingest/stream   (SSE)
  POST /api/mapping/agent/run       (SSE)
  POST /api/mapping/decision

This module produces Capone-compatible SSE frames and decision bodies while
delegating judgement to jpmc-port orchestrator / teach→learn.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from typing import Any, Iterator, Optional

from . import local_state
from .handler import _run

log = logging.getLogger("scudo.dashboard_api")

PRIORITY_VENDORS: tuple[str, ...] = (
    "LSEG",
    "S&P Global",
    "Bloomberg",
    "ICE",
    "FactSet",
)

_ETL_STAGE_NODES: dict[str, list[str]] = {
    "received": ["etl-eventbridge", "etl-sqs"],
    "parse": ["etl-lambda"],
    "validate": ["etl-validate", "etl-s3-quarantine"],
    "sink": ["etl-s3-sink", "etl-dynamodb"],
}


def list_vendors() -> dict:
    return {
        "vendors": list(PRIORITY_VENDORS),
        "default": PRIORITY_VENDORS[0],
    }


def _normalize_vendor(vendor: str) -> str:
    return (vendor or "").strip()


def _vendor_key(vendor: str) -> str:
    """Lowercase slug for IRIs / WORKING_SET (LSEG → lseg, S&P Global → s&p global)."""
    return _normalize_vendor(vendor).lower()


def validate_vendor(vendor: str) -> Optional[str]:
    v = _normalize_vendor(vendor)
    if not v:
        return "vendor is required"
    if v not in PRIORITY_VENDORS and v.lower() not in {
        x.lower() for x in PRIORITY_VENDORS
    }:
        return f"unknown vendor {vendor!r} (valid: {', '.join(PRIORITY_VENDORS)})"
    return None


def _canonical_vendor(vendor: str) -> str:
    v = _normalize_vendor(vendor)
    for p in PRIORITY_VENDORS:
        if p.lower() == v.lower():
            return p
    return v


def parse_vendor_file(filename: str, data: bytes) -> list[dict]:
    """Parse CSV/JSON upload into product dicts {product_id, name, description}."""
    text = data.decode("utf-8-sig", errors="replace").strip()
    if not text:
        return []
    name = (filename or "").lower()
    rows: list[dict] = []
    if name.endswith(".json") or text[:1] in "[{":
        payload = json.loads(text)
        if isinstance(payload, dict):
            payload = payload.get("products") or payload.get("items") or [payload]
        for i, item in enumerate(payload or []):
            if not isinstance(item, dict):
                continue
            pid = str(
                item.get("product_id")
                or item.get("vendor_product_ref")
                or item.get("id")
                or item.get("Code")
                or item.get("PermID")
                or f"PROD-{i + 1:03d}"
            )
            rows.append(
                {
                    "product_id": pid,
                    "name": str(
                        item.get("name")
                        or item.get("title")
                        or item.get("Title")
                        or pid
                    ),
                    "description": str(
                        item.get("description")
                        or item.get("SummaryText")
                        or item.get("name")
                        or ""
                    ),
                }
            )
        return rows

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames:
        for i, item in enumerate(reader):
            lower = {str(k).lower(): v for k, v in item.items() if k is not None}
            pid = str(
                lower.get("product_id")
                or lower.get("vendor_product_ref")
                or lower.get("id")
                or lower.get("code")
                or lower.get("permid")
                or f"PROD-{i + 1:03d}"
            )
            rows.append(
                {
                    "product_id": pid,
                    "name": str(lower.get("name") or lower.get("title") or pid),
                    "description": str(
                        lower.get("description")
                        or lower.get("summarytext")
                        or lower.get("name")
                        or ""
                    ),
                }
            )
    return rows


def ingest_products(vendor: str, products: list[dict]) -> list[dict]:
    canon = _canonical_vendor(vendor)
    out = []
    for p in products:
        pid = str(p["product_id"])
        rec = {
            "vendor": canon,
            "product_id": pid,
            "name": p.get("name") or pid,
            "description": p.get("description") or "",
        }
        local_state.WORKING_SET[(_vendor_key(canon), pid)] = rec
        out.append(rec)
    return out


def iter_ingest_sse(vendor: str, filename: str, data: bytes) -> Iterator[str]:
    err = validate_vendor(vendor)
    if err:
        yield _sse({"type": "error", "error": err})
        yield _sse({"type": "done"})
        return
    try:
        products = parse_vendor_file(filename, data)
    except Exception as exc:
        yield _sse({"type": "error", "error": f"cannot parse vendor file: {exc}"})
        yield _sse({"type": "done"})
        return
    if not products:
        yield _sse({"type": "error", "error": "no products found in file"})
        yield _sse({"type": "done"})
        return

    stages = [
        ("received", {"bytes": len(data), "filename": filename}),
        ("parse", {"rows": len(products)}),
        ("validate", {"accepted": len(products), "rejected": 0}),
        ("sink", {"written": len(products)}),
    ]
    for stage, detail in stages:
        yield _sse(
            {
                "type": "stage",
                "stage": stage,
                "nodeIds": _ETL_STAGE_NODES.get(stage, []),
                "detail": detail,
            }
        )
    stored = ingest_products(vendor, products)
    yield _sse(
        {
            "type": "final_result",
            "ingested": len(stored),
            "products": [
                {
                    "vendor": p["vendor"],
                    "product_id": p["product_id"],
                    "name": p["name"],
                }
                for p in stored
            ],
        }
    )
    yield _sse({"type": "done"})


def _outcome_to_status(outcome: str, confidence: float) -> str:
    if outcome == "published":
        return "auto_mapped"
    if outcome == "research_queued":
        return "out_of_scope"
    if outcome in {"hitl", "retry"}:
        return "needs_review"
    if confidence >= 0.80:
        return "auto_mapped"
    return "needs_review"


def iter_agent_run_sse(body: dict) -> Iterator[str]:
    vendor = _canonical_vendor(str(body.get("vendor") or ""))
    product_id = str(body.get("product_id") or "").strip()
    err = validate_vendor(vendor)
    if err:
        yield _sse({"type": "error", "error": err})
        yield _sse({"type": "done"})
        return
    if not product_id:
        yield _sse({"type": "error", "error": "product_id is required"})
        yield _sse({"type": "done"})
        return

    frame = local_state.WORKING_SET.get((_vendor_key(vendor), product_id))
    name = str(body.get("name") or (frame or {}).get("name") or product_id)
    description = str(
        body.get("description") or (frame or {}).get("description") or name
    )

    yield _sse({"type": "start", "vendor": vendor, "product_id": product_id})
    yield _sse(
        {
            "type": "agent_message",
            "content": f"Mapping {vendor} / {product_id} via jpmc-port Mapping Specialist…",
        }
    )
    yield _sse(
        {"type": "tool_call", "tool": "find_similar_products", "args": {"query": name}}
    )
    yield _sse(
        {
            "type": "tool_result",
            "tool": "find_similar_products",
            "result": {"ok": True},
        }
    )
    yield _sse(
        {
            "type": "tool_call",
            "tool": "map_vendor_product",
            "args": {"product_id": product_id},
        }
    )

    try:
        payload = _run(
            {
                "vendor": _vendor_key(vendor),
                "vendor_product_ref": product_id,
                "name": name,
                "description": description,
            }
        )
    except Exception as exc:
        log.exception("dashboard agent run failed")
        yield _sse({"type": "error", "error": f"{type(exc).__name__}: {exc}"})
        yield _sse({"type": "done"})
        return

    yield _sse(
        {
            "type": "tool_result",
            "tool": "map_vendor_product",
            "result": {"outcome": payload.get("outcome")},
        }
    )

    mr = payload.get("mapping_result") or {}
    target = mr.get("proposed_target_iri") or ""
    confidence = float(mr.get("confidence") or 0.0)
    band = mr.get("band")
    status = _outcome_to_status(str(payload.get("outcome") or ""), confidence)
    label = target.rsplit(":", 1)[-1] if target else None
    candidates = []
    # Prefer bundle candidates if present on audit; else single target
    if target:
        candidates = [
            {
                "node": {"iri": target, "label": label or target},
                "similarity": confidence,
            }
        ]

    yield _sse(
        {
            "type": "final_result",
            "mapping": {
                "status": status,
                "mapped_node_iri": target or None,
                "mapped_node_label": label,
                "confidence": confidence,
                "band": band,
                "rationale": mr.get("rationale") or payload.get("outcome_reason"),
                "candidates": candidates,
                "outcome": payload.get("outcome"),
                "hitl_ticket": payload.get("hitl_ticket"),
                "requires_human_review": bool(mr.get("requires_human_review")),
            },
        }
    )
    yield _sse({"type": "done"})


def record_dashboard_decision(body: dict) -> tuple[int, dict]:
    """Map dashboard decision body → port learn_from_teaching + review record."""
    from . import aurora_memory, aws_resources

    vendor = _canonical_vendor(str(body.get("vendor") or ""))
    product_id = str(body.get("product_id") or "").strip()
    decision_raw = str(body.get("decision") or "").strip().lower()
    node_iri = str(body.get("node_iri") or "").strip()
    confidence = body.get("suggested_confidence")
    if confidence is None:
        confidence = 1.0 if decision_raw in {"approve", "override"} else 0.0

    err = validate_vendor(vendor)
    if err:
        return 400, {"error": err}
    if not product_id:
        return 400, {"error": "product_id is required"}
    if decision_raw not in {"approve", "override", "reject"}:
        return 400, {"error": "decision must be approve|override|reject"}
    if decision_raw in {"approve", "override"} and not node_iri:
        return 400, {"error": "node_iri required for approve/override"}

    port_decision = {
        "approve": "approve",
        "override": "correct",
        "reject": "reject",
    }[decision_raw]

    ticket = str(body.get("ticket") or f"HITL-UI-{vendor}-{product_id}")
    frame = local_state.WORKING_SET.get((_vendor_key(vendor), product_id)) or {}
    source_iri = str(frame.get("vendor_product_iri") or "")

    aws_resources.put_review_record(ticket=ticket, payload=body)
    try:
        learned = aurora_memory.learn_from_teaching(
            ticket=ticket,
            decision=port_decision,
            vendor=_vendor_key(vendor),
            vendor_product_ref=product_id,
            source_iri=source_iri,
            target_iri=node_iri,
            lesson=str(
                body.get("lesson")
                or body.get("rationale")
                or f"Dashboard {decision_raw}"
            ),
            confidence=float(confidence or 0.0),
            mapping_object={
                "mapping_result": {
                    "vendor_product_iri": source_iri
                    or f"mds.{_vendor_key(vendor)}:dashboard",
                    "proposed_target_iri": node_iri,
                    "confidence": float(confidence or 0.0),
                    "band": "high" if float(confidence or 0) >= 0.8 else "medium",
                    "rationale": f"dashboard {decision_raw}",
                    "evidence": [],
                    "proposed_triples": [],
                    "requires_human_review": False,
                }
            },
        )
    except ValueError as exc:
        return 400, {"error": str(exc), "learned": False}
    except Exception as exc:
        log.exception("dashboard decision learn failed")
        return 500, {"error": str(exc), "learned": False}

    return 200, {
        "ok": True,
        "ticket": ticket,
        "decision": decision_raw,
        "vendor": vendor,
        "product_id": product_id,
        "node_iri": node_iri,
        **learned,
    }


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"
