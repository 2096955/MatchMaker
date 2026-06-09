"""Ingestion engine tests — Excel (XLSX) format.

Run directly:  python test_ingest_xlsx.py

XlsxIngester reads the sheet specified by tp_dataset.sheet_name,
defaulting to sheet index 0 when not set.

Scenarios tested:
1.  New XLSX file (default sheet) → success
2.  Named sheet configured — data read from the correct sheet
3.  Same file twice → second run skipped
4.  Extra columns in file → extras ignored
5.  Missing schema column → loaded as NULL
6.  Empty landing folder → single skipped entry
7.  Multiple files in one run
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pandas as pd
from _ingest_helpers import (
    create_dataset, destroy_dataset, run_ingestion,
    query_ingestion_table, sample_df,
    write_xlsx, sample_df_extra_cols, sample_df_missing_col,
)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_xlsx_new_file_success():
    ctx = create_dataset(fmt='xlsx', pattern='*.xlsx')
    try:
        write_xlsx(ctx['landing_dir'])
        results = run_ingestion(ctx['dataset_id'])

        r = results[0]
        assert r['run_status'] == 'success',  f"Status: {r['run_status']}"
        assert r['column_status'] == 'match', f"Column status: {r['column_status']}"
        assert r['records_found']   == 5
        assert r['records_loaded']  == 5
        assert r['records_skipped'] == 0
    finally:
        destroy_dataset(ctx)
    print("PASS: xlsx — new file ingested successfully")


def test_xlsx_named_sheet_is_read_correctly():
    """When sheet_name is set the engine must read from that sheet, not sheet 0."""
    ctx = create_dataset(fmt='xlsx', pattern='*.xlsx',
                         extra_payload={'sheet_name': 'Sheet2'})
    try:
        path = os.path.join(ctx['landing_dir'], 'multi.xlsx')
        with pd.ExcelWriter(path, engine='openpyxl') as writer:
            # Decoy data on Sheet1.
            pd.DataFrame([{'id': 99, 'name': 'Decoy', 'amount': 0.0}]).to_excel(
                writer, sheet_name='Sheet1', index=False)
            # Real data on Sheet2.
            sample_df().to_excel(writer, sheet_name='Sheet2', index=False)

        results = run_ingestion(ctx['dataset_id'])
        r = results[0]
        assert r['run_status'] == 'success'
        assert r['records_found'] == 5,   f"Expected 5 rows from Sheet2, got {r['records_found']}"
    finally:
        destroy_dataset(ctx)
    print("PASS: xlsx — data loaded from configured named sheet (not sheet 0)")


def test_xlsx_same_file_twice_second_run_skipped():
    ctx = create_dataset(fmt='xlsx', pattern='*.xlsx')
    try:
        write_xlsx(ctx['landing_dir'], 'data.xlsx')
        r1 = run_ingestion(ctx['dataset_id'])
        assert r1[0]['run_status'] == 'success'

        write_xlsx(ctx['landing_dir'], 'data.xlsx')
        r2 = run_ingestion(ctx['dataset_id'])
        assert r2[0]['run_status'] == 'skipped'
        assert 'Already processed' in (r2[0]['status_notes'] or '')
    finally:
        destroy_dataset(ctx)
    print("PASS: xlsx — identical file re-submitted is skipped on second run")


def test_xlsx_extra_columns_in_file_are_ignored():
    ctx = create_dataset(fmt='xlsx', pattern='*.xlsx')
    try:
        write_xlsx(ctx['landing_dir'], df=sample_df_extra_cols())
        results = run_ingestion(ctx['dataset_id'])

        r = results[0]
        assert r['run_status'] == 'success'
        assert r['column_status'] == 'extra_cols_in_file', \
            f"Column status: {r['column_status']}"
        assert r['records_loaded'] == 5
    finally:
        destroy_dataset(ctx)
    print("PASS: xlsx — extra columns in file ignored, 5 rows loaded")


def test_xlsx_missing_column_loaded_as_null():
    ctx = create_dataset(fmt='xlsx', pattern='*.xlsx')
    try:
        write_xlsx(ctx['landing_dir'], df=sample_df_missing_col())
        results = run_ingestion(ctx['dataset_id'])

        r = results[0]
        assert r['run_status'] in ('success', 'partial')
        assert r['column_status'] == 'missing_cols_in_file'
        assert r['records_loaded'] == 5

        rows = query_ingestion_table(ctx['target_table'], column='amount')
        assert all(row['amount'] is None for row in rows)
    finally:
        destroy_dataset(ctx)
    print("PASS: xlsx — missing schema column loaded as NULL")


def test_xlsx_empty_landing_folder_returns_skipped():
    ctx = create_dataset(fmt='xlsx', pattern='*.xlsx')
    try:
        results = run_ingestion(ctx['dataset_id'])

        assert len(results) == 1
        assert results[0]['run_status'] == 'skipped'
        assert results[0]['records_found'] == 0
    finally:
        destroy_dataset(ctx)
    print("PASS: xlsx — empty landing folder returns skipped entry")


def test_xlsx_multiple_files_processed():
    ctx = create_dataset(fmt='xlsx', pattern='*.xlsx')
    try:
        write_xlsx(ctx['landing_dir'], 'a.xlsx')
        write_xlsx(ctx['landing_dir'], 'b.xlsx')
        results = run_ingestion(ctx['dataset_id'])

        assert len(results) == 2
        assert all(r['run_status'] == 'success' for r in results)
        assert sum(r['records_loaded'] for r in results) == 10
    finally:
        destroy_dataset(ctx)
    print("PASS: xlsx — two files processed, 10 rows total")


# ── Runner ────────────────────────────────────────────────────────────────────

TESTS = [
    test_xlsx_new_file_success,
    test_xlsx_named_sheet_is_read_correctly,
    test_xlsx_same_file_twice_second_run_skipped,
    test_xlsx_extra_columns_in_file_are_ignored,
    test_xlsx_missing_column_loaded_as_null,
    test_xlsx_empty_landing_folder_returns_skipped,
    test_xlsx_multiple_files_processed,
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
