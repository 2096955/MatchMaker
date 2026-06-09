"""Ingestion engine tests — JSON format.

Run directly:  python test_ingest_json.py

JsonIngester uses pandas.read_json (records-oriented array).

Scenarios tested:
1.  New JSON file → success
2.  Same file twice → second run skipped
3.  Extra columns in file → extras ignored
4.  Missing schema column → loaded as NULL
5.  Empty landing folder → single skipped entry
6.  Schema extended via edit → new column loaded in subsequent run
7.  Data values verified in ingestion table
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from _ingest_helpers import (
    create_dataset, destroy_dataset, run_ingestion, extend_schema,
    query_ingestion_table,
    write_json, sample_df_extra_cols, sample_df_missing_col,
)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_json_new_file_success():
    ctx = create_dataset(fmt='json', pattern='*.json')
    try:
        write_json(ctx['landing_dir'])
        results = run_ingestion(ctx['dataset_id'])

        r = results[0]
        assert r['run_status'] == 'success',  f"Status: {r['run_status']}"
        assert r['column_status'] == 'match', f"Column status: {r['column_status']}"
        assert r['records_found']   == 5
        assert r['records_loaded']  == 5
        assert r['records_skipped'] == 0
    finally:
        destroy_dataset(ctx)
    print("PASS: json — new file ingested successfully")


def test_json_same_file_twice_second_run_skipped():
    ctx = create_dataset(fmt='json', pattern='*.json')
    try:
        write_json(ctx['landing_dir'], 'data.json')
        r1 = run_ingestion(ctx['dataset_id'])
        assert r1[0]['run_status'] == 'success'

        write_json(ctx['landing_dir'], 'data.json')
        r2 = run_ingestion(ctx['dataset_id'])
        assert r2[0]['run_status'] == 'skipped'
        assert 'Already processed' in (r2[0]['status_notes'] or '')
    finally:
        destroy_dataset(ctx)
    print("PASS: json — identical file re-submitted is skipped on second run")


def test_json_extra_columns_in_file_are_ignored():
    ctx = create_dataset(fmt='json', pattern='*.json')
    try:
        write_json(ctx['landing_dir'], df=sample_df_extra_cols())
        results = run_ingestion(ctx['dataset_id'])

        r = results[0]
        assert r['run_status'] == 'success'
        assert r['column_status'] == 'extra_cols_in_file', \
            f"Column status: {r['column_status']}"
        assert r['records_loaded'] == 5
    finally:
        destroy_dataset(ctx)
    print("PASS: json — extra columns in file ignored, 5 rows loaded")


def test_json_missing_column_loaded_as_null():
    ctx = create_dataset(fmt='json', pattern='*.json')
    try:
        write_json(ctx['landing_dir'], df=sample_df_missing_col())
        results = run_ingestion(ctx['dataset_id'])

        r = results[0]
        assert r['run_status'] in ('success', 'partial')
        assert r['column_status'] == 'missing_cols_in_file'
        assert r['records_loaded'] == 5

        rows = query_ingestion_table(ctx['target_table'], column='amount')
        assert all(row['amount'] is None for row in rows)
    finally:
        destroy_dataset(ctx)
    print("PASS: json — missing schema column loaded as NULL")


def test_json_empty_landing_folder_returns_skipped():
    ctx = create_dataset(fmt='json', pattern='*.json')
    try:
        results = run_ingestion(ctx['dataset_id'])

        assert len(results) == 1
        assert results[0]['run_status'] == 'skipped'
        assert results[0]['records_found'] == 0
    finally:
        destroy_dataset(ctx)
    print("PASS: json — empty landing folder returns skipped entry")


def test_json_new_schema_column_loaded_after_schema_edit():
    ctx = create_dataset(fmt='json', pattern='*.json')
    try:
        did = ctx['dataset_id']

        # Run 1: file has status + category → extras ignored.
        write_json(ctx['landing_dir'], 'v1.json', df=sample_df_extra_cols())
        r1 = run_ingestion(did)
        assert r1[0]['column_status'] == 'extra_cols_in_file'

        # Extend schema.
        extend_schema(did, [
            {'col_name': 'status',   'col_type': 'string', 'col_position': 4},
            {'col_name': 'category', 'col_type': 'string', 'col_position': 5},
        ])

        # Run 2: same file content, all columns now in schema → match.
        write_json(ctx['landing_dir'], 'v2.json', df=sample_df_extra_cols())
        r2 = run_ingestion(did)
        assert r2[0]['column_status'] == 'match',  f"Column status: {r2[0]['column_status']}"
        assert r2[0]['run_status'] == 'success'
        assert r2[0]['records_loaded'] == 5
    finally:
        destroy_dataset(ctx)
    print("PASS: json — after schema edit new columns are loaded correctly")


def test_json_data_values_stored_correctly():
    ctx = create_dataset(fmt='json', pattern='*.json')
    try:
        write_json(ctx['landing_dir'])
        run_ingestion(ctx['dataset_id'])

        rows = query_ingestion_table(ctx['target_table'])
        assert len(rows) == 5
        assert rows[0]['name']   == 'Alice'
        assert float(rows[0]['amount']) == 100.50
        assert int(rows[0]['id'])       == 1
    finally:
        destroy_dataset(ctx)
    print("PASS: json — data values stored correctly in ingestion table")


# ── Runner ────────────────────────────────────────────────────────────────────

TESTS = [
    test_json_new_file_success,
    test_json_same_file_twice_second_run_skipped,
    test_json_extra_columns_in_file_are_ignored,
    test_json_missing_column_loaded_as_null,
    test_json_empty_landing_folder_returns_skipped,
    test_json_new_schema_column_loaded_after_schema_edit,
    test_json_data_values_stored_correctly,
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
