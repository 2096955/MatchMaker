"""db.py must refuse a silent passwordless connect to a non-local host.

CONSOLE_DB_PASSWORD defaults to "" — fine for a local dev Postgres, but a
misconfigured deploy that forgets the secret would otherwise attempt an
unauthenticated connection to a real Aurora endpoint. Local hosts
(localhost/127.0.0.1/::1, the dev default) are exempt.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


def _reset_env(monkeypatch):
    for var in ("CONSOLE_DB_HOST", "CONSOLE_DB_PASSWORD"):
        monkeypatch.delenv(var, raising=False)


def test_non_local_host_without_password_raises(monkeypatch):
    import db

    _reset_env(monkeypatch)
    monkeypatch.setenv(
        "CONSOLE_DB_HOST", "prod-aurora.cluster-xyz.us-east-1.rds.amazonaws.com"
    )

    called = []
    monkeypatch.setattr(db.psycopg, "connect", lambda **kw: called.append(kw))

    with pytest.raises(RuntimeError, match="CONSOLE_DB_PASSWORD"):
        db.get_conn()
    assert called == []  # must fail before ever attempting the connection


def test_local_host_without_password_is_allowed(monkeypatch):
    import db

    _reset_env(monkeypatch)
    monkeypatch.setenv("CONSOLE_DB_HOST", "localhost")

    called = []
    monkeypatch.setattr(db.psycopg, "connect", lambda **kw: called.append(kw) or "conn")

    assert db.get_conn() == "conn"
    assert called and called[0]["host"] == "localhost"


def test_non_local_host_with_password_is_allowed(monkeypatch):
    import db

    _reset_env(monkeypatch)
    monkeypatch.setenv(
        "CONSOLE_DB_HOST", "prod-aurora.cluster-xyz.us-east-1.rds.amazonaws.com"
    )
    monkeypatch.setenv("CONSOLE_DB_PASSWORD", "s3cret")

    called = []
    monkeypatch.setattr(db.psycopg, "connect", lambda **kw: called.append(kw) or "conn")

    assert db.get_ingestion_conn() == "conn"
    assert called and called[0]["password"] == "s3cret"
