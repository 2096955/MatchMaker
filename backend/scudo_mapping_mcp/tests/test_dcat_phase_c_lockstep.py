"""Phase C — widened DCAT models, graph extraction, and the loader/projector
lockstep rule (spec 2026-07-13-catalogue-rights-uml-gap-analysis.md, Phase C).

Two parallel paths produce matcher-facing ``TaxonomyNode`` shapes:
  A. ``dcat_loader._extract_node`` (via ``load_dcat_taxonomy_from_graph``)
  B. ``models_dcat`` projectors (via ``enrichment_dcat_projection``)
The lockstep test below feeds ONE in-memory rdflib graph to both and compares.
"""

from __future__ import annotations

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCAT, DCTERMS, RDF

from scudo_mapping_mcp.csvw_aliases import CAT_ONTOLOGY_NS
from scudo_mapping_mcp.loaders.dcat_loader import (
    extract_dcat_data_service,
    extract_dcat_dataset,
    extract_dcat_distribution,
    load_dcat_taxonomy_from_graph,
)
from scudo_mapping_mcp.models_dcat import (
    DcatDataDictionary,
    DcatDeliveryChannel,
    DcatProductPackage,
    project_dcat_dataset,
    project_dcat_entity,
)

CAT = Namespace(CAT_ONTOLOGY_NS)
EX = Namespace("https://example.com/cat/")


def _dataset_graph() -> tuple[Graph, URIRef]:
    g = Graph()
    ds = EX["eq-prices"]
    g.add((ds, RDF.type, DCAT.Dataset))
    g.add((ds, DCTERMS.title, Literal("Equity Prices")))
    g.add((ds, DCTERMS.description, Literal("End-of-day equity prices.")))
    g.add((ds, DCAT.theme, EX["Equities"]))
    g.add((ds, DCAT.keyword, Literal("prices")))
    # Phase E matching signals + rights-side updatePeriod + the UML coverage
    # block — present in the graph; extraction must capture them into the
    # MODEL but neither TaxonomyNode path may leak them into matching text
    # in Phase C.
    g.add((ds, CAT.businessConcept, Literal("Pricing")))
    g.add((ds, CAT.assetClass, Literal("AC-10294")))
    g.add((ds, CAT.superAssetClass, Literal("PAC-88371")))
    g.add((ds, CAT.updatePeriod, Literal("P1D")))
    g.add((ds, CAT.geographicCoverage, Literal("EMEA-COV")))
    g.add((ds, CAT.temporalCoverage, Literal("2001-2026-COV")))
    g.add((ds, CAT.industryCoverage, Literal("Banking-COV")))
    g.add((ds, CAT.contentTypeCoverage, Literal("Prices-COV")))
    return g, ds


def _distribution_service_graph() -> tuple[Graph, URIRef, URIRef]:
    g = Graph()
    ds = EX["eq-prices"]
    dist = EX["eq-prices-csv"]
    svc = EX["eq-prices-api"]
    g.add((dist, RDF.type, DCAT.Distribution))
    g.add((dist, DCTERMS.title, Literal("CSV Feed")))
    g.add((dist, DCAT.mediaType, Literal("text/csv")))
    g.add((dist, DCTERMS.conformsTo, EX["dictionary-1"]))
    g.add((dist, DCTERMS.type, Literal("Download")))
    g.add((ds, DCAT.distribution, dist))
    g.add((svc, RDF.type, DCAT.DataService))
    g.add((svc, DCTERMS.title, Literal("Price API")))
    g.add((svc, DCAT.endpointURL, EX["endpoint"]))
    g.add((svc, DCTERMS.type, Literal("API Data Service")))
    g.add((svc, CAT.deploymentType, Literal("cloud")))
    g.add((svc, CAT.apiType, Literal("REST")))
    g.add((svc, DCAT.servesDataset, ds))
    return g, dist, svc


# ── extraction: new attrs land on the widened models ──────────────────────
def test_extract_dcat_dataset_captures_phase_c_fields():
    g, ds = _dataset_graph()
    model = extract_dcat_dataset(g, ds)
    assert model.title == "Equity Prices"
    assert model.description == "End-of-day equity prices."
    assert model.themes == [str(EX["Equities"])]
    assert model.keywords == ["prices"]
    assert model.business_concept == "Pricing"
    assert model.asset_class == "AC-10294"
    assert model.super_asset_class == "PAC-88371"
    assert model.update_period == "P1D"
    assert model.geographic_coverage == "EMEA-COV"
    assert model.temporal_coverage == "2001-2026-COV"
    assert model.industry_coverage == "Banking-COV"
    assert model.content_type_coverage == "Prices-COV"


def test_extract_dcat_distribution_captures_phase_c_fields():
    g, dist, _svc = _distribution_service_graph()
    model = extract_dcat_distribution(g, dist)
    assert model.title == "CSV Feed"
    assert model.media_type == "text/csv"
    assert model.conforms_to == str(EX["dictionary-1"])
    assert model.distribution_type == "Download"
    assert model.dataset_iri == str(EX["eq-prices"])


def test_extract_dcat_data_service_captures_phase_c_fields():
    g, _dist, svc = _distribution_service_graph()
    model = extract_dcat_data_service(g, svc)
    assert model.title == "Price API"
    assert model.endpoint_url == str(EX["endpoint"])
    assert model.service_type == "API Data Service"
    assert model.deployment_type == "cloud"
    assert model.api_type == "REST"
    assert model.dataset_iri == str(EX["eq-prices"])


# ── lockstep rule: loader path vs projector path ──────────────────────────
def test_dataset_loader_and_projector_paths_agree():
    """Full model_dump() equality, with ONE documented pre-existing delta
    normalised before comparing.

    The delta: ``parent_iri`` — ``_extract_node`` derives it from
    ``skos:broader`` (None when absent) while ``project_dcat_dataset``
    promotes ``themes[0]``. Both endpoint values are pinned explicitly
    first, then the projected side's parent_iri is copied over the loaded
    side so the remaining comparison covers EVERY other TaxonomyNode field
    (children_iris, superproperty_iris, ... included) — matching the full
    equality the Distribution/DataService lockstep test already enforces.
    """
    g, ds = _dataset_graph()
    loaded = {n.iri: n for n in load_dcat_taxonomy_from_graph(g)}[str(ds)]
    projected = project_dcat_dataset(extract_dcat_dataset(g, ds))

    # Pin the documented delta's two endpoint values before normalising.
    assert loaded.parent_iri is None
    assert projected.parent_iri == str(EX["Equities"])

    normalised = loaded.model_copy(update={"parent_iri": projected.parent_iri})
    assert normalised.model_dump() == projected.model_dump()


def test_distribution_and_service_loader_and_projector_paths_identical():
    g, dist, svc = _distribution_service_graph()
    loaded = {n.iri: n for n in load_dcat_taxonomy_from_graph(g)}

    dist_model = extract_dcat_distribution(g, dist)
    dist_projected = project_dcat_entity(
        iri=dist_model.iri, title=dist_model.title, description=dist_model.description
    )
    assert loaded[str(dist)].model_dump() == dist_projected.model_dump()

    svc_model = extract_dcat_data_service(g, svc)
    svc_projected = project_dcat_entity(
        iri=svc_model.iri, title=svc_model.title, description=svc_model.description
    )
    assert loaded[str(svc)].model_dump() == svc_projected.model_dump()


def test_phase_e_signals_surface_as_structured_fields_on_both_paths():
    """Phase E lockstep extension: BOTH TaxonomyNode-producing paths surface
    businessConcept / assetClass / superAssetClass as STRUCTURED fields (the
    full model_dump equality above would pass trivially if both paths
    dropped them, so the population is pinned explicitly here)."""
    g, ds = _dataset_graph()
    loaded = {n.iri: n for n in load_dcat_taxonomy_from_graph(g)}[str(ds)]
    projected = project_dcat_dataset(extract_dcat_dataset(g, ds))
    for node in (loaded, projected):
        assert node.business_concept == "Pricing"
        assert node.asset_class == "AC-10294"
        assert node.super_asset_class == "PAC-88371"


def test_phase_e_signals_do_not_leak_into_matching_text():
    """Phase C stops at the model boundary: businessConcept / assetClass /
    superAssetClass / updatePeriod AND the coverage block (geographic /
    temporal / industry / contentType — matching-signal candidates, same
    boundary) must not appear in either path's TaxonomyNode text surfaces
    (definition / alt_labels / label). Their composition into BM25/dense
    text is Phase E's measured rollout."""
    g, ds = _dataset_graph()
    loaded = {n.iri: n for n in load_dcat_taxonomy_from_graph(g)}[str(ds)]
    projected = project_dcat_dataset(extract_dcat_dataset(g, ds))
    for node in (loaded, projected):
        text = " ".join([node.label, node.definition, *node.alt_labels])
        for signal in (
            "Pricing",
            "AC-10294",
            "PAC-88371",
            "P1D",
            "EMEA-COV",
            "2001-2026-COV",
            "Banking-COV",
            "Prices-COV",
        ):
            assert signal not in text


# ── G19: ProductPackage / DeliveryChannel / DataDictionary model shapes ───
def test_g19_model_shapes_carry_uml_attrs():
    pkg = DcatProductPackage(
        iri=str(EX["pkg-1"]),
        title="Equity Bundle",
        long_name="LSEG Equity Prices Bundle",
        description="Bundle of equity price feeds",
        dataset_iri=str(EX["eq-prices"]),
        product_id="PKG-001",
    )
    assert pkg.long_name == "LSEG Equity Prices Bundle"
    assert pkg.product_id == "PKG-001"

    channel = DcatDeliveryChannel(
        iri=str(EX["chan-1"]), title="SFTP", distribution_type="file"
    )
    assert channel.distribution_type == "file"

    dictionary = DcatDataDictionary(
        iri=str(EX["dict-1"]), title="EQ Dictionary", dataset_iri=str(EX["eq-prices"])
    )
    assert dictionary.dataset_iri == str(EX["eq-prices"])
