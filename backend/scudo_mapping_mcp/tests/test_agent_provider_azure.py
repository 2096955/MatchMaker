"""Provider-selector routing gap (streaming /mapping/agent/run path).

Prior behaviour: ``get_agent(provider="azure")`` warned and returned
``BedrockMappingAgent`` (or silently returned ``ScriptedMappingAgent`` when
``SCUDO_AGENT_BACKEND`` was not "bedrock") — the UI's Azure choice never
actually reached Azure OpenAI. A second, symmetric gap: selecting "Amazon
Bedrock (Claude)" in the same dropdown sent ``agent_provider="bedrock"``,
but ``get_agent(provider="bedrock")`` ignored ``provider`` for any
non-"azure" value and dispatched purely off ``SCUDO_AGENT_BACKEND`` — with
that env var unset (the default), selecting Bedrock silently ran
``ScriptedMappingAgent`` too. These tests pin both fixes:

  1. ``get_agent(provider="azure")`` returns a real Azure-backed agent and
     NEVER constructs ``BedrockMappingAgent`` (proven by monkeypatching its
     ``__init__`` to raise if called).
  2. ``get_agent(provider="bedrock")`` returns a real Bedrock-backed agent
     and NEVER constructs ``ScriptedMappingAgent``, regardless of
     ``SCUDO_AGENT_BACKEND`` (same proof technique, mirrored).
  3. Missing Azure config fails CLEARLY (a specific ``RuntimeError``) rather
     than silently falling back to Bedrock/scripted.
  4. ``POST /api/mapping/agent/run`` passes ``agent_provider`` through to
     ``get_agent`` unchanged.

Run per-file (mapping suite has known store-cache cross-file pollution):

    PYTHONPATH=. python3 -m pytest \
        scudo_mapping_mcp/tests/test_agent_provider_azure.py -q
"""

from __future__ import annotations

import os

import pytest

from scudo_mapping_mcp.models import Candidate, TaxonomyNode, VendorProductRef


def _ref() -> VendorProductRef:
    os.environ.setdefault("STORE_BACKEND", "memory")
    os.environ.setdefault("FRAME_SOURCE", "mock")
    return VendorProductRef(vendor="LSEG", product_id="X-1", name="Some Product")


class _FakeStore:
    """Minimal store returning one fixed candidate — no DB/network."""

    def __init__(self) -> None:
        self._node = TaxonomyNode(iri="jpmorgan:data:cdao:concept:x", label="X Concept")

    def get_precedent_mapping(self, *a, **k):
        return None

    def find_similar_products(self, ref, max_results=8):
        return [Candidate(node=self._node, similarity=0.9)]

    def get_taxonomy_node(self, iri):
        return self._node


_AZURE_ENV = {
    "AZURE_OPENAI_ENDPOINT": "https://example-azure.openai.azure.com",
    "AZURE_OPENAI_API_KEY": "test-key",
    "AZURE_OPENAI_API_VERSION": "2024-12-01-preview",
    "AZURE_OPENAI_SPECIALIST_DEPLOYMENT": "gpt-5-specialist",
}


def _clear_azure_env(monkeypatch):
    for k in (
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_API_VERSION",
        "AZURE_OPENAI_SPECIALIST_DEPLOYMENT",
        "AZURE_OPENAI_VERIFIER_DEPLOYMENT",
        "AZURE_OPENAI_REASONING_EFFORT",
    ):
        monkeypatch.delenv(k, raising=False)


def test_get_agent_azure_never_constructs_bedrock(monkeypatch):
    """provider="azure" must return an azure-backed agent and must NEVER
    instantiate BedrockMappingAgent — even when SCUDO_AGENT_BACKEND=bedrock
    (the exact combination the old code warned-then-fell-back on)."""
    from scudo_mapping_mcp import agent as agent_mod

    monkeypatch.setenv("SCUDO_AGENT_BACKEND", "bedrock")

    def _boom(self, *a, **k):
        raise AssertionError(
            "BedrockMappingAgent must not be constructed for provider=azure"
        )

    monkeypatch.setattr(agent_mod.BedrockMappingAgent, "__init__", _boom)

    result = agent_mod.get_agent(provider="azure")
    assert result.NAME == "azure"


def test_get_agent_azure_case_and_whitespace_insensitive(monkeypatch):
    """Matches the route's own normalisation (``.strip().lower()``)."""
    from scudo_mapping_mcp import agent as agent_mod

    assert agent_mod.get_agent(provider="  Azure  ").NAME == "azure"


def test_get_agent_no_provider_still_follows_scudo_agent_backend(monkeypatch):
    """When the CALLER SPECIFIES NO PROVIDER AT ALL (None), dispatch is
    unchanged: SCUDO_AGENT_BACKEND decides scripted vs bedrock. This is the
    legacy/non-UI path, distinct from an explicit "bedrock"/"azure" selection
    (see test_get_agent_bedrock_never_constructs_scripted below)."""
    from scudo_mapping_mcp import agent as agent_mod

    monkeypatch.delenv("SCUDO_AGENT_BACKEND", raising=False)
    assert agent_mod.get_agent(provider=None).NAME == "scripted"

    monkeypatch.setenv("SCUDO_AGENT_BACKEND", "bedrock")
    assert agent_mod.get_agent(provider=None).NAME == "bedrock"


def test_get_agent_bedrock_never_constructs_scripted(monkeypatch):
    """provider="bedrock" must return a bedrock-backed agent and must NEVER
    instantiate ScriptedMappingAgent — even when SCUDO_AGENT_BACKEND is unset
    (the demo dropdown's "Amazon Bedrock (Claude)" option previously silently
    ran ScriptedMappingAgent in exactly this configuration)."""
    from scudo_mapping_mcp import agent as agent_mod

    monkeypatch.delenv("SCUDO_AGENT_BACKEND", raising=False)

    def _boom(self, *a, **k):
        raise AssertionError(
            "ScriptedMappingAgent must not be constructed for provider=bedrock"
        )

    monkeypatch.setattr(agent_mod.ScriptedMappingAgent, "__init__", _boom)

    result = agent_mod.get_agent(provider="bedrock")
    assert result.NAME == "bedrock"


def test_get_agent_bedrock_case_and_whitespace_insensitive(monkeypatch):
    """Matches the route's own normalisation (``.strip().lower()``), same as
    the azure branch."""
    from scudo_mapping_mcp import agent as agent_mod

    monkeypatch.delenv("SCUDO_AGENT_BACKEND", raising=False)
    assert agent_mod.get_agent(provider="  Bedrock  ").NAME == "bedrock"


def test_azure_agent_run_fails_clearly_when_config_missing(monkeypatch):
    """No silent Bedrock/scripted fallback — a specific, actionable error."""
    from scudo_mapping_mcp import agent as agent_mod

    _clear_azure_env(monkeypatch)
    monkeypatch.setattr(agent_mod, "get_store", lambda: _FakeStore())

    agent = agent_mod.get_agent(provider="azure")
    with pytest.raises(RuntimeError, match="Missing Azure OpenAI configuration"):
        list(agent.run(_ref()))


def test_azure_agent_run_uses_azure_client_and_yields_expected_events(monkeypatch):
    """With config present, run() calls the (mocked) Azure OpenAI client and
    yields the standard start -> tool_call -> tool_result -> agent_message ->
    final_result -> done sequence — the SAME AgentEvent contract every other
    backend uses."""
    from scudo_mapping_mcp import agent as agent_mod
    from scudo_mapping_mcp import matching as matching_mod

    for k, v in _AZURE_ENV.items():
        monkeypatch.setenv(k, v)

    store = _FakeStore()
    monkeypatch.setattr(agent_mod, "get_store", lambda: store)
    monkeypatch.setattr(matching_mod, "get_store", lambda: store)

    calls = []

    class _FakeMessage:
        content = "Recommend jpmorgan:data:cdao:concept:x (similarity 0.90)."

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeResponse:
        choices = [_FakeChoice()]

    class _FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return _FakeResponse()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeAzureClient:
        chat = _FakeChat()

    def _boom_bedrock(self, *a, **k):
        raise AssertionError("Bedrock must not be touched on the azure path")

    monkeypatch.setattr(agent_mod.BedrockMappingAgent, "__init__", _boom_bedrock)
    monkeypatch.setattr(
        agent_mod, "_build_azure_openai_client", lambda cfg: _FakeAzureClient()
    )

    agent = agent_mod.get_agent(provider="azure")
    events = list(agent.run(_ref()))
    types = [e.type for e in events]

    assert types == [
        "start",
        "tool_call",
        "tool_result",
        "agent_message",
        "final_result",
        "done",
    ], types
    assert events[0].payload["agent_backend"] == "azure"
    assert events[3].payload["content"] == _FakeMessage.content
    # The mocked Azure client was genuinely invoked with the specialist deployment.
    assert calls, "Azure OpenAI client was never called"
    assert calls[0]["model"] == "gpt-5-specialist"


def test_agent_run_route_passes_agent_provider_through_to_get_agent(monkeypatch):
    """POST /api/mapping/agent/run must forward body.agent_provider to
    get_agent(provider=...) unchanged — proves the route-level plumbing the
    UI's Vendor Select / Inference Runtime dropdowns depend on."""
    os.environ.setdefault("STORE_BACKEND", "memory")
    os.environ.setdefault("FRAME_SOURCE", "mock")
    os.environ.setdefault("SCUDO_AUTH_ALLOW_DEV", "1")
    os.environ.setdefault("SCUDO_AUTH_DEV_PRINCIPAL", "test@local")

    import routes.mapping as mapping_routes
    from app import app

    captured = {}

    class _StubAgent:
        NAME = "stub"

        def run(self, ref, **kw):
            yield from ()

    def _fake_get_agent(provider=None):
        captured["provider"] = provider
        return _StubAgent()

    monkeypatch.setattr(mapping_routes, "get_agent", _fake_get_agent)

    client = app.test_client()
    client.post(
        "/api/mapping/agent/run",
        json={
            "vendor": "LSEG",
            "product_id": "X-1",
            "name": "Some Product",
            "agent_provider": "azure",
        },
    )

    assert captured.get("provider") == "azure"


def test_describe_agent_azure_enabled_requires_full_config(monkeypatch):
    """GET /mapping/agent/describe must only advertise azure as enabled when
    ALL fields AzureMappingAgent._require_config needs are present — endpoint
    alone is not enough. Otherwise the UI offers a runtime that will always
    fail the moment it's actually run."""
    os.environ.setdefault("STORE_BACKEND", "memory")
    os.environ.setdefault("FRAME_SOURCE", "mock")
    os.environ.setdefault("SCUDO_AUTH_ALLOW_DEV", "1")
    os.environ.setdefault("SCUDO_AUTH_DEV_PRINCIPAL", "test@local")

    from app import app

    client = app.test_client()

    monkeypatch.setenv(
        "AZURE_OPENAI_ENDPOINT", "https://example-azure.openai.azure.com"
    )
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_SPECIALIST_DEPLOYMENT", raising=False)
    r = client.get("/api/mapping/agent/describe")
    azure = next(p for p in r.get_json()["providers"] if p["id"] == "azure")
    assert azure["enabled"] is False, "endpoint-only config must not read as enabled"

    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AZURE_OPENAI_SPECIALIST_DEPLOYMENT", "gpt-5-specialist")
    r = client.get("/api/mapping/agent/describe")
    azure = next(p for p in r.get_json()["providers"] if p["id"] == "azure")
    assert azure["enabled"] is True


def test_run_agent_rejects_unknown_provider(monkeypatch):
    """A typo'd/unknown agent_provider must 400, matching the Lambda path's
    existing validation (scudo/lambda_handler.py), not silently fall through
    to whatever SCUDO_AGENT_BACKEND happens to resolve to."""
    os.environ.setdefault("STORE_BACKEND", "memory")
    os.environ.setdefault("FRAME_SOURCE", "mock")
    os.environ.setdefault("SCUDO_AUTH_ALLOW_DEV", "1")
    os.environ.setdefault("SCUDO_AUTH_DEV_PRINCIPAL", "test@local")

    import routes.mapping as mapping_routes
    from app import app

    called = []
    monkeypatch.setattr(
        mapping_routes, "get_agent", lambda provider=None: called.append(provider)
    )

    client = app.test_client()
    r = client.post(
        "/api/mapping/agent/run",
        json={
            "vendor": "LSEG",
            "product_id": "X-1",
            "name": "Some Product",
            "agent_provider": "azur",
        },
    )

    assert r.status_code == 400
    assert "azur" in r.get_json().get("error", "")
    assert not called, "get_agent must not be reached for an unknown provider"
