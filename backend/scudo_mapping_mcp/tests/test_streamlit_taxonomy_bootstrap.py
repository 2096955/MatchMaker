from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

import pytest

from scudo_mapping_mcp.ingest import seed_taxonomy
from scudo_mapping_mcp.models import TaxonomyNode, VendorProductRef
from scudo_mapping_mcp.store import storage_ready
from scudo_mapping_mcp.store.scipy_sqlite_store import ScipySQLiteStore


REPO_ROOT = Path(__file__).resolve().parents[3]
STREAMLIT_APP = REPO_ROOT / "streamlit_app.py"
CUSTOM_IRI = "jpmorgan:data:cdao:dataset:uploaded-rerun-regression"
CUSTOM_LABEL = "Uploaded Rerun Regression Dataset"


def _load_uncached_bootstrap(store: ScipySQLiteStore):
    """Load the Streamlit bootstrap body without importing/rendering the page."""

    module = ast.parse(STREAMLIT_APP.read_text(encoding="utf-8"))
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_bootstrap"
    )
    function.decorator_list = []

    def seed_test_store() -> int:
        with patch("scudo_mapping_mcp.ingest.get_store", return_value=store):
            return seed_taxonomy()

    namespace = {
        "get_store": lambda: store,
        "seed_taxonomy": seed_test_store,
        "storage_ready": storage_ready,
    }
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
            str(STREAMLIT_APP),
            "exec",
        ),
        namespace,
    )
    return namespace["_bootstrap"]


def _configure_seed(monkeypatch, database: Path) -> None:
    monkeypatch.setenv("STORE_BACKEND", "scipy_sqlite")
    monkeypatch.setenv("SCUDO_PERSIST_TARGET", "scipy_sqlite")
    monkeypatch.setenv("SCUDO_SCIPY_SQLITE_PATH", str(database))
    monkeypatch.setenv(
        "SCUDO_TAXONOMY_SEED",
        str(REPO_ROOT / "backend/scudo/fixtures/cdao_catalogue.json"),
    )
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "jaro_winkler")


def _custom_node() -> TaxonomyNode:
    return TaxonomyNode(iri=CUSTOM_IRI, label=CUSTOM_LABEL)


def _assert_custom_node_is_active_and_matchable(store: ScipySQLiteStore) -> None:
    assert store.get_taxonomy_node(CUSTOM_IRI) == _custom_node()
    assert CUSTOM_IRI in {node.iri for node in store.list_taxonomy_nodes()}

    candidates = store.find_similar_products(
        VendorProductRef(
            vendor="TEST",
            product_id="UPLOADED-1",
            name=CUSTOM_LABEL,
        )
    )
    assert candidates
    assert candidates[0].node.iri == CUSTOM_IRI


def test_streamlit_rerun_bootstrap_preserves_uploaded_catalogue_node(
    monkeypatch, tmp_path
):
    database = tmp_path / "matching.sqlite3"
    _configure_seed(monkeypatch, database)
    store = ScipySQLiteStore(database)
    bootstrap = _load_uncached_bootstrap(store)

    bootstrap()
    seeded = store.taxonomy_size()
    store.upsert_taxonomy_node(_custom_node())
    bootstrap()
    displayed_count = store.taxonomy_size()

    assert seeded >= 14
    assert displayed_count == seeded + 1
    _assert_custom_node_is_active_and_matchable(store)


def test_streamlit_bootstrap_seeds_a_fresh_empty_database(monkeypatch, tmp_path):
    database = tmp_path / "matching.sqlite3"
    _configure_seed(monkeypatch, database)
    store = ScipySQLiteStore(database)

    _load_uncached_bootstrap(store)()
    displayed_count = store.taxonomy_size()

    assert displayed_count >= 14
    assert store.taxonomy_size() == displayed_count
    assert store.health()


def test_streamlit_bootstrap_fails_closed_on_an_empty_seed(monkeypatch, tmp_path):
    database = tmp_path / "matching.sqlite3"
    empty_seed = tmp_path / "empty-seed.json"
    empty_seed.write_text("[]", encoding="utf-8")
    _configure_seed(monkeypatch, database)
    monkeypatch.setenv("SCUDO_TAXONOMY_SEED", str(empty_seed))
    store = ScipySQLiteStore(database)

    with pytest.raises(RuntimeError, match="produced an empty taxonomy"):
        _load_uncached_bootstrap(store)()

    assert store.taxonomy_size() == 0
    assert not store.health()


def test_streamlit_bootstrap_preserves_uploaded_node_after_process_restart(
    monkeypatch, tmp_path
):
    database = tmp_path / "matching.sqlite3"
    _configure_seed(monkeypatch, database)
    first_process = ScipySQLiteStore(database)
    _load_uncached_bootstrap(first_process)()
    first_process.upsert_taxonomy_node(_custom_node())
    expected_count = first_process.taxonomy_size()
    first_process.close()

    restarted_process = ScipySQLiteStore(database)
    _load_uncached_bootstrap(restarted_process)()
    displayed_count = restarted_process.taxonomy_size()

    assert displayed_count == expected_count
    _assert_custom_node_is_active_and_matchable(restarted_process)
