"""Physical ingestion INSERT against Aurora PostgreSQL (Task 10).

Exercises the ported ingestion path end-to-end on the PostgreSQL container:
create a dataset (physical table built via psycopg.sql DDL), drop a CSV in the
landing folder, run the engine (INSERT via psycopg.sql.Identifier with per-row
SAVEPOINTs), and verify the rows land. The second test proves the per-row
savepoint isolates a failing row without aborting the whole transaction.
"""

from __future__ import annotations

import pandas as pd
from psycopg import sql

from _ingest_helpers import (
    create_dataset,
    destroy_dataset,
    query_ingestion_table,
    run_ingestion,
    write_csv,
)
from db import get_ingestion_conn


def test_physical_ingestion_insert_loads_rows():
    ctx = create_dataset("csv", "*.csv")
    try:
        write_csv(ctx["landing_dir"], "data.csv")  # 5 sample rows
        logs = run_ingestion(ctx["dataset_id"])
        assert logs, "no run-log entries returned"
        assert any(lg.get("run_status") == "success" for lg in logs), logs

        rows = query_ingestion_table(ctx["target_table"])
        assert len(rows) == 5
        assert [r["id"] for r in rows] == [1, 2, 3, 4, 5]
        assert rows[0]["name"] == "Alice"
    finally:
        destroy_dataset(ctx)


def test_physical_ingestion_row_savepoint_isolates_failure():
    """A UNIQUE violation on one row is rolled back to its per-row SAVEPOINT; the
    other rows still load and the surrounding transaction is not aborted (without
    savepoints the first failure would abort the txn and drop every later row)."""
    ctx = create_dataset("csv", "*.csv")
    try:
        # Add a UNIQUE constraint so a duplicate id fails at INSERT time.
        ing = get_ingestion_conn()
        try:
            with ing.cursor() as c:
                c.execute(
                    sql.SQL(
                        "ALTER TABLE {} ADD CONSTRAINT uq_ing_id UNIQUE (id)"
                    ).format(sql.Identifier(ctx["target_table"]))
                )
            ing.commit()
        finally:
            ing.close()

        # ids [1, 2, 2, 3] - the second id=2 violates the unique constraint.
        df = pd.DataFrame(
            [
                {"id": 1, "name": "A", "amount": 1.0},
                {"id": 2, "name": "B", "amount": 2.0},
                {"id": 2, "name": "dup", "amount": 3.0},
                {"id": 3, "name": "C", "amount": 4.0},
            ]
        )
        write_csv(ctx["landing_dir"], "data.csv", df=df)

        run_ingestion(ctx["dataset_id"])

        rows = query_ingestion_table(ctx["target_table"])
        ids = sorted(r["id"] for r in rows)
        assert ids == [1, 2, 3], f"savepoint did not isolate the bad row: got {ids}"
    finally:
        destroy_dataset(ctx)
