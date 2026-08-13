from __future__ import annotations

import os
import importlib.util
import asyncio
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND = REPO_ROOT / "backend"


def test_settings_accepts_scipy_sqlite_and_path(monkeypatch, tmp_path):
    from scudo_mapping_mcp.config import Settings

    database = tmp_path / "matching.sqlite3"
    monkeypatch.setenv("STORE_BACKEND", "scipy_sqlite")
    monkeypatch.setenv("SCUDO_PERSIST_TARGET", "scipy_sqlite")
    monkeypatch.setenv("SCUDO_SCIPY_SQLITE_PATH", str(database))

    selected = Settings.from_env()

    assert selected.store_backend == "scipy_sqlite"
    assert selected.persist_target == "scipy_sqlite"
    assert selected.scipy_sqlite_path == str(database)


def test_streamlit_fresh_database_bootstraps_in_subprocess(tmp_path):
    database = tmp_path / "matching.sqlite3"
    script = f"""
import os
import socket

def blocked(*_args, **_kwargs):
    raise AssertionError("network access forbidden")

socket.socket.connect = blocked
socket.create_connection = blocked
os.environ["STORE_BACKEND"] = "scipy_sqlite"
os.environ["SCUDO_PERSIST_TARGET"] = "scipy_sqlite"
os.environ["SCUDO_SCIPY_SQLITE_PATH"] = {str(database)!r}
os.environ["SCUDO_TAXONOMY_SEED"] = {str(BACKEND / "scudo/fixtures/cdao_catalogue.json")!r}
os.environ["FRAME_SOURCE"] = "mock"

from streamlit.testing.v1 import AppTest
app = AppTest.from_file({str(REPO_ROOT / "streamlit_app.py")!r})
app.run(timeout=30)
assert not app.exception, app.exception

from scudo_mapping_mcp.store.scipy_sqlite_store import ScipySQLiteStore
store = ScipySQLiteStore({str(database)!r})
assert store.storage_ready()
assert store.health()
assert store.taxonomy_size() > 0
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("backend", ["scipy_sqlite", "memory"])
@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_settings_blank_persist_target_derives_backend(monkeypatch, backend, blank):
    from scudo_mapping_mcp.config import Settings

    monkeypatch.setenv("STORE_BACKEND", backend)
    monkeypatch.setenv("SCUDO_PERSIST_TARGET", blank)

    assert Settings.from_env().persist_target == backend


def test_settings_preserves_invalid_nonblank_target_as_validation_error(monkeypatch):
    from scudo_mapping_mcp.config import Settings

    monkeypatch.setenv("STORE_BACKEND", "scipy_sqlite")
    monkeypatch.setenv("SCUDO_PERSIST_TARGET", " invalid-target ")

    with pytest.raises(ValueError, match="invalid-target"):
        Settings.from_env()


def test_settings_uses_repository_local_matching_path(monkeypatch):
    from scudo_mapping_mcp.config import Settings

    monkeypatch.delenv("SCUDO_SCIPY_SQLITE_PATH", raising=False)

    assert Settings.from_env().scipy_sqlite_path == str(
        BACKEND / ".local" / "scudo_matching.sqlite3"
    )


def test_factory_selects_scipy_sqlite_lazily(monkeypatch, tmp_path):
    import scudo_mapping_mcp.config as config_module
    import scudo_mapping_mcp.store.factory as factory
    from scudo_mapping_mcp.config import Settings
    from scudo_mapping_mcp.store.scipy_sqlite_store import ScipySQLiteStore

    database = tmp_path / "matching.sqlite3"
    monkeypatch.setenv("STORE_BACKEND", "scipy_sqlite")
    monkeypatch.setenv("SCUDO_SCIPY_SQLITE_PATH", str(database))
    monkeypatch.setattr(config_module, "settings", Settings.from_env())
    monkeypatch.setattr(factory, "settings", config_module.settings)
    factory.reset_store_cache()
    try:
        store = factory.get_store()
        assert isinstance(store, ScipySQLiteStore)
        assert store._path == database
    finally:
        store.close()
        factory.reset_store_cache()


def test_close_store_closes_cached_instance_and_allows_reentry(monkeypatch):
    import scudo_mapping_mcp.store.factory as factory

    stores = []

    class Store:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    def build():
        store = Store()
        stores.append(store)
        return store

    monkeypatch.setattr(factory, "_build_store", build)
    factory.close_store()
    first = factory.get_store()
    factory.close_store()
    second = factory.get_store()
    factory.close_store()

    assert first is not second
    assert [store.closed for store in stores] == [True, True]


@pytest.mark.parametrize(
    "module_name",
    ["scudo_mapping_mcp.mcp_server", "scudo_mapping_mcp.match_verify_mcp"],
)
def test_mcp_lifespan_can_enter_and_exit_twice(monkeypatch, module_name):
    module = importlib.import_module(module_name)
    closes = []
    monkeypatch.setattr(module, "seed_taxonomy", lambda: 1)
    monkeypatch.setattr(module, "hydrate", lambda strict=False: _Hydrated())
    monkeypatch.setattr(module, "close_store", lambda: closes.append(True))

    async def exercise():
        async with module._lifespan(object()):
            pass
        async with module._lifespan(object()):
            pass

    asyncio.run(exercise())
    assert closes == [True, True]


class _Hydrated:
    skipped_no_bundle = True


def test_start_local_prefers_separate_matching_database():
    spec = importlib.util.spec_from_file_location(
        "start_local_under_test", REPO_ROOT / "start_local.py"
    )
    assert spec and spec.loader
    start_local = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(start_local)

    assert start_local.LOCAL_ENV["STORE_BACKEND"] == "scipy_sqlite"
    env = {}
    start_local._apply_local_defaults(env)
    assert env["SCUDO_PERSIST_TARGET"] == "scipy_sqlite"
    assert Path(start_local.LOCAL_ENV["SCUDO_SCIPY_SQLITE_PATH"]).name == (
        "scudo_matching.sqlite3"
    )
    assert Path(start_local.LOCAL_ENV["SCUDO_SCIPY_SQLITE_PATH"]).name != (
        "console.sqlite3"
    )


@pytest.mark.parametrize("backend", ["memory", "falkordb"])
def test_start_local_derives_missing_target_from_effective_backend(backend):
    spec = importlib.util.spec_from_file_location(
        "start_local_under_test", REPO_ROOT / "start_local.py"
    )
    assert spec and spec.loader
    start_local = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(start_local)
    env = {"STORE_BACKEND": backend}

    start_local._apply_local_defaults(env)

    assert env["STORE_BACKEND"] == backend
    assert env["SCUDO_PERSIST_TARGET"] == backend


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_start_local_treats_blank_target_as_unset(blank):
    spec = importlib.util.spec_from_file_location(
        "start_local_under_test", REPO_ROOT / "start_local.py"
    )
    assert spec and spec.loader
    start_local = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(start_local)
    env = {"STORE_BACKEND": "memory", "SCUDO_PERSIST_TARGET": blank}

    start_local._apply_local_defaults(env)

    assert env["SCUDO_PERSIST_TARGET"] == "memory"


def test_start_local_preserves_explicit_target():
    spec = importlib.util.spec_from_file_location(
        "start_local_under_test", REPO_ROOT / "start_local.py"
    )
    assert spec and spec.loader
    start_local = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(start_local)
    env = {"STORE_BACKEND": "memory", "SCUDO_PERSIST_TARGET": "none"}

    start_local._apply_local_defaults(env)

    assert env["SCUDO_PERSIST_TARGET"] == "none"


def test_backend_run_local_preserves_explicit_store_environment(tmp_path):
    fake_app = tmp_path / "app.py"
    fake_app.write_text("app = object()\n", encoding="utf-8")
    script = """
import os, runpy, sys
sys.path.insert(0, {fake_path!r})
os.environ["STORE_BACKEND"] = "memory"
os.environ["SCUDO_PERSIST_TARGET"] = "none"
runpy.run_path({launcher!r}, run_name="scudo_run_local_test")
assert os.environ["STORE_BACKEND"] == "memory"
assert os.environ["SCUDO_PERSIST_TARGET"] == "none"
""".format(fake_path=str(tmp_path), launcher=str(BACKEND / "run_local.py"))
    subprocess.run([sys.executable, "-c", script], check=True)


@pytest.mark.parametrize("backend", ["memory", "falkordb"])
def test_backend_run_local_derives_missing_target_from_backend(tmp_path, backend):
    fake_app = tmp_path / "app.py"
    fake_app.write_text("app = object()\n", encoding="utf-8")
    script = """
import os, runpy, sys
sys.path.insert(0, {fake_path!r})
os.environ["STORE_BACKEND"] = {backend!r}
os.environ.pop("SCUDO_PERSIST_TARGET", None)
runpy.run_path({launcher!r}, run_name="scudo_run_local_test")
assert os.environ["SCUDO_PERSIST_TARGET"] == {backend!r}
""".format(
        fake_path=str(tmp_path),
        backend=backend,
        launcher=str(BACKEND / "run_local.py"),
    )
    subprocess.run([sys.executable, "-c", script], check=True)


def test_backend_run_local_treats_blank_target_as_unset(tmp_path):
    fake_app = tmp_path / "app.py"
    fake_app.write_text("app = object()\n", encoding="utf-8")
    script = """
import os, runpy, sys
sys.path.insert(0, {fake_path!r})
os.environ["STORE_BACKEND"] = "memory"
os.environ["SCUDO_PERSIST_TARGET"] = "  "
runpy.run_path({launcher!r}, run_name="scudo_run_local_test")
assert os.environ["SCUDO_PERSIST_TARGET"] == "memory"
""".format(fake_path=str(tmp_path), launcher=str(BACKEND / "run_local.py"))
    subprocess.run([sys.executable, "-c", script], check=True)


def test_streamlit_store_selection_prefers_scipy_sqlite_without_importing_config():
    source = (REPO_ROOT / "streamlit_app.py").read_text(encoding="utf-8")
    prefix = source.split("import streamlit as st", 1)[0]
    namespace = {"__file__": str(REPO_ROOT / "streamlit_app.py")}

    old_backend = os.environ.pop("STORE_BACKEND", None)
    old_target = os.environ.pop("SCUDO_PERSIST_TARGET", None)
    old_path = os.environ.pop("SCUDO_SCIPY_SQLITE_PATH", None)
    sys.modules.pop("scudo_mapping_mcp.config", None)
    try:
        exec(prefix, namespace)
        assert os.environ["STORE_BACKEND"] == "scipy_sqlite"
        assert os.environ["SCUDO_PERSIST_TARGET"] == "scipy_sqlite"
        assert "scudo_mapping_mcp.config" not in sys.modules
        assert Path(os.environ["SCUDO_SCIPY_SQLITE_PATH"]).name == (
            "scudo_matching.sqlite3"
        )
    finally:
        for key, value in (
            ("STORE_BACKEND", old_backend),
            ("SCUDO_PERSIST_TARGET", old_target),
            ("SCUDO_SCIPY_SQLITE_PATH", old_path),
        ):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.mark.parametrize("backend", ["memory", "local_file", "unknown"])
def test_streamlit_preserves_explicit_backend(backend):
    source = (REPO_ROOT / "streamlit_app.py").read_text(encoding="utf-8")
    prefix = source.split("import streamlit as st", 1)[0]
    namespace = {"__file__": str(REPO_ROOT / "streamlit_app.py")}
    old_backend = os.environ.get("STORE_BACKEND")
    old_target = os.environ.get("SCUDO_PERSIST_TARGET")
    try:
        os.environ["STORE_BACKEND"] = backend
        os.environ["SCUDO_PERSIST_TARGET"] = backend
        exec(prefix, namespace)
        assert os.environ["STORE_BACKEND"] == backend
        assert os.environ["SCUDO_PERSIST_TARGET"] == backend
    finally:
        if old_backend is None:
            os.environ.pop("STORE_BACKEND", None)
        else:
            os.environ["STORE_BACKEND"] = old_backend
        if old_target is None:
            os.environ.pop("SCUDO_PERSIST_TARGET", None)
        else:
            os.environ["SCUDO_PERSIST_TARGET"] = old_target


@pytest.mark.parametrize("backend", ["memory", "falkordb"])
def test_streamlit_derives_missing_target_from_backend(backend):
    source = (REPO_ROOT / "streamlit_app.py").read_text(encoding="utf-8")
    prefix = source.split("import streamlit as st", 1)[0]
    namespace = {"__file__": str(REPO_ROOT / "streamlit_app.py")}
    old_backend = os.environ.get("STORE_BACKEND")
    old_target = os.environ.pop("SCUDO_PERSIST_TARGET", None)
    try:
        os.environ["STORE_BACKEND"] = backend
        exec(prefix, namespace)
        assert os.environ["SCUDO_PERSIST_TARGET"] == backend
    finally:
        if old_backend is None:
            os.environ.pop("STORE_BACKEND", None)
        else:
            os.environ["STORE_BACKEND"] = old_backend
        if old_target is None:
            os.environ.pop("SCUDO_PERSIST_TARGET", None)
        else:
            os.environ["SCUDO_PERSIST_TARGET"] = old_target


def test_streamlit_treats_blank_target_as_unset():
    source = (REPO_ROOT / "streamlit_app.py").read_text(encoding="utf-8")
    prefix = source.split("import streamlit as st", 1)[0]
    namespace = {"__file__": str(REPO_ROOT / "streamlit_app.py")}
    old_backend = os.environ.get("STORE_BACKEND")
    old_target = os.environ.get("SCUDO_PERSIST_TARGET")
    try:
        os.environ["STORE_BACKEND"] = "falkordb"
        os.environ["SCUDO_PERSIST_TARGET"] = " "
        exec(prefix, namespace)
        assert os.environ["SCUDO_PERSIST_TARGET"] == "falkordb"
    finally:
        if old_backend is None:
            os.environ.pop("STORE_BACKEND", None)
        else:
            os.environ["STORE_BACKEND"] = old_backend
        if old_target is None:
            os.environ.pop("SCUDO_PERSIST_TARGET", None)
        else:
            os.environ["SCUDO_PERSIST_TARGET"] = old_target


def test_whitespace_sqlite_path_uses_absolute_default(monkeypatch):
    from scudo_mapping_mcp.config import Settings

    monkeypatch.setenv("SCUDO_SCIPY_SQLITE_PATH", "   ")
    selected = Settings.from_env()
    assert Path(selected.scipy_sqlite_path).is_absolute()
    assert selected.scipy_sqlite_path.endswith("backend/.local/scudo_matching.sqlite3")
