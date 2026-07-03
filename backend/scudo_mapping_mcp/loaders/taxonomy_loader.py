"""Taxonomy loader dispatch — ``SCUDO_TAXONOMY_LOADER`` seam.

Loader names are validated in ``config.Settings.from_env`` against
``config.ALLOWED_TAXONOMY_LOADERS``. The registry below must stay in
lockstep with that tuple — an assertion enforces it at import time.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

from ..config import ALLOWED_TAXONOMY_LOADERS, Settings
from ..models import TaxonomyNode

_CATALOGUE_FIXTURE = (
    Path(__file__).resolve().parents[2] / "scudo" / "fixtures" / "cdao_catalogue.json"
)

LoaderFn = Callable[[Settings], list[TaxonomyNode]]


def _coerce_ref(value) -> str | None:
    if isinstance(value, dict):
        value = value.get("@id")
    return str(value) if value else None


def _normalize_catalogue_node(raw: dict) -> dict:
    iri = _coerce_ref(raw.get("iri") or raw.get("@id"))
    label = raw.get("label") or raw.get("prefLabel") or raw.get("title")
    parent = _coerce_ref(
        raw.get("parent_iri") or raw.get("inSubdomain") or raw.get("inDomain")
    )
    if not label and iri:
        label = iri.rsplit(":", 1)[-1].replace("-", " ").replace("_", " ").title()
    return {"iri": iri, "label": label, "parent_iri": parent}


def load_cdao_taxonomy(path: Path | None = None) -> list[TaxonomyNode]:
    """Load taxonomy nodes from the canonical CDAO JSON catalogue fixture."""
    fixture = path or _CATALOGUE_FIXTURE
    data = json.loads(fixture.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("nodes") or data.get("@graph") or []
    nodes: list[TaxonomyNode] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        node = _normalize_catalogue_node(raw)
        if node.get("iri") and node.get("label"):
            nodes.append(
                TaxonomyNode(
                    iri=str(node["iri"]),
                    label=str(node["label"]),
                    parent_iri=node.get("parent_iri") or None,
                )
            )
    return nodes


def _load_cdao(_settings: Settings) -> list[TaxonomyNode]:
    return load_cdao_taxonomy()


def _load_dcat(settings: Settings) -> list[TaxonomyNode]:
    from .dcat_loader import load_dcat_taxonomy

    source = settings.taxonomy_source or os.getenv("SCUDO_TAXONOMY_SOURCE", "").strip()
    if not source:
        raise ValueError(
            "SCUDO_TAXONOMY_LOADER=dcat requires SCUDO_TAXONOMY_SOURCE "
            "(path to .ttl / .rdf / .xml / .jsonld)"
        )
    return load_dcat_taxonomy(
        source,
        subsumption_depth=settings.subsumption_depth,
    )


# Registry keyed exactly by ``ALLOWED_TAXONOMY_LOADERS`` — add a loader by
# extending the tuple in config.py AND wiring the function here.
_TAXONOMY_LOADERS: dict[str, LoaderFn] = {
    "cdao": _load_cdao,
    "dcat": _load_dcat,
}

if set(_TAXONOMY_LOADERS) != set(ALLOWED_TAXONOMY_LOADERS):
    missing = set(ALLOWED_TAXONOMY_LOADERS) - set(_TAXONOMY_LOADERS)
    extra = set(_TAXONOMY_LOADERS) - set(ALLOWED_TAXONOMY_LOADERS)
    raise RuntimeError(
        "taxonomy loader registry out of sync with ALLOWED_TAXONOMY_LOADERS: "
        f"missing={sorted(missing)!r} extra={sorted(extra)!r}"
    )


def load_taxonomy_nodes(settings: Settings) -> list[TaxonomyNode]:
    """Return taxonomy nodes for ``settings.taxonomy_loader``."""
    loader = settings.taxonomy_loader
    try:
        fn = _TAXONOMY_LOADERS[loader]
    except KeyError as e:
        raise ValueError(
            f"SCUDO_TAXONOMY_LOADER={loader!r} has no wired loader "
            f"(allowed: {sorted(ALLOWED_TAXONOMY_LOADERS)!r})"
        ) from e
    return fn(settings)
