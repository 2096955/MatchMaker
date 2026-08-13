from __future__ import annotations

import importlib
import io
import json
import socket
import stat
import sys
import warnings
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
ILLUSTRATIVE_TAXONOMY = REPO_ROOT / "backend/scudo/fixtures/cdao_catalogue.json"
CONSOLE_SQLITE = REPO_ROOT / "backend/.local/console.sqlite3"
EQUITY_IRI = "jpmorgan:data:cdao:concept:equity-prices"
FIXED_INCOME_IRI = "jpmorgan:data:cdao:concept:fixed-income-prices"
MARKET_DATA_IRI = "jpmorgan:data:cdao:domain:market-data"


def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("network access is forbidden in scipy_sqlite E2E")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)

    try:
        import boto3
    except ImportError:
        pass
    else:
        monkeypatch.setattr(boto3, "client", blocked)
        monkeypatch.setattr(boto3.session.Session, "client", blocked)

    try:
        import requests
    except ImportError:
        pass
    else:
        monkeypatch.setattr(requests, "request", blocked)
        monkeypatch.setattr(requests, "get", blocked)
        monkeypatch.setattr(requests.sessions.Session, "request", blocked)


def _configure_factory(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
) -> dict[str, Any]:
    monkeypatch.setenv("STORE_BACKEND", "scipy_sqlite")
    monkeypatch.setenv("SCUDO_PERSIST_TARGET", "scipy_sqlite")
    monkeypatch.setenv("SCUDO_SCIPY_SQLITE_PATH", str(database))
    monkeypatch.setenv("SCUDO_TAXONOMY_SEED", str(ILLUSTRATIVE_TAXONOMY))
    monkeypatch.setenv("FRAME_SOURCE", "mock")
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "jaro_winkler")
    monkeypatch.delenv("SCUDO_CANONICAL_BUNDLE_URI", raising=False)
    monkeypatch.delenv("SCUDO_CANONICAL_BUNDLE_KEY", raising=False)
    monkeypatch.delenv("S3_WORKING_SET_BUCKET", raising=False)

    import scudo_mapping_mcp.config as config_module
    import scudo_mapping_mcp.store as store_package
    import scudo_mapping_mcp.store.factory as factory_module

    factory_module.close_store()
    config_module = importlib.reload(config_module)
    factory_module = importlib.reload(factory_module)
    store_package = importlib.reload(store_package)

    import scudo_mapping_mcp.bundle as bundle_module
    import scudo_mapping_mcp.feedback as feedback_module
    import scudo_mapping_mcp.frames as frames_module
    import scudo_mapping_mcp.ingest as ingest_module
    import scudo_mapping_mcp.matching as matching_module

    frames_module = importlib.reload(frames_module)
    ingest_module = importlib.reload(ingest_module)
    feedback_module = importlib.reload(feedback_module)
    bundle_module = importlib.reload(bundle_module)
    matching_module = importlib.reload(matching_module)
    store = store_package.get_store()

    from scudo_mapping_mcp.store.scipy_sqlite_store import ScipySQLiteStore

    assert isinstance(store, ScipySQLiteStore)
    assert store._path == database
    return {
        "bundle": bundle_module,
        "config": config_module,
        "factory": factory_module,
        "feedback": feedback_module,
        "frames": frames_module,
        "ingest": ingest_module,
        "matching": matching_module,
        "store": store,
    }


def _revision(path: Path) -> int:
    from scudo_mapping_mcp.store.scipy_sqlite_schema import connect

    with connect(path) as conn:
        return int(
            conn.execute(
                "SELECT value FROM store_metadata WHERE key='taxonomy_revision'"
            ).fetchone()[0]
        )


def _semantic_equity_specialist(calls: list[str]):
    def specialist(ref, candidates):
        calls.append(ref.product_id)
        chosen = next(
            candidate
            for candidate in candidates
            if candidate.node.iri == EQUITY_IRI
            and candidate.node.label == "Equity Prices"
        )
        return chosen

    return specialist


def test_real_scipy_sqlite_lifecycle_survives_restart_and_bundle_cutover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_network(monkeypatch)
    primary_path = tmp_path / "matching.sqlite3"
    imported_path = tmp_path / "imported.sqlite3"
    assert primary_path.resolve() != CONSOLE_SQLITE.resolve()

    runtime = _configure_factory(monkeypatch, primary_path)
    bundle_module = runtime["bundle"]
    factory_module = runtime["factory"]
    feedback_module = runtime["feedback"]
    ingest_module = runtime["ingest"]
    matching_module = runtime["matching"]
    store = runtime["store"]

    from scudo_mapping_mcp.config import FAIL_CUT, PASS_CUT
    from scudo_mapping_mcp.models import (
        ConceptualEdge,
        ConceptualEdgeKind,
        ConceptualNode,
        ConceptualNodeKind,
        ContentDeliveryModel,
        ContractTerms,
        MappingStatus,
        PartyProfile,
        TaxonomyNode,
    )
    from scudo_mapping_mcp.taxonomy_graph import analyse_taxonomy

    try:
        seeded = ingest_module.seed_taxonomy()
        assert seeded > 0
        assert _revision(primary_path) == 1
        assert store.health()
        assert store.taxonomy_size() == seeded
        assert stat.S_IMODE(primary_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(primary_path.stat().st_mode) == 0o600

        products = [
            {"product_id": "APPROVE-1", "name": "Equity Prices", "description": ""},
            {"product_id": "OVERRIDE-1", "name": "Eq", "description": ""},
            {
                "product_id": "REJECT-1",
                "name": "Fixed Income Prices",
                "description": "",
            },
            {"product_id": "FAIL-1", "name": "QZXJ KLMN", "description": ""},
        ]
        refs = ingest_module.ingest_bytes(
            "LSEG",
            "illustrative-products.json",
            json.dumps(products).encode("utf-8"),
            upsert=True,
        )
        refs_by_id = {ref.product_id: ref for ref in refs}
        assert set(refs_by_id) == {
            "APPROVE-1",
            "OVERRIDE-1",
            "REJECT-1",
            "FAIL-1",
        }

        specialist_calls: list[str] = []
        specialist = _semantic_equity_specialist(specialist_calls)
        predictions = {
            product_id: matching_module.map_vendor_product(
                ref,
                specialist=specialist,
            )
            for product_id, ref in refs_by_id.items()
        }
        approved_prediction = predictions["APPROVE-1"]
        borderline_prediction = predictions["OVERRIDE-1"]
        rejected_prediction = predictions["REJECT-1"]
        fail_prediction = predictions["FAIL-1"]

        assert PASS_CUT == 0.80
        assert FAIL_CUT == 0.70
        assert specialist_calls == ["OVERRIDE-1"]
        assert approved_prediction.candidates[0].similarity == pytest.approx(1.0)
        assert (
            approved_prediction.confidence
            == approved_prediction.candidates[0].similarity
        )
        assert approved_prediction.band == "pass"
        assert approved_prediction.status is MappingStatus.AUTO_MAPPED
        assert approved_prediction.mapped_node_iri == EQUITY_IRI

        assert FAIL_CUT <= borderline_prediction.candidates[0].similarity < PASS_CUT
        assert borderline_prediction.confidence == pytest.approx(
            borderline_prediction.candidates[0].similarity
        )
        assert borderline_prediction.band == "borderline"
        assert borderline_prediction.mapped_node_iri == EQUITY_IRI

        assert rejected_prediction.candidates[0].node.iri == FIXED_INCOME_IRI
        assert fail_prediction.candidates[0].similarity < FAIL_CUT
        assert fail_prediction.confidence == fail_prediction.candidates[0].similarity
        assert fail_prediction.band == "fail"
        assert fail_prediction.status is MappingStatus.NEEDS_REVIEW

        approved_ref = refs_by_id["APPROVE-1"].model_copy(
            update={
                "source_content_hash": "sha256:approve",
                "source_file_audit_id": "audit-approve",
            }
        )
        overridden_ref = refs_by_id["OVERRIDE-1"].model_copy(
            update={
                "source_content_hash": "sha256:override",
                "source_file_audit_id": "audit-override",
            }
        )
        rejected_ref = refs_by_id["REJECT-1"].model_copy(
            update={
                "source_content_hash": "sha256:reject",
                "source_file_audit_id": "audit-reject",
            }
        )
        approved = feedback_module.apply_decision(
            approved_ref,
            decision="approve",
            decided_by="reviewer@local",
            node_iri=EQUITY_IRI,
            suggested_confidence=approved_prediction.confidence,
        )
        overridden = feedback_module.apply_decision(
            overridden_ref,
            decision="override",
            decided_by="reviewer@local",
            node_iri=FIXED_INCOME_IRI,
        )
        rejected = feedback_module.apply_decision(
            rejected_ref,
            decision="reject",
            decided_by="reviewer@local",
            node_iri=FIXED_INCOME_IRI,
        )
        assert approved.status is MappingStatus.APPROVED
        assert overridden.status is MappingStatus.OVERRIDDEN
        assert rejected.status is MappingStatus.REJECTED
        assert store.get_negative_precedents("LSEG", "REJECT-1") == [FIXED_INCOME_IRI]
        assert all(
            candidate.node.iri != FIXED_INCOME_IRI
            for candidate in store.find_similar_products(rejected_ref)
        )
        assert store.rank_signals_for(
            store.vendor_signature(
                approved_ref.vendor, approved_ref.name, approved_ref.product_id
            )
        ) == {EQUITY_IRI: 1}
        assert store.rank_signals_for(
            store.vendor_signature(
                overridden_ref.vendor,
                overridden_ref.name,
                overridden_ref.product_id,
            )
        ) == {FIXED_INCOME_IRI: 1}

        confidence_before_graph_reads = approved_prediction.confidence
        neighbourhood = store.get_ontology_neighbourhood(
            MARKET_DATA_IRI,
            max_depth=3,
            max_nodes=50,
        )
        assert EQUITY_IRI in {node.iri for node in neighbourhood.nodes}
        assert neighbourhood.edges
        graph_evidence = analyse_taxonomy(
            store.list_taxonomy_nodes(),
            candidate_iris=[EQUITY_IRI, FIXED_INCOME_IRI],
            anchor_iris=[EQUITY_IRI],
        )
        assert graph_evidence.evidence_valid
        assert graph_evidence.node_count == seeded
        assert {item.candidate_iri for item in graph_evidence.candidates} == {
            EQUITY_IRI,
            FIXED_INCOME_IRI,
        }
        assert approved_prediction.confidence == confidence_before_graph_reads

        contract = ConceptualNode(
            iri="mds.enrich:contract-e2e",
            kind=ConceptualNodeKind.CONTRACT,
            label="Illustrative Equity Contract",
            attaches_to_concept_iri=EQUITY_IRI,
            sequence_number=7,
            notation="EQ-CONTRACT",
            cdm=ContentDeliveryModel.DIRECT_ACCESS_SERVICE,
            description="Illustrative contract metadata",
            contract_terms=ContractTerms(
                status="active",
                legal_basis="contract",
                licensing_model="enterprise",
                renewal_type="annual",
                term="P1Y",
                initial_term="P1Y",
                initial_term_end="2027-08-13",
                renewal_term="P1Y",
                store_purpose="valuation",
                post_term_store_purpose="audit",
                internal_controls="least privilege",
            ),
        )
        party = ConceptualNode(
            iri="mds.enrich:party-e2e",
            kind=ConceptualNodeKind.PARTY,
            label="Illustrative Vendor",
            attaches_to_concept_iri=EQUITY_IRI,
            party_profile=PartyProfile(
                perm_id="P-123",
                supply_chain_status="direct",
                organization_type="vendor",
                issuer_on="2026-08-13",
                member_of="illustrative-market",
                exchange="XLON",
            ),
        )
        edge = ConceptualEdge(
            from_iri=contract.iri,
            to_iri=party.iri,
            kind=ConceptualEdgeKind.DATASET_PARTY,
            label="licensed from",
        )
        store.upsert_conceptual_node(contract)
        store.upsert_conceptual_node(party)
        store.upsert_conceptual_edge(edge)
        conceptual = store.get_conceptual_graph(EQUITY_IRI)
        assert {node.iri: node.model_dump() for node in conceptual.nodes} == {
            contract.iri: contract.model_dump(),
            party.iri: party.model_dump(),
        }
        assert conceptual.edges == [edge]

        full_taxonomy = store.list_taxonomy_nodes()
        taxonomy_without_fixed_income = [
            node.model_copy(
                update={
                    "children_iris": [
                        child
                        for child in node.children_iris
                        if child != FIXED_INCOME_IRI
                    ]
                }
            )
            for node in full_taxonomy
            if node.iri != FIXED_INCOME_IRI
        ]
        store.replace_taxonomy(taxonomy_without_fixed_income)
        assert store.get_taxonomy_node(FIXED_INCOME_IRI) is None
        assert store.get_precedent_mapping("LSEG", "OVERRIDE-1") is None
        assert store.get_negative_precedents("LSEG", "REJECT-1") == [FIXED_INCOME_IRI]
        assert {
            record["product_id"] for record in store.list_confirmed_precedents()
        } == {"APPROVE-1", "OVERRIDE-1"}
        assert (
            matching_module.map_vendor_product(overridden_ref).rationale != "precedent"
        )

        store.replace_taxonomy(full_taxonomy)
        assert (
            store.get_precedent_mapping("LSEG", "OVERRIDE-1").status
            is MappingStatus.OVERRIDDEN
        )

        factory_module.close_store()
        runtime = _configure_factory(monkeypatch, primary_path)
        factory_module = runtime["factory"]
        store = runtime["store"]
        bundle_module = runtime["bundle"]
        matching_module = runtime["matching"]
        approved_reuse = matching_module.map_vendor_product(approved_ref)
        overridden_reuse = matching_module.map_vendor_product(overridden_ref)
        assert approved_reuse.status is MappingStatus.APPROVED
        assert approved_reuse.rationale == "precedent"
        assert approved_reuse.source_content_hash == "sha256:approve"
        assert overridden_reuse.status is MappingStatus.OVERRIDDEN
        assert overridden_reuse.rationale == "precedent"
        assert overridden_reuse.mapped_node_iri == FIXED_INCOME_IRI
        assert all(
            candidate.node.iri != FIXED_INCOME_IRI
            for candidate in store.find_similar_products(rejected_ref)
        )

        exported = bundle_module.export_bundle(
            source_env="e2e-primary",
            created_at="2026-08-13T08:00:00.000Z",
        )
        assert len(exported.patterns) == 2
        exported_records = store.list_confirmed_precedents()

        factory_module.close_store()
        imported_runtime = _configure_factory(monkeypatch, imported_path)
        imported = imported_runtime["store"]
        imported.replace_taxonomy(full_taxonomy)
        first_import = imported_runtime["bundle"].import_bundle(exported)
        second_import = imported_runtime["bundle"].import_bundle(exported)
        assert first_import.total == first_import.applied == 2
        assert first_import.skipped_unknown_node == 0
        assert first_import.taxonomy_version_source == (
            first_import.taxonomy_version_local
        )
        assert second_import == first_import
        assert imported.list_confirmed_precedents() == exported_records
        assert imported.rank_signals_for(
            imported.vendor_signature(
                approved_ref.vendor, approved_ref.name, approved_ref.product_id
            )
        ) == {EQUITY_IRI: 1}
        assert stat.S_IMODE(imported_path.stat().st_mode) == 0o600

        factory_module.close_store()
        primary_runtime = _configure_factory(monkeypatch, primary_path)
        primary = primary_runtime["store"]
        from scudo_mapping_mcp.store.scipy_sqlite_store import ScipySQLiteStore

        observer = ScipySQLiteStore(primary_path)
        before_replace = observer._snapshot_manager.capture()
        extended_taxonomy = primary.list_taxonomy_nodes() + [
            TaxonomyNode(
                iri="jpmorgan:data:cdao:concept:e2e-added",
                label="Illustrative E2E Added Concept",
            )
        ]
        primary.replace_taxonomy(extended_taxonomy)
        observed = observer.get_taxonomy_node("jpmorgan:data:cdao:concept:e2e-added")
        assert observed is not None
        assert (
            observer._snapshot_manager.capture().revision == before_replace.revision + 1
        )

        active_revision = _revision(primary_path)
        active_snapshot = observer._snapshot_manager.capture()
        with pytest.raises(ValueError, match="missing"):
            primary.replace_taxonomy(
                [
                    TaxonomyNode(
                        iri="jpmorgan:data:cdao:concept:invalid",
                        label="Invalid",
                        parent_iri="jpmorgan:data:cdao:missing",
                    )
                ]
            )
        assert _revision(primary_path) == active_revision
        assert observer._snapshot_manager.capture() is active_snapshot
        assert observer.get_taxonomy_node(EQUITY_IRI) is not None
        observer.close()

        assert "falkordb" not in sys.modules
    finally:
        factory_module.close_store()
        runtime["frames"].clear_frames()


def test_flask_api_smoke_uses_factory_selected_scipy_sqlite_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_network(monkeypatch)
    database = tmp_path / "api-matching.sqlite3"
    monkeypatch.setenv("SCUDO_AUTH_ALLOW_DEV", "1")
    monkeypatch.setenv("SCUDO_AUTH_DEV_PRINCIPAL", "e2e@local")
    monkeypatch.setenv("SCUDO_AUTH_ALLOW_DEV_WRITES", "1")
    monkeypatch.setenv("SCUDO_SPECIALIST_BACKEND", "local")
    monkeypatch.delenv("SCUDO_MV_ALLOW_INLINE_FRAME", raising=False)
    runtime = _configure_factory(monkeypatch, database)
    factory_module = runtime["factory"]

    import routes.mapping as mapping_routes

    mapping_routes = importlib.reload(mapping_routes)
    from app import app

    mapping_routes._seeded = False
    mapping_routes._readiness = {"seed_ok": False, "last_error": None}
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            client = app.test_client()
            vendors = client.get("/api/mapping/vendors")
            assert vendors.status_code == 200
            assert "LSEG" in vendors.get_json()["vendors"]

            upload = client.post(
                "/api/mapping/ingest",
                data={
                    "vendor": "LSEG",
                    "file": (
                        io.BytesIO(
                            b"product_id,name,description\n"
                            b"API-1,Equity Prices,Illustrative API smoke\n"
                        ),
                        "products.csv",
                    ),
                },
                content_type="multipart/form-data",
            )
            assert upload.status_code == 200
            assert upload.get_json()["ingested"] == 1

            candidates = client.post(
                "/api/mapping/similar",
                json={"vendor": "LSEG", "product_id": "API-1"},
            )
            assert candidates.status_code == 200
            assert candidates.get_json()["candidates"][0]["node"]["iri"] == EQUITY_IRI

            mapped = client.post(
                "/api/mapping/map",
                json={"vendor": "LSEG", "product_id": "API-1"},
            )
            assert mapped.status_code == 200
            assert mapped.get_json()["band"] == "pass"
            assert mapped.get_json()["confidence"] >= 0.80

            ready = client.get("/readyz")
            assert ready.status_code == 200
            assert ready.get_json() == {"ready": True}
            assert factory_module.get_store()._path == database
            assert database.exists()
            assert stat.S_IMODE(database.stat().st_mode) == 0o600
            assert database.resolve() != CONSOLE_SQLITE.resolve()
            assert "falkordb" not in sys.modules

        assert not [
            warning
            for warning in caught
            if "Pydantic serializer warnings" in str(warning.message)
            or "Expected `list" in str(warning.message)
        ]
    finally:
        runtime["frames"].clear_frames()
        factory_module.close_store()
