"""Store-contract round-trip for fully-populated rights ConceptualNodes.

Parametrised over FakeStore (in-memory whole-model) and the FalkorDB
encode/decode helpers (Cypher property path) so nested contract_terms /
party_profile cannot silently drop on either backend.
"""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from scudo_mapping_mcp.models import (
    ConceptualNode,
    ConceptualNodeKind,
    ContentDeliveryModel,
    ContractTerms,
    PartyProfile,
    conceptual_node_from_fixture_raw,
)
from scudo_mapping_mcp.store.falkordb_store import (
    FalkorDBStore,
    conceptual_node_from_props,
    conceptual_node_props,
)
from scudo_mapping_mcp.tests.fake_store import FakeStore


def _fully_populated_rights_node() -> ConceptualNode:
    # Phase C catalogue attrs (sequence_number / notation / group_type) are
    # populated too — the shared ConceptualNode shape carries them regardless
    # of kind (no per-kind validator, same as vendor_field_name), so one
    # fully-populated node keeps round-trip coverage complete.
    return ConceptualNode(
        iri="mds.enrich:rights-roundtrip",
        kind=ConceptualNodeKind.POLICY,
        label="Display Policy",
        attaches_to_concept_iri="jpmorgan:data:cdao:EquityPrices",
        sequence_number=7,
        notation="EQ_PX_TBL",
        group_type="database",
        cdm=ContentDeliveryModel.DISPLAY_SERVICE,
        time_interval="P1Y",
        deadline="2027-01-01T00:00:00Z",
        party_scope="external",
        description="fully populated rights node for store contract",
        contract_terms=ContractTerms(
            status="active",
            legal_basis="licence",
            licensing_model="enterprise",
            renewal_type="auto",
            term="P1Y",
            initial_term="P1Y",
            initial_term_end="2026-12-31",
            renewal_term="P1Y",
            store_purpose="research",
            post_term_store_purpose="archive",
            internal_controls="segregation",
        ),
        party_profile=PartyProfile(
            perm_id="P-1",
            supply_chain_status="active",
            organization_type="vendor",
            issuer_on="LSEG",
            member_of="exchange-A",
            exchange="XLON",
        ),
    )


def _assert_field_for_field(original: ConceptualNode, restored: ConceptualNode) -> None:
    assert restored.model_dump() == original.model_dump()


def test_fake_store_rights_node_round_trip():
    store = FakeStore()
    node = _fully_populated_rights_node()
    store.upsert_conceptual_node(node)
    graph = store.get_conceptual_graph(node.attaches_to_concept_iri)
    assert len(graph.nodes) == 1
    _assert_field_for_field(node, graph.nodes[0])


def test_falkordb_props_codec_rights_node_round_trip():
    """FalkorDB path: encode → decode through the shared property helpers
    used by upsert_conceptual_node / get_conceptual_graph (no live container
    required; the live SET/RETURN lists call these helpers)."""
    node = _fully_populated_rights_node()
    props = conceptual_node_props(node)
    restored = conceptual_node_from_props(
        iri=props["iri"],
        kind=props["kind"],
        label=props["label"],
        attaches=props["concept"],
        vfn=props["vfn"],
        dtype=props["dtype"],
        pk=props["pk"],
        nullable=props["nullable"],
        dbnot=props["dbnot"],
        schnot=props["schnot"],
        sequence_number=props["sequence_number"],
        notation=props["notation"],
        group_type=props["group_type"],
        subtype=props["subtype"],
        cdm=props["cdm"],
        time_interval=props["time_interval"],
        deadline=props["deadline"],
        party_scope=props["party_scope"],
        description=props["description"],
        contract_terms=props["contract_terms"],
        party_profile=props["party_profile"],
        concept_iri=node.attaches_to_concept_iri,
    )
    _assert_field_for_field(node, restored)


def test_fixture_raw_helper_carries_phase_c_fields():
    """conceptual_node_from_fixture_raw is the shared ingest/fixture seeding
    path — a Phase C field it drops would vanish silently on BOTH seeding
    paths, so pin it here alongside the store codec."""
    raw = {
        "label": "Price Record",
        "database_notation": "EQ_PRICE_RECORD",
        "schema_notation": "equity.price_record",
        "notation": "PRICE_RECORD",
        "group_type": "database",
        "sequence_number": 3,
    }
    node = conceptual_node_from_fixture_raw(
        raw,
        iri="mds.enrich:fixture-phase-c",
        kind=ConceptualNodeKind.FIELD_GROUP,
        concept_iri="jpmorgan:data:cdao:EquityPrices",
    )
    assert node.notation == "PRICE_RECORD"
    assert node.group_type == "database"
    assert node.sequence_number == 3


def _minimal_field_kwargs() -> dict:
    return {
        "iri": "mds.enrich:seq-strict",
        "kind": ConceptualNodeKind.FIELD,
        "label": "Ticker",
        "attaches_to_concept_iri": "jpmorgan:data:cdao:EquityPrices",
    }


def test_sequence_number_accepts_int_and_none():
    assert (
        ConceptualNode(**_minimal_field_kwargs(), sequence_number=3).sequence_number
        == 3
    )
    assert (
        ConceptualNode(**_minimal_field_kwargs(), sequence_number=None).sequence_number
        is None
    )


def test_sequence_number_rejects_bool_on_model():
    """Pydantic's lax Optional[int] coerces True→1 — silent contract drift
    against enrichment's strict `_validated_fields`. The model must fail
    loudly instead (corrupt fixture data ≠ a valid ordering index)."""
    with pytest.raises(ValidationError):
        ConceptualNode(**_minimal_field_kwargs(), sequence_number=True)


def test_sequence_number_rejects_numeric_string_on_model():
    with pytest.raises(ValidationError):
        ConceptualNode(**_minimal_field_kwargs(), sequence_number="3")


def test_fixture_raw_bool_sequence_number_fails_loudly():
    """A conceptual_layer.json-style raw dict carrying `"sequence_number":
    true` is corrupt data: it must raise at ingest, not silently become 1."""
    raw = {"label": "Ticker", "sequence_number": True}
    with pytest.raises(ValidationError):
        conceptual_node_from_fixture_raw(
            raw,
            iri="mds.enrich:seq-bool",
            kind=ConceptualNodeKind.FIELD,
            concept_iri="jpmorgan:data:cdao:EquityPrices",
        )


def test_props_codec_bool_sequence_number_fails_loudly():
    """Same guard on the FalkorDB decode path: a bool stored where the
    ordering int belongs must raise on read, not silently coerce."""
    with pytest.raises(ValidationError):
        conceptual_node_from_props(
            iri="mds.enrich:seq-bool-props",
            kind=ConceptualNodeKind.FIELD.value,
            label="Ticker",
            attaches="jpmorgan:data:cdao:EquityPrices",
            sequence_number=True,
        )


def test_live_set_clause_covers_every_props_key():
    """The props→codec round-trip above cannot catch a field added to the
    helpers but forgotten in the HAND-ENUMERATED Cypher SET string inside
    upsert_conceptual_node — that field would silently drop in production
    while the codec test stays green. Pin the live clause to the helper."""
    source = inspect.getsource(FalkorDBStore.upsert_conceptual_node)
    props = conceptual_node_props(_fully_populated_rights_node())
    missing = [key for key in props if f"${key}" not in source]
    assert not missing, (
        f"conceptual_node_props keys absent from upsert_conceptual_node's "
        f"SET clause (silent drop on write): {missing}"
    )


def test_live_return_clause_covers_every_model_field():
    """Same guard for the read side: every ConceptualNode field must appear
    as an ``n.<field>`` column in get_conceptual_graph's RETURN string."""
    source = inspect.getsource(FalkorDBStore.get_conceptual_graph)
    missing = [
        field for field in ConceptualNode.model_fields if f"n.{field}" not in source
    ]
    assert not missing, (
        f"ConceptualNode fields absent from get_conceptual_graph's RETURN "
        f"clause (silent drop on read): {missing}"
    )


@pytest.mark.parametrize(
    "backend",
    ["fake_store", "falkordb_props"],
)
def test_parametrised_store_contract_round_trip(backend: str):
    node = _fully_populated_rights_node()
    if backend == "fake_store":
        store = FakeStore()
        store.upsert_conceptual_node(node)
        restored = store.get_conceptual_graph(node.attaches_to_concept_iri).nodes[0]
    else:
        props = conceptual_node_props(node)
        restored = conceptual_node_from_props(
            iri=props["iri"],
            kind=props["kind"],
            label=props["label"],
            attaches=props["concept"],
            vfn=props["vfn"],
            dtype=props["dtype"],
            pk=props["pk"],
            nullable=props["nullable"],
            dbnot=props["dbnot"],
            schnot=props["schnot"],
            sequence_number=props["sequence_number"],
            notation=props["notation"],
            group_type=props["group_type"],
            subtype=props["subtype"],
            cdm=props["cdm"],
            time_interval=props["time_interval"],
            deadline=props["deadline"],
            party_scope=props["party_scope"],
            description=props["description"],
            contract_terms=props["contract_terms"],
            party_profile=props["party_profile"],
            concept_iri=node.attaches_to_concept_iri,
        )
    _assert_field_for_field(node, restored)
