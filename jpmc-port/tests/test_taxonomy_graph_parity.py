"""Byte and full-behavior parity for the vendored canonical graph analyzer."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from scudo.taxonomy_graph import (
    TaxonomyGraphInputError as JpmcInputError,
    analyse_taxonomy as analyse_jpmc,
)
from scudo.taxonomy_graph_models import TaxonomyNode as JpmcNode

ROOT = Path(__file__).resolve().parents[2]


def _load_backend():
    package_dir = ROOT / "backend" / "scudo_mapping_mcp"
    package_spec = importlib.util.spec_from_file_location(
        "parity_backend",
        package_dir / "__init__.py",
        submodule_search_locations=[str(package_dir)],
    )
    assert package_spec and package_spec.loader
    package = importlib.util.module_from_spec(package_spec)
    sys.modules["parity_backend"] = package
    package_spec.loader.exec_module(package)
    graph_spec = importlib.util.spec_from_file_location(
        "parity_backend.taxonomy_graph",
        package_dir / "taxonomy_graph.py",
    )
    models_spec = importlib.util.spec_from_file_location(
        "parity_backend.taxonomy_graph_models",
        package_dir / "taxonomy_graph_models.py",
    )
    assert graph_spec and graph_spec.loader and models_spec and models_spec.loader
    models = importlib.util.module_from_spec(models_spec)
    sys.modules["parity_backend.taxonomy_graph_models"] = models
    models_spec.loader.exec_module(models)
    graph = importlib.util.module_from_spec(graph_spec)
    sys.modules["parity_backend.taxonomy_graph"] = graph
    graph_spec.loader.exec_module(graph)
    return graph, models


def _dump_pair(records: list[dict], **kwargs):
    backend, backend_models = _load_backend()
    backend_result = backend.analyse_taxonomy(
        [backend_models.TaxonomyNode(**record) for record in records], **kwargs
    )
    jpmc_result = analyse_jpmc([JpmcNode(**record) for record in records], **kwargs)
    return (
        backend_result.model_dump(mode="json"),
        jpmc_result.model_dump(mode="json"),
    )


def test_vendored_files_are_byte_identical_to_canonical_sources():
    pairs = [
        (
            ROOT / "backend" / "scudo_mapping_mcp" / "taxonomy_graph.py",
            ROOT / "jpmc-port" / "scudo" / "taxonomy_graph.py",
        ),
        (
            ROOT / "backend" / "scudo_mapping_mcp" / "taxonomy_graph_models.py",
            ROOT / "jpmc-port" / "scudo" / "taxonomy_graph_models.py",
        ),
    ]
    for canonical, vendored in pairs:
        assert vendored.read_bytes() == canonical.read_bytes()


@pytest.mark.parametrize(
    ("records", "kwargs"),
    [
        (
            [
                {
                    "iri": "illustrative:root",
                    "label": "Root",
                    "children_iris": ["illustrative:a", "illustrative:b"],
                },
                {
                    "iri": "illustrative:a",
                    "label": "A",
                    "parent_iri": "illustrative:root",
                },
                {
                    "iri": "illustrative:b",
                    "label": "B",
                    "parent_iri": "illustrative:root",
                },
            ],
            {
                "candidate_iris": ["illustrative:a", "illustrative:b"],
                "anchor_iris": ["illustrative:root"],
                "confirmed_precedent_iris": ["illustrative:a"],
            },
        ),
        (
            [
                {
                    "iri": "illustrative:root",
                    "label": "Root",
                    "children_iris": ["illustrative:l", "illustrative:r"],
                },
                {
                    "iri": "illustrative:l",
                    "label": "L",
                    "node_kind": "class",
                    "superclass_iris": ["illustrative:root"],
                    "children_iris": ["illustrative:x", "illustrative:y"],
                },
                {
                    "iri": "illustrative:r",
                    "label": "R",
                    "node_kind": "class",
                    "superclass_iris": ["illustrative:root"],
                    "children_iris": ["illustrative:x", "illustrative:y"],
                },
                {
                    "iri": "illustrative:x",
                    "label": "X",
                    "node_kind": "class",
                    "superclass_iris": ["illustrative:l", "illustrative:r"],
                },
                {
                    "iri": "illustrative:y",
                    "label": "Y",
                    "node_kind": "class",
                    "superclass_iris": ["illustrative:l", "illustrative:r"],
                },
            ],
            {"candidate_iris": ["illustrative:x", "illustrative:y"]},
        ),
        (
            [
                {
                    "iri": "illustrative:a",
                    "label": "A",
                    "parent_iri": "illustrative:b",
                },
                {
                    "iri": "illustrative:b",
                    "label": "B",
                    "parent_iri": "illustrative:a",
                },
                {
                    "iri": "illustrative:bad",
                    "label": "Bad",
                    "parent_iri": "illustrative:missing",
                },
            ],
            {"candidate_iris": ["illustrative:a"]},
        ),
        (
            [
                {
                    "iri": "illustrative:c",
                    "label": "C",
                    "children_iris": ["illustrative:d"],
                },
                {
                    "iri": "illustrative:d",
                    "label": "D",
                    "parent_iri": "illustrative:c",
                },
                {
                    "iri": "illustrative:p",
                    "label": "P",
                    "node_kind": "property",
                },
                {
                    "iri": "illustrative:q",
                    "label": "Q",
                    "node_kind": "property",
                    "superproperty_iris": ["illustrative:p"],
                },
            ],
            {
                "candidate_iris": ["illustrative:d", "illustrative:q"],
                "anchor_iris": ["illustrative:c", "illustrative:p"],
            },
        ),
    ],
)
def test_full_model_dump_behavioral_parity(records, kwargs):
    assert _dump_pair(records, **kwargs)[0] == _dump_pair(records, **kwargs)[1]


def test_bounds_and_input_validation_behavioral_parity():
    backend, _backend_models = _load_backend()
    with pytest.raises(backend.TaxonomyGraphInputError) as backend_error:
        backend.analyse_taxonomy([], candidate_iris=["x", " x "])
    with pytest.raises(JpmcInputError) as jpmc_error:
        analyse_jpmc([], candidate_iris=["x", " x "])
    assert str(backend_error.value) == str(jpmc_error.value)
