"""Readiness honesty (Codex A8): a failed seed must NOT mark the app seeded,
must leave readiness False, and /readyz must 503 until seeding succeeds.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _fresh_mapping_module(monkeypatch):
    # Import the module and reset its module-level seed state for each test.
    import routes.mapping as m

    monkeypatch.setattr(m, "_seeded", False, raising=False)
    monkeypatch.setattr(
        m, "_readiness", {"seed_ok": False, "last_error": None}, raising=False
    )
    return m


def test_seed_failure_leaves_unseeded_and_not_ready(monkeypatch):
    m = _fresh_mapping_module(monkeypatch)

    def _boom():
        raise RuntimeError("falkordb down")

    monkeypatch.setattr(m, "seed_taxonomy", _boom)
    m._ensure_seeded()

    assert m._seeded is False, (
        "a failed seed must NOT mark the app seeded (retry next call)"
    )
    assert m.readiness()["seed_ok"] is False
    assert "falkordb down" in (m.readiness()["last_error"] or "")


def test_seed_success_marks_ready(monkeypatch):
    m = _fresh_mapping_module(monkeypatch)
    monkeypatch.setattr(m, "seed_taxonomy", lambda: 19)
    # hydrate is best-effort; stub it so the test doesn't need a bundle/store.
    monkeypatch.setattr(m, "hydrate", lambda strict=False: _DummyHydrate())
    monkeypatch.setattr(m, "get_store", lambda: _HealthyStore())

    m._ensure_seeded()
    assert m._seeded is True
    assert m.readiness()["seed_ok"] is True
    assert m.readiness()["last_error"] is None


def test_seed_success_with_unhealthy_store_stays_not_ready(monkeypatch):
    m = _fresh_mapping_module(monkeypatch)
    monkeypatch.setattr(m, "seed_taxonomy", lambda: 19)
    monkeypatch.setattr(m, "get_store", lambda: _UnhealthyStore())

    m._ensure_seeded()

    assert m._seeded is False
    assert m.readiness()["seed_ok"] is False
    assert "unhealthy" in (m.readiness()["last_error"] or "")


def test_concurrent_seed_failure_cannot_poison_success(monkeypatch):
    m = _fresh_mapping_module(monkeypatch)
    entered = threading.Barrier(2)
    calls = 0

    def seed_once():
        nonlocal calls
        calls += 1
        return 19

    monkeypatch.setattr(m, "seed_taxonomy", seed_once)
    monkeypatch.setattr(m, "seed_conceptual_layer", lambda: 0)
    monkeypatch.setattr(m, "hydrate", lambda strict=False: _DummyHydrate())
    monkeypatch.setattr(m, "get_store", lambda: _HealthyStore())

    def concurrent_start():
        entered.wait(timeout=5)
        m._ensure_seeded()

    threads = [threading.Thread(target=concurrent_start) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert m._seeded is True
    assert m.readiness()["seed_ok"] is True
    assert calls == 1


class _DummyHydrate:
    skipped_no_bundle = True
    applied = 0
    skipped_unknown_node = 0
    skipped_out_of_scope = 0
    bundle_version = None
    bundle_taxonomy_version = None


class _UnhealthyStore:
    def health(self):
        return False


class _HealthyStore:
    def health(self):
        return True

    def taxonomy_size(self):
        return 1


def test_readyz_endpoint_503_then_200(monkeypatch):
    m = _fresh_mapping_module(monkeypatch)
    from app import app

    client = app.test_client()

    # not ready yet
    r = client.get("/readyz")
    assert r.status_code == 503
    assert r.get_json()["ready"] is False

    # mark ready and re-probe
    monkeypatch.setattr(
        m, "_readiness", {"seed_ok": True, "last_error": None}, raising=False
    )
    monkeypatch.setattr(m, "get_store", lambda: _HealthyStore())
    r2 = client.get("/readyz")
    assert r2.status_code == 200
    assert r2.get_json()["ready"] is True


def test_readiness_fails_when_seeded_store_becomes_closed(monkeypatch):
    m = _fresh_mapping_module(monkeypatch)
    monkeypatch.setattr(m, "_seeded", True, raising=False)
    monkeypatch.setattr(
        m, "_readiness", {"seed_ok": True, "last_error": None}, raising=False
    )
    monkeypatch.setattr(m, "get_store", lambda: _UnhealthyStore())

    state = m.readiness()

    assert state["seed_ok"] is False
    assert "unhealthy" in (state["last_error"] or "")


def test_readiness_fails_when_seeded_taxonomy_becomes_empty(monkeypatch):
    m = _fresh_mapping_module(monkeypatch)
    monkeypatch.setattr(m, "_seeded", True, raising=False)
    monkeypatch.setattr(
        m, "_readiness", {"seed_ok": True, "last_error": None}, raising=False
    )
    monkeypatch.setattr(m, "get_store", lambda: _EmptyHealthyStore())

    assert m.readiness()["seed_ok"] is False


class _EmptyHealthyStore:
    def health(self):
        return True

    def taxonomy_size(self):
        return 0


def test_scipy_sqlite_storage_is_live_before_seed_and_ready_after_seed(tmp_path):
    from scudo_mapping_mcp.store.scipy_sqlite_store import ScipySQLiteStore
    from scudo_mapping_mcp.models import TaxonomyNode

    store = ScipySQLiteStore(tmp_path / "matching.sqlite3")
    try:
        assert store.storage_ready() is True
        assert store.health() is False
        store.replace_taxonomy([TaxonomyNode(iri="cdao:root", label="Root")])
        assert store.health() is True
    finally:
        store.close()


def test_scipy_sqlite_readiness_rejects_corrupt_database(tmp_path):
    from scudo_mapping_mcp.store.scipy_sqlite_store import ScipySQLiteStore

    database = tmp_path / "matching.sqlite3"
    database.write_bytes(b"not sqlite")

    with pytest.raises(Exception):
        ScipySQLiteStore(database)


def test_scipy_sqlite_readiness_rejects_unwritable_parent(tmp_path, monkeypatch):
    from scudo_mapping_mcp.store.scipy_sqlite_store import ScipySQLiteStore

    parent = tmp_path / "readonly"
    parent.mkdir()
    monkeypatch.setattr(
        Path,
        "mkdir",
        lambda self, *args, **kwargs: (_ for _ in ()).throw(
            PermissionError("unwritable")
        ),
    )

    with pytest.raises(PermissionError, match="unwritable"):
        ScipySQLiteStore(parent / "matching.sqlite3")
