"""Shared fixtures for backend/scudo tests.

built_matching_graph rebuilds the matching-graph fixture fresh for each test
that needs it, writing to a scratch tmp_path instead of the tracked
backend/scudo/fixtures/*.json. build_knowledge_graph() stamps a live
datetime.now() into project.analyzedAt, so any test that ran main() against
the real _OUT/_META_OUT paths regenerated the tracked fixture with a new
timestamp on every run — a spurious git diff with no real content change.
Production behavior of main() is untouched (a real dashboard sync SHOULD
stamp a real build time); this is a test-isolation fix only.
"""

from __future__ import annotations

import json
import os

import pytest


@pytest.fixture
def built_matching_graph(tmp_path, monkeypatch):
    os.environ.setdefault("STORE_BACKEND", "memory")
    os.environ.setdefault("FRAME_SOURCE", "mock")

    from scudo import build_matching_graph

    graph_path = tmp_path / "matching-graph.json"
    meta_path = tmp_path / "meta.json"
    monkeypatch.setattr(build_matching_graph, "_OUT", str(graph_path))
    monkeypatch.setattr(build_matching_graph, "_META_OUT", str(meta_path))

    build_matching_graph.main()

    with open(graph_path, encoding="utf-8") as fh:
        graph = json.load(fh)
    with open(meta_path, encoding="utf-8") as fh:
        meta = json.load(fh)
    return {"graph": graph, "meta": meta}
