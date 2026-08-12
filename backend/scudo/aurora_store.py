"""Durable persistence on the single Aurora PostgreSQL cluster via the RDS
Data API. FAIL-LOUD: every writer raises on failure (missing config or a
Data API error), so the caller's request fails instead of silently dropping
the audit/lineage trail. Boto3 imports stay lazy for credential-free tests.
"""

from __future__ import annotations

import os
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Mapping

import boto3  # type: ignore


def _rds_data():
    return boto3.client("rds-data")


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"{name} is not set — Aurora persistence is required")
    return val


def _str_param(name: str, value: str) -> dict:
    return {"name": name, "value": {"stringValue": value}}


def _long_param(name: str, value: int) -> dict:
    return {"name": name, "value": {"longValue": int(value)}}


def _json_param(name: str, value: Mapping[str, Any]) -> dict:
    return {
        "name": name,
        "value": {"stringValue": json.dumps(dict(value), default=str)},
        "typeHint": "JSON",
    }


def _execute(sql: str, params: list[dict]) -> dict:
    # Validate configuration BEFORE constructing the boto3 client, so a missing
    # env var fails loud without touching boto3 at all (no client side effects,
    # and the fail-loud test can assert _rds_data is never reached).
    resource_arn = _require("SCUDO_AURORA_CLUSTER_ARN")
    secret_arn = _require("SCUDO_AURORA_SECRET_ARN")
    database = _require("SCUDO_AURORA_DATABASE_NAME")
    return _rds_data().execute_statement(
        resourceArn=resource_arn,
        secretArn=secret_arn,
        database=database,
        sql=sql,
        parameters=params,
    )


@dataclass(frozen=True)
class Transaction:
    client: Any
    resource_arn: str
    secret_arn: str
    database: str
    transaction_id: str

    def execute(
        self,
        sql: str,
        params: list[dict],
        *,
        expected_rows: int | None = None,
    ) -> dict:
        result = self.client.execute_statement(
            resourceArn=self.resource_arn,
            secretArn=self.secret_arn,
            database=self.database,
            transactionId=self.transaction_id,
            sql=sql,
            parameters=params,
        )
        if (
            expected_rows is not None
            and result.get("numberOfRecordsUpdated") != expected_rows
        ):
            raise RuntimeError(
                "transaction statement expected "
                f"{expected_rows} updated row(s), got "
                f"{result.get('numberOfRecordsUpdated', 0)}"
            )
        return result


@contextmanager
def transaction() -> Iterator[Transaction]:
    resource_arn = _require("SCUDO_AURORA_CLUSTER_ARN")
    secret_arn = _require("SCUDO_AURORA_SECRET_ARN")
    database = _require("SCUDO_AURORA_DATABASE_NAME")
    client = _rds_data()
    started = client.begin_transaction(
        resourceArn=resource_arn,
        secretArn=secret_arn,
        database=database,
    )
    transaction_id = started["transactionId"]
    tx = Transaction(
        client=client,
        resource_arn=resource_arn,
        secret_arn=secret_arn,
        database=database,
        transaction_id=transaction_id,
    )
    try:
        yield tx
    except Exception:
        client.rollback_transaction(
            resourceArn=resource_arn,
            secretArn=secret_arn,
            transactionId=transaction_id,
        )
        raise
    else:
        try:
            client.commit_transaction(
                resourceArn=resource_arn,
                secretArn=secret_arn,
                transactionId=transaction_id,
            )
        except Exception:
            client.rollback_transaction(
                resourceArn=resource_arn,
                secretArn=secret_arn,
                transactionId=transaction_id,
            )
            raise


def put_audit_record(
    *, item_id: str, event_type: str, payload: Mapping[str, Any]
) -> None:
    _execute(
        "insert into scudo.audit_events (item_id, event_type, created_at_ms, payload) "
        "values (:item_id, :event_type, :created_at_ms, :payload::jsonb)",
        [
            _str_param("item_id", item_id),
            _str_param("event_type", event_type),
            _str_param("created_at_ms", str(int(time.time() * 1000))),
            _json_param("payload", payload),
        ],
    )


def put_review_record(*, ticket: str, payload: Mapping[str, Any]) -> None:
    if not ticket:
        raise RuntimeError("review record requires a ticket")
    _execute(
        "insert into scudo.mapping_decisions (ticket, status, created_at_ms, payload) "
        "values (:ticket, 'OPEN', :created_at_ms, :payload::jsonb)",
        [
            _str_param("ticket", ticket),
            _str_param("created_at_ms", str(int(time.time() * 1000))),
            _json_param("payload", payload),
        ],
    )


def put_outbox_record(
    *, event_id: str, detail_type: str, detail: Mapping[str, Any]
) -> None:
    _execute(
        "insert into scudo.publish_outbox (event_id, detail_type, dispatched, created_at_ms, detail) "
        "values (:event_id, :detail_type, false, :created_at_ms, :detail::jsonb) "
        "on conflict (event_id) do nothing",
        [
            _str_param("event_id", event_id),
            _str_param("detail_type", detail_type),
            _str_param("created_at_ms", str(int(time.time() * 1000))),
            _json_param("detail", detail),
        ],
    )


def put_facts_record(
    *,
    source_bucket: str,
    source_key: str,
    content_hash: str,
    payload: Mapping[str, Any],
) -> None:
    _execute(
        "insert into scudo.lineage_facts (source_bucket, source_key, content_hash, payload) "
        "values (:source_bucket, :source_key, :content_hash, :payload::jsonb)",
        [
            _str_param("source_bucket", source_bucket),
            _str_param("source_key", source_key),
            _str_param("content_hash", content_hash),
            _json_param("payload", payload),
        ],
    )


def update_job_status(
    *, job_id: str, status: str, fields: Mapping[str, Any] | None = None
) -> None:
    _execute(
        "insert into scudo.etl_jobs (job_id, status, updated_at_ms, fields) "
        "values (:job_id, :status, :updated_at_ms, :fields::jsonb) "
        "on conflict (job_id) do update set status = excluded.status, "
        "updated_at_ms = excluded.updated_at_ms, fields = excluded.fields",
        [
            _str_param("job_id", job_id),
            _str_param("status", status),
            _str_param("updated_at_ms", str(int(time.time() * 1000))),
            _json_param("fields", fields or {}),
        ],
    )


def ensure_schema() -> None:
    """Idempotently create the `scudo` schema and its 7 durable tables.

    Mirrors ``projection_handler._ensure_aurora_schema``'s DDL-issuing style
    (a flat list of statements run one-by-one through the RDS Data API), but
    owns the schema-qualified tables this module's fail-loud writers target.
    Called from ``init_data_platform._init_aurora`` only when Aurora env is
    configured; like the other functions here it is FAIL-LOUD once invoked.
    """
    statements = [
        "create schema if not exists scudo",
        """
        create table if not exists scudo.audit_events (
          item_id text,
          event_type text,
          created_at_ms bigint,
          payload jsonb
        )
        """,
        """
        create table if not exists scudo.mapping_decisions (
          ticket text primary key,
          status text,
          created_at_ms bigint,
          payload jsonb
        )
        """,
        """
        create table if not exists scudo.publish_outbox (
          event_id text primary key,
          detail_type text,
          dispatched boolean default false,
          created_at_ms bigint,
          detail jsonb
        )
        """,
        """
        create table if not exists scudo.lineage_facts (
          source_bucket text,
          source_key text,
          content_hash text,
          payload jsonb
        )
        """,
        """
        create table if not exists scudo.catalogue_products (
          iri text primary key,
          payload jsonb
        )
        """,
        """
        create table if not exists scudo.cdao_taxonomy (
          iri text primary key,
          payload jsonb
        )
        """,
        """
        create table if not exists scudo.etl_jobs (
          job_id text primary key,
          status text,
          updated_at_ms bigint,
          fields jsonb
        )
        """,
        """
        create table if not exists scudo.agent_memory (
          memory_key text primary key,
          memory_type text,
          updated_at_ms bigint,
          payload jsonb
        )
        """,
    ]
    for sql in statements:
        _execute(sql, [])
