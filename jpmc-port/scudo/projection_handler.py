"""Outbox → Neptune + OpenSearch. Local mode writes local_state sinks."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any
from urllib.parse import quote

from . import aurora_store, local_state

log = logging.getLogger("scudo.projection")

_IRIREF_ILLEGAL = re.compile(r'[\x00-\x20<>"{}|^`\\]')


def _sparql_iri(iri: str) -> str:
    """Percent-encode IRIREF-illegal chars — injection guard."""
    return "<" + _IRIREF_ILLEGAL.sub(lambda m: quote(m.group(0), safe=""), iri) + ">"


def _sparql_literal(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _sparql_object(obj: str) -> str:
    if obj.startswith("jpmorgan:") or obj.startswith("mds.") or obj.startswith("http"):
        return _sparql_iri(obj)
    if ":" in obj and not obj.startswith("http") and " " not in obj:
        return obj  # prefixed name
    return _sparql_literal(obj)


def _publish_neptune(named_graph: str, triples: list[dict]) -> None:
    if local_state.is_local():
        local_state.NEPTUNE_GRAPHS[named_graph] = list(triples)
        return
    endpoint = os.environ.get("NEPTUNE_ENDPOINT")
    if not endpoint:
        raise RuntimeError("NEPTUNE_ENDPOINT is not set")
    lines = [f"GRAPH {_sparql_iri(named_graph)} {{"]
    for t in triples:
        lines.append(
            f"  {_sparql_iri(t['subject'])} {t['predicate']} {_sparql_object(t['object'])} ."
        )
    lines.append("}")
    update = "INSERT DATA {\n" + "\n".join(lines) + "\n}"
    # SigV4 POST omitted here — wire via botocore when endpoint is set in Atlas.
    log.info("neptune update bytes=%d graph=%s", len(update), named_graph)
    import urllib.request

    req = urllib.request.Request(
        f"https://{endpoint}:8182/sparql",
        data=update.encode("utf-8"),
        headers={"Content-Type": "application/sparql-update"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def _index_opensearch(named_graph: str, triples: list[dict]) -> None:
    doc = {"named_graph": named_graph, "triple_count": len(triples), "triples": triples}
    if local_state.is_local():
        local_state.OPENSEARCH_DOCS[named_graph] = doc
        return
    # Production: SigV4 index — skip silently if unset.
    if not os.environ.get("OPENSEARCH_ENDPOINT"):
        return
    log.info("opensearch index graph=%s triples=%d", named_graph, len(triples))


def _fetch_undispatched(limit: int) -> list[dict]:
    if local_state.is_local():
        return [r for r in local_state.OUTBOX if not r.get("dispatched")][:limit]
    result = aurora_store._execute(
        "select event_id, detail_type, detail::text from scudo.publish_outbox "
        "where dispatched = false order by created_at_ms asc limit :lim",
        [aurora_store._str_param("lim", str(limit))],
    )
    rows = []
    for rec in result.get("records") or []:
        rows.append(
            {
                "event_id": rec[0].get("stringValue"),
                "detail_type": rec[1].get("stringValue"),
                "detail": json.loads(rec[2].get("stringValue") or "{}"),
            }
        )
    return rows


def _mark_dispatched(event_id: str) -> None:
    if local_state.is_local():
        for r in local_state.OUTBOX:
            if r["event_id"] == event_id:
                r["dispatched"] = True
        return
    aurora_store._execute(
        "update scudo.publish_outbox set dispatched = true where event_id = :id",
        [aurora_store._str_param("id", event_id)],
    )


def _project_one(row: dict) -> None:
    detail = row.get("detail") or {}
    graph = detail.get("named_graph")
    triples = detail.get("triples") or []
    if not graph or not triples:
        raise RuntimeError(f"outbox row {row.get('event_id')} missing graph/triples")
    _publish_neptune(graph, triples)
    _index_opensearch(graph, triples)


def sweep_outbox(*, limit: int = 50) -> dict[str, Any]:
    rows = _fetch_undispatched(limit)
    ok = 0
    errors: list[str] = []
    for row in rows:
        try:
            _project_one(row)
            _mark_dispatched(row["event_id"])
            ok += 1
        except Exception as exc:
            log.exception("project failed event_id=%s", row.get("event_id"))
            errors.append(f"{row.get('event_id')}: {exc}")
    return {"dispatched": ok, "errors": errors, "seen": len(rows)}


def handler(event, context=None):
    return sweep_outbox(limit=int((event or {}).get("limit") or 50))
