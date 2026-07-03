"""DCAT entity models and projection into matchable TaxonomyNode shapes."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from .models import TaxonomyNode

_DEFINITION_MAX_LEN = 2000


class FieldDef(BaseModel):
    property_url: str = ""
    title: str = ""
    description: str = ""


class DcatDataset(BaseModel):
    iri: str
    title: str = ""
    description: str = ""
    themes: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class DcatDistribution(BaseModel):
    iri: str
    title: str = ""
    description: str = ""
    access_url: str = ""
    dataset_iri: Optional[str] = None


class DcatDataService(BaseModel):
    iri: str
    title: str = ""
    description: str = ""
    endpoint_url: str = ""


def _join_definition(*parts: str) -> str:
    joined = " | ".join(p.strip() for p in parts if p and p.strip())
    if len(joined) > _DEFINITION_MAX_LEN:
        return joined[: _DEFINITION_MAX_LEN - 3] + "..."
    return joined


def project_dcat_dataset(ds: DcatDataset) -> TaxonomyNode:
    """Project a DCAT dataset to the extended TaxonomyNode the matcher consumes."""
    parent = ds.themes[0] if ds.themes else None
    return TaxonomyNode(
        iri=ds.iri,
        label=ds.title or ds.iri.rsplit("/", 1)[-1],
        parent_iri=parent,
        definition=ds.description,
        alt_labels=ds.keywords,
        node_kind="concept",
        superclass_iris=list(ds.themes),
    )


def project_dcat_entity(
    *,
    iri: str,
    title: str,
    description: str = "",
    alt_labels: list[str] | None = None,
    themes: list[str] | None = None,
    node_kind: Literal["concept", "class", "property"] = "concept",
) -> TaxonomyNode:
    """Generic DCAT-shaped projection used by the RDF loader."""
    theme_list = themes or []
    return TaxonomyNode(
        iri=iri,
        label=title or iri.rsplit("/", 1)[-1],
        parent_iri=theme_list[0] if theme_list else None,
        definition=description,
        alt_labels=alt_labels or [],
        node_kind=node_kind,
        superclass_iris=list(theme_list),
    )
