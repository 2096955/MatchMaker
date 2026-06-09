"""Ingestion engine tests — Apache Parquet format.

Run directly:  python test_ingest_parquet.py

ParquetIngester uses pandas.read_parquet (requires pyarrow).

Scenarios tested:
1.  New Parquet file → success
2.  Same file twice → second run skipped
3.  Extra columns in file → extras ignored
4.  Missing schema column → loaded as NULL
5.  Empty landing folder → single skipped entry
6.  Schema extended via edit → new column loaded in subsequent run
7.  Column types (int/float) preserved in ingestion table
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pandas as pd
from _ingest_helpers import (
    create_dataset, destroy_dataset, run_ingestion, extend_schema,
    query_ingestion_table,
    write_parquet, sample_df_extra_cols, sample_df_missing_col,
)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_parquet_new_file_success():
    ctx = create_dataset(fmt='parquet', pattern='*.parquet')
    try:
        write_parquet(ctx['landing_dir'])
        results = run_ingestion(ctx['dataset_id'])

        r = results[0]
        assert r['run_status'] == 'success',  f"Status: {r['run_status']}"
        assert r['column_status'] == 'match', f"Column status: {r['column_status']}"
        assert r['records_found']   == 5
        assert r['records_loaded']  == 5
        assert r['records_skipped'] == 0
    finally:
        destroy_dataset(ctx)
    print("PASS: parquet — new file ingested successfully")


def test_parquet_same_file_twice_second_run_skipped():
    ctx = create_dataset(fmt='parquet', pattern='*.parquet')
    try:
        write_parquet(ctx['landing_dir'], 'data.parquet')
        r1 = run_ingestion(ctx['dataset_id'])
        assert r1[0]['run_status'] == 'success'

        write_parquet(ctx['landing_dir'], 'data.parquet')
        r2 = run_ingestion(ctx['dataset_id'])
        assert r2[0]['run_status'] == 'skipped'
        assert 'Already processed' in (r2[0]['status_notes'] or '')
    finally:
        destroy_dataset(ctx)
    print("PASS: parquet — identical file re-submitted is skipped on second run")


def test_parquet_extra_columns_in_file_are_ignored():
    ctx = create_dataset(fmt='parquet', pattern='*.parquet')
    try:
        write_parquet(ctx['landing_dir'], df=sample_df_extra_cols())
        results = run_ingestion(ctx['dataset_id'])

        r = results[0]
        assert r['run_status'] == 'success'
        assert r['column_status'] == 'extra_cols_in_file', \
            f"Column status: {r['column_status']}"
        assert r['records_loaded'] == 5
    finally:
        destroy_dataset(ctx)
    print("PASS: parquet — extra columns in file ignored, 5 rows loaded")


def test_parquet_missing_column_loaded_as_null():
    ctx = create_dataset(fmt='parquet', pattern='*.parquet')
    try:
        write_parquet(ctx['landing_dir'], df=sample_df_missing_col())
        results = run_ingestion(ctx['dataset_id'])

        r = results[0]
        assert r['run_status'] in ('success', 'partial')
        assert r['column_status'] == 'missing_cols_in_file'
        assert r['records_loaded'] == 5

        rows = query_ingestion_table(ctx['target_table'], column='amount')
        assert all(row['amount'] is None for row in rows)
    finally:
        destroy_dataset(ctx)
    print("PASS: parquet — missing schema column loaded as NULL")


def test_parquet_empty_landing_folder_returns_skipped():
    ctx = create_dataset(fmt='parquet', pattern='*.parquet')
    try:
        results = run_ingestion(ctx['dataset_id'])

        assert len(results) == 1
        assert results[0]['run_status'] == 'skipped'
        assert results[0]['records_found'] == 0
    finally:
        destroy_dataset(ctx)
    print("PASS: parquet — empty landing folder returns skipped entry")


def test_parquet_new_schema_column_loaded_after_schema_edit():
    ctx = create_dataset(fmt='parquet', pattern='*.parquet')
    try:
        did = ctx['dataset_id']

        # Run 1: file has status + category → extras ignored.
        write_parquet(ctx['landing_dir'], 'v1.parquet', df=sample_df_extra_cols())
        r1 = run_ingestion(did)
        assert r1[0]['column_status'] == 'extra_cols_in_file'

        # Extend schema.
        extend_schema(did, [
            {'col_name': 'status',   'col_type': 'string', 'col_position': 4},
            {'col_name': 'category', 'col_type': 'string', 'col_position': 5},
        ])

        # Run 2: all columns now in schema → match.
        write_parquet(ctx['landing_dir'], 'v2.parquet', df=sample_df_extra_cols())
        r2 = run_ingestion(did)
        assert r2[0]['column_status'] == 'match'
        assert r2[0]['run_status'] == 'success'
        assert r2[0]['records_loaded'] == 5
    finally:
        destroy_dataset(ctx)
    print("PASS: parquet — after schema edit new columns are loaded correctly")


def test_parquet_column_types_preserved():
    """Integer id and float amount should be stored with correct values."""
    ctx = create_dataset(fmt='parquet', pattern='*.parquet')
    try:
        df = pd.DataFrame({
            'id':     pd.array([1, 2, 3, 4, 5], dtype='int64'),
            'name':   ['Alice', 'Bob', 'Charlie', 'Diana', 'Eve'],
            'amount': pd.array([100.50, 200.75, 300.0, 450.25, 500.0], dtype='float64'),
        })
        write_parquet(ctx['landing_dir'], df=df)
        run_ingestion(ctx['dataset_id'])

        rows = query_ingestion_table(ctx['target_table'])
        assert len(rows) == 5
        assert int(rows[0]['id'])         == 1
        assert rows[0]['name']            == 'Alice'
        assert float(rows[0]['amount'])   == 100.50
    finally:
        destroy_dataset(ctx)
    print("PASS: parquet — int and float column values stored correctly")


# ── Runner ────────────────────────────────────────────────────────────────────

TESTS = [
    test_parquet_new_file_success,
    test_parquet_same_file_twice_second_run_skipped,
    test_parquet_extra_columns_in_file_are_ignored,
    test_parquet_missing_column_loaded_as_null,
    test_parquet_empty_landing_folder_returns_skipped,
    test_parquet_new_schema_column_loaded_after_schema_edit,
    test_parquet_column_types_preserved,
]

if __name__ == '__main__':
    passed = failed = 0
    for test in TESTS:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {test.__name__} — {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR: {test.__name__} — {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
