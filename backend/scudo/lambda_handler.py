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
import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from strands import Agent
from strands.models import BedrockModel

from . import aurora_memory
from .aws_resources import (
    env_resource_summary,
    put_audit_record,
    put_eventbridge_event,
    put_outbox_record,
    put_review_record,
)
from .orchestrator import Orchestrator
from .prompts import mapping_prompt, verifier_prompt  # noqa: F401  (kept for parity)
from .schemas import (
    BriefBundle,
    CandidateNode,
    ConflictRecord,
    IntakeRequest,
    MappingObject,
    Outcome,
    PrecedentMapping,
    Route,
    SCHEMA_VERSION,
)
from .shared.bedrock import aws_region, bedrock_llm_id
from .sidecar import mock as sidecar_mock
from .stubs import InMemoryHitlQueue, InMemoryPublishSink, InMemoryResearchQueue

log = logging.getLogger("scudo.lambda")
log.setLevel(logging.INFO)


_ONTOLOGY_SNAPSHOT = os.environ.get("SCUDO_ONTOLOGY_SNAPSHOT", "cdao-2026-05-19")
_RUBRIC_VERSION = os.environ.get("SCUDO_RUBRIC_VERSION", "v1")
_PROMPT_VERSION = os.environ.get("SCUDO_PROMPT_VERSION", "v1")
_MATCHER_VERSION = os.environ.get("SCUDO_MATCHER_VERSION", "orchestrator-agent-v1")


def _resp(status: int, body: Any) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=str),
    }


def _decision_publish_payload(payload: dict, ticket: str) -> dict:
    """Normalise a HITL approve body into the same outbox shape as auto-publish."""
    mapping_object = payload.get("mapping_object")
    if isinstance(mapping_object, dict):
        mapping_object = dict(mapping_object)
    else:
        mapping_result = payload.get("mapping_result")
        if not isinstance(mapping_result, dict):
            raise ValueError(
                "approve decision requires a mapping_object or mapping_result"
            )
        mapping_object = {
            "schema_version": SCHEMA_VERSION,
            "route": str(payload.get("route") or Route.NEW_MAPPING.value),
            "bundle_ref": str(payload.get("bundle_ref") or ticket),
            "mapping_result": mapping_result,
            "verifier_report": payload.get("verifier_report"),
            "outcome": "published",
            "outcome_reason": payload.get("outcome_reason")
            or "human approved decision",
            "published_graph": payload.get("published_graph"),
            "hitl_ticket": ticket,
            "invocation_pins": payload.get("invocation_pins") or {},
        }

    mapping_result = mapping_object.get("mapping_result")
    if not isinstance(mapping_result, dict):
        raise ValueError("approve decision requires mapping_object.mapping_result")

    mapping_object["outcome"] = "published"
    mapping_object["hitl_ticket"] = mapping_object.get("hitl_ticket") or ticket
    mapping_object["bundle_ref"] = mapping_object.get("bundle_ref") or str(
        payload.get("bundle_ref") or ticket
    )
    mapping_object["schema_version"] = (
        mapping_object.get("schema_version") or SCHEMA_VERSION
    )
    mapping_object["invocation_pins"] = mapping_object.get("invocation_pins") or {}
    if not mapping_object.get("published_graph"):
        triples = mapping_result.get("proposed_triples") or []
        if triples and isinstance(triples[0], dict):
            mapping_object["published_graph"] = triples[0].get("graph")

    return {**payload, "mapping_object": mapping_object}


def _check_api_key(event: dict) -> bool:
    expected = os.environ.get("API_KEY")
    if not expected:
        return True  # no key configured → open (PoC default)
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    presented = headers.get("x-api-key", "")
    return hmac.compare_digest(expected, presented)


def _serialise_record(rec: dict) -> dict:
    """Serialise a catalogue record to canonical RDF (DCAT) + adapted-ODRL rights
    via the rdf serialisers (never hand-rolled Turtle). Degrades to the raw
    payload if a serialiser is unavailable, so a read never 500s."""
    payload = rec.get("payload") or {}
    try:
        from .tools import rdf_serialise_mapping, rdf_serialise_rights

        return {
            "rdf": rdf_serialise_mapping(payload),
            "rights": rdf_serialise_rights(payload),
            "payload": payload,
        }
    except Exception:  # noqa: BLE001 — never fail a read on a serialiser hiccup
        log.exception("catalogue serialise failed for %s", rec.get("iri"))
        return {"payload": payload}


def _candidate_dicts(payload: dict, vendor_product: dict, term: str) -> list[dict]:
    """Draw candidates from a configured durable retrieval store when enabled."""
    truthy = ("1", "true", "yes", "on")
    backend = (os.environ.get("STORE_BACKEND") or "").strip().lower()
    neutral_flag = (
        os.environ.get("SCUDO_USE_RETRIEVAL_STORE") or ""
    ).strip().lower() in truthy
    legacy_flag = (os.environ.get("SCUDO_USE_FALKORDB") or "").strip().lower() in truthy
    use_store = neutral_flag or (backend == "falkordb" and legacy_flag)
    # Mock fallback must be EXPLICIT (Codex B7): a deployed stack must not
    # silently serve mock candidates when FalkorDB is enabled but failing —
    # that makes a broken store look like a successful real match. Only degrade
    # to mock when SCUDO_ALLOW_MOCK_FALLBACK is set; otherwise fail visibly.
    allow_mock = (
        os.environ.get("SCUDO_ALLOW_MOCK_FALLBACK") or ""
    ).strip().lower() in truthy
    if use_store:
        try:
            from .matcher_bridge import retrieval_store_ready, retrieve_candidates

            ready, reason = retrieval_store_ready()
            if not ready:
                raise RuntimeError(
                    f"{backend or 'retrieval store'} is not ready for Lambda "
                    f"candidate retrieval: {reason or 'unknown readiness failure'}"
                )

            vp = {
                "vendor": payload.get("vendor", ""),
                "product_id": payload.get("vendor_product_ref", ""),
                **(vendor_product or {}),
            }
            cands = retrieve_candidates(vp, term=term, limit=10)
            if cands:
                log.info("candidates: %d from %s", len(cands), backend)
                return cands
            if not allow_mock:
                raise RuntimeError(
                    f"{backend} returned 0 candidates and mock fallback is disabled "
                    "(set SCUDO_ALLOW_MOCK_FALLBACK=1 for a demo)."
                )
            log.warning("%s returned 0 candidates; falling back to mock", backend)
        except Exception as exc:
            if not allow_mock:
                log.error(
                    "%s retrieval failed (%s); NOT masking with mock", backend, exc
                )
                raise
            log.warning("%s retrieval failed (%s); falling back to mock", backend, exc)
    return sidecar_mock.candidate_nodes(term=term, limit=10)


# ────────────────────────────────────────────────────────────────────────────
# Canonical vendor-product IRI mint
#
# WHAT THIS REPLACES. This module used to mint the bundle's vendor IRI INLINE:
#
#     ns = uuid5(NAMESPACE_URL, "https://mds.jpmc.internal/catalogue")
#     vendor_iri = f"mds.{request.vendor}:{uuid5(ns, f'{vendor}:{ref}')}"
#
# Three defects in one line. It used a DIFFERENT namespace seed, a DIFFERENT key
# separator (single ':' vs the canonical '::') and interpolated the RAW vendor
# string as the slug — so an in-scope vendor name emitted
# ``mds.S&P Global:<uuid>``: a space and an ampersand inside the IRI. That
# violates the documented ``mds.<vendor>:<uuid5>`` convention
# (scudo_mapping_mcp/config.py) and is rejected outright by the orchestrator's
# publish gate (``orchestrator._IRI_DETERMINISM``).
#
# WHY IT MATTERS. The IRI is the MERGE key for the VendorProduct node
# (``MERGE (v:VendorProduct {iri:$v})`` in falkordb_store). Two mints for one
# logical product fork the NODE, which breaks the deterministic-identity
# invariant (I8) and the single-positive-precedent guarantee built on it.
# (Precedent LOOKUP itself keys on the (vendor, product_id) literals, not on the
# IRI, so reuse was never broken by this — the damage is node identity.)
#
# WHY THERE IS A REPLICATION AND NOT JUST AN IMPORT. ``scudo`` must import with
# no third-party deps and no network so the offline smoke
# (``python -m scudo.tests.smoke``) runs anywhere; ``scudo_mapping_mcp`` pulls in
# pydantic. matcher_bridge.py's docstring spells out that contract. So the
# canonical module is imported LAZILY and preferred when present, with a
# stdlib-only replication of the exact same algorithm as the fallback for a
# Lambda image that did not vendor the package. The replication is pinned
# byte-for-byte against ``models.mds_iri`` by
# ``backend/scudo/tests/test_iri_mint_parity.py`` so it cannot silently drift.
# ────────────────────────────────────────────────────────────────────────────

# Mirrors scudo_mapping_mcp.models._IRI_SEED and config.IRI_NAMESPACE. Changing
# either constant re-forks every IRI in the system; the parity test is the gate.
_IRI_SEED = uuid.UUID("6f2a9c4e-1d3b-4f8a-9c7e-2b5d8a1f4c63")
_IRI_NAMESPACE = "mds"


def _mds_iri_offline(vendor: str, product_id: str) -> str:
    """Stdlib-only replication of ``scudo_mapping_mcp.models.mds_iri``.

    MUST stay byte-identical to it — see test_iri_mint_parity.py. Do not "tidy"
    the strip/lower/replace order: it is the identity rule for every
    VendorProduct node in the store.
    """
    key = f"{vendor.strip().lower()}::{product_id.strip()}"
    u = uuid.uuid5(_IRI_SEED, key)
    slug = vendor.strip().lower().replace(" ", "").replace("&", "and")
    return f"{_IRI_NAMESPACE}.{slug}:{u}"


def _canonical_vendor_iri(vendor: str, vendor_product_ref: str) -> str:
    """Return the canonical ``mds.<slug>:<uuid5>`` IRI for a vendor product.

    Prefers the real ``scudo_mapping_mcp.models.mds_iri`` (single source of
    truth) via a LAZY import; falls back to the byte-identical stdlib
    replication when that package is not vendored into the runtime image. The
    fallback is safe precisely because parity is test-enforced — it is not a
    "best effort" degrade.
    """
    try:
        from scudo_mapping_mcp.models import mds_iri as _mds_iri
    except Exception:  # noqa: BLE001 — package not vendored / deps absent
        return _mds_iri_offline(vendor, vendor_product_ref)
    return _mds_iri(vendor, vendor_product_ref)


def _build_bundle_assembler(payload: dict):
    """Returns a callable(IntakeRequest, Route) -> BriefBundle that reads the
    vendor product from the request body and the candidates from FalkorDB (or
    the sidecar mock fallback), scoring against `candidates_term` or the title."""
    vendor_product = payload.get("vendor_product") or {}
    term = payload.get("candidates_term") or vendor_product.get("title", "")
    candidate_dicts = _candidate_dicts(payload, vendor_product, term)
    candidates = [CandidateNode(**c) for c in candidate_dicts]

    def _assemble(request: IntakeRequest, route: Route) -> BriefBundle:
        # Deterministic vendor IRI for the bundle — the CANONICAL mint, shared
        # with Runtime-B. See _canonical_vendor_iri below for why this is not
        # minted inline any more.
        vendor_iri = _canonical_vendor_iri(request.vendor, request.vendor_product_ref)

        # CONSULT — real Aurora-backed precedent (scudo.agent_memory), not the
        # fabricated canned mapping this used to invent whenever the caller
        # asserted has_precedent=True. has_precedent still independently
        # drives Route selection (orchestrator.py) — untouched here; this only
        # decides what the bundle's `precedent` PAYLOAD actually contains.
        # Fails open (aurora_memory.consult_priors never raises).
        priors = aurora_memory.consult_priors(
            vendor=request.vendor, vendor_product_ref=request.vendor_product_ref
        )
        precedent = None
        if priors.precedent:
            precedent = PrecedentMapping(
                source_iri=vendor_iri,
                target_iri=priors.precedent["target_iri"],
                rationale=priors.precedent.get("rationale"),
                confidence=priors.precedent["confidence"],
            )
        conflicts = []
        if request.has_conflict:
            conflicts.append(
                ConflictRecord(
                    other_vendor="spglobal",
                    other_vendor_product_ref="SPG-EQRES-77",
                    other_target_iri="jpmorgan:data:cdao:NewsAndResearch",
                    note="competing equivalent",
                )
            )
        # Part D — current best matching skill (SkillOpt-style), if one has
        # ever been promoted. Same CONSULT/fail-open pattern as precedent
        # above; None until skillopt_sleep_runner.py's offline gate promotes
        # a candidate. mapping_prompt() surfaces this prominently when set.
        best_skill = aurora_memory.consult_best_skill()
        skill_hint = best_skill["skill_text"] if best_skill else None
        skill_version = best_skill.get("version") if best_skill else None
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
            skill_hint=skill_hint,
            skill_version=skill_version,
            assembled_at=datetime.now(tz=timezone.utc),
            bundle_ref=f"lambda-{uuid4()}",
            ontology_snapshot=_ONTOLOGY_SNAPSHOT,
            rubric_version=_RUBRIC_VERSION,
        )

    return _assemble


def _record_precedent_if_published(
    obj: MappingObject,
    *,
    vendor: str,
    vendor_product_ref: str,
    source_content_hash: str | None = None,
    source_file_audit_id: str | None = None,
    input_snapshot: dict | None = None,
) -> None:
    """DISTILL — write a durable precedent on a verified auto-pass.

    Replaces InMemoryPublishSink()'s behaviour of evaporating every outcome at
    the end of the Lambda invocation. Only PUBLISHED outcomes are already-
    verified (verifier >= 16, confidence >= floor — orchestrator.py's publish
    gate) so no extra verification is needed here. Fails loud
    (aurora_memory.record_verified_precedent never swallows an error) — a
    lost precedent write defeats the entire point of this feature.
    """
    if obj.outcome != Outcome.PUBLISHED or obj.mapping_result is None:
        return
    aurora_memory.record_verified_precedent(
        vendor=vendor,
        vendor_product_ref=vendor_product_ref,
        target_iri=obj.mapping_result.proposed_target_iri,
        confidence=obj.mapping_result.confidence,
        rationale=obj.mapping_result.rationale,
        source_outcome_ref=obj.bundle_ref,
    )
    # Part D — SkillOpt-style rollout evidence for the offline harvest step
    # (skillopt_sleep_runner.py), alongside the precedent write above.
    aurora_memory.record_trajectory(
        bundle_ref=obj.bundle_ref,
        vendor=vendor,
        vendor_product_ref=vendor_product_ref,
        target_iri=obj.mapping_result.proposed_target_iri,
        confidence=obj.mapping_result.confidence,
        rationale=obj.mapping_result.rationale,
        outcome=obj.outcome.value,
        status="auto_mapped",
        band=obj.mapping_result.band.value,
        auto_pass=True,
        verifier_score=(
            obj.verifier_report.total_score if obj.verifier_report is not None else None
        ),
        matcher_version=_MATCHER_VERSION,
        ontology_snapshot=obj.invocation_pins.get(
            "ontology_snapshot", _ONTOLOGY_SNAPSHOT
        ),
        rubric_version=obj.invocation_pins.get("rubric_version", _RUBRIC_VERSION),
        prompt_version=_PROMPT_VERSION,
        skill_version=obj.invocation_pins.get("skill_version"),
        source_content_hash=source_content_hash,
        source_file_audit_id=source_file_audit_id,
        surface="agent",
        input_snapshot=input_snapshot or {},
        decision_snapshot={
            "mapping_result": obj.mapping_result.model_dump(mode="json"),
            "verifier_report": (
                obj.verifier_report.model_dump(mode="json")
                if obj.verifier_report is not None
                else None
            ),
            "invocation_pins": obj.invocation_pins,
        },
    )


class AzureOpenAIShim:
    def __init__(self, system_prompt: str, deployment_env_var: str):
        self.system_prompt = system_prompt
        self.deployment_env_var = deployment_env_var

    def __call__(self, prompt: str, structured_output_model: Any) -> Any:
        parsed_result = self.structured_output(structured_output_model, prompt)

        class ResultWrapper:
            def __init__(self, val):
                self.structured_output = val

        return ResultWrapper(parsed_result)

    def structured_output(self, output_model: Any, prompt: str) -> Any:
        from openai import AzureOpenAI

        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        api_key = os.environ.get("AZURE_OPENAI_API_KEY")
        api_version = os.environ.get("AZURE_OPENAI_API_VERSION")
        deployment = os.environ.get(self.deployment_env_var)

        if not endpoint or not api_key or not deployment:
            raise ValueError(
                "Missing Azure OpenAI configuration. "
                "Ensure AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, and "
                f"{self.deployment_env_var} are set."
            )

        client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
        )

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": prompt},
        ]

        reasoning_effort = os.environ.get("AZURE_OPENAI_REASONING_EFFORT", "medium")

        try:
            response = client.beta.chat.completions.parse(
                model=deployment,
                messages=messages,
                response_format=output_model,
                reasoning_effort=reasoning_effort,
            )
            return response.choices[0].message.parsed
        except Exception:
            response = client.beta.chat.completions.parse(
                model=deployment,
                messages=messages,
                response_format=output_model,
            )
            return response.choices[0].message.parsed


_AGENTS_CACHE: dict[str, tuple[Any, Any]] = {}


def _build_bedrock_agents() -> tuple[Agent, Agent]:
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
            "medium>=0.5, low<0.5. Leave proposed_triples empty; the "
            "orchestrator serialises deterministic DCAT triples."
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


def _build_azure_agents() -> tuple[AzureOpenAIShim, AzureOpenAIShim]:
    log.info("Building Azure OpenAI shims")
    # Named distinctly from the .prompts.mapping_prompt/verifier_prompt
    # imports above (kept for parity) — same names would shadow those
    # imports within this function (ruff F811).
    mapping_system_prompt = (
        "You are the SCUDO Mapping Specialist. Map ONE vendor product to "
        "ONE CDAO node from bundle.candidates. Cite at least one Evidence "
        "entry whose source_iris contain BOTH the chosen candidate IRI and "
        "the ontology_snapshot value. Set confidence_band: high>=0.8, "
        "medium>=0.5, low<0.5. Leave proposed_triples empty; the "
        "orchestrator serialises deterministic DCAT triples."
    )
    verifier_system_prompt = (
        "You are the SCUDO Verifier. Score MappingResult on the 10-"
        "dimension rubric (0/1/2 each). Do not redo the mapping; assess "
        "it. taxonomy_freshness=2 only if the ontology_snapshot appears "
        "in any Evidence entry."
    )

    mapping = AzureOpenAIShim(
        system_prompt=mapping_system_prompt,
        deployment_env_var="AZURE_OPENAI_SPECIALIST_DEPLOYMENT",
    )
    verifier = AzureOpenAIShim(
        system_prompt=verifier_system_prompt,
        deployment_env_var="AZURE_OPENAI_VERIFIER_DEPLOYMENT",
    )
    return mapping, verifier


def _get_agents_for_provider(provider: str) -> tuple[Any, Any]:
    global _AGENTS_CACHE
    if provider not in _AGENTS_CACHE:
        if provider == "azure":
            _AGENTS_CACHE[provider] = _build_azure_agents()
        else:
            _AGENTS_CACHE[provider] = _build_bedrock_agents()
    return _AGENTS_CACHE[provider]


def handler(event: dict, context: Any) -> dict:
    t0 = time.time()

    # Cheap health-check, no auth — for ALB/curl ping + cold-start warmup.
    path = (event.get("rawPath") or event.get("path") or "").rstrip("/")
    method = (
        event.get("requestContext", {}).get("http", {}).get("method")
        or event.get("httpMethod")
        or "POST"
    ).upper()
    if path.endswith("/health") and method == "GET":
        return _resp(
            200,
            {
                "ok": True,
                "model": bedrock_llm_id(),
                "region": aws_region(),
                "ontology_snapshot": _ONTOLOGY_SNAPSHOT,
                "rubric_version": _RUBRIC_VERSION,
                "resources": env_resource_summary(),
            },
        )

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

    # ── SCUDO catalogue API: approved records as canonical RDF + adapted-ODRL,
    #    and the HITL decision write. Consumers never touch Neptune directly. ──
    if path.endswith("/catalogue") and method == "GET":
        from . import catalogue

        return _resp(200, {"records": catalogue.list_approved()})

    if "/catalogue/" in path and method == "GET":
        from . import catalogue

        iri = path.split("/catalogue/", 1)[1]
        rec = catalogue.get_record(iri)
        if rec is None:
            return _resp(404, {"error": f"no catalogue record for {iri}"})
        return _resp(200, {"iri": iri, **_serialise_record(rec)})

    if path.endswith("/api/mapping/decision") and method == "POST":
        from . import catalogue

        ticket = payload.get("ticket")
        if not ticket:
            return _resp(400, {"error": "decision requires a ticket"})
        put_review_record(ticket=ticket, payload=payload)
        # An approved decision feeds the same publish path as an auto-approve:
        # the record lands in the catalogue and an outbox event drives projection.
        if payload.get("decision") == "approve":
            if not payload.get("iri"):
                return _resp(400, {"error": "approve decision requires an iri"})
            try:
                publish_payload = _decision_publish_payload(payload, ticket)
            except ValueError as e:
                return _resp(400, {"error": str(e)})
            catalogue.upsert_record(payload["iri"], publish_payload)
            put_outbox_record(
                event_id=f"{ticket}:approved",
                detail_type="MappingCompleted",
                detail=publish_payload,
            )
        return _resp(200, {"ok": True, "ticket": ticket})

    provider_default = (
        os.environ.get("SCUDO_AGENT_PROVIDER_DEFAULT", "bedrock").strip().lower()
    )
    agent_provider = (payload.get("agent_provider") or provider_default).strip().lower()
    # JPMC-LOCAL: "scripted" is deliberately NOT accepted here, unlike in
    # routes/mapping.py. This is the deployed Lambda path, and
    # _get_agents_for_provider() above sends every provider except "azure" to
    # _build_bedrock_agents(). Accepting "scripted" here would therefore call
    # Bedrock while telling the caller it ran the offline narrator -- worse
    # than the 400 it returns today, because it is silent. The local Flask
    # route is safe because get_agent() has a real "scripted" branch.
    if agent_provider not in ("bedrock", "azure"):
        return _resp(400, {"error": f"unknown agent provider: {agent_provider}"})

    try:
        IntakeRequest.model_validate(
            {
                "vendor": payload.get("vendor", ""),
                "vendor_product_ref": payload.get("vendor_product_ref", ""),
                "has_precedent": bool(payload.get("has_precedent", False)),
                "has_conflict": bool(payload.get("has_conflict", False)),
                "ontology_gap": bool(payload.get("ontology_gap", False)),
                "agent_provider": agent_provider,
            }
        )
    except Exception as e:
        return _resp(400, {"error": f"invalid intake: {e}"})

    mapping_agent, verifier_agent = _get_agents_for_provider(agent_provider)

    # The mapping specialist emits only the semantic decision. The orchestrator
    # serialises + stamps the named graph before the publish gate runs.
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
        obj = orch.run(
            {
                "vendor": payload["vendor"],
                "vendor_product_ref": payload["vendor_product_ref"],
                "has_precedent": bool(payload.get("has_precedent", False)),
                "has_conflict": bool(payload.get("has_conflict", False)),
                "ontology_gap": bool(payload.get("ontology_gap", False)),
                "agent_provider": agent_provider,
            }
        )
    except Exception as e:  # surface a clean error envelope to callers
        log.exception("orchestrator failed")
        return _resp(500, {"error": "orchestrator_failed", "detail": str(e)})

    mapping_object = json.loads(obj.model_dump_json())
    event_id = f"{obj.bundle_ref}:{obj.outcome.value}"
    audit_payload = {
        "request": {
            "vendor": payload["vendor"],
            "vendor_product_ref": payload["vendor_product_ref"],
        },
        "mapping_object": mapping_object,
    }
    # Audit EVERY outcome — the audit log is the full trail (event_type encodes
    # the outcome: MAPPING_PUBLISHED / MAPPING_HITL / MAPPING_RETRY / ...).
    put_audit_record(
        item_id=obj.bundle_ref,
        event_type=f"MAPPING_{obj.outcome.value.upper()}",
        payload=audit_payload,
    )
    # Only a genuinely PUBLISHED mapping is "completed": emit MappingCompleted
    # to the outbox + event bus (the async-projection path) ONLY then. HITL /
    # RETRY / RESEARCH must not masquerade as completed mappings to downstream
    # projection consumers — the HITL case is captured by the review record below.
    if obj.outcome.value == "published":
        put_outbox_record(
            event_id=event_id,
            detail_type="MappingCompleted",
            detail=audit_payload,
        )
        put_eventbridge_event(
            detail_type="MappingCompleted",
            detail={"event_id": event_id, **audit_payload},
        )
        # DISTILL — durable Aurora precedent for a verified auto-pass, replacing
        # InMemoryPublishSink's evaporate-on-return behaviour.
        _record_precedent_if_published(
            obj,
            vendor=payload["vendor"],
            vendor_product_ref=payload["vendor_product_ref"],
            source_content_hash=(payload.get("vendor_product") or {}).get(
                "source_content_hash"
            ),
            source_file_audit_id=(payload.get("vendor_product") or {}).get(
                "source_file_audit_id"
            ),
            input_snapshot=payload.get("vendor_product") or {},
        )
    if obj.hitl_ticket:
        put_review_record(ticket=obj.hitl_ticket, payload=audit_payload)

    return _resp(
        200,
        {
            "mapping_object": mapping_object,
            "execution_time_ms": int((time.time() - t0) * 1000),
        },
    )
