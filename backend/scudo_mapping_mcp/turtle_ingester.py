"""Turtle upload routing for taxonomy seeding on the vendor ingest surface."""

from __future__ import annotations

import os
from typing import Any, Callable

from rdflib import Graph

from scudo_mapping_mcp.config import settings
from scudo_mapping_mcp.loaders.dcat_loader import load_dcat_taxonomy_from_graph
from scudo_mapping_mcp.store import get_store

_TURTLE_SUFFIXES = (".ttl", ".turtle")


class TurtleIngester:
    """Parse Turtle uploads and upsert taxonomy nodes (fail-closed on bad RDF)."""

    @classmethod
    def handles(cls, filename: str) -> bool:
        return os.path.splitext(filename)[1].lower() in _TURTLE_SUFFIXES

    def ingest(
        self,
        data: bytes,
        *,
        filename: str = "upload.ttl",
        subsumption_depth: int | None = None,
        on_stage: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> int:
        """Parse Turtle bytes, upsert taxonomy nodes, return node count."""
        del filename  # reserved for future format hints / logging
        if not data or not data.strip():
            raise ValueError("empty turtle file")

        if on_stage:
            on_stage("received", {"format": "turtle"})

        graph = self._parse_turtle(data)
        depth = (
            subsumption_depth
            if subsumption_depth is not None
            else settings.subsumption_depth
        )
        nodes = load_dcat_taxonomy_from_graph(graph, subsumption_depth=depth)
        if not nodes:
            raise ValueError("turtle file contains no supported taxonomy terms")

        if on_stage:
            on_stage("parse", {"nodes": len(nodes)})

        store = get_store()
        for node in nodes:
            store.upsert_taxonomy_node(node)

        if on_stage:
            on_stage("validate", {"nodes": len(nodes)})
            on_stage("sink", {"nodes": len(nodes)})

        return len(nodes)

    @staticmethod
    def _parse_turtle(data: bytes) -> Graph:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("turtle file must be UTF-8") from exc
        graph = Graph()
        try:
            graph.parse(data=text, format="turtle")
        except Exception as exc:
            raise ValueError(f"invalid turtle: {exc}") from exc
        return graph
