"""Zone 3 — Matching Engine.

The cost ladder: scope gate → precedent reuse → hybrid retrieval (dense + BM25
lexical + RRF + structural tilt) → confidence gate (PASS ≥0.80 / BORDERLINE
0.70-0.80 / FAIL <0.70). ``matching`` is the strict gate ladder; ``retrieval``
orchestrates the multi-path candidate search; ``matcher_bridge`` is the
Lambda-side bridge that runs the real cost ladder over the graph store;
``dense_scorer`` is the Opus-as-dense-scorer arm.

AWS entrypoint: ``scudo_mapping_mcp.match_verify_mcp`` (ECS, port 8002).
``matcher_bridge`` is invoked in-process by the mapping Lambda
(``scudo.lambda_handler``).

Aurora/graph-store role: **this is the one zone whose reads come from the graph
store, not Aurora.** Candidate discovery is served by ``store`` (FalkorDB now,
Neptune the dormant cutover) selected via ``STORE_BACKEND`` — a retrieval index,
not the system of record. JPMC did not adopt the graph store as canonical; it is
retained behind the single swap point ``scudo_mapping_mcp.store.factory`` in case
that decision changes. Aurora is written only *after* a decision (Zone 5).

These are re-exports — the modules physically live in ``scudo_mapping_mcp`` /
``scudo``. See ``ZONES.md``.
"""

from scudo import matcher_bridge
from scudo_mapping_mcp import (
    dense_scorer,
    matching,
    retrieval,
    match_verify_mcp,
)
from scudo_mapping_mcp import store

__all__ = [
    "matching",
    "retrieval",
    "matcher_bridge",
    "dense_scorer",
    "match_verify_mcp",
    "store",
]
