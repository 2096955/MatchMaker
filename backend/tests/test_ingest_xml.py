"""Ingestion engine tests — XML format.

Run directly:  python test_ingest_xml.py

XmlIngester uses pandas.read_xml.
Test files use <data><row>…</row></data> structure.

Scenarios tested:
1.  New XML file → success
2.  Same file twice → second run skipped
3.  Extra columns in file → extras ignored
4.  Missing schema column → loaded as NULL
5.  Empty landing folder → single skipped entry
6.  Malformed XML → run marked failed, error_detail captured
7.  Data values verified in ingestion table
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from _ingest_helpers import (
    create_dataset, destroy_dataset, run_ingestion,
    query_ingestion_table,
    write_xml, sample_df_extra_cols, sample_df_missing_col,
)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_xml_new_file_success():
    ctx = create_dataset(fmt='xml', pattern='*.xml')
    try:
        write_xml(ctx['landing_dir'])
        results = run_ingestion(ctx['dataset_id'])

        r = results[0]
        assert r['run_status'] == 'success',  f"Status: {r['run_status']}"
        assert r['column_status'] == 'match', f"Column status: {r['column_status']}"
        assert r['records_found']   == 5
        assert r['records_loaded']  == 5
        assert r['records_skipped'] == 0
    finally:
        destroy_dataset(ctx)
    print("PASS: xml — new file ingested successfully")


def test_xml_same_file_twice_second_run_skipped():
    ctx = create_dataset(fmt='xml', pattern='*.xml')
    try:
        write_xml(ctx['landing_dir'], 'data.xml')
        r1 = run_ingestion(ctx['dataset_id'])
        assert r1[0]['run_status'] == 'success'

        write_xml(ctx['landing_dir'], 'data.xml')
        r2 = run_ingestion(ctx['dataset_id'])
        assert r2[0]['run_status'] == 'skipped'
        assert 'Already processed' in (r2[0]['status_notes'] or '')
    finally:
        destroy_dataset(ctx)
    print("PASS: xml — identical file re-submitted is skipped on second run")


def test_xml_extra_columns_in_file_are_ignored():
    ctx = create_dataset(fmt='xml', pattern='*.xml')
    try:
        write_xml(ctx['landing_dir'], df=sample_df_extra_cols())
        results = run_ingestion(ctx['dataset_id'])

        r = results[0]
        assert r['run_status'] == 'success'
        assert r['column_status'] == 'extra_cols_in_file', \
            f"Column status: {r['column_status']}"
        assert r['records_loaded'] == 5
    finally:
        destroy_dataset(ctx)
    print("PASS: xml — extra columns in file ignored, 5 rows loaded")


def test_xml_missing_column_loaded_as_null():
    ctx = create_dataset(fmt='xml', pattern='*.xml')
    try:
        write_xml(ctx['landing_dir'], df=sample_df_missing_col())
        results = run_ingestion(ctx['dataset_id'])

        r = results[0]
        assert r['run_status'] in ('success', 'partial')
        assert r['column_status'] == 'missing_cols_in_file'
        assert r['records_loaded'] == 5

        rows = query_ingestion_table(ctx['target_table'], column='amount')
        assert all(row['amount'] is None for row in rows)
    finally:
        destroy_dataset(ctx)
    print("PASS: xml — missing schema column loaded as NULL")


def test_xml_empty_landing_folder_returns_skipped():
    ctx = create_dataset(fmt='xml', pattern='*.xml')
    try:
        results = run_ingestion(ctx['dataset_id'])

        assert len(results) == 1
        assert results[0]['run_status'] == 'skipped'
        assert results[0]['records_found'] == 0
    finally:
        destroy_dataset(ctx)
    print("PASS: xml — empty landing folder returns skipped entry")


def test_xml_malformed_file_run_marked_failed():
    ctx = create_dataset(fmt='xml', pattern='*.xml')
    try:
        bad_path = os.path.join(ctx['landing_dir'], 'bad.xml')
        with open(bad_path, 'w') as fh:
            fh.write('<data><row><id>1</id><unclosed></data>')

        results = run_ingestion(ctx['dataset_id'])

        r = results[0]
        assert r['run_status'] == 'failed', f"Expected failed, got {r['run_status']}"
        assert r['error_detail'] is not None, "error_detail should capture the traceback"
    finally:
        destroy_dataset(ctx)
    print("PASS: xml — malformed file causes failed run with error_detail captured")


def test_xml_data_values_stored_correctly():
    ctx = create_dataset(fmt='xml', pattern='*.xml')
    try:
        write_xml(ctx['landing_dir'])
        run_ingestion(ctx['dataset_id'])

        rows = query_ingestion_table(ctx['target_table'])
        assert len(rows) == 5
        assert rows[0]['name'] == 'Alice'
        assert int(rows[0]['id']) == 1
    finally:
        destroy_dataset(ctx)
    print("PASS: xml — data values stored correctly in ingestion table")


# ── Runner ────────────────────────────────────────────────────────────────────

TESTS = [
    test_xml_new_file_success,
    test_xml_same_file_twice_second_run_skipped,
    test_xml_extra_columns_in_file_are_ignored,
    test_xml_missing_column_loaded_as_null,
    test_xml_empty_landing_folder_returns_skipped,
    test_xml_malformed_file_run_marked_failed,
    test_xml_data_values_stored_correctly,
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
