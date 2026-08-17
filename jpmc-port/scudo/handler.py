"""API handler — /health /run /catalogue /decision /project."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from . import aurora_memory, aws_resources, catalogue, local_state
from .agents import get_agents
from .orchestrator import Orchestrator
from .schemas import (
    BriefBundle,
    CandidateNode,
    IntakeRequest,
    Outcome,
    PrecedentMapping,
    Route,
)
from .sidecar import mock as sidecar_mock
from .stubs import InMemoryHitlQueue, InMemoryPublishSink, InMemoryResearchQueue

log = logging.getLogger("scudo.handler")

_ONTOLOGY_SNAPSHOT = os.environ.get("SCUDO_ONTOLOGY_SNAPSHOT", "cdao-2026-05-19")
_RUBRIC_VERSION = os.environ.get("SCUDO_RUBRIC_VERSION", "rubric-v1")
_VENDOR_NS = uuid5(NAMESPACE_URL, "https://mds.jpmc.internal/vendors")


def _resp(status: int, body: Any) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": body if isinstance(body, dict) else {"message": str(body)},
    }


def _check_api_key(headers: dict) -> bool:
    expected = os.environ.get("SCUDO_API_KEY", "local-dev-key")
    provided = (
        headers.get("x-api-key")
        or headers.get("X-Api-Key")
        or headers.get("X-API-KEY")
        or ""
    )
    return hmac.compare_digest(str(provided), str(expected))


def _vendor_iri(vendor: str, product_ref: str) -> str:
    return f"mds.{vendor.lower()}:{uuid5(_VENDOR_NS, f'{vendor}:{product_ref}')}"


def _candidate_dicts(vp: dict, term: str) -> list[dict]:
    if os.environ.get("SCUDO_USE_FALKORDB", "").strip() in {"1", "true", "yes"}:
        try:
            from .matcher_bridge import retrieve_candidates

            return retrieve_candidates(vp, term=term, limit=10)
        except Exception as exc:
            if os.environ.get("SCUDO_ALLOW_MOCK_FALLBACK", "").strip() not in {
                "1",
                "true",
                "yes",
            }:
                raise
            log.warning("FalkorDB failed; mock fallback: %s", exc)
    return sidecar_mock.candidate_nodes(term, limit=10)


def _build_bundle_assembler():
    def assemble(request: IntakeRequest, route: Route) -> BriefBundle:
        iri = _vendor_iri(request.vendor, request.vendor_product_ref)
        assertion = {
            "vendor": request.vendor,
            "vendor_product_ref": request.vendor_product_ref,
            "name": getattr(request, "name", None),
        }
        # intake may carry extra via raw dict — handler merges before validate
        term = str(
            assertion.get("name") or request.vendor_product_ref or request.vendor
        )
        raw_vp = {
            "vendor": request.vendor,
            "vendor_product_ref": request.vendor_product_ref,
            "name": term,
            "description": term,
        }
        cands = [
            CandidateNode.model_validate(c) for c in _candidate_dicts(raw_vp, term)
        ]
        from .zone_context import system_context_text

        skill_hint, skill_version = aurora_memory.consult_best_skill()
        teachings = aurora_memory.consult_teaching_notes(limit=20)
        if teachings:
            block = (
                "USER TEACHINGS (distilled from HITL — honour these on every turn):\n"
                f"{teachings}"
            )
            skill_hint = f"{skill_hint}\n\n{block}" if skill_hint else block
        priors = aurora_memory.consult_priors(
            vendor=request.vendor, vendor_product_ref=request.vendor_product_ref
        )
        precedent = None
        if priors.precedent:
            try:
                prec = dict(priors.precedent)
                prec.setdefault("source_iri", iri)
                precedent = PrecedentMapping.model_validate(prec)
            except Exception:
                precedent = None
        return BriefBundle(
            request=request,
            route=route,
            vendor_product_iri=iri,
            vendor_assertion=raw_vp,
            candidates=cands,
            precedent=precedent,
            skill_hint=skill_hint,
            skill_version=skill_version,
            promoted_rules=list(priors.rules),
            system_context=system_context_text(),
            assembled_at=datetime.now(timezone.utc),
            bundle_ref=f"bundle:{uuid.uuid4()}",
            ontology_snapshot=_ONTOLOGY_SNAPSHOT,
            rubric_version=_RUBRIC_VERSION,
        )

    return assemble


class _OutboxPublishSink:
    """Publish sink that also writes the transactional outbox."""

    def __init__(self, inner: InMemoryPublishSink) -> None:
        self.inner = inner

    def publish(self, *, named_graph: str, triples: list[dict]) -> str:
        self.inner.publish(named_graph=named_graph, triples=triples)
        event_id = hashlib.sha256(f"{named_graph}:{len(triples)}".encode()).hexdigest()[
            :32
        ]
        aws_resources.put_outbox_record(
            event_id=event_id,
            detail_type="MappingPublished",
            detail={"named_graph": named_graph, "triples": triples},
        )
        return named_graph


def _build_orchestrator() -> Orchestrator:
    mapping, verifier, rights, _catalogue = get_agents(
        ontology_snapshot=_ONTOLOGY_SNAPSHOT,
        rubric_version=_RUBRIC_VERSION,
    )
    hitl = InMemoryHitlQueue()
    research = InMemoryResearchQueue()
    sink = _OutboxPublishSink(InMemoryPublishSink())
    return Orchestrator(
        mapping_specialist=mapping,
        rights_specialist=rights,
        verifier=verifier,
        hitl_queue=hitl,
        research_queue=research,
        publish_sink=sink,
        ontology_snapshot=_ONTOLOGY_SNAPSHOT,
        rubric_version=_RUBRIC_VERSION,
        bundle_assembler=_build_bundle_assembler(),
    )


def _decision_publish_payload(decision: dict, mapping_object: dict) -> dict:
    """Normalise HITL approve into auto-publish outbox shape."""
    return {
        "named_graph": decision.get("named_graph")
        or mapping_object.get("published_graph"),
        "triples": decision.get("triples")
        or [
            t
            for t in (
                (mapping_object.get("mapping_result") or {}).get("proposed_triples")
                or []
            )
        ],
        "decision": "approve",
        "ticket": decision.get("ticket"),
    }


def _fill_catalogue(body: dict) -> dict:
    """Populate CatalogueOntology v0.1 fields via catalogue-fill specialist."""
    from .prompts import catalogue_fill_prompt
    from .zone_context import system_context_text

    vendor = str(body.get("vendor") or "").strip()
    ref = str(body.get("vendor_product_ref") or "").strip()
    if not vendor or not ref:
        raise ValueError("vendor and vendor_product_ref required")
    assertion = dict(body.get("vendor_assertion") or body)
    assertion.setdefault("vendor", vendor)
    assertion.setdefault("vendor_product_ref", ref)
    _, _, _, fill_agent = get_agents(
        ontology_snapshot=_ONTOLOGY_SNAPSHOT,
        rubric_version=_RUBRIC_VERSION,
    )
    prompt = catalogue_fill_prompt(
        vendor=vendor,
        vendor_product_ref=ref,
        vendor_assertion=assertion,
        system_context=system_context_text(),
    )
    from .catalogue_fill import CatalogueFillResult

    try:
        result = fill_agent(prompt, structured_output_model=CatalogueFillResult)
        payload = getattr(result, "structured_output", result)
    except TypeError:
        payload = fill_agent.structured_output(CatalogueFillResult, prompt)
    if hasattr(payload, "model_dump"):
        out = payload.model_dump(mode="json")
    else:
        out = dict(payload)
    aws_resources.put_audit_record(
        item_id=ref, event_type="catalogue_fill", payload=out
    )
    return out


def _run(body: dict) -> dict:
    # Allow name/description on intake without schema forbid failure
    name = body.pop("name", None)
    description = body.pop("description", None)
    orch = _build_orchestrator()
    obj = orch.run(body)
    payload = obj.model_dump(mode="json")
    aws_resources.put_audit_record(
        item_id=body.get("vendor_product_ref", "unknown"),
        event_type="run",
        payload=payload,
    )
    if obj.outcome is Outcome.PUBLISHED and obj.mapping_result:
        aurora_memory.record_verified_precedent(
            vendor=body["vendor"],
            vendor_product_ref=body["vendor_product_ref"],
            source_iri=obj.mapping_result.vendor_product_iri,
            target_iri=obj.mapping_result.proposed_target_iri,
            confidence=obj.mapping_result.confidence,
            rationale=obj.mapping_result.rationale,
        )
        aurora_memory.record_trajectory(
            vendor=body["vendor"],
            vendor_product_ref=body["vendor_product_ref"],
            mapping_result=obj.mapping_result,
            mapping_object=payload,
        )
        catalogue.upsert_record(
            obj.mapping_result.vendor_product_iri,
            {
                "vendor": body["vendor"],
                "vendor_product_ref": body["vendor_product_ref"],
                "target_iri": obj.mapping_result.proposed_target_iri,
                "name": name,
                "description": description,
                "status": "approved",
            },
        )
    return payload


def handle(event: dict) -> dict:
    path = (event.get("path") or "/").rstrip("/") or "/"
    method = (event.get("httpMethod") or "GET").upper()
    headers = event.get("headers") or {}
    body = event.get("body") or {}
    if isinstance(body, str):
        body = json.loads(body) if body else {}

    if path == "/health":
        from .shared.bedrock import aws_region, bedrock_llm_id

        mode = (os.environ.get("SCUDO_AGENT_MODE") or "").strip() or (
            "deterministic" if local_state.is_local() else "bedrock"
        )
        return _resp(
            200,
            {
                "ok": True,
                "agent_mode": mode,
                "model": bedrock_llm_id(),
                "region": aws_region(),
                "ontology_snapshot": _ONTOLOGY_SNAPSHOT,
                "rubric_version": _RUBRIC_VERSION,
                "resources": aws_resources.env_resource_summary(),
            },
        )

    if path in {"/run", "/api/mapping/run"} and method == "POST":
        if not _check_api_key(headers):
            return _resp(401, {"error": "unauthorized"})
        try:
            return _resp(200, _run(dict(body)))
        except Exception as exc:
            log.exception("run failed")
            return _resp(500, {"error": str(exc)})

    if path in {"/fill", "/api/catalogue/fill"} and method == "POST":
        if not _check_api_key(headers):
            return _resp(401, {"error": "unauthorized"})
        try:
            return _resp(200, _fill_catalogue(dict(body)))
        except Exception as exc:
            log.exception("catalogue fill failed")
            return _resp(500, {"error": str(exc)})

    if path in {"/catalogue", "/api/catalogue"} and method == "GET":
        return _resp(200, {"items": catalogue.list_approved()})

    if path in {"/api/mapping/decision", "/decision"} and method == "POST":
        if not _check_api_key(headers):
            return _resp(401, {"error": "unauthorized"})
        ticket = body.get("ticket") or ""
        aws_resources.put_review_record(ticket=ticket, payload=body)
        # Iron rule: every user teaching updates agent memory (fail-loud).
        try:
            learned = aurora_memory.learn_from_teaching(
                ticket=ticket,
                decision=str(body.get("decision") or ""),
                vendor=str(body.get("vendor") or ""),
                vendor_product_ref=str(body.get("vendor_product_ref") or ""),
                source_iri=str(body.get("source_iri") or ""),
                target_iri=str(body.get("target_iri") or body.get("iri") or ""),
                lesson=str(body.get("lesson") or body.get("rationale") or ""),
                confidence=float(body.get("confidence") or 1.0),
                mapping_object=body.get("mapping_object") or {},
            )
        except ValueError as exc:
            return _resp(400, {"error": str(exc), "learned": False})
        except Exception as exc:
            log.exception("learn_from_teaching failed")
            return _resp(500, {"error": str(exc), "learned": False})
        if body.get("decision") == "approve":
            detail = _decision_publish_payload(body, body.get("mapping_object") or {})
            aws_resources.put_outbox_record(
                event_id=hashlib.sha256(ticket.encode()).hexdigest()[:32],
                detail_type="MappingApproved",
                detail=detail,
            )
            approved_iri = body.get("target_iri") or body.get("iri")
            if learned.get("precedent_written") and approved_iri:
                catalogue.upsert_record(
                    approved_iri,
                    {
                        "vendor": body.get("vendor"),
                        "vendor_product_ref": body.get("vendor_product_ref"),
                        "target_iri": approved_iri,
                        "status": "approved",
                        "ticket": ticket,
                    },
                )
        return _resp(200, {"ok": True, "ticket": ticket, **learned})

    if path in {"/project", "/api/project"} and method == "POST":
        from .projection_handler import sweep_outbox

        result = sweep_outbox(limit=50)
        return _resp(200, result)

    return _resp(404, {"error": f"unknown path {path}"})


# Lambda entry
def lambda_handler(event, context=None):
    result = handle(event)
    return {
        **result,
        "body": json.dumps(result["body"], default=str),
    }
