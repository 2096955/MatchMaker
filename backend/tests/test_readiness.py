"""Readiness honesty (Codex A8): a failed seed must NOT mark the app seeded,
must leave readiness False, and /readyz must 503 until seeding succeeds.
"""

from __future__ import annotations

import os
import sys

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

    m._ensure_seeded()
    assert m._seeded is True
    assert m.readiness()["seed_ok"] is True
    assert m.readiness()["last_error"] is None


class _DummyHydrate:
    skipped_no_bundle = True
    applied = 0
    skipped_unknown_node = 0
    skipped_out_of_scope = 0
    bundle_version = None
    bundle_taxonomy_version = None


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
    r2 = client.get("/readyz")
    assert r2.status_code == 200
    assert r2.get_json()["ready"] is True
