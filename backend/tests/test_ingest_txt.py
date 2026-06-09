"""Ingestion engine tests — TXT (delimiter-separated) format.

Run directly:  python test_ingest_txt.py

TxtIngester reads the delimiter from tp_dataset.delimiter.
These tests use pipe ('|') to distinguish from CSV.

Scenarios tested:
1.  New pipe-delimited file → success
2.  Delimiter mismatch (comma file, pipe expected) → failed (no known columns)
3.  Same file twice → second run skipped
4.  Extra columns in file → extras ignored
5.  Missing schema column → loaded as NULL
6.  Empty landing folder → single skipped entry
7.  Multiple files in one run
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from _ingest_helpers import (
    create_dataset, destroy_dataset, run_ingestion,
    query_ingestion_table,
    write_txt, sample_df_extra_cols, sample_df_missing_col,
)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_txt_pipe_delimited_new_file_success():
    ctx = create_dataset(fmt='txt', pattern='*.txt', extra_payload={'delimiter': '|'})
    try:
        write_txt(ctx['landing_dir'], delimiter='|')
        results = run_ingestion(ctx['dataset_id'])

        r = results[0]
        assert r['run_status'] == 'success',  f"Status: {r['run_status']}"
        assert r['column_status'] == 'match', f"Column status: {r['column_status']}"
        assert r['records_found']  == 5
        assert r['records_loaded'] == 5
    finally:
        destroy_dataset(ctx)
    print("PASS: txt — pipe-delimited file ingested successfully")


def test_txt_delimiter_mismatch_causes_failed_run():
    """File uses comma but dataset expects pipe → entire line parsed as one column."""
    ctx = create_dataset(fmt='txt', pattern='*.txt', extra_payload={'delimiter': '|'})
    try:
        write_txt(ctx['landing_dir'], delimiter=',')   # wrong delimiter
        results = run_ingestion(ctx['dataset_id'])

        r = results[0]
        assert r['run_status'] == 'failed', \
            f"Expected failed due to no loadable columns, got {r['run_status']}"
    finally:
        destroy_dataset(ctx)
    print("PASS: txt — delimiter mismatch causes failed run (no loadable columns)")


def test_txt_same_file_twice_second_run_skipped():
    ctx = create_dataset(fmt='txt', pattern='*.txt', extra_payload={'delimiter': '|'})
    try:
        write_txt(ctx['landing_dir'], 'data.txt', delimiter='|')
        r1 = run_ingestion(ctx['dataset_id'])
        assert r1[0]['run_status'] == 'success'

        write_txt(ctx['landing_dir'], 'data.txt', delimiter='|')
        r2 = run_ingestion(ctx['dataset_id'])
        assert r2[0]['run_status'] == 'skipped'
        assert 'Already processed' in (r2[0]['status_notes'] or '')
    finally:
        destroy_dataset(ctx)
    print("PASS: txt — identical file re-submitted is skipped on second run")


def test_txt_extra_columns_in_file_are_ignored():
    ctx = create_dataset(fmt='txt', pattern='*.txt', extra_payload={'delimiter': '|'})
    try:
        write_txt(ctx['landing_dir'], df=sample_df_extra_cols(), delimiter='|')
        results = run_ingestion(ctx['dataset_id'])

        r = results[0]
        assert r['run_status'] == 'success'
        assert r['column_status'] == 'extra_cols_in_file', \
            f"Column status: {r['column_status']}"
        assert r['records_loaded'] == 5
    finally:
        destroy_dataset(ctx)
    print("PASS: txt — extra columns in file ignored, 5 rows loaded")


def test_txt_missing_column_loaded_as_null():
    ctx = create_dataset(fmt='txt', pattern='*.txt', extra_payload={'delimiter': '|'})
    try:
        write_txt(ctx['landing_dir'], df=sample_df_missing_col(), delimiter='|')
        results = run_ingestion(ctx['dataset_id'])

        r = results[0]
        assert r['run_status'] in ('success', 'partial')
        assert r['column_status'] == 'missing_cols_in_file'
        assert r['records_loaded'] == 5

        rows = query_ingestion_table(ctx['target_table'], column='amount')
        assert all(row['amount'] is None for row in rows)
    finally:
        destroy_dataset(ctx)
    print("PASS: txt — missing schema column loaded as NULL")


def test_txt_empty_landing_folder_returns_skipped():
    ctx = create_dataset(fmt='txt', pattern='*.txt', extra_payload={'delimiter': '|'})
    try:
        results = run_ingestion(ctx['dataset_id'])

        assert len(results) == 1
        assert results[0]['run_status'] == 'skipped'
        assert results[0]['records_found'] == 0
    finally:
        destroy_dataset(ctx)
    print("PASS: txt — empty landing folder returns skipped entry")


def test_txt_multiple_files_processed():
    ctx = create_dataset(fmt='txt', pattern='*.txt', extra_payload={'delimiter': '|'})
    try:
        write_txt(ctx['landing_dir'], 'a.txt', delimiter='|')
        write_txt(ctx['landing_dir'], 'b.txt', delimiter='|')
        results = run_ingestion(ctx['dataset_id'])

        assert len(results) == 2
        assert all(r['run_status'] == 'success' for r in results)
        assert sum(r['records_loaded'] for r in results) == 10
    finally:
        destroy_dataset(ctx)
    print("PASS: txt — two files processed, 10 rows total")


# ── Runner ────────────────────────────────────────────────────────────────────

TESTS = [
    test_txt_pipe_delimited_new_file_success,
    test_txt_delimiter_mismatch_causes_failed_run,
    test_txt_same_file_twice_second_run_skipped,
    test_txt_extra_columns_in_file_are_ignored,
    test_txt_missing_column_loaded_as_null,
    test_txt_empty_landing_folder_returns_skipped,
    test_txt_multiple_files_processed,
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
