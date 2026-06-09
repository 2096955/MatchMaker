"""Run every test file and print a consolidated summary.

Usage (from the backend/tests directory):
    python run_all_tests.py
"""
import sys
import os
import importlib

sys.path.insert(0, os.path.dirname(__file__))

TEST_MODULES = [
    'test_providers',
    'test_datasets',
    'test_ingest_csv',
    'test_ingest_txt',
    'test_ingest_xlsx',
    'test_ingest_json',
    'test_ingest_xml',
    'test_ingest_parquet',
]

total_passed = total_failed = 0

for mod_name in TEST_MODULES:
    print(f"\n{'─'*60}")
    print(f"  {mod_name}")
    print(f"{'─'*60}")
    mod = importlib.import_module(mod_name)
    passed = failed = 0
    for test in mod.TESTS:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {test.__name__} — {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {test.__name__} — {type(e).__name__}: {e}")
            failed += 1
    print(f"  → {passed} passed, {failed} failed")
    total_passed += passed
    total_failed += failed

print(f"\n{'='*60}")
print(f"TOTAL: {total_passed} passed, {total_failed} failed")
sys.exit(1 if total_failed else 0)
