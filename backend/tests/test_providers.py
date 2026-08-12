"""Provider registration tests.

Each function is a standalone test.  Run the file directly:
    python test_providers.py

Requires PostgreSQL running locally (the `console` and `ingestion` schemas).
JPMC-LOCAL: this line used to say MySQL. It was stale -- the console DB was
ported to Aurora PostgreSQL (see db.py, psycopg v3) and no MySQL driver
remains anywhere in the Python code. `docker compose up -d` provides it.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import app
from db import get_conn

app.config["TESTING"] = True
client = app.test_client()

# Console /api routes authenticate via the trusted upstream principal header
# (auth._resolve_from_header). Drive the app through its real auth by sending a
# fixed test principal on every call - no SCUDO_AUTH_ALLOW_DEV bypass.
AUTH = {"X-Authenticated-User": "test@local"}

# ── API helpers ───────────────────────────────────────────────────────────────


def add_provider(name, desc=None, website=None):
    payload = {"provider_name": name}
    if desc:
        payload["provider_desc"] = desc
    if website:
        payload["provider_website"] = website
    r = client.post("/api/providers", json=payload, headers=AUTH)
    return r.status_code, r.get_json()


def get_provider(provider_id):
    r = client.get(f"/api/providers/{provider_id}", headers=AUTH)
    return r.status_code, r.get_json()


def update_provider(provider_id, name, desc=None):
    payload = {"provider_name": name}
    if desc:
        payload["provider_desc"] = desc
    r = client.put(f"/api/providers/{provider_id}", json=payload, headers=AUTH)
    return r.status_code, r.get_json()


def delete_provider(provider_id):
    r = client.delete(f"/api/providers/{provider_id}", headers=AUTH)
    return r.status_code, r.get_json()


def list_providers(search=None):
    url = "/api/providers" + (f"?q={search}" if search else "")
    r = client.get(url, headers=AUTH)
    return r.status_code, r.get_json()


def _hard_delete_provider(provider_id):
    """Remove all DB rows for a test provider (API only does logical delete)."""
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM tp_provider WHERE provider_id = %s", (provider_id,))
        conn.commit()
    finally:
        conn.close()


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_add_provider_success():
    status, data = add_provider("Acme Corporation", desc="Global data supplier")
    assert status == 201, f"Expected 201, got {status}: {data}"
    assert data["provider_name"] == "Acme Corporation"
    assert data["provider_desc"] == "Global data supplier"
    assert data["version"] == 1
    assert data["current_flag"] == "y"
    assert data["provider_id"] is not None
    _hard_delete_provider(data["provider_id"])
    print("PASS: add_provider — creates provider with correct fields")


def test_add_provider_with_all_optional_fields():
    status, data = add_provider(
        "Beta Feeds",
        desc="Financial data feed",
        website="https://betafeeds.example.com",
    )
    assert status == 201
    assert data["provider_website"] == "https://betafeeds.example.com"
    _hard_delete_provider(data["provider_id"])
    print("PASS: add_provider — optional fields stored correctly")


def test_add_provider_missing_name_returns_400():
    resp = client.post(
        "/api/providers", json={"provider_desc": "no name"}, headers=AUTH
    )
    status = resp.status_code
    assert status == 400, f"Expected 400, got {status}"
    print("PASS: add_provider — missing name returns 400")


def test_add_provider_blank_name_returns_400():
    status, data = add_provider("   ")
    assert status == 400, f"Expected 400, got {status}"
    print("PASS: add_provider — blank name returns 400")


def test_add_provider_duplicate_name_returns_409():
    status1, data1 = add_provider("Duplicate Corp")
    assert status1 == 201
    status2, data2 = add_provider("Duplicate Corp")
    assert status2 == 409, f"Expected 409, got {status2}"
    assert "already exists" in data2["error"]
    _hard_delete_provider(data1["provider_id"])
    print("PASS: add_provider — duplicate name returns 409")


def test_add_provider_duplicate_name_case_insensitive():
    status1, data1 = add_provider("Case Provider")
    assert status1 == 201
    status2, _ = add_provider("CASE PROVIDER")
    assert status2 == 409, f"Expected 409, got {status2}"
    _hard_delete_provider(data1["provider_id"])
    print("PASS: add_provider — case-insensitive duplicate check returns 409")


def test_get_provider_returns_correct_record():
    _, created = add_provider("Get Test Provider")
    pid = created["provider_id"]

    status, data = get_provider(pid)
    assert status == 200, f"Expected 200, got {status}"
    assert data["provider_id"] == pid
    assert data["provider_name"] == "Get Test Provider"
    _hard_delete_provider(pid)
    print("PASS: get_provider — returns correct record by ID")


def test_get_provider_not_found_returns_404():
    status, data = get_provider(999999999)
    assert status == 404, f"Expected 404, got {status}"
    assert "error" in data
    print("PASS: get_provider — unknown ID returns 404")


def test_update_provider_creates_new_version():
    _, created = add_provider("Update Me")
    pid = created["provider_id"]

    status, data = update_provider(pid, "Update Me v2", desc="New description")
    assert status == 200, f"Expected 200, got {status}"
    assert data["provider_name"] == "Update Me v2"
    assert data["version"] == 2
    assert data["current_flag"] == "y"
    _hard_delete_provider(pid)
    print("PASS: update_provider — creates SCD-2 version 2 with new name")


def test_update_provider_same_name_allowed():
    _, created = add_provider("Same Name Provider")
    pid = created["provider_id"]

    status, data = update_provider(pid, "Same Name Provider", desc="Changed only desc")
    assert status == 200
    assert data["version"] == 2
    _hard_delete_provider(pid)
    print("PASS: update_provider — updating description without changing name succeeds")


def test_update_provider_name_collision_returns_409():
    _, prov1 = add_provider("Provider Alpha")
    _, prov2 = add_provider("Provider Beta")

    status, data = update_provider(prov1["provider_id"], "Provider Beta")
    assert status == 409, f"Expected 409, got {status}"
    _hard_delete_provider(prov1["provider_id"])
    _hard_delete_provider(prov2["provider_id"])
    print("PASS: update_provider — renaming to existing provider name returns 409")


def test_update_provider_not_found_returns_404():
    status, _ = update_provider(999999999, "Ghost Provider")
    assert status == 404, f"Expected 404, got {status}"
    print("PASS: update_provider — unknown ID returns 404")


def test_update_provider_missing_name_returns_400():
    _, created = add_provider("Needs Name")
    pid = created["provider_id"]

    r = client.put(
        f"/api/providers/{pid}", json={"provider_desc": "no name field"}, headers=AUTH
    )
    assert r.status_code == 400, f"Expected 400, got {r.status_code}"
    _hard_delete_provider(pid)
    print("PASS: update_provider — missing name returns 400")


def test_delete_provider_sets_inactive():
    _, created = add_provider("Delete Me Provider")
    pid = created["provider_id"]

    status, data = delete_provider(pid)
    assert status == 200, f"Expected 200, got {status}"
    assert data["provider_id"] == pid

    # Provider should no longer appear in GET.
    get_status, _ = get_provider(pid)
    assert get_status == 404
    _hard_delete_provider(pid)
    print("PASS: delete_provider — provider no longer retrievable after deletion")


def test_delete_provider_not_found_returns_404():
    status, _ = delete_provider(999999999)
    assert status == 404, f"Expected 404, got {status}"
    print("PASS: delete_provider — unknown ID returns 404")


def test_list_providers_returns_array():
    status, data = list_providers()
    assert status == 200
    assert isinstance(data, list)
    print("PASS: list_providers — returns JSON array")


def test_list_providers_search_filters_results():
    _, prov = add_provider("SearchableProviderXYZ")
    pid = prov["provider_id"]

    status, results = list_providers(search="SearchableProviderXYZ")
    assert status == 200
    names = [p["provider_name"] for p in results]
    assert "SearchableProviderXYZ" in names
    _hard_delete_provider(pid)
    print("PASS: list_providers — search by name returns matching provider")


def test_list_providers_search_no_match_returns_empty():
    status, results = list_providers(search="__no_match_99zz99__")
    assert status == 200
    assert results == []
    print("PASS: list_providers — search with no match returns empty list")


# ── Runner ────────────────────────────────────────────────────────────────────

TESTS = [
    test_add_provider_success,
    test_add_provider_with_all_optional_fields,
    test_add_provider_missing_name_returns_400,
    test_add_provider_blank_name_returns_400,
    test_add_provider_duplicate_name_returns_409,
    test_add_provider_duplicate_name_case_insensitive,
    test_get_provider_returns_correct_record,
    test_get_provider_not_found_returns_404,
    test_update_provider_creates_new_version,
    test_update_provider_same_name_allowed,
    test_update_provider_name_collision_returns_409,
    test_update_provider_not_found_returns_404,
    test_update_provider_missing_name_returns_400,
    test_delete_provider_sets_inactive,
    test_delete_provider_not_found_returns_404,
    test_list_providers_returns_array,
    test_list_providers_search_filters_results,
    test_list_providers_search_no_match_returns_empty,
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
