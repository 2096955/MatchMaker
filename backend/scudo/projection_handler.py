"""Async projection Lambda for SCUDO published mappings.

The orchestrator writes the transactional outbox and emits EventBridge events.
This worker consumes the projection queue and fans a completed mapping into the
target-state stores:

* Aurora PostgreSQL via the RDS Data API for audit/catalog/persistent memory.
* Neptune SPARQL for canonical RDF named-graph triples.
* OpenSearch for lexical + k-NN retrieval, with Titan embeddings when enabled.
* AppSync mutation for the data-steward WebSocket subscription stream.

Every sink is environment-gated. Missing endpoint/secret env vars leave that
sink disabled, which keeps local tests and partial PoC stacks import-clean.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any, Iterable
from urllib.parse import quote

log = logging.getLogger("scudo.projection")
log.setLevel(logging.INFO)

_AURORA_BOOTSTRAPPED = False
_OPENSEARCH_INDEX_READY = False

_PREFIXES = """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX dcat: <http://www.w3.org/ns/dcat#>
PREFIX dct: <http://purl.org/dc/terms/>
PREFIX prov: <http://www.w3.org/ns/prov#>
"""
_SPARQL_PREFIX_NAMES = {"rdf", "rdfs", "dcat", "dct", "prov", "skos", "xsd"}


def _boto3():
    import boto3  # type: ignore

    return boto3


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))


def _records(event: dict) -> Iterable[dict]:
    for record in event.get("Records") or []:
        body = record.get("body")
        if not body:
            continue
        payload = json.loads(body) if isinstance(body, str) else body
        yield payload


def _event_detail(payload: dict) -> dict:
    detail = payload.get("detail")
    if isinstance(detail, str):
        return json.loads(detail)
    if isinstance(detail, dict):
        return detail
    return payload


def _event_id(detail: dict, mapping_object: dict) -> str:
    explicit = detail.get("event_id") or detail.get("eventId")
    if explicit:
        return str(explicit)
    bundle_ref = mapping_object.get("bundle_ref") or "mapping"
    outcome = mapping_object.get("outcome") or "published"
    return f"{bundle_ref}:{outcome}"


def _rds_client():
    return _boto3().client("rds-data")


def _aurora_env() -> tuple[str, str, str] | None:
    cluster_arn = os.environ.get("SCUDO_AURORA_CLUSTER_ARN", "").strip()
    secret_arn = os.environ.get("SCUDO_AURORA_SECRET_ARN", "").strip()
    database = os.environ.get("SCUDO_AURORA_DATABASE_NAME", "scudo").strip()
    if not cluster_arn or not secret_arn:
        return None
    return cluster_arn, secret_arn, database


def _execute_statement(sql: str, parameters: list[dict] | None = None) -> None:
    env = _aurora_env()
    if env is None:
        return
    cluster_arn, secret_arn, database = env
    _rds_client().execute_statement(
        resourceArn=cluster_arn,
        secretArn=secret_arn,
        database=database,
        sql=sql,
        parameters=parameters or [],
    )


def _ensure_aurora_schema() -> None:
    global _AURORA_BOOTSTRAPPED
    if _AURORA_BOOTSTRAPPED or _aurora_env() is None:
        return
    statements = [
        """
        CREATE TABLE IF NOT EXISTS scudo_audit_events (
          event_id TEXT PRIMARY KEY,
          detail_type TEXT NOT NULL,
          outcome TEXT,
          vendor TEXT,
          vendor_product_ref TEXT,
          payload JSONB NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS scudo_catalog_projection (
          vendor_product_iri TEXT PRIMARY KEY,
          target_iri TEXT,
          graph_iri TEXT,
          confidence DOUBLE PRECISION,
          payload JSONB NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS scudo_agent_memory (
          memory_key TEXT PRIMARY KEY,
          memory_type TEXT NOT NULL,
          payload JSONB NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    ]
    for sql in statements:
        _execute_statement(sql)
    _AURORA_BOOTSTRAPPED = True


def _write_aurora(detail: dict, mapping_object: dict) -> None:
    if _aurora_env() is None:
        return
    _ensure_aurora_schema()
    mapping_result = mapping_object.get("mapping_result") or {}
    request = detail.get("request") or {}
    event_id = _event_id(detail, mapping_object)
    detail_type = (
        detail.get("detail-type") or detail.get("detail_type") or "MappingCompleted"
    )
    payload = {"detail": detail, "mapping_object": mapping_object}
    _execute_statement(
        """
        INSERT INTO scudo_audit_events
          (event_id, detail_type, outcome, vendor, vendor_product_ref, payload)
        VALUES
          (:event_id, :detail_type, :outcome, :vendor, :vendor_product_ref, CAST(:payload AS jsonb))
        ON CONFLICT (event_id) DO UPDATE SET
          detail_type = EXCLUDED.detail_type,
          outcome = EXCLUDED.outcome,
          vendor = EXCLUDED.vendor,
          vendor_product_ref = EXCLUDED.vendor_product_ref,
          payload = EXCLUDED.payload,
          updated_at = now()
        """,
        [
            {"name": "event_id", "value": {"stringValue": event_id}},
            {"name": "detail_type", "value": {"stringValue": str(detail_type)}},
            {
                "name": "outcome",
                "value": {"stringValue": str(mapping_object.get("outcome", ""))},
            },
            {
                "name": "vendor",
                "value": {"stringValue": str(request.get("vendor", ""))},
            },
            {
                "name": "vendor_product_ref",
                "value": {"stringValue": str(request.get("vendor_product_ref", ""))},
            },
            {"name": "payload", "value": {"stringValue": _json_dumps(payload)}},
        ],
    )
    vendor_iri = str(mapping_result.get("vendor_product_iri") or "")
    if vendor_iri:
        _execute_statement(
            """
            INSERT INTO scudo_catalog_projection
              (vendor_product_iri, target_iri, graph_iri, confidence, payload)
            VALUES
              (:vendor_product_iri, :target_iri, :graph_iri, :confidence, CAST(:payload AS jsonb))
            ON CONFLICT (vendor_product_iri) DO UPDATE SET
              target_iri = EXCLUDED.target_iri,
              graph_iri = EXCLUDED.graph_iri,
              confidence = EXCLUDED.confidence,
              payload = EXCLUDED.payload,
              updated_at = now()
            """,
            [
                {"name": "vendor_product_iri", "value": {"stringValue": vendor_iri}},
                {
                    "name": "target_iri",
                    "value": {
                        "stringValue": str(
                            mapping_result.get("proposed_target_iri", "")
                        )
                    },
                },
                {
                    "name": "graph_iri",
                    "value": {
                        "stringValue": str(mapping_object.get("published_graph", ""))
                    },
                },
                {
                    "name": "confidence",
                    "value": {
                        "doubleValue": float(mapping_result.get("confidence") or 0.0)
                    },
                },
                {
                    "name": "payload",
                    "value": {"stringValue": _json_dumps(mapping_object)},
                },
            ],
        )


def _sparql_name(value: str) -> str:
    head, _, tail = value.partition(":")
    if head in _SPARQL_PREFIX_NAMES and tail and re.match(r"^[A-Za-z0-9_.-]+$", tail):
        return value
    return f"<{_sparql_iri(value)}>"


def _sparql_iri(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace(">", "%3E").replace("<", "%3C")


def _sparql_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _sparql_object(predicate: str, value: str) -> str:
    if predicate == "rdf:type":
        return _sparql_name(value)
    if value.startswith(("http://", "https://", "urn:", "mds.", "jpmorgan:data:")):
        return f"<{_sparql_iri(value)}>"
    if value.split(":", 1)[0] in _SPARQL_PREFIX_NAMES:
        return _sparql_name(value)
    return _sparql_literal(value)


def _signed_post(url: str, body: bytes, *, service: str, content_type: str) -> bytes:
    import urllib3
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    region = (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )
    session = _boto3().Session(region_name=region)
    creds = session.get_credentials()
    if creds is None:
        raise RuntimeError("could not resolve AWS credentials for signed HTTP request")
    aws_req = AWSRequest(
        method="POST",
        url=url,
        data=body,
        headers={
            "Content-Type": content_type,
            "Host": url.split("://", 1)[1].split("/", 1)[0],
        },
    )
    SigV4Auth(creds, service, region).add_auth(aws_req)
    resp = urllib3.PoolManager().request(
        "POST", url, body=body, headers=dict(aws_req.headers.items())
    )
    if resp.status < 200 or resp.status >= 300:
        raise RuntimeError(
            f"{service} POST failed: HTTP {resp.status} {resp.data[:512]!r}"
        )
    return bytes(resp.data)


def _publish_neptune(mapping_object: dict) -> None:
    endpoint = os.environ.get("SCUDO_NEPTUNE_ENDPOINT") or os.environ.get(
        "NEPTUNE_ENDPOINT", ""
    )
    endpoint = endpoint.strip()
    if not endpoint:
        return
    host = (
        endpoint.replace("https://", "")
        .replace("http://", "")
        .split("/", 1)[0]
        .split(":", 1)[0]
    )
    url = f"https://{host}:8182/sparql"
    mapping_result = mapping_object.get("mapping_result") or {}
    triples = mapping_result.get("proposed_triples") or []
    if not triples:
        return
    lines = []
    for triple in triples:
        subject = f"<{_sparql_iri(str(triple['subject']))}>"
        predicate = _sparql_name(str(triple["predicate"]))
        obj = _sparql_object(str(triple["predicate"]), str(triple["object"]))
        lines.append(f"  {subject} {predicate} {obj} .")
    graph = str(mapping_object.get("published_graph") or triples[0].get("graph") or "")
    update = (
        _PREFIXES
        + f"\nINSERT DATA {{ GRAPH <{_sparql_iri(graph)}> {{\n"
        + "\n".join(lines)
        + "\n} }"
    )
    body = ("update=" + quote(update, safe="")).encode("utf-8")
    _signed_post(
        url,
        body,
        service="neptune-db",
        content_type="application/x-www-form-urlencoded",
    )


def _titan_embedding(text: str) -> list[float] | None:
    model_id = os.environ.get("SCUDO_EMBEDDINGS_MODEL_ID", "").strip()
    if not model_id or not text.strip():
        return None
    client = _boto3().client("bedrock-runtime")
    payload = {"inputText": text[:8000], "dimensions": 1024, "normalize": True}
    try:
        response = client.invoke_model(
            modelId=model_id,
            body=_json_dumps(payload).encode("utf-8"),
            accept="application/json",
            contentType="application/json",
        )
    except Exception:
        log.exception("Titan embedding call failed; indexing lexical document only")
        return None
    body = response["body"].read()
    parsed = json.loads(body.decode("utf-8"))
    embedding = parsed.get("embedding")
    return embedding if isinstance(embedding, list) else None


def _opensearch_url(path: str) -> str | None:
    endpoint = os.environ.get("SCUDO_OPENSEARCH_ENDPOINT", "").strip()
    if not endpoint:
        return None
    host = endpoint.replace("https://", "").replace("http://", "").rstrip("/")
    return f"https://{host}{path}"


def _opensearch_request(method: str, path: str, body: dict | None = None) -> bytes:
    import urllib3
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    url = _opensearch_url(path)
    if url is None:
        return b""
    region = (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )
    session = _boto3().Session(region_name=region)
    creds = session.get_credentials()
    if creds is None:
        raise RuntimeError("could not resolve AWS credentials for OpenSearch request")
    raw = _json_dumps(body or {}).encode("utf-8") if body is not None else b""
    aws_req = AWSRequest(
        method=method,
        url=url,
        data=raw,
        headers={
            "Content-Type": "application/json",
            "Host": url.split("://", 1)[1].split("/", 1)[0],
        },
    )
    SigV4Auth(creds, "es", region).add_auth(aws_req)
    resp = urllib3.PoolManager().request(
        method, url, body=raw, headers=dict(aws_req.headers.items())
    )
    if resp.status < 200 or resp.status >= 300:
        raise RuntimeError(
            f"OpenSearch {method} {path} failed: HTTP {resp.status} {resp.data[:512]!r}"
        )
    return bytes(resp.data)


def _ensure_opensearch_index() -> None:
    global _OPENSEARCH_INDEX_READY
    if _OPENSEARCH_INDEX_READY or not os.environ.get("SCUDO_OPENSEARCH_ENDPOINT"):
        return
    index = os.environ.get("SCUDO_OPENSEARCH_INDEX", "scudo-catalog")
    try:
        _opensearch_request("HEAD", f"/{index}")
        _OPENSEARCH_INDEX_READY = True
        return
    except Exception:
        pass
    mapping = {
        "settings": {"index": {"knn": True}},
        "mappings": {
            "properties": {
                "doc_type": {"type": "keyword"},
                "vendor": {"type": "keyword"},
                "vendor_product_ref": {"type": "keyword"},
                "vendor_product_iri": {"type": "keyword"},
                "target_iri": {"type": "keyword"},
                "graph_iri": {"type": "keyword"},
                "label": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                "description": {"type": "text"},
                "payload": {"type": "object", "enabled": False},
                "embedding": {
                    "type": "knn_vector",
                    "dimension": 1024,
                    "method": {
                        "name": "hnsw",
                        "engine": "nmslib",
                        "space_type": "cosinesimil",
                    },
                },
            }
        },
    }
    _opensearch_request("PUT", f"/{index}", mapping)
    _OPENSEARCH_INDEX_READY = True


def _index_opensearch(detail: dict, mapping_object: dict) -> None:
    if not os.environ.get("SCUDO_OPENSEARCH_ENDPOINT"):
        return
    _ensure_opensearch_index()
    index = os.environ.get("SCUDO_OPENSEARCH_INDEX", "scudo-catalog")
    mapping_result = mapping_object.get("mapping_result") or {}
    request = detail.get("request") or {}
    text = " ".join(
        str(x)
        for x in [
            request.get("vendor"),
            request.get("vendor_product_ref"),
            mapping_result.get("proposed_target_iri"),
            mapping_result.get("rationale"),
        ]
        if x
    )
    document = {
        "doc_type": "mapping",
        "event_id": _event_id(detail, mapping_object),
        "vendor": request.get("vendor", ""),
        "vendor_product_ref": request.get("vendor_product_ref", ""),
        "vendor_product_iri": mapping_result.get("vendor_product_iri", ""),
        "target_iri": mapping_result.get("proposed_target_iri", ""),
        "graph_iri": mapping_object.get("published_graph", ""),
        "label": request.get("vendor_product_ref", ""),
        "description": mapping_result.get("rationale", ""),
        "payload": mapping_object,
    }
    embedding = _titan_embedding(text)
    if embedding:
        document["embedding"] = embedding
    doc_id = hashlib.sha256(str(document["event_id"]).encode("utf-8")).hexdigest()
    _opensearch_request("PUT", f"/{index}/_doc/{doc_id}", document)


def _publish_appsync(detail: dict, mapping_object: dict) -> None:
    url = os.environ.get("SCUDO_APPSYNC_API_URL", "").strip()
    api_key = os.environ.get("SCUDO_APPSYNC_API_KEY", "").strip()
    if not url or not api_key:
        return
    import urllib3

    query = """
      mutation PublishMapping($eventId: ID!, $payload: AWSJSON!) {
        publishMapping(eventId: $eventId, payload: $payload) {
          eventId
          payload
        }
      }
    """
    body = {
        "query": query,
        "variables": {
            "eventId": _event_id(detail, mapping_object),
            "payload": _json_dumps(mapping_object),
        },
    }
    resp = urllib3.PoolManager().request(
        "POST",
        url,
        body=_json_dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-api-key": api_key},
    )
    if resp.status < 200 or resp.status >= 300:
        raise RuntimeError(
            f"AppSync publish failed: HTTP {resp.status} {resp.data[:512]!r}"
        )


def _project_one(row: dict) -> bool:
    """Project ONE outbox row into every target sink. Returns True if it was a
    published mapping (projected), False if there was nothing to project. Raises
    if a sink fails — the caller then leaves the row undispatched for an
    at-least-once retry. Idempotent: every sink upserts / uses a deterministic
    doc id, so re-running a row is safe."""
    detail = row.get("detail") or {}
    mapping_object = detail.get("mapping_object") or {}
    if mapping_object.get("outcome") != "published":
        log.info(
            "skipping non-published outbox row %s: outcome=%s",
            row.get("event_id"),
            mapping_object.get("outcome"),
        )
        return False
    _write_aurora(detail, mapping_object)
    _publish_neptune(mapping_object)
    _index_opensearch(detail, mapping_object)
    _publish_appsync(detail, mapping_object)
    return True


def _run(sql: str, params: list[dict], execute=None) -> dict:
    """Run one Data API statement and return the raw result dict. ``execute``
    optionally injects a runner (``execute(sql, params) -> dict``) for tests or a
    shared transaction; otherwise the single Aurora store (aurora_store._execute,
    fail-loud) runs it, so the outbox sweep shares one Data API path."""
    if execute is None:
        from . import aurora_store

        execute = aurora_store._execute
    return execute(sql, params) or {}


def _fetch_undispatched(limit: int = 100, *, execute=None) -> list[dict]:
    """Read undispatched rows from scudo.publish_outbox — the Aurora outbox that
    replaced the DynamoDB-stream / per-publish SQS projection feed."""
    result = _run(
        "SELECT event_id, detail_type, detail FROM scudo.publish_outbox "
        "WHERE dispatched = false ORDER BY created_at_ms LIMIT :limit",
        [{"name": "limit", "value": {"longValue": int(limit)}}],
        execute=execute,
    )
    rows: list[dict] = []
    for rec in result.get("records", []):
        raw = (rec[2] or {}).get("stringValue") if len(rec) > 2 else None
        try:
            detail = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            detail = {}
        rows.append(
            {
                "event_id": (rec[0] or {}).get("stringValue", ""),
                "detail_type": (rec[1] or {}).get("stringValue", ""),
                "detail": detail,
            }
        )
    return rows


def _mark_dispatched(event_id: str, *, execute=None) -> None:
    """Flip dispatched=true for one outbox row — only AFTER its sinks acked."""
    _run(
        "UPDATE scudo.publish_outbox SET dispatched = true WHERE event_id = :event_id",
        [{"name": "event_id", "value": {"stringValue": event_id}}],
        execute=execute,
    )


def sweep_outbox(*, execute=None, batch_limit: int = 100) -> list[dict]:
    """At-least-once sweep: project each undispatched outbox row, marking it
    dispatched only after its sinks succeed. A row whose projection RAISES is
    left undispatched (retried next sweep); a row with nothing to project is
    still marked dispatched so it can never become a poison row. ``execute``
    injects a Data API runner for the fetch/mark statements (tests or a shared
    transaction)."""
    swept: list[dict] = []
    for row in _fetch_undispatched(batch_limit, execute=execute):
        try:
            _project_one(row)
        except Exception:  # noqa: BLE001 — leave undispatched, retry next sweep
            log.exception(
                "projection failed for %s; leaving undispatched", row.get("event_id")
            )
            continue
        _mark_dispatched(row["event_id"], execute=execute)
        swept.append(row)
    return swept


def handler(event: dict, context: Any) -> dict:
    """EventBridge-scheduled entry: drain the Aurora publish_outbox into the
    projection sinks. This is the target-state feed that replaced the
    DynamoDB-stream / per-publish SQS trigger; the template schedule invokes it."""
    swept = sweep_outbox()
    return {"projected": len(swept)}
