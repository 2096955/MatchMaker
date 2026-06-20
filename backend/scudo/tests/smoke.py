"""SCUDO orchestrator smoke driver.

Exercises every routing branch + publish gate without touching Bedrock by
injecting fake agents whose `structured_output()` returns canned Pydantic
objects. Bundle assembly DOES reach the real catalogue MCP via stdio, so this
also confirms the catalogue → bundle path is wired correctly.

Run:
  python -m scudo.tests.smoke
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

# Force UTF-8 stdout on Windows so en/em dashes in outcome_reason don't blow up
# the cp1252 console encoder.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import uuid4

# Make both vendor_catalogue_mcp and scudo importable when run from backend dir.
from scudo import authoritative as scudo_authoritative
from scudo import sidecar as scudo_sidecar
from scudo.catalogue import catalogue_mcp_client, get_product_via_mcp
from scudo.orchestrator import Orchestrator
from scudo.schemas import (
    Band,
    BriefBundle,
    CandidateNode,
    ConflictRecord,
    Evidence,
    IntakeRequest,
    MappingResult,
    Outcome,
    PrecedentMapping,
    ProposedTriple,
    Route,
    VerifierDimension,
    VerifierReport,
    VerifierScore,
)
from scudo.stubs import InMemoryHitlQueue, InMemoryPublishSink, InMemoryResearchQueue


# ────────────────────────────────────────────────────────────────────────────
# Fake agents (no Strands, no Bedrock) — implement just `structured_output` and
# `__call__` so we can drive every orchestrator branch deterministically.
# ────────────────────────────────────────────────────────────────────────────
@dataclass
class _FakeAgentResult:
    """Mirrors strands.agent.agent_result.AgentResult — only the field the
    orchestrator reads (`structured_output`) is needed for the fake."""

    structured_output: Any = None


@dataclass
class FakeAgent:
    structured_responder: Callable[[type, str], Any] = None
    call_responder: Callable[[str], str] = None

    # Legacy API used by the smoke's direct calls (scenarios #7/#8).
    def structured_output(self, model_cls, prompt):
        if self.structured_responder is None:
            raise AssertionError(f"No structured_responder for {model_cls.__name__}")
        return self.structured_responder(model_cls, prompt)

    # Strands 1.42+ API used by the orchestrator: agent(prompt,
    # structured_output_model=...) returns an AgentResult-shaped object whose
    # `.structured_output` field is the Pydantic instance.
    def __call__(self, prompt=None, *, structured_output_model=None, **kwargs):
        if structured_output_model is not None:
            return _FakeAgentResult(
                structured_output=self.structured_output(
                    structured_output_model, prompt or ""
                )
            )
        if self.call_responder is None:
            return f"<fake-agent-response for {(prompt or '')[:40]!r}...>"
        return self.call_responder(prompt or "")


# ────────────────────────────────────────────────────────────────────────────
# Bundle assembler — reads from the real catalogue MCP, fakes the rest.
# ────────────────────────────────────────────────────────────────────────────
def make_bundle_assembler(mcp_client):
    """Build a BriefBundle assembler reading the catalogue MCP for the vendor
    product, the sidecar for candidates, and the authoritative graph for
    precedents / conflicts.

    Mode (real AWS vs in-memory mock) is decided inside scudo.sidecar and
    scudo.authoritative by their own env vars — this assembler is agnostic.
    """

    def _assemble(request: IntakeRequest, route: Route) -> BriefBundle:
        product = get_product_via_mcp(
            mcp_client, request.vendor, request.vendor_product_ref
        )

        # Candidate retrieval — lexical SIDECAR (non-authoritative).
        term = product.get("title") or request.vendor_product_ref
        candidate_dicts = scudo_sidecar.candidate_nodes(term=term, limit=10)
        candidates = [CandidateNode(**c) for c in candidate_dicts]

        # Precedent — AUTHORITATIVE graph; only when the intake flag is set.
        precedent: Optional[PrecedentMapping] = None
        if request.has_precedent:
            existing = scudo_authoritative.existing_mapping(
                vendor=request.vendor,
                vendor_product_ref=request.vendor_product_ref,
            )
            if existing:
                precedent = PrecedentMapping(**existing)

        # Conflicts — AUTHORITATIVE graph; only when the intake flag is set.
        conflict_list = []
        if request.has_conflict:
            for c in scudo_authoritative.conflicts(
                vendor_product_ref=request.vendor_product_ref
            ):
                conflict_list.append(ConflictRecord(**c))

        return BriefBundle(
            request=request,
            route=route,
            vendor_product_iri=product["iri"],
            vendor_assertion=product,
            candidates=candidates,
            precedent=precedent,
            conflicts=conflict_list,
            assembled_at=datetime(2026, 5, 19, 10, 0, 0, tzinfo=timezone.utc),
            bundle_ref=f"bundle-{uuid4()}",
        )

    return _assemble


# ────────────────────────────────────────────────────────────────────────────
# Verifier responders — fixed scores so we drive each gate branch.
# ────────────────────────────────────────────────────────────────────────────
def _verifier_with_total(total: int, rubric_version: str):
    """Distribute `total` across the 10 dimensions, capped at 2 each."""
    scores: list[VerifierScore] = []
    remaining = total
    for dim in VerifierDimension:
        s = min(2, remaining)
        remaining -= s
        scores.append(VerifierScore(dimension=dim, score=s))
    return VerifierReport(
        scores=scores, total_score=total, defects=[], rubric_version=rubric_version
    )


def mapping_responder(
    *,
    target_iri: str,
    confidence: float,
    requires_review: bool = False,
    include_evidence: bool = True,
):
    def _respond(model_cls, prompt):
        evidence = (
            [
                Evidence(
                    claim=f"vendor product maps to {target_iri}",
                    source_iris=[
                        target_iri,
                        "mds.lseg:9b911986-764b-529c-be8f-9744d86ec0b8",
                    ],
                    quote="estimates: I/B/E/S consensus estimates align with EquityResearch",
                ),
            ]
            if include_evidence
            else []
        )
        return MappingResult(
            vendor_product_iri="mds.lseg:9b911986-764b-529c-be8f-9744d86ec0b8",
            proposed_target_iri=target_iri,
            rationale="Bundle candidate #1 matches the vendor assertion on theme.",
            confidence=confidence,
            band=Band.for_confidence(confidence),
            requires_human_review=requires_review,
            evidence=evidence,
            proposed_triples=[
                ProposedTriple(
                    subject="mds.lseg:9b911986-764b-529c-be8f-9744d86ec0b8",
                    predicate="dcat:theme",
                    object=target_iri,
                    graph="jpmorgan:data:cdao:graphs:enrichment-2026-05-19",
                ),
            ],
        )

    return _respond


def verifier_responder(total: int, rubric_version: str):
    return lambda model_cls, prompt: _verifier_with_total(total, rubric_version)


# ────────────────────────────────────────────────────────────────────────────
# The driver
# ────────────────────────────────────────────────────────────────────────────
def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    rubric_version = "v1"
    ontology_snapshot = "cdao-2026-05-19"
    candidate_iri = "jpmorgan:data:cdao:EquityResearch"

    with catalogue_mcp_client() as mcp:
        # Probe — catalogue MCP must come up cleanly.
        tools = mcp.list_tools_sync()
        tool_names = [t.tool_name for t in tools]
        print(f"catalogue MCP tools: {tool_names}")
        assert "catalogue_get_product" in tool_names

        assembler = make_bundle_assembler(mcp)

        def _build(
            *, verifier_total: int, confidence: float, requires_review: bool = False
        ):
            mapping_agent = FakeAgent(
                structured_responder=mapping_responder(
                    target_iri=candidate_iri,
                    confidence=confidence,
                    requires_review=requires_review,
                ),
                call_responder=lambda p: "RESEARCH write-up (fake).",
            )
            verifier_agent = FakeAgent(
                structured_responder=verifier_responder(verifier_total, rubric_version)
            )
            return Orchestrator(
                mapping_specialist=mapping_agent,
                rights_specialist=None,
                verifier=verifier_agent,
                hitl_queue=InMemoryHitlQueue(),
                research_queue=InMemoryResearchQueue(),
                publish_sink=InMemoryPublishSink(),
                ontology_snapshot=ontology_snapshot,
                rubric_version=rubric_version,
                bundle_assembler=assembler,
            )

        # 1) High verifier + high confidence → PUBLISHED
        orch = _build(verifier_total=18, confidence=0.91)
        obj = orch.run(
            {
                "vendor": "lseg",
                "vendor_product_ref": "LSEG-IBES-EST-001",
                "has_precedent": False,
                "has_conflict": False,
                "ontology_gap": False,
            }
        )
        print(
            f"\n[1] route={obj.route.value} outcome={obj.outcome.value} reason={obj.outcome_reason}"
        )
        assert obj.outcome is Outcome.PUBLISHED
        assert obj.route is Route.NEW_MAPPING
        assert obj.published_graph == "jpmorgan:data:cdao:graphs:enrichment-2026-05-19"
        assert orch.publisher.published[0]["graph"] == obj.published_graph

        # 2) Verifier in 12–15 retry band → RETRY
        orch = _build(verifier_total=14, confidence=0.85)
        obj = orch.run(
            {
                "vendor": "lseg",
                "vendor_product_ref": "LSEG-IBES-EST-001",
                "has_precedent": True,
                "has_conflict": False,
                "ontology_gap": False,
            }
        )
        print(
            f"[2] route={obj.route.value} outcome={obj.outcome.value} reason={obj.outcome_reason}"
        )
        assert obj.outcome is Outcome.RETRY
        assert obj.route is Route.EXTEND_MAPPING

        # 3) Confidence below floor → HITL
        orch = _build(verifier_total=18, confidence=0.70)
        obj = orch.run(
            {
                "vendor": "lseg",
                "vendor_product_ref": "LSEG-IBES-EST-001",
                "has_precedent": False,
                "has_conflict": False,
                "ontology_gap": False,
            }
        )
        print(
            f"[3] route={obj.route.value} outcome={obj.outcome.value} reason={obj.outcome_reason}"
        )
        assert obj.outcome is Outcome.HITL
        assert obj.hitl_ticket and obj.hitl_ticket.startswith("HITL-")

        # 4) self-flag (requires_human_review=True) → HITL even with high scores
        orch = _build(verifier_total=20, confidence=0.95, requires_review=True)
        obj = orch.run(
            {
                "vendor": "lseg",
                "vendor_product_ref": "LSEG-IBES-EST-001",
                "has_precedent": False,
                "has_conflict": False,
                "ontology_gap": False,
            }
        )
        print(
            f"[4] route={obj.route.value} outcome={obj.outcome.value} reason={obj.outcome_reason}"
        )
        assert obj.outcome is Outcome.HITL

        # 5) ontology_gap → RESEARCH (never publishes)
        orch = _build(verifier_total=20, confidence=0.95)
        obj = orch.run(
            {
                "vendor": "lseg",
                "vendor_product_ref": "LSEG-IBES-EST-001",
                "has_precedent": False,
                "has_conflict": False,
                "ontology_gap": True,
            }
        )
        print(
            f"[5] route={obj.route.value} outcome={obj.outcome.value} reason={obj.outcome_reason}"
        )
        assert obj.route is Route.RESEARCH
        assert obj.outcome is Outcome.RESEARCH_QUEUED
        assert orch.publisher.published == [], "RESEARCH must never publish"

        # 6) has_conflict → RECONCILE_CONFLICT routing
        orch = _build(verifier_total=18, confidence=0.91)
        obj = orch.run(
            {
                "vendor": "lseg",
                "vendor_product_ref": "LSEG-IBES-EST-001",
                "has_precedent": False,
                "has_conflict": True,
                "ontology_gap": False,
            }
        )
        print(f"[6] route={obj.route.value} outcome={obj.outcome.value}")
        assert obj.route is Route.RECONCILE_CONFLICT

        # 7) Mapping result outside bundle.candidates → still publishable per
        #    score, but pre-verifier defects should surface (smoke that the
        #    orchestrator's pre-checks fire).
        from scudo.orchestrator import Orchestrator as O

        bad_iri = "jpmorgan:data:cdao:NotInBundle"
        mapping_agent = FakeAgent(
            structured_responder=mapping_responder(target_iri=bad_iri, confidence=0.95)
        )
        verifier_agent = FakeAgent(
            structured_responder=verifier_responder(20, rubric_version)
        )
        orch7 = O(
            mapping_specialist=mapping_agent,
            rights_specialist=None,
            verifier=verifier_agent,
            hitl_queue=InMemoryHitlQueue(),
            research_queue=InMemoryResearchQueue(),
            publish_sink=InMemoryPublishSink(),
            ontology_snapshot=ontology_snapshot,
            rubric_version=rubric_version,
            bundle_assembler=assembler,
        )
        bundle = assembler(
            IntakeRequest(vendor="lseg", vendor_product_ref="LSEG-IBES-EST-001"),
            Route.NEW_MAPPING,
        )
        result = mapping_agent.structured_output(MappingResult, "")
        defects = orch7._pre_verify_defects(result, bundle)
        print(f"[7] pre-verify defects on out-of-bundle IRI: {defects}")
        assert defects, "must surface out-of-bundle IRI as a defect"
        assert any("not in bundle.candidates" in d for d in defects)

        # 8) Verifier total recomputed if model returns inconsistent sum.
        from scudo.schemas import VerifierReport as VR

        bad_report = VR(
            scores=[VerifierScore(dimension=d, score=2) for d in VerifierDimension],
            total_score=5,  # inconsistent — should be 20
            defects=[],
            rubric_version=rubric_version,
        )
        # Build a one-off orchestrator and call the private path.
        v_agent = FakeAgent(structured_responder=lambda mc, p: bad_report)
        m_agent = FakeAgent(
            structured_responder=mapping_responder(
                target_iri=candidate_iri, confidence=0.95
            )
        )
        orch8 = O(
            mapping_specialist=m_agent,
            rights_specialist=None,
            verifier=v_agent,
            hitl_queue=InMemoryHitlQueue(),
            research_queue=InMemoryResearchQueue(),
            publish_sink=InMemoryPublishSink(),
            ontology_snapshot=ontology_snapshot,
            rubric_version=rubric_version,
            bundle_assembler=assembler,
        )
        result8 = m_agent.structured_output(MappingResult, "")
        bundle8 = assembler(
            IntakeRequest(vendor="lseg", vendor_product_ref="LSEG-IBES-EST-001"),
            Route.NEW_MAPPING,
        )
        report8 = orch8._call_verifier(result8, defects_pre=[], bundle=bundle8)
        print(f"[8] inconsistent verifier total recomputed: {report8.total_score}")
        assert report8.total_score == 20, (
            "orchestrator must recompute model's inconsistent total"
        )

        # 9) RejectRawQueryHook now catches openCypher signatures too.
        from scudo.hooks import _RAW_QUERY_MARKERS

        sentinel_inputs = [
            {"term": "MATCH (n) RETURN n"},  # raw cypher
            {"q": "MERGE (a {x:1})"},  # raw cypher
            {"sparql": "SELECT ?s WHERE { ?s ?p ?o }"},  # raw sparql
            {"turtle": "@prefix ex: <http://example/> ."},  # raw turtle
        ]
        hits = sum(
            1
            for inp in sentinel_inputs
            if any(m in repr(inp).lower() for m in _RAW_QUERY_MARKERS)
        )
        print(
            f"\n[9] raw-query markers caught {hits}/{len(sentinel_inputs)} sentinel payloads"
        )
        assert hits == len(sentinel_inputs)

        # 10) Mock facade actually supplied bundle candidates (proof the
        #     candidate retrieval path is in the loop, not bypassed).
        request = IntakeRequest(vendor="lseg", vendor_product_ref="LSEG-IBES-EST-001")
        sample_bundle = assembler(request, Route.NEW_MAPPING)
        print(f"[10] mock-backed bundle: {len(sample_bundle.candidates)} candidates")
        assert sample_bundle.candidates, "candidate retrieval returned no candidates"
        assert any("cdao" in c.iri.lower() for c in sample_bundle.candidates)

        # 11) Pre-verify defect: confidence > 0.5 with empty evidence is flagged.
        bad_evidence_responder = mapping_responder(
            target_iri=candidate_iri,
            confidence=0.85,
            include_evidence=False,
        )
        bad_result = bad_evidence_responder(MappingResult, "")
        orch_ev = _build(verifier_total=18, confidence=0.85)
        ev_defects = orch_ev._pre_verify_defects(bad_result, sample_bundle)
        print(f"[11] evidence-when-confident defects: {ev_defects}")
        assert any("evidence is empty" in d for d in ev_defects), (
            "evidence-when-confident invariant not enforced"
        )

        # 12) Band must match confidence — a mismatched band is flagged.
        from scudo.schemas import Band

        wrong_band = bad_evidence_responder(MappingResult, "").model_copy(
            update={
                "band": Band.LOW,
                "evidence": [Evidence(claim="x", source_iris=[candidate_iri])],
            },
        )
        band_defects = orch_ev._pre_verify_defects(wrong_band, sample_bundle)
        print(f"[12] band-mismatch defects: {band_defects}")
        assert any("disagrees with confidence" in d for d in band_defects)

        # 13) Gate-2 thresholds are per-instance: raising verifier_retry_hi to
        #     17 turns a verifier total of 16 — normally PUBLISHED — into a RETRY.
        mapping_agent_13 = FakeAgent(
            structured_responder=mapping_responder(
                target_iri=candidate_iri,
                confidence=0.91,
                requires_review=False,
            ),
            call_responder=lambda p: "RESEARCH write-up (fake).",
        )
        verifier_agent_13 = FakeAgent(
            structured_responder=verifier_responder(16, rubric_version)
        )
        orch_13 = Orchestrator(
            mapping_specialist=mapping_agent_13,
            rights_specialist=None,
            verifier=verifier_agent_13,
            hitl_queue=InMemoryHitlQueue(),
            research_queue=InMemoryResearchQueue(),
            publish_sink=InMemoryPublishSink(),
            ontology_snapshot=ontology_snapshot,
            rubric_version=rubric_version,
            bundle_assembler=assembler,
            verifier_retry_hi=17,
        )
        obj_13 = orch_13.run(
            {
                "vendor": "lseg",
                "vendor_product_ref": "LSEG-IBES-EST-001",
                "has_precedent": False,
                "has_conflict": False,
                "ontology_gap": False,
            }
        )
        print(
            f"[13] route={obj_13.route.value} outcome={obj_13.outcome.value} reason={obj_13.outcome_reason}"
        )
        assert obj_13.outcome is Outcome.RETRY, f"expected RETRY, got {obj_13.outcome}"
        assert obj_13.route is Route.NEW_MAPPING

    print("\nSCUDO SMOKE OK")


if __name__ == "__main__":
    main()
