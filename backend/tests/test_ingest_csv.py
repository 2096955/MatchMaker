"""Ingestion engine tests — CSV format.

Run directly:  python test_ingest_csv.py

Scenarios tested:
1.  New file — all columns match → success
2.  Same file submitted twice → second run skipped (dedup by name + size)
3.  File has extra columns not in schema → extras ignored, run succeeds
4.  File missing a schema column → missing column loaded as NULL
5.  Empty landing folder → single skipped entry returned
6.  Multiple files in one run → each processed independently
7.  Schema extended via edit → new column loaded in subsequent run
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from _ingest_helpers import (
    create_dataset, destroy_dataset, run_ingestion, extend_schema,
    query_ingestion_table,
    write_csv, sample_df, sample_df_extra_cols, sample_df_missing_col,
)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_csv_new_file_success():
    ctx = create_dataset(fmt='csv', pattern='*.csv')
    try:
        write_csv(ctx['landing_dir'])
        results = run_ingestion(ctx['dataset_id'])

        assert len(results) == 1
        r = results[0]
        assert r['run_status'] == 'success',       f"Status: {r['run_status']}"
        assert r['column_status'] == 'match',      f"Column status: {r['column_status']}"
        assert r['records_found']   == 5
        assert r['records_loaded']  == 5
        assert r['records_skipped'] == 0
    finally:
        destroy_dataset(ctx)
    print("PASS: csv — new file ingested successfully (5 rows, all columns match)")


def test_csv_same_file_twice_second_run_skipped():
    ctx = create_dataset(fmt='csv', pattern='*.csv')
    try:
        write_csv(ctx['landing_dir'], 'repeat.csv')
        r1 = run_ingestion(ctx['dataset_id'])
        assert r1[0]['run_status'] == 'success', "First run should succeed"

        # Place an identical copy back (same name + same content = same byte-size).
        write_csv(ctx['landing_dir'], 'repeat.csv')
        r2 = run_ingestion(ctx['dataset_id'])

        assert len(r2) == 1
        assert r2[0]['run_status'] == 'skipped',              f"Status: {r2[0]['run_status']}"
        assert 'Already processed' in (r2[0]['status_notes'] or '')
    finally:
        destroy_dataset(ctx)
    print("PASS: csv — identical file re-submitted is skipped on second run")


def test_csv_extra_columns_in_file_are_ignored():
    ctx = create_dataset(fmt='csv', pattern='*.csv')
    try:
        write_csv(ctx['landing_dir'], df=sample_df_extra_cols())
        results = run_ingestion(ctx['dataset_id'])

        r = results[0]
        assert r['run_status'] == 'success',              f"Status: {r['run_status']}"
        assert r['column_status'] == 'extra_cols_in_file', f"Column status: {r['column_status']}"
        assert r['records_loaded'] == 5

        # Extra columns must not appear in the target table.
        row = query_ingestion_table(ctx['target_table'])[0]
        assert 'status'   not in row
        assert 'category' not in row
    finally:
        destroy_dataset(ctx)
    print("PASS: csv — extra columns in file ignored, only schema columns loaded")


def test_csv_missing_column_loaded_as_null():
    ctx = create_dataset(fmt='csv', pattern='*.csv')
    try:
        write_csv(ctx['landing_dir'], df=sample_df_missing_col())  # no 'amount' column
        results = run_ingestion(ctx['dataset_id'])

        r = results[0]
        assert r['run_status'] in ('success', 'partial')
        assert r['column_status'] == 'missing_cols_in_file', f"Column status: {r['column_status']}"
        assert r['records_loaded'] == 5

        rows = query_ingestion_table(ctx['target_table'], column='amount')
        assert all(row['amount'] is None for row in rows), "Missing column should be NULL"
    finally:
        destroy_dataset(ctx)
    print("PASS: csv — missing schema column loaded as NULL for all rows")


def test_csv_empty_landing_folder_returns_skipped():
    ctx = create_dataset(fmt='csv', pattern='*.csv')
    try:
        results = run_ingestion(ctx['dataset_id'])  # no files placed

        assert len(results) == 1
        assert results[0]['run_status'] == 'skipped',  f"Status: {results[0]['run_status']}"
        assert results[0]['records_found'] == 0
        assert 'No files' in (results[0]['status_notes'] or '')
    finally:
        destroy_dataset(ctx)
    print("PASS: csv — empty landing folder returns single skipped log entry")


def test_csv_multiple_files_each_processed_independently():
    ctx = create_dataset(fmt='csv', pattern='*.csv')
    try:
        write_csv(ctx['landing_dir'], 'file_a.csv')
        write_csv(ctx['landing_dir'], 'file_b.csv')
        results = run_ingestion(ctx['dataset_id'])

        assert len(results) == 2,                             f"Expected 2 results, got {len(results)}"
        assert all(r['run_status'] == 'success' for r in results)
        assert sum(r['records_loaded'] for r in results) == 10
    finally:
        destroy_dataset(ctx)
    print("PASS: csv — two files processed in one run, 10 rows total")


def test_csv_new_schema_column_loaded_after_schema_edit():
    ctx = create_dataset(fmt='csv', pattern='*.csv')
    try:
        did = ctx['dataset_id']

        # Run 1: file has status + category columns, schema doesn't → extras ignored.
        write_csv(ctx['landing_dir'], 'v1.csv', df=sample_df_extra_cols())
        r1 = run_ingestion(did)
        assert r1[0]['column_status'] == 'extra_cols_in_file', \
            f"Expected extra_cols_in_file, got {r1[0]['column_status']}"

        # Extend schema to include status + category.
        extend_schema(did, [
            {'col_name': 'status',   'col_type': 'string', 'col_position': 4},
            {'col_name': 'category', 'col_type': 'string', 'col_position': 5},
        ])

        # Run 2: same file content, all 5 columns now in schema → match.
        write_csv(ctx['landing_dir'], 'v2.csv', df=sample_df_extra_cols())
        r2 = run_ingestion(did)
        assert r2[0]['column_status'] == 'match',  f"Column status: {r2[0]['column_status']}"
        assert r2[0]['run_status'] == 'success',   f"Status: {r2[0]['run_status']}"
        assert r2[0]['records_loaded'] == 5
    finally:
        destroy_dataset(ctx)
    print("PASS: csv — after schema edit new column is loaded correctly")


def test_csv_data_values_stored_correctly():
    ctx = create_dataset(fmt='csv', pattern='*.csv')
    try:
        write_csv(ctx['landing_dir'])
        run_ingestion(ctx['dataset_id'])

        rows = query_ingestion_table(ctx['target_table'])
        assert len(rows) == 5
        assert rows[0]['name'] == 'Alice'
        assert float(rows[0]['amount']) == 100.50
        assert int(rows[0]['id']) == 1
    finally:
        destroy_dataset(ctx)
    print("PASS: csv — data values stored correctly in ingestion table")


# ── Runner ────────────────────────────────────────────────────────────────────

TESTS = [
    test_csv_new_file_success,
    test_csv_same_file_twice_second_run_skipped,
    test_csv_extra_columns_in_file_are_ignored,
    test_csv_missing_column_loaded_as_null,
    test_csv_empty_landing_folder_returns_skipped,
    test_csv_multiple_files_each_processed_independently,
    test_csv_new_schema_column_loaded_after_schema_edit,
    test_csv_data_values_stored_correctly,
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
