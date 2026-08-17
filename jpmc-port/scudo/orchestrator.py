"""Deterministic orchestrator — LLM judges; Python routes and publish-gates."""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from pydantic import ValidationError

from .agent_loop import AgentLoopResult, run_agentic_structured
from .prompts import mapping_prompt, research_prompt, verifier_prompt
from .rdf.backend import serialise_mapping, validate_shapes
from .schemas import (
    BriefBundle,
    IntakeRequest,
    MappingObject,
    MappingResult,
    Outcome,
    ProposedTriple,
    Route,
    SCHEMA_VERSION,
    VerifierReport,
)
from .stubs import HitlQueue, PublishSink, ResearchQueue

log = logging.getLogger("scudo.orchestrator")

VERIFIER_AUTOPUBLISH = 16
VERIFIER_RETRY_LO, VERIFIER_RETRY_HI = 12, 15
CONFIDENCE_FLOOR = 0.80
EVIDENCE_REQUIRED_ABOVE = 0.5
_IRI_DETERMINISM = re.compile(
    r"^(mds\.[A-Za-z0-9_-]+:[0-9a-f-]{36}|jpmorgan:data:cdao:.+)$"
)


class PublishGateError(RuntimeError):
    pass


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
        verifier_retry_lo: int = VERIFIER_RETRY_LO,
        verifier_retry_hi: int = VERIFIER_RETRY_HI,
        confidence_floor: float = CONFIDENCE_FLOOR,
    ) -> None:
        self.mapping = mapping_specialist
        self.rights = rights_specialist
        self.verifier = verifier
        self.hitl = hitl_queue
        self.research = research_queue
        self.publisher = publish_sink
        self.ontology_snapshot = ontology_snapshot
        self.rubric_version = rubric_version
        self._assemble_bundle_fn = bundle_assembler
        self.verifier_retry_lo = verifier_retry_lo
        self.verifier_retry_hi = verifier_retry_hi
        self.confidence_floor = confidence_floor
        self.last_mapping_loop: Optional[AgentLoopResult] = None
        self.last_verifier_loop: Optional[AgentLoopResult] = None
        if not (0 <= self.verifier_retry_lo <= self.verifier_retry_hi <= 20):
            raise ValueError("invalid verifier retry band")
        if not (0.0 <= self.confidence_floor <= 1.0):
            raise ValueError("invalid confidence_floor")

    @staticmethod
    def route(request: IntakeRequest) -> Route:
        if request.ontology_gap:
            return Route.RESEARCH
        if request.has_conflict:
            return Route.RECONCILE_CONFLICT
        if request.has_precedent:
            return Route.EXTEND_MAPPING
        return Route.NEW_MAPPING

    def run(
        self, request_payload: dict, *, prior_rejection: str | None = None
    ) -> MappingObject:
        request = IntakeRequest.model_validate(request_payload)
        route = self.route(request)
        bundle = self._assemble_bundle(request, route)
        pins = {
            "ontology_snapshot": self.ontology_snapshot,
            "rubric_version": self.rubric_version,
            "schema_version": SCHEMA_VERSION,
            "route": route.value,
            "skill_version": bundle.skill_version,
        }
        if route is Route.RESEARCH:
            return self._handle_research(bundle, pins)
        result = self._call_mapping(bundle, prior_rejection=prior_rejection)
        result = self._ensure_serialised_triples(result, bundle)
        defects = self._pre_verify_defects(result, bundle)
        report = self._call_verifier(result, defects_pre=defects, bundle=bundle)
        return self._gate_and_decide(route, bundle, result, report, pins)

    def _assemble_bundle(self, request: IntakeRequest, route: Route) -> BriefBundle:
        if self._assemble_bundle_fn is None:
            raise NotImplementedError("bundle_assembler required")
        bundle = self._assemble_bundle_fn(request, route)
        if not bundle.ontology_snapshot or not bundle.rubric_version:
            bundle = bundle.model_copy(
                update={
                    "ontology_snapshot": bundle.ontology_snapshot
                    or self.ontology_snapshot,
                    "rubric_version": bundle.rubric_version or self.rubric_version,
                }
            )
        try:
            BriefBundle.model_validate(bundle.model_dump())
        except ValidationError as e:
            raise ValueError(f"invalid BriefBundle: {e}") from e
        return bundle

    def _agentic_call(self, agent: Any, output_model, prompt: str) -> AgentLoopResult:
        """Multi-turn tool+reasoning loop → validated structured output."""
        loop = run_agentic_structured(agent, prompt, output_model)
        log.info(
            "agentic loop agent=%s turns=%s tools=%s",
            type(agent).__name__,
            loop.turns,
            [c.get("name") for c in loop.tool_calls],
        )
        return loop

    def _call_mapping(
        self, bundle: BriefBundle, *, prior_rejection: str | None = None
    ) -> MappingResult:
        prompt = mapping_prompt(bundle)
        if prior_rejection:
            prompt += f"\n\nPrior rejection — fix:\n  - {prior_rejection}"
        loop = self._agentic_call(self.mapping, MappingResult, prompt)
        self.last_mapping_loop = loop
        return loop.output

    def _ensure_serialised_triples(
        self, result: MappingResult, bundle: BriefBundle
    ) -> MappingResult:
        if result.proposed_triples:
            return result
        try:
            payload = result.model_dump(mode="json")
            payload["ontology_snapshot"] = (
                bundle.ontology_snapshot or self.ontology_snapshot
            )
            serialised = serialise_mapping(payload)
            validation = validate_shapes(serialised.get("triples") or [])
            if not serialised.get("conforms") or not validation.get("conforms"):
                return result.model_copy(update={"requires_human_review": True})
            return result.model_copy(
                update={
                    "proposed_triples": [
                        ProposedTriple(**t) for t in serialised["triples"]
                    ]
                }
            )
        except Exception:
            log.exception("rdf serialisation failed")
            return result.model_copy(update={"requires_human_review": True})

    def _call_verifier(
        self, result: MappingResult, *, defects_pre: list[str], bundle: BriefBundle
    ) -> VerifierReport:
        prompt = verifier_prompt(
            result,
            rubric_version=self.rubric_version,
            ontology_snapshot=bundle.ontology_snapshot,
        )
        if defects_pre:
            prompt += "\n\nPre-verifier defects:\n  - " + "\n  - ".join(defects_pre)
        loop = self._agentic_call(self.verifier, VerifierReport, prompt)
        self.last_verifier_loop = loop
        report = loop.output
        recomputed = report.recompute_total()
        if recomputed != report.total_score:
            report = report.model_copy(update={"total_score": recomputed})
        return report

    @staticmethod
    def _pre_verify_defects(result: MappingResult, bundle: BriefBundle) -> list[str]:
        defects: list[str] = []
        iris = {c.iri for c in bundle.candidates}
        if (
            result.proposed_target_iri
            and iris
            and result.proposed_target_iri not in iris
        ):
            defects.append("proposed_target_iri not in bundle.candidates")
        if result.confidence > EVIDENCE_REQUIRED_ABOVE and not result.evidence:
            defects.append("confident mapping missing evidence")
        for ev in result.evidence:
            if not ev.source_iris:
                defects.append(f"evidence {ev.claim!r} has no source_iris")
        expected = result.band.for_confidence(result.confidence)
        if result.band is not expected:
            defects.append("confidence_band mismatch")
        for t in result.proposed_triples:
            if not t.graph:
                defects.append("triple missing named graph")
            if not _IRI_DETERMINISM.match(t.subject):
                defects.append(f"non-deterministic subject: {t.subject!r}")
        return defects

    def _gate_and_decide(self, route, bundle, result, report, pins) -> MappingObject:
        total, conf = report.total_score, result.confidence
        if (
            total < self.verifier_retry_lo
            or conf < self.confidence_floor
            or result.requires_human_review
        ):
            reason = (
                f"floor breach (verifier={total}, confidence={conf:.2f}, "
                f"self_flag={result.requires_human_review})"
            )
            ticket = self.hitl.enqueue(
                mapping_result=result, verifier_report=report, reason=reason
            )
            return self._object(
                route,
                bundle,
                result,
                report,
                Outcome.HITL,
                reason,
                hitl_ticket=ticket,
                pins=pins,
            )
        if self.verifier_retry_lo <= total <= self.verifier_retry_hi:
            return self._object(
                route,
                bundle,
                result,
                report,
                Outcome.RETRY,
                f"verifier {total} in retry band",
                pins=pins,
            )
        if route is Route.RESEARCH:
            raise PublishGateError("RESEARCH never publishes")
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
        return self._object(
            route,
            bundle,
            result,
            report,
            Outcome.PUBLISHED,
            f"verifier>{self.verifier_retry_hi}, confidence>={self.confidence_floor:.2f}",
            published_graph=named_graph,
            pins=pins,
        )

    @staticmethod
    def _named_graph_for(result: MappingResult) -> str:
        graphs = {t.graph for t in result.proposed_triples}
        if not graphs:
            raise PublishGateError("no triples")
        if len(graphs) > 1:
            raise PublishGateError(f"multiple graphs: {sorted(graphs)}")
        return next(iter(graphs))

    def _handle_research(self, bundle: BriefBundle, pins: dict) -> MappingObject:
        writeup = self.mapping(research_prompt(bundle))
        ticket = self.research.enqueue(
            writeup=str(writeup), bundle_ref=bundle.bundle_ref
        )
        return self._object(
            Route.RESEARCH,
            bundle,
            None,
            None,
            Outcome.RESEARCH_QUEUED,
            "ontology gap",
            hitl_ticket=ticket,
            pins=pins,
        )

    @staticmethod
    def _object(
        route,
        bundle,
        mapping_result,
        verifier_report,
        outcome,
        outcome_reason,
        *,
        pins,
        published_graph=None,
        hitl_ticket=None,
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
