"""Zone-aware agent context — lets a mapping agent recognise catalogue/DCAT
concepts (the modelled "top half") vs rights/contract concepts (the new
"bottom half": Party/Contract/Policy/Duty/Permission) and adjust accordingly.

Delivery is asymmetric because the two live-LLM backends have different
shapes (verified this session): BedrockMappingAgent runs a real Strands
tool-calling loop, so it gets a new callable @tool. AzureMappingAgent does
one candidate lookup + one plain chat completion (no tool loop), so it gets
the same text pre-injected into its one-shot prompt instead. Same underlying
text (_system_context_text), two delivery mechanisms.
"""

from __future__ import annotations

import os

from scudo_mapping_mcp.models import Candidate, TaxonomyNode, VendorProductRef


def _ref() -> VendorProductRef:
    os.environ.setdefault("STORE_BACKEND", "memory")
    os.environ.setdefault("FRAME_SOURCE", "mock")
    return VendorProductRef(vendor="LSEG", product_id="X-1", name="Some Product")


class _FakeStore:
    def __init__(self) -> None:
        self._node = TaxonomyNode(iri="jpmorgan:data:cdao:concept:x", label="X Concept")

    def get_precedent_mapping(self, *a, **k):
        return None

    def find_similar_products(self, ref, max_results=8):
        return [Candidate(node=self._node, similarity=0.9)]

    def get_taxonomy_node(self, iri):
        return self._node


def test_system_context_text_mentions_zones_and_both_ontology_halves():
    from scudo_mapping_mcp.agent import _system_context_text

    text = _system_context_text()
    assert "zone" in text.lower()
    # top half — already modelled, catalogue/DCAT
    assert "catalogue" in text.lower() or "dcat" in text.lower()
    # bottom half — new rights/contract kinds from this session's Part B
    for term in (
        "party",
        "contract",
        "policy",
        "duty",
        "permission",
        "obligation",
        "document",
    ):
        assert term in text.lower(), f"{term!r} missing from system context text"


def test_system_context_text_lists_every_conceptual_node_kind_exhaustively():
    """Adversarial-verify caught a real drift: an earlier hand-typed version
    of this text silently dropped 4 of the 13 catalogue-half
    ConceptualNodeKind members (DELIVERY_PRODUCT, MARKETING_DATASET,
    BUSINESS_CONCEPT_ELEMENT, BUSINESS_DATA_ELEMENT) and renamed
    DISTRIBUTED_DATASET. A hand-maintained string can drift again the same
    way, so this asserts EVERY current ConceptualNodeKind member's value is
    present, derived straight from the enum — not a hardcoded name list that
    could itself go stale."""
    from scudo_mapping_mcp.agent import _system_context_text
    from scudo_mapping_mcp.models import ConceptualNodeKind

    text = _system_context_text().lower()
    missing = [
        kind.value
        for kind in ConceptualNodeKind
        if kind.value.replace("_", "") not in text.replace("_", "").replace(" ", "")
    ]
    assert not missing, (
        f"ConceptualNodeKind members missing from system context text: {missing}"
    )


def test_bedrock_tool_surface_includes_describe_system_context():
    from scudo_mapping_mcp.agent import _strands_tools_for_mapping, _system_context_text

    tools = _strands_tools_for_mapping()
    names = [t.tool_name for t in tools]
    assert "describe_system_context" in names

    the_tool = next(t for t in tools if t.tool_name == "describe_system_context")
    # Strands @tool-wrapped callables expose the original function; calling it
    # directly must return exactly the shared context text.
    result = the_tool._tool_func()
    assert result == _system_context_text()


def test_azure_prompt_includes_system_context_text(monkeypatch):
    from scudo_mapping_mcp import agent as agent_mod
    from scudo_mapping_mcp import matching as matching_mod

    for k, v in {
        "AZURE_OPENAI_ENDPOINT": "https://example-azure.openai.azure.com",
        "AZURE_OPENAI_API_KEY": "test-key",
        "AZURE_OPENAI_SPECIALIST_DEPLOYMENT": "gpt-5-specialist",
    }.items():
        monkeypatch.setenv(k, v)

    store = _FakeStore()
    monkeypatch.setattr(agent_mod, "get_store", lambda: store)
    monkeypatch.setattr(matching_mod, "get_store", lambda: store)
    monkeypatch.setattr(agent_mod, "_build_azure_openai_client", lambda cfg: object())

    captured = {}

    def _fake_recommend(self, client, cfg, prompt):
        captured["prompt"] = prompt
        return "Recommend jpmorgan:data:cdao:concept:x."

    monkeypatch.setattr(agent_mod.AzureMappingAgent, "_recommend", _fake_recommend)

    agent = agent_mod.AzureMappingAgent()
    list(agent.run(_ref()))

    assert captured["prompt"], "AzureMappingAgent never called _recommend"
    assert agent_mod._system_context_text() in captured["prompt"]
