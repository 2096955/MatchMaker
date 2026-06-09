"""SCUDO Lambda handler — API Gateway HTTP API event in, MappingObject out.

Wraps the orchestrator so the entire SCUDO loop runs behind one HTTPS endpoint.
No catalogue MCP, no real Neptune — bundle is assembled from the request body
(intake fields + the vendor product blob) plus the in-memory CDAO sidecar mock.

Request shape (POST /run, JSON body):
    {
      "vendor": "lseg",
      "vendor_product_ref": "LSEG-IBES-EST-001",
      "vendor_product": {                          # NormalisedProduct fields
        "title": "I/B/E/S Estimates - Global Equities",
        "description": "...",
        "theme": "Investment Data",
        "asset_class": "Equities"
      },
      "has_precedent": false,
      "has_conflict": false,
      "ontology_gap": false,
      "candidates_term": "estimates"               # optional; defaults to title
    }

Response (200): MappingObject as JSON.
Auth: shared-secret header `x-api-key`, compared against API_KEY env var.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from strands import Agent
from strands.models import BedrockModel

from .orchestrator import Orchestrator
from .prompts import mapping_prompt, verifier_prompt  # noqa: F401  (kept for parity)
from .schemas import (
    BriefBundle, CandidateNode, ConflictRecord, IntakeRequest,
    PrecedentMapping, Route,
)
from .shared.bedrock import aws_region, bedrock_llm_id
from .sidecar import mock as sidecar_mock
from .stubs import InMemoryHitlQueue, InMemoryPublishSink, InMemoryResearchQueue

log = logging.getLogger("scudo.lambda")
log.setLevel(logging.INFO)


_ONTOLOGY_SNAPSHOT = os.environ.get("SCUDO_ONTOLOGY_SNAPSHOT", "cdao-2026-05-19")
_RUBRIC_VERSION = os.environ.get("SCUDO_RUBRIC_VERSION", "v1")


def _resp(status: int, body: Any) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=str),
    }


def _check_api_key(event: dict) -> bool:
    expected = os.environ.get("API_KEY")
    if not expected:
        return True  # no key configured → open (PoC default)
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    presented = headers.get("x-api-key", "")
    return hmac.compare_digest(expected, presented)


def _build_bundle_assembler(payload: dict):
    """Returns a callable(IntakeRequest, Route) -> BriefBundle that reads the
    vendor product from the request body and the candidates from the sidecar
    mock, scoring against either `candidates_term` or the product title."""
    vendor_product = payload.get("vendor_product") or {}
    term = payload.get("candidates_term") or vendor_product.get("title", "")
    candidate_dicts = sidecar_mock.candidate_nodes(term=term, limit=10)
    candidates = [CandidateNode(**c) for c in candidate_dicts]

    def _assemble(request: IntakeRequest, route: Route) -> BriefBundle:
        precedent = None
        if request.has_precedent:
            precedent = PrecedentMapping(
                source_iri=f"mds.{request.vendor}:placeholder",
                target_iri=(candidates[0].iri if candidates else
                            "jpmorgan:data:cdao:EquityResearch"),
                rationale="prior accepted mapping",
                confidence=0.88,
            )
        conflicts = []
        if request.has_conflict:
            conflicts.append(ConflictRecord(
                other_vendor="spglobal",
                other_vendor_product_ref="SPG-EQRES-77",
                other_target_iri="jpmorgan:data:cdao:NewsAndResearch",
                note="competing equivalent",
            ))
        # Synthesise a deterministic vendor IRI from (vendor, ref) for the bundle.
        from .schemas import IntakeRequest as _IR  # avoid F401 if unused
        from uuid import uuid5, NAMESPACE_URL
        ns = uuid5(NAMESPACE_URL, "https://mds.jpmc.internal/catalogue")
        vendor_iri = f"mds.{request.vendor}:{uuid5(ns, f'{request.vendor}:{request.vendor_product_ref}')}"
        return BriefBundle(
            request=request,
            route=route,
            vendor_product_iri=vendor_iri,
            vendor_assertion={
                "iri": vendor_iri,
                "vendor": request.vendor,
                "vendor_product_ref": request.vendor_product_ref,
                **vendor_product,
            },
            candidates=candidates,
            precedent=precedent,
            conflicts=conflicts,
            assembled_at=datetime.now(tz=timezone.utc),
            bundle_ref=f"lambda-{uuid4()}",
            ontology_snapshot=_ONTOLOGY_SNAPSHOT,
            rubric_version=_RUBRIC_VERSION,
        )
    return _assemble


def _build_agents() -> tuple[Agent, Agent]:
    model_id = bedrock_llm_id()
    region = aws_region()
    log.info("Bedrock model=%s region=%s", model_id, region)
    mapping_model = BedrockModel(model_id=model_id, region_name=region)
    verifier_model = BedrockModel(model_id=model_id, region_name=region)
    mapping = Agent(
        model=mapping_model,
        system_prompt=(
            "You are the SCUDO Mapping Specialist. Map ONE vendor product to "
            "ONE CDAO node from bundle.candidates. Cite at least one Evidence "
            "entry whose source_iris contain BOTH the chosen candidate IRI and "
            "the ontology_snapshot value. Set confidence_band: high>=0.8, "
            "medium>=0.5, low<0.5. Set proposed_triples=[]."
        ),
    )
    verifier = Agent(
        model=verifier_model,
        system_prompt=(
            "You are the SCUDO Verifier. Score MappingResult on the 10-"
            "dimension rubric (0/1/2 each). Do not redo the mapping; assess "
            "it. taxonomy_freshness=2 only if the ontology_snapshot appears "
            "in any Evidence entry."
        ),
    )
    return mapping, verifier


# Reused across warm invocations to dodge cold-start cost on every call.
_AGENTS: tuple[Agent, Agent] | None = None


def handler(event: dict, context: Any) -> dict:
    t0 = time.time()

    # Cheap health-check, no auth — for ALB/curl ping + cold-start warmup.
    path = (event.get("rawPath") or event.get("path") or "").rstrip("/")
    method = (event.get("requestContext", {}).get("http", {}).get("method")
              or event.get("httpMethod") or "POST").upper()
    if path.endswith("/health") and method == "GET":
        return _resp(200, {
            "ok": True,
            "model": bedrock_llm_id(),
            "region": aws_region(),
            "ontology_snapshot": _ONTOLOGY_SNAPSHOT,
            "rubric_version": _RUBRIC_VERSION,
        })

    if not _check_api_key(event):
        return _resp(401, {"error": "missing or invalid x-api-key"})

    raw_body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw_body = base64.b64decode(raw_body).decode("utf-8")
    try:
        payload = json.loads(raw_body) if isinstance(raw_body, str) else raw_body
    except json.JSONDecodeError as e:
        return _resp(400, {"error": f"invalid JSON body: {e}"})

    try:
        IntakeRequest.model_validate({
            "vendor": payload.get("vendor", ""),
            "vendor_product_ref": payload.get("vendor_product_ref", ""),
            "has_precedent": bool(payload.get("has_precedent", False)),
            "has_conflict": bool(payload.get("has_conflict", False)),
            "ontology_gap": bool(payload.get("ontology_gap", False)),
        })
    except Exception as e:
        return _resp(400, {"error": f"invalid intake: {e}"})

    global _AGENTS
    if _AGENTS is None:
        _AGENTS = _build_agents()
    mapping_agent, verifier_agent = _AGENTS

    # After the fake RDF serialiser landed, the orchestrator's mapping
    # specialist is told to leave proposed_triples empty; we serialise + stamp
    # the named graph from the orchestrator side, then the gate publishes.
    # The catalogue MCP is NOT in the loop — bundle is assembled in-process.
    orch = Orchestrator(
        mapping_specialist=mapping_agent,
        rights_specialist=None,
        verifier=verifier_agent,
        hitl_queue=InMemoryHitlQueue(),
        research_queue=InMemoryResearchQueue(),
        publish_sink=InMemoryPublishSink(),
        ontology_snapshot=_ONTOLOGY_SNAPSHOT,
        rubric_version=_RUBRIC_VERSION,
        bundle_assembler=_build_bundle_assembler(payload),
    )

    try:
        obj = orch.run({
            "vendor": payload["vendor"],
            "vendor_product_ref": payload["vendor_product_ref"],
            "has_precedent": bool(payload.get("has_precedent", False)),
            "has_conflict": bool(payload.get("has_conflict", False)),
            "ontology_gap": bool(payload.get("ontology_gap", False)),
        })
    except Exception as e:  # surface a clean error envelope to callers
        log.exception("orchestrator failed")
        return _resp(500, {"error": "orchestrator_failed", "detail": str(e)})

    return _resp(200, {
        "mapping_object": json.loads(obj.model_dump_json()),
        "execution_time_ms": int((time.time() - t0) * 1000),
    })
