from __future__ import annotations

import os
import importlib.util
import asyncio
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def restore_environ():
    """Snapshot and restore the WHOLE environment.

    Several tests below `exec()` the top-of-file prefix of the repo-root
    `streamlit_app.py` to assert its store-selection logic. That prefix writes
    ~13 variables into the REAL os.environ, but the bespoke `finally` blocks
    restored only three of them — so `SCUDO_DENSE_BACKEND=opus` (and the three
    dev-write gates) leaked into every later test in the session.

    The visible casualty was
    `test_scipy_sqlite_store.py::test_candidate_filter_clamps_and_preserves_raw_similarity`:
    under `opus` the candidate list is truncated to _MAX_OPUS_NOMINEES=25
    BEFORE the filter drops one, so it asserted 25 and got 24. It passed alone
    and failed in the suite, which is the signature of exactly this bug.

    Restoring everything is the fix; a longer hand-written key list is not,
    because the next variable added to that prefix would leak again.
    """
    saved = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


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


def test_streamlit_apptest_match_review_precedent_chat_and_accumulation(tmp_path):
    """Exercise the local steward journey in a process with a clean env/cache."""
    database = tmp_path / "matching-e2e.sqlite3"
    script = f"""
import json
import os
import socket
import sys

def blocked(*_args, **_kwargs):
    raise AssertionError("network access forbidden")

def state_has(app, key):
    try:
        app.session_state[key]
    except KeyError:
        return False
    return True

def selectbox(app, label):
    return next(widget for widget in app.selectbox if widget.label == label)

def button(app, label):
    return next(widget for widget in app.button if widget.label == label)

socket.socket.connect = blocked
socket.create_connection = blocked
sys.path.insert(0, {str(BACKEND)!r})

os.environ["STORE_BACKEND"] = "scipy_sqlite"
os.environ["SCUDO_PERSIST_TARGET"] = "scipy_sqlite"
os.environ["SCUDO_SCIPY_SQLITE_PATH"] = {str(database)!r}
os.environ["SCUDO_TAXONOMY_SEED"] = {str(BACKEND / "scudo/fixtures/cdao_catalogue.json")!r}
os.environ["FRAME_SOURCE"] = "mock"
os.environ["SCUDO_AGENT_BACKEND"] = "scripted"
os.environ["SCUDO_DENSE_BACKEND"] = "jaro_winkler"
os.environ["SCUDO_SPECIALIST_BACKEND"] = "local"
os.environ["SCUDO_AUTH_ALLOW_DEV"] = "1"
os.environ["SCUDO_AUTH_ALLOW_DEV_WRITES"] = "1"
os.environ["SCUDO_AUTH_DEV_PRINCIPAL"] = "apptest@local"
os.environ["SCUDO_VERDICT_ALLOW_DEV"] = "1"
os.environ["SCUDO_PERSIST_ALLOW_DEV_WRITES"] = "1"
os.environ["SCUDO_PERSIST_WRITE_TOKEN"] = "apptest-write-token"

from streamlit.testing.v1 import AppTest
from scudo_mapping_mcp.store.scipy_sqlite_store import ScipySQLiteStore

app = AppTest.from_file({str(REPO_ROOT / "streamlit_app.py")!r})
app.run(timeout=30)
assert not app.exception, app.exception
assert app.session_state["products"] == []
assert not state_has(app, "last_decision")
assert not [widget for widget in app.selectbox if widget.label == "Contract"]

app.button(key="demo_LSEG").click().run(timeout=30)
assert not app.exception, app.exception
products = app.session_state["products"]
assert len(products) == 3
assert {{product["vendor"] for product in products}} == {{"LSEG"}}
contract_picker = selectbox(app, "Contract")
assert len(contract_picker.options) == 3
assert all(label.startswith("LSEG · Q-CONTRACT-") for label in contract_picker.options)

button(app, "Run match").click().run(timeout=30)
assert not app.exception, app.exception
assert state_has(app, "last_decision")
decision = app.session_state["last_decision"]
assert decision["ref"].vendor == "LSEG"
assert decision["ref"].product_id == "Q-CONTRACT-X"
assert decision["iri"]
assert decision["confidence"] > 0
assert button(app, "Approve")
assert button(app, "Reject")

approved_iri = decision["iri"]
approved_confidence = decision["confidence"]
button(app, "Approve").click().run(timeout=30)
assert not app.exception, app.exception
assert any(
    "Approved — the next match of this product will reuse it." in message.value
    for message in app.success
)
assert not state_has(app, "last_decision")

store = ScipySQLiteStore({str(database)!r})
precedent = store.get_precedent_mapping("LSEG", "Q-CONTRACT-X")
assert precedent is not None
assert precedent.mapped_node_iri == approved_iri
assert precedent.confidence == approved_confidence
assert precedent.rationale == "precedent"

button(app, "Run match").click().run(timeout=30)
assert not app.exception, app.exception
reused = store.get_precedent_mapping("LSEG", "Q-CONTRACT-X")
assert reused is not None
assert reused.status.value == "approved"
assert reused.rationale == "precedent"
rendered_json = [
    json.loads(element.value) if isinstance(element.value, str) else element.value
    for element in app.json
]
assert any(
    payload.get("status") == "approved" and payload.get("rationale") == "precedent"
    for payload in rendered_json
    if isinstance(payload, dict)
), rendered_json

app.chat_input[0].set_value("How does scoring work?").run(timeout=30)
assert not app.exception, app.exception
chat = app.session_state["chat"]
assert [turn["role"] for turn in chat[-2:]] == ["user", "assistant"]
assert chat[-2]["content"] == "How does scoring work?"
assert "deterministic" in chat[-1]["content"].lower()
assert "describe_system_context" in chat[-1]["tools"]
assert any(
    "How does scoring work?" in element.value for element in app.markdown
)
assert any(
    "describe_system_context" in element.value for element in app.markdown
)

app.button(key="demo_Bloomberg").click().run(timeout=30)
assert not app.exception, app.exception
products = app.session_state["products"]
assert len(products) == 5
assert {{product["vendor"] for product in products}} == {{"LSEG", "Bloomberg"}}
contract_picker = selectbox(app, "Contract")
assert len(contract_picker.options) == 5
assert any(label.startswith("LSEG · Q-CONTRACT-X") for label in contract_picker.options)
assert any(
    label.startswith("Bloomberg · P-CONTRACT-Y")
    for label in contract_picker.options
)
assert any(
    "2 vendors loaded: Bloomberg, LSEG" in element.value
    for element in app.caption
)
store.close()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _run_streamlit_bedrock_preflight(
    tmp_path,
    *,
    stream_error: str = "",
    invoke_error: str = "",
    invoke_read_error: str = "",
    malformed_invoke_output: bool = False,
    change_model_after_test: bool = False,
) -> subprocess.CompletedProcess:
    database = tmp_path / "matching-bedrock-preflight.sqlite3"
    token = "bedrock-api-key-apptest-secret"
    selected_label = "Claude Sonnet 4.5"
    selected_model = "eu.anthropic.claude-sonnet-4-5-20250929-v1:0"
    script = f"""
import os
import json
import socket
import sys
import types

def blocked(*_args, **_kwargs):
    raise AssertionError("network access forbidden")

def selectbox(app, label):
    return next(widget for widget in app.selectbox if widget.label == label)

def text_input(app, label):
    return next(widget for widget in app.text_input if widget.label == label)

def button(app, label):
    return next(widget for widget in app.button if widget.label == label)

class AccessDeniedException(Exception):
    pass

class ValidationException(Exception):
    pass

class FakeStream:
    def __init__(self):
        self.consumed = 0

    def __iter__(self):
        self.consumed += 1
        yield {{"messageStart": {{"role": "assistant"}}}}
        self.consumed += 1
        if {stream_error!r}:
            error_type = globals()[{stream_error!r}]
            raise error_type({stream_error!r} + ": stream rejected")
        yield {{"contentBlockDelta": {{"delta": {{"text": "ok"}}}}}}
        self.consumed += 1
        yield {{"messageStop": {{"stopReason": "end_turn"}}}}

class FakeBody:
    def __init__(self):
        self.reads = 0

    def read(self):
        self.reads += 1
        if {invoke_read_error!r}:
            error_type = globals()[{invoke_read_error!r}]
            raise error_type({invoke_read_error!r} + ": body read rejected")
        if {malformed_invoke_output!r}:
            return b"not-json-and-not-generated-text"
        return b'{{"content":[{{"type":"text","text":"ok"}}]}}'

class FakeBedrockRuntime:
    def __init__(self):
        self.calls = []
        self.stream = FakeStream()
        self.invoke_calls = []
        self.body = FakeBody()

    def converse(self, **_kwargs):
        raise AssertionError("preflight must use converse_stream")

    def converse_stream(self, **kwargs):
        self.calls.append(kwargs)
        return {{"stream": self.stream}}

    def invoke_model(self, **kwargs):
        self.invoke_calls.append(kwargs)
        if {invoke_error!r}:
            error_type = globals()[{invoke_error!r}]
            raise error_type({invoke_error!r} + ": invoke rejected")
        return {{"body": self.body, "contentType": "malformed/is-irrelevant"}}

fake_runtime = FakeBedrockRuntime()
client_calls = []

def fake_client(service_name, *, region_name=None, **kwargs):
    client_calls.append((service_name, region_name, kwargs))
    assert os.environ["AWS_BEARER_TOKEN_BEDROCK"] == {token!r}
    return fake_runtime

socket.socket.connect = blocked
socket.create_connection = blocked
sys.modules["boto3"] = types.SimpleNamespace(client=fake_client)
sys.path.insert(0, {str(BACKEND)!r})

os.environ["STORE_BACKEND"] = "scipy_sqlite"
os.environ["SCUDO_PERSIST_TARGET"] = "scipy_sqlite"
os.environ["SCUDO_SCIPY_SQLITE_PATH"] = {str(database)!r}
os.environ["SCUDO_TAXONOMY_SEED"] = {str(BACKEND / "scudo/fixtures/cdao_catalogue.json")!r}
os.environ["FRAME_SOURCE"] = "mock"
os.environ["SCUDO_AGENT_BACKEND"] = "scripted"
os.environ["SCUDO_DENSE_BACKEND"] = "jaro_winkler"
os.environ["SCUDO_SPECIALIST_BACKEND"] = "local"
os.environ.pop("AWS_REGION", None)
os.environ.pop("AWS_DEFAULT_REGION", None)
os.environ.pop("AWS_BEARER_TOKEN_BEDROCK", None)
os.environ.pop("SCUDO_BEDROCK_MODEL_ID", None)

from streamlit.testing.v1 import AppTest

app = AppTest.from_file({str(REPO_ROOT / "streamlit_app.py")!r})
app.run(timeout=30)
assert not app.exception, app.exception

selectbox(app, "Agent").set_value("bedrock").run(timeout=30)
assert not app.exception, app.exception
# AppTest can finish the selectbox rerun before the form's element tree is
# observable when this follows another AppTest subprocess in the same pytest
# run. One plain rerun settles the tree without changing the selected value.
if not [widget for widget in app.text_input if widget.label == "API key"]:
    app.run(timeout=30)
assert selectbox(app, "Agent").value == "bedrock"
text_input(app, "API key").set_value({token!r})
selectbox(app, "Model").set_value({selected_label!r})
button(app, "Apply & test").click().run(timeout=30)
assert not app.exception, app.exception

assert os.environ["AWS_BEARER_TOKEN_BEDROCK"] == {token!r}
assert os.environ["SCUDO_BEDROCK_MODEL_ID"] == {selected_model!r}
assert client_calls == [("bedrock-runtime", "eu-west-2", {{}})]
assert len(fake_runtime.calls) == 1
call = fake_runtime.calls[0]
assert call["modelId"] == {selected_model!r}
assert call["inferenceConfig"] == {{"maxTokens": 8}}
assert len(fake_runtime.invoke_calls) == 1
invoke_call = fake_runtime.invoke_calls[0]
assert invoke_call["modelId"] == {selected_model!r}
assert invoke_call["contentType"] == "application/json"
assert invoke_call["accept"] == "application/json"
assert json.loads(invoke_call["body"]) == {{
    "anthropic_version": "bedrock-2023-05-31",
    "max_tokens": 1,
    "messages": [
        {{"role": "user", "content": [{{"type": "text", "text": "ok"}}]}}
    ],
}}
assert fake_runtime.body.reads == {0 if invoke_error else 1}

if {change_model_after_test!r}:
    selectbox(app, "Model").set_value("Claude Haiku 4.5").run(timeout=30)
    assert os.environ["SCUDO_BEDROCK_MODEL_ID"] != {selected_model!r}

rendered = []
for collection_name in ("markdown", "error", "success", "warning", "info", "caption"):
    rendered.extend(
        str(element.value)
        for element in getattr(app, collection_name)
    )
assert all({token!r} not in value for value in rendered), rendered

if {change_model_after_test!r}:
    expected = "Not tested — model or region changed. Press Apply & test again."
    assert any(element.value == expected for element in app.info), app.info
    assert not app.success
    assert not app.warning
    assert not app.error
elif {stream_error!r} and {invoke_error!r}:
    assert fake_runtime.stream.consumed == 2
    assert not app.success
    assert not app.warning
    assert any(
        "InvokeModelWithResponseStream failed: AccessDeniedException." in element.value
        and "bedrock:InvokeModelWithResponseStream" in element.value
        and "InvokeModel failed: AccessDeniedException." in element.value
        and "bedrock:InvokeModel" in element.value
        for element in app.error
    ), app.error
elif {stream_error!r}:
    assert fake_runtime.stream.consumed == 2
    assert not any("Ready" in element.value for element in app.success)
    assert not app.error
    assert any(
        "Degraded" in element.value
        and "InvokeModelWithResponseStream failed: AccessDeniedException." in element.value
        and "bedrock:InvokeModelWithResponseStream" in element.value
        and "scripted fallback" in element.value
        and "InvokeModel passed" in element.value
        for element in app.warning
    ), app.warning
elif {invoke_error!r} or {invoke_read_error!r}:
    assert fake_runtime.stream.consumed == 3
    assert not app.success
    assert not app.error
    assert any(
        "Degraded" in element.value
        and "InvokeModel failed: AccessDeniedException." in element.value
        and "bedrock:InvokeModel" in element.value
        and "safe fallback" in element.value
        and "Agent stream is ready" in element.value
        for element in app.warning
    ), app.warning
else:
    assert fake_runtime.stream.consumed == 3
    assert not app.error
    assert not app.warning
    assert any(
        element.value
        == (
            "Ready — Claude Sonnet 4.5: InvokeModelWithResponseStream and "
            "InvokeModel passed (agent stream + dense/local specialist)."
        )
        for element in app.success
    ), app.success
"""
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_streamlit_bearer_apply_and_test_drains_converse_stream(tmp_path):
    result = _run_streamlit_bedrock_preflight(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "bedrock-api-key-apptest-secret" not in result.stdout
    assert "bedrock-api-key-apptest-secret" not in result.stderr


def test_streamlit_bearer_apply_and_test_rejects_late_stream_error(tmp_path):
    result = _run_streamlit_bedrock_preflight(
        tmp_path,
        stream_error="AccessDeniedException",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "bedrock-api-key-apptest-secret" not in result.stdout
    assert "bedrock-api-key-apptest-secret" not in result.stderr


def test_streamlit_bedrock_preflight_degrades_when_invoke_is_denied(tmp_path):
    result = _run_streamlit_bedrock_preflight(
        tmp_path,
        invoke_error="AccessDeniedException",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_streamlit_bedrock_preflight_errors_when_both_operations_are_denied(
    tmp_path,
):
    result = _run_streamlit_bedrock_preflight(
        tmp_path,
        stream_error="AccessDeniedException",
        invoke_error="AccessDeniedException",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_streamlit_bedrock_preflight_does_not_parse_invoke_output(tmp_path):
    result = _run_streamlit_bedrock_preflight(
        tmp_path,
        malformed_invoke_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_streamlit_bedrock_preflight_degrades_when_invoke_body_read_fails(tmp_path):
    result = _run_streamlit_bedrock_preflight(
        tmp_path,
        invoke_read_error="AccessDeniedException",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_streamlit_bedrock_preflight_suppresses_stale_model_result(tmp_path):
    result = _run_streamlit_bedrock_preflight(
        tmp_path,
        change_model_after_test=True,
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


def test_streamlit_store_selection_prefers_scipy_sqlite_without_importing_config(
    restore_environ,
):
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
def test_streamlit_preserves_explicit_backend(restore_environ, backend):
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
def test_streamlit_derives_missing_target_from_backend(restore_environ, backend):
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


def test_streamlit_treats_blank_target_as_unset(restore_environ):
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
