"""The /demo/* static route for dashboard-dist is OFF by default and only
registers when SCUDO_SERVE_DASHBOARD_DIST is set - must never change behavior
for the deployed app, which serves the SPA via CloudFront, not Flask."""

from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_demo_route_absent_by_default(monkeypatch):
    monkeypatch.delenv("SCUDO_SERVE_DASHBOARD_DIST", raising=False)
    import app as app_module

    importlib.reload(app_module)
    client = app_module.app.test_client()
    r = client.get("/demo/")
    assert r.status_code == 404


def test_demo_route_serves_dashboard_dist_when_enabled(monkeypatch):
    monkeypatch.setenv("SCUDO_SERVE_DASHBOARD_DIST", "1")
    import app as app_module

    importlib.reload(app_module)
    client = app_module.app.test_client()
    r = client.get("/demo/index.html")
    assert r.status_code == 200
    assert b"<html" in r.data.lower() or b"<!doctype" in r.data.lower()

    # Teardown: reload again with the env var unset so later test modules
    # (imported once per process by pytest) see the default, off state.
    monkeypatch.delenv("SCUDO_SERVE_DASHBOARD_DIST", raising=False)
    importlib.reload(app_module)
