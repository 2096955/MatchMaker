"""Self-contained taxonomy node contract used by the vendored graph analyzer."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class TaxonomyNode(BaseModel):
    iri: str
    label: str
    parent_iri: Optional[str] = None
    children_iris: list[str] = Field(default_factory=list)
    node_kind: Literal["concept", "class", "property"] = "concept"
    superclass_iris: list[str] = Field(default_factory=list)
    superproperty_iris: list[str] = Field(default_factory=list)
