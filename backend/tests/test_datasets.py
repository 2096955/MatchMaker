"""Dataset registration and transforms tests.

Each function is a standalone test.  Run the file directly:
    python test_datasets.py
"""

import sys
import os
import shutil
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import app
from db import get_conn, get_ingestion_conn

app.config["TESTING"] = True
client = app.test_client()

# Console /api routes authenticate via the trusted upstream principal header
# (auth._resolve_from_header). Send a fixed test principal on every call so the
# tests exercise the real auth path - no SCUDO_AUTH_ALLOW_DEV bypass.
AUTH = {"X-Authenticated-User": "test@local"}

SAMPLE_COLS = [
    {"col_name": "id", "col_type": "integer", "col_position": 1},
    {"col_name": "name", "col_type": "string", "col_position": 2},
    {"col_name": "amount", "col_type": "decimal", "col_position": 3},
]

# ── API helpers ───────────────────────────────────────────────────────────────


def add_provider(name):
    r = client.post("/api/providers", json={"provider_name": name}, headers=AUTH)
    return r.status_code, r.get_json()


def add_dataset(
    provider_id,
    name,
    landing_folder,
    fmt="csv",
    pattern="*.csv",
    columns=None,
    **kwargs,
):
    payload = {
        "provider_id": provider_id,
        "dataset_name": name,
        "data_format": fmt,
        "file_name_pattern": pattern,
        "landing_folder": landing_folder,
        "columns": columns or SAMPLE_COLS,
    }
    payload.update(kwargs)
    r = client.post("/api/datasets", json=payload, headers=AUTH)
    return r.status_code, r.get_json()


def get_dataset(dataset_id):
    r = client.get(f"/api/datasets/{dataset_id}", headers=AUTH)
    return r.status_code, r.get_json()


def update_dataset(
    dataset_id,
    provider_id,
    name,
    landing_folder,
    fmt="csv",
    pattern="*.csv",
    columns=None,
    **kwargs,
):
    payload = {
        "provider_id": provider_id,
        "dataset_name": name,
        "data_format": fmt,
        "file_name_pattern": pattern,
        "landing_folder": landing_folder,
        "columns": columns or SAMPLE_COLS,
    }
    payload.update(kwargs)
    r = client.put(f"/api/datasets/{dataset_id}", json=payload, headers=AUTH)
    return r.status_code, r.get_json()


def delete_dataset(dataset_id):
    r = client.delete(f"/api/datasets/{dataset_id}", headers=AUTH)
    return r.status_code, r.get_json()


def save_transforms(dataset_id, transforms):
    r = client.put(
        f"/api/datasets/{dataset_id}/transforms", json=transforms, headers=AUTH
    )
    return r.status_code, r.get_json()


def get_transforms(dataset_id):
    r = client.get(f"/api/datasets/{dataset_id}/transforms", headers=AUTH)
    return r.status_code, r.get_json()


def _hard_delete(provider_id, dataset_id=None, target_table=None):
    """Remove all test rows across both databases."""
    conn = get_conn()
    ing = get_ingestion_conn()
    try:
        with conn.cursor() as c:
            if dataset_id:
                c.execute(
                    "DELETE FROM etl_run_log               WHERE dataset_id = %s",
                    (dataset_id,),
                )
                c.execute(
                    "DELETE FROM tp_dataset_col_transforms  WHERE dataset_id = %s",
                    (dataset_id,),
                )
                c.execute(
                    "DELETE FROM tp_dataset_col             WHERE dataset_id = %s",
                    (dataset_id,),
                )
                c.execute(
                    "DELETE FROM tp_dataset                 WHERE dataset_id = %s",
                    (dataset_id,),
                )
            c.execute("DELETE FROM tp_provider WHERE provider_id = %s", (provider_id,))
        conn.commit()
        if target_table:
            with ing.cursor() as ic:
                ic.execute(f'DROP TABLE IF EXISTS "{target_table}"')
            ing.commit()
    finally:
        conn.close()
        ing.close()


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_add_dataset_success():
    d = tempfile.mkdtemp()
    try:
        _, prov = add_provider("__test_ds_prov_1")
        pid = prov["provider_id"]

        status, data = add_dataset(pid, "Sales Data", d)
        assert status == 201, f"Expected 201, got {status}: {data}"
        assert data["dataset_name"] == "Sales Data"
        assert data["version"] == 1
        assert data["current_flag"] == "y"
        assert data["target_table"] is not None
        assert len(data["columns"]) == len(SAMPLE_COLS)

        _hard_delete(pid, data["dataset_id"], data["target_table"])
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("PASS: add_dataset — creates dataset with columns and target table")


def test_add_dataset_missing_required_fields_returns_400():
    resp = client.post(
        "/api/datasets", json={"dataset_name": "no provider"}, headers=AUTH
    )
    status = resp.status_code
    assert status == 400
    print("PASS: add_dataset — missing provider_id returns 400")


def test_add_dataset_provider_not_found_returns_404():
    d = tempfile.mkdtemp()
    try:
        status, data = add_dataset(999999999, "Orphan DS", d)
        assert status == 404, f"Expected 404, got {status}"
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("PASS: add_dataset — non-existent provider returns 404")


def test_add_dataset_landing_folder_not_exists_returns_400():
    _, prov = add_provider("__test_ds_prov_2")
    pid = prov["provider_id"]
    try:
        status, data = add_dataset(pid, "No Folder DS", "/path/that/does/not/exist")
        assert status == 400, f"Expected 400, got {status}"
        assert "does not exist" in data["error"]
    finally:
        _hard_delete(pid)
    print("PASS: add_dataset — non-existent landing folder returns 400")


def test_add_dataset_duplicate_name_in_same_provider_returns_409():
    d = tempfile.mkdtemp()
    try:
        _, prov = add_provider("__test_ds_prov_3")
        pid = prov["provider_id"]

        _, ds1 = add_dataset(pid, "Duplicate DS", d, pattern="a_*.csv")
        status2, data2 = add_dataset(pid, "Duplicate DS", d, pattern="b_*.csv")
        assert status2 == 409, f"Expected 409, got {status2}"
        assert "already exists" in data2["error"]

        _hard_delete(pid, ds1["dataset_id"], ds1["target_table"])
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("PASS: add_dataset — duplicate name in same provider returns 409")


def test_add_dataset_same_landing_folder_different_provider_returns_409():
    d = tempfile.mkdtemp()
    try:
        _, prov1 = add_provider("__test_ds_prov_4a")
        _, prov2 = add_provider("__test_ds_prov_4b")

        _, ds1 = add_dataset(prov1["provider_id"], "DS Prov1", d, pattern="p1_*.csv")
        status2, data2 = add_dataset(
            prov2["provider_id"], "DS Prov2", d, pattern="p2_*.csv"
        )
        assert status2 == 409, f"Expected 409, got {status2}"
        assert "already used by" in data2["error"]

        _hard_delete(prov1["provider_id"], ds1["dataset_id"], ds1["target_table"])
        _hard_delete(prov2["provider_id"])
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("PASS: add_dataset — same landing folder from different provider returns 409")


def test_add_dataset_same_file_pattern_in_same_provider_returns_409():
    d1 = tempfile.mkdtemp()
    d2 = tempfile.mkdtemp()
    try:
        _, prov = add_provider("__test_ds_prov_5")
        pid = prov["provider_id"]

        _, ds1 = add_dataset(pid, "Pattern DS1", d1, pattern="shared_*.csv")
        status2, data2 = add_dataset(pid, "Pattern DS2", d2, pattern="shared_*.csv")
        assert status2 == 409, f"Expected 409, got {status2}"
        assert "already used by" in data2["error"]

        _hard_delete(pid, ds1["dataset_id"], ds1["target_table"])
    finally:
        shutil.rmtree(d1, ignore_errors=True)
        shutil.rmtree(d2, ignore_errors=True)
    print("PASS: add_dataset — same file pattern in same provider returns 409")


def test_get_dataset_returns_correct_record():
    d = tempfile.mkdtemp()
    try:
        _, prov = add_provider("__test_ds_prov_6")
        pid = prov["provider_id"]
        _, ds = add_dataset(pid, "Get Me DS", d)

        status, data = get_dataset(ds["dataset_id"])
        assert status == 200, f"Expected 200, got {status}"
        assert data["dataset_name"] == "Get Me DS"
        assert len(data["columns"]) == 3

        _hard_delete(pid, ds["dataset_id"], ds["target_table"])
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("PASS: get_dataset — returns correct dataset with columns")


def test_get_dataset_not_found_returns_404():
    status, data = get_dataset(999999999)
    assert status == 404, f"Expected 404, got {status}"
    print("PASS: get_dataset — unknown ID returns 404")


def test_update_dataset_creates_new_version():
    d = tempfile.mkdtemp()
    try:
        _, prov = add_provider("__test_ds_prov_7")
        pid = prov["provider_id"]
        _, ds = add_dataset(pid, "Update DS", d)
        did = ds["dataset_id"]

        status, data = update_dataset(
            did, pid, "Update DS", d, dataset_desc="Updated description"
        )
        assert status == 200, f"Expected 200, got {status}"
        assert data["version"] == 2
        assert data["current_flag"] == "y"

        _hard_delete(pid, did, ds["target_table"])
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("PASS: update_dataset — creates SCD-2 version 2")


def test_update_dataset_adds_new_column():
    d = tempfile.mkdtemp()
    try:
        _, prov = add_provider("__test_ds_prov_8")
        pid = prov["provider_id"]
        _, ds = add_dataset(pid, "Add Column DS", d)
        did = ds["dataset_id"]

        new_cols = SAMPLE_COLS + [
            {"col_name": "status", "col_type": "string", "col_position": 4}
        ]
        status, data = update_dataset(did, pid, "Add Column DS", d, columns=new_cols)
        assert status == 200
        col_names = [c["col_name"] for c in data["columns"]]
        assert "status" in col_names
        assert len(data["columns"]) == 4

        _hard_delete(pid, did, ds["target_table"])
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("PASS: update_dataset — new column added to schema and ingestion table")


def test_update_dataset_preserves_existing_columns():
    d = tempfile.mkdtemp()
    try:
        _, prov = add_provider("__test_ds_prov_9")
        pid = prov["provider_id"]
        _, ds = add_dataset(pid, "Preserve Cols DS", d)
        did = ds["dataset_id"]

        # Submit empty column list — existing columns must be preserved.
        status, data = update_dataset(did, pid, "Preserve Cols DS", d, columns=[])
        assert status == 200
        assert len(data["columns"]) == 3

        _hard_delete(pid, did, ds["target_table"])
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("PASS: update_dataset — existing columns preserved when none submitted")


def test_update_dataset_landing_folder_not_exists_returns_400():
    d = tempfile.mkdtemp()
    try:
        _, prov = add_provider("__test_ds_prov_10")
        pid = prov["provider_id"]
        _, ds = add_dataset(pid, "Folder Check DS", d)
        did = ds["dataset_id"]

        status, data = update_dataset(
            did, pid, "Folder Check DS", "/nonexistent/edit/path"
        )
        assert status == 400, f"Expected 400, got {status}"

        _hard_delete(pid, did, ds["target_table"])
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("PASS: update_dataset — non-existent landing folder returns 400")


def test_delete_dataset_sets_inactive():
    d = tempfile.mkdtemp()
    try:
        _, prov = add_provider("__test_ds_prov_11")
        pid = prov["provider_id"]
        _, ds = add_dataset(pid, "Delete Me DS", d)
        did = ds["dataset_id"]

        status, data = delete_dataset(did)
        assert status == 200, f"Expected 200, got {status}"
        assert data["dataset_id"] == did

        get_status, _ = get_dataset(did)
        assert get_status == 404

        _hard_delete(pid, did, ds["target_table"])
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("PASS: delete_dataset — dataset no longer retrievable after deletion")


def test_delete_dataset_not_found_returns_404():
    status, _ = delete_dataset(999999999)
    assert status == 404
    print("PASS: delete_dataset — unknown ID returns 404")


def test_save_and_get_transforms():
    d = tempfile.mkdtemp()
    try:
        _, prov = add_provider("__test_ds_prov_12")
        pid = prov["provider_id"]
        _, ds = add_dataset(pid, "Transforms DS", d)
        did = ds["dataset_id"]

        transforms = [
            {
                "col_name": "name",
                "trim_option": "trim",
                "null_replace": "UNKNOWN",
                "case_option": "upper",
                "date_fmt_from": None,
                "date_fmt_to": None,
            },
            {
                "col_name": "amount",
                "trim_option": None,
                "null_replace": "0",
                "case_option": None,
                "date_fmt_from": None,
                "date_fmt_to": None,
            },
            {
                "col_name": "id",
                "trim_option": None,
                "null_replace": None,
                "case_option": None,
                "date_fmt_from": None,
                "date_fmt_to": None,
            },
        ]
        save_status, save_data = save_transforms(did, transforms)
        assert save_status == 200, f"Expected 200, got {save_status}"
        assert save_data["saved"] == 2  # 'id' row has no transforms → skipped

        get_status, get_data = get_transforms(did)
        assert get_status == 200
        saved = {t["col_name"]: t for t in get_data["transforms"]}
        assert saved["name"]["trim_option"] == "trim"
        assert saved["name"]["case_option"] == "upper"
        assert saved["name"]["null_replace"] == "UNKNOWN"
        assert saved["amount"]["null_replace"] == "0"
        assert saved["id"]["trim_option"] is None

        _hard_delete(pid, did, ds["target_table"])
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print(
        "PASS: save_transforms / get_transforms — rules saved and retrieved correctly"
    )


def test_save_transforms_replaces_previous_rules():
    d = tempfile.mkdtemp()
    try:
        _, prov = add_provider("__test_ds_prov_13")
        pid = prov["provider_id"]
        _, ds = add_dataset(pid, "Replace Transforms DS", d)
        did = ds["dataset_id"]

        save_transforms(
            did,
            [
                {
                    "col_name": "name",
                    "trim_option": "ltrim",
                    "null_replace": None,
                    "case_option": None,
                    "date_fmt_from": None,
                    "date_fmt_to": None,
                },
            ],
        )
        save_transforms(
            did,
            [
                {
                    "col_name": "amount",
                    "trim_option": None,
                    "null_replace": "0",
                    "case_option": None,
                    "date_fmt_from": None,
                    "date_fmt_to": None,
                },
            ],
        )

        _, get_data = get_transforms(did)
        saved = {t["col_name"]: t for t in get_data["transforms"]}
        assert saved["name"]["trim_option"] is None  # previous rule wiped
        assert saved["amount"]["null_replace"] == "0"

        _hard_delete(pid, did, ds["target_table"])
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("PASS: save_transforms — second save replaces all previous rules")


def test_get_transforms_returns_all_columns_even_without_rules():
    d = tempfile.mkdtemp()
    try:
        _, prov = add_provider("__test_ds_prov_14")
        pid = prov["provider_id"]
        _, ds = add_dataset(pid, "Empty Transforms DS", d)
        did = ds["dataset_id"]

        status, data = get_transforms(did)
        assert status == 200
        assert len(data["transforms"]) == 3  # one row per column
        col_names = [t["col_name"] for t in data["transforms"]]
        assert "id" in col_names and "name" in col_names and "amount" in col_names

        _hard_delete(pid, did, ds["target_table"])
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("PASS: get_transforms — all columns returned even with no saved rules")


# ── Runner ────────────────────────────────────────────────────────────────────

TESTS = [
    test_add_dataset_success,
    test_add_dataset_missing_required_fields_returns_400,
    test_add_dataset_provider_not_found_returns_404,
    test_add_dataset_landing_folder_not_exists_returns_400,
    test_add_dataset_duplicate_name_in_same_provider_returns_409,
    test_add_dataset_same_landing_folder_different_provider_returns_409,
    test_add_dataset_same_file_pattern_in_same_provider_returns_409,
    test_get_dataset_returns_correct_record,
    test_get_dataset_not_found_returns_404,
    test_update_dataset_creates_new_version,
    test_update_dataset_adds_new_column,
    test_update_dataset_preserves_existing_columns,
    test_update_dataset_landing_folder_not_exists_returns_400,
    test_delete_dataset_sets_inactive,
    test_delete_dataset_not_found_returns_404,
    test_save_and_get_transforms,
    test_save_transforms_replaces_previous_rules,
    test_get_transforms_returns_all_columns_even_without_rules,
]

if __name__ == "__main__":
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
    print(f"\n{'=' * 50}")
    print(f"Results: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
