"""Deterministic orchestrator.

The point of this file is: the LLM is in the *judgement* path, never the
*routing* or *publish* path. Routing is a rule on the intake payload. Publish
gating is a code-level check (verifier >= 16, confidence >= 0.80, RESEARCH-never-
publishes, deterministic IRI, named graph). Both run unconditionally in Python,
so a weaker/older model cannot route around them [hooks.md model portability].

The orchestrator depends on protocols (HitlQueue, ResearchQueue, PublishSink)
and on agents whose only public surface is `structured_output(Model, prompt)` —
so the smoke test injects fake agents without touching Strands or Bedrock.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import ValidationError

from .prompts import mapping_prompt, research_prompt, verifier_prompt
from .schemas import (
    BriefBundle,
    IntakeRequest,
    MappingObject,
    MappingResult,
    Outcome,
    Route,
    SCHEMA_VERSION,
    VerifierReport,
)
from .stubs import HitlQueue, PublishSink, ResearchQueue

log = logging.getLogger("scudo.orchestrator")

VERIFIER_AUTOPUBLISH = 16          # blueprint §9
VERIFIER_RETRY_LO, VERIFIER_RETRY_HI = 12, 15
CONFIDENCE_FLOOR = 0.80            # clarif. F31

# Only IRIs matching one of these patterns can flow through publish.
# `mds.<vendor>:<uuid>` for vendor products, `jpmorgan:data:cdao:` for CDAO nodes.
_IRI_DETERMINISM = re.compile(
    r"^(mds\.[a-z0-9_]+:[0-9a-f-]{36}|jpmorgan:data:cdao:.+)$"
)


class PublishGateError(RuntimeError):
    """Raised when a candidate MappingResult fails the deterministic gate.
    Caller routes the request to HITL; never bypasses the gate."""


class Orchestrator:
    def __init__(
        self,
        *,
        mapping_specialist: Any,
        rights_specialist: Optional[Any],
        verifier: Any,
        hitl_queue: HitlQueue,
        research_queue: ResearchQueue,
        publish_sink: PublishSink,
        ontology_snapshot: str,
        rubric_version: str,
        bundle_assembler=None,
    ) -> None:
        self.mapping = mapping_specialist
        self.rights = rights_specialist
        self.verifier = verifier
        self.hitl = hitl_queue
        self.research = research_queue
        self.publisher = publish_sink
        self.ontology_snapshot = ontology_snapshot
        self.rubric_version = rubric_version
        # bundle_assembler is a callable(IntakeRequest, Route) -> BriefBundle.
        # Smoke tests inject a fake; production wires the JPMC bundle pipeline.
        self._assemble_bundle_fn = bundle_assembler

    # ── routing — deterministic rule on intake flags ─────────────────────
    @staticmethod
    def route(request: IntakeRequest) -> Route:
        if request.ontology_gap:
            return Route.RESEARCH
        if request.has_conflict:
            return Route.RECONCILE_CONFLICT
        if request.has_precedent:
            return Route.EXTEND_MAPPING
        return Route.NEW_MAPPING

    # ── run — end to end ─────────────────────────────────────────────────
    def run(self, request_payload: dict) -> MappingObject:
        request = IntakeRequest.model_validate(request_payload)
        route = self.route(request)
        log.info("scudo route=%s vendor=%s vpr=%s",
                 route.value, request.vendor, request.vendor_product_ref)

        bundle = self._assemble_bundle(request, route)
        pins = {
            "ontology_snapshot": self.ontology_snapshot,
            "rubric_version": self.rubric_version,
            "schema_version": SCHEMA_VERSION,
            "route": route.value,
        }

        if route is Route.RESEARCH:
            return self._handle_research(bundle, pins)

        result = self._call_mapping(bundle)
        # Two cheap guards BEFORE the verifier — failing them never wastes a
        # verifier call. The verifier itself can still flag them via dimensions.
        defects_pre = self._pre_verify_defects(result, bundle)
        report = self._call_verifier(result, defects_pre=defects_pre, bundle=bundle)
        return self._gate_and_decide(route, bundle, result, report, pins)

    # ── bundle assembly ──────────────────────────────────────────────────
    def _assemble_bundle(self, request: IntakeRequest, route: Route) -> BriefBundle:
        if self._assemble_bundle_fn is None:
            raise NotImplementedError(
                "Orchestrator was constructed without a bundle_assembler. "
                "Inject one (catalogue MCP read + Neptune candidate retrieval + "
                "precedent + conflicts) before calling run()."
            )
        bundle = self._assemble_bundle_fn(request, route)
        # Backfill the version pins if the assembler omitted them so every
        # downstream call (mapping, verifier, MappingObject) has them.
        if not bundle.ontology_snapshot or not bundle.rubric_version:
            bundle = bundle.model_copy(update={
                "ontology_snapshot": bundle.ontology_snapshot or self.ontology_snapshot,
                "rubric_version": bundle.rubric_version or self.rubric_version,
            })
        try:
            BriefBundle.model_validate(bundle.model_dump())
        except ValidationError as e:
            raise ValueError(f"Bundle assembler returned an invalid BriefBundle: {e}")
        return bundle

    # ── agent calls ──────────────────────────────────────────────────────
    def _structured_call(self, agent: Any, output_model, prompt: str):
        """Strands 1.42+ structured-output pattern, with backward fallback
        for older fakes that only expose the deprecated .structured_output()
        method."""
        try:
            result = agent(prompt, structured_output_model=output_model)
        except TypeError:
            # Older fakes / Strands shims may not accept the kwarg yet.
            return agent.structured_output(output_model, prompt)
        # New API returns an AgentResult; unwrap the structured payload.
        return getattr(result, "structured_output", result)

    def _call_mapping(self, bundle: BriefBundle) -> MappingResult:
        return self._structured_call(self.mapping, MappingResult, mapping_prompt(bundle))

    def _call_verifier(self, result: MappingResult, *, defects_pre: list[str],
                       bundle: BriefBundle) -> VerifierReport:
        prompt = verifier_prompt(
            result,
            rubric_version=self.rubric_version,
            ontology_snapshot=bundle.ontology_snapshot,
        )
        if defects_pre:
            prompt += (
                "\n\nPre-verifier defects already detected by the orchestrator "
                "(set the matching dimension to 0 and surface them in `defects`):\n  - "
                + "\n  - ".join(defects_pre)
            )
        report = self._structured_call(self.verifier, VerifierReport, prompt)
        # Recompute total to guard against the model writing an inconsistent sum.
        recomputed = report.recompute_total()
        if recomputed != report.total_score:
            log.warning(
                "Verifier total %d inconsistent with sum %d — overriding.",
                report.total_score, recomputed,
            )
            report = report.model_copy(update={"total_score": recomputed})
        return report

    # ── pre-verifier orchestrator-side checks ───────────────────────────
    EVIDENCE_REQUIRED_ABOVE = 0.5  # taxonomy-mapping skill

    @staticmethod
    def _pre_verify_defects(result: MappingResult, bundle: BriefBundle) -> list[str]:
        defects: list[str] = []
        candidate_iris = {c.iri for c in bundle.candidates}
        if result.proposed_target_iri and candidate_iris and result.proposed_target_iri not in candidate_iris:
            defects.append(
                f"proposed_target_iri {result.proposed_target_iri!r} is not in "
                "bundle.candidates — specialists must select from the candidates."
            )
        # Evidence-when-confident: every confident assertion needs cited evidence.
        if result.confidence > Orchestrator.EVIDENCE_REQUIRED_ABOVE and not result.evidence:
            defects.append(
                f"confidence={result.confidence:.2f} exceeds the "
                f"{Orchestrator.EVIDENCE_REQUIRED_ABOVE} evidence-required floor "
                "but MappingResult.evidence is empty — evidence-free = hallucination."
            )
        for ev in result.evidence:
            if not ev.source_iris:
                defects.append(f"evidence entry {ev.claim!r} has no source_iris cited.")
        # Confidence band must match the actual confidence value.
        expected_band = result.band.for_confidence(result.confidence)
        if result.band is not expected_band:
            defects.append(
                f"confidence_band {result.band.value!r} disagrees with confidence "
                f"{result.confidence:.2f} (expected {expected_band.value!r})."
            )
        for t in result.proposed_triples:
            if not t.graph:
                defects.append("a proposed triple is missing its named graph (no provenance).")
            if not _IRI_DETERMINISM.match(t.subject):
                defects.append(f"non-deterministic triple subject IRI: {t.subject!r}")
        return defects

    # ── publish gate + outcome decision ─────────────────────────────────
    def _gate_and_decide(
        self,
        route: Route,
        bundle: BriefBundle,
        result: MappingResult,
        report: VerifierReport,
        pins: dict,
    ) -> MappingObject:
        total = report.total_score
        conf = result.confidence

        # Floor breach OR self-flag → HITL, no retry path.
        if total < VERIFIER_RETRY_LO or conf < CONFIDENCE_FLOOR or result.requires_human_review:
            reason = (
                f"floor breach (verifier={total}, confidence={conf:.2f}, "
                f"self_flag={result.requires_human_review})"
            )
            ticket = self.hitl.enqueue(mapping_result=result, verifier_report=report, reason=reason)
            return self._object(route, bundle, result, report, Outcome.HITL, reason, hitl_ticket=ticket, pins=pins)

        if VERIFIER_RETRY_LO <= total <= VERIFIER_RETRY_HI:
            # Single retry then HITL — the orchestrator stamps the retry marker
            # so the caller (or a calling workflow) can re-invoke run().
            return self._object(route, bundle, result, report, Outcome.RETRY,
                                outcome_reason=f"verifier {total} in 12–15 — retry once then HITL",
                                pins=pins)

        # total >= 16 and conf >= floor → attempt publish. Final, unconditional
        # invariant checks run again here so a malformed result cannot publish
        # even if the pre-verifier defects somehow slipped through.
        if route is Route.RESEARCH:
            raise PublishGateError("RESEARCH never publishes — bug in routing.")
        for t in result.proposed_triples:
            if not t.graph:
                raise PublishGateError("triple missing named graph")
            if not _IRI_DETERMINISM.match(t.subject):
                raise PublishGateError(f"non-deterministic IRI: {t.subject!r}")

        named_graph = self._named_graph_for(result)
        self.publisher.publish(
            named_graph=named_graph,
            triples=[t.model_dump() for t in result.proposed_triples],
        )
        return self._object(route, bundle, result, report, Outcome.PUBLISHED,
                            outcome_reason="verifier >= 16, confidence >= floor",
                            published_graph=named_graph, pins=pins)

    @staticmethod
    def _named_graph_for(result: MappingResult) -> str:
        """Pick the named graph from the proposed_triples — they must all share one."""
        graphs = {t.graph for t in result.proposed_triples}
        if not graphs:
            raise PublishGateError("MappingResult carries no triples to publish.")
        if len(graphs) > 1:
            raise PublishGateError(f"proposed_triples span multiple graphs: {sorted(graphs)}")
        return next(iter(graphs))

    # ── RESEARCH path: write up, queue for ontology owner, never publish ──
    def _handle_research(self, bundle: BriefBundle, pins: dict) -> MappingObject:
        # The mapping specialist doubles as the research writer for the v1
        # scaffold. Production will split this out per blueprint §4.2.
        writeup = self.mapping(research_prompt(bundle))
        ticket = self.research.enqueue(writeup=str(writeup), bundle_ref=bundle.bundle_ref)
        return self._object(
            route=Route.RESEARCH,
            bundle=bundle,
            mapping_result=None,
            verifier_report=None,
            outcome=Outcome.RESEARCH_QUEUED,
            outcome_reason="ontology gap — ontology-owner write-up queued",
            hitl_ticket=ticket,
            pins=pins,
        )

    # ── MappingObject builder ────────────────────────────────────────────
    @staticmethod
    def _object(
        route: Route,
        bundle: BriefBundle,
        mapping_result: Optional[MappingResult],
        verifier_report: Optional[VerifierReport],
        outcome: Outcome,
        outcome_reason: str,
        *,
        pins: dict,
        published_graph: Optional[str] = None,
        hitl_ticket: Optional[str] = None,
    ) -> MappingObject:
        return MappingObject(
            schema_version=SCHEMA_VERSION,
            route=route,
            bundle_ref=bundle.bundle_ref,
            mapping_result=mapping_result,
            verifier_report=verifier_report,
            outcome=outcome,
            outcome_reason=outcome_reason,
            published_graph=published_graph,
            hitl_ticket=hitl_ticket,
            invocation_pins=pins,
        )


__all__ = [
    "Orchestrator", "PublishGateError",
    "VERIFIER_AUTOPUBLISH", "VERIFIER_RETRY_LO", "VERIFIER_RETRY_HI", "CONFIDENCE_FLOOR",
]
