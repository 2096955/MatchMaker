"""Approved catalogue CRUD on Aurora (local_state when SCUDO_LOCAL)."""

from __future__ import annotations

import json
from typing import Any, Optional

from . import aurora_store, local_state


def _row_to_record(iri: str, payload: dict) -> dict:
    out = dict(payload)
    out.setdefault("iri", iri)
    return out


def list_approved(*, limit: int = 100) -> list[dict]:
    if local_state.is_local():
        return [
            _row_to_record(iri, p)
            for iri, p in list(local_state.CATALOGUE.items())[:limit]
        ]
    result = aurora_store._execute(
        "select iri, payload::text from scudo.catalogue_products limit :lim",
        [aurora_store._str_param("lim", str(limit))],
    )
    rows = []
    for rec in result.get("records") or []:
        iri = rec[0].get("stringValue")
        payload = json.loads(rec[1].get("stringValue") or "{}")
        rows.append(_row_to_record(iri, payload))
    return rows


def get_record(iri: str) -> Optional[dict]:
    if local_state.is_local():
        p = local_state.CATALOGUE.get(iri)
        return _row_to_record(iri, p) if p else None
    result = aurora_store._execute(
        "select payload::text from scudo.catalogue_products where iri = :iri",
        [aurora_store._str_param("iri", iri)],
    )
    records = result.get("records") or []
    if not records:
        return None
    return _row_to_record(iri, json.loads(records[0][0].get("stringValue") or "{}"))


def upsert_record(iri: str, payload: dict[str, Any]) -> None:
    body = dict(payload)
    body["iri"] = iri
    if local_state.is_local():
        local_state.CATALOGUE[iri] = body
        return
    aurora_store._execute(
        "insert into scudo.catalogue_products (iri, payload) values (:iri, :p::jsonb) "
        "on conflict (iri) do update set payload = excluded.payload",
        [
            aurora_store._str_param("iri", iri),
            aurora_store._json_param("p", body),
        ],
    )
