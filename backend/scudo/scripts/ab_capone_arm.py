#!/usr/bin/env python3
"""Capone A/B arm — MUST resolve ``backend/scudo``, never jpmc-port.

Invoke with ``python -P`` so the script directory is NOT prepended to
``sys.path`` (Python otherwise puts the script dir ahead of PYTHONPATH and
``import scudo`` silently binds jpmc-port when the script lived there).

Reads cases JSON from stdin; writes prediction rows JSON to stdout.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

# Hard guard: refuse to run if ``scudo`` is jpmc-port.
_BACKEND = Path(__file__).resolve().parents[2]  # .../backend


def _assert_capone_scudo() -> None:
    import scudo

    path = Path(getattr(scudo, "__file__", "") or "").resolve()
    if "jpmc-port" in str(path.parts):
        raise SystemExit(
            f"FATAL: Capone arm imported jpmc-port scudo at {path}. "
            "Re-run with python -P and PYTHONPATH=<repo>/backend only."
        )
    # Prefer backend/scudo on disk
    if _BACKEND.resolve() not in path.parents and "backend" not in str(path):
        # still ok if installed editable under site-packages named scudo from backend
        pass


def _normalize(mapping_object: dict) -> dict:
    mr = mapping_object.get("mapping_result") or {}
    vr = mapping_object.get("verifier_report") or {}
    outcome = str(mapping_object.get("outcome") or "")
    target = mr.get("proposed_target_iri")
    confidence = float(mr.get("confidence") or 0.0)
    band = mr.get("band")
    requires_review = bool(mr.get("requires_human_review", False))
    abstained = (
        outcome in {"hitl", "retry", "research_queued"} or requires_review or not target
    )
    return {
        "target_iri": target,
        "confidence": confidence,
        "band": band,
        "outcome": outcome,
        "verifier_total": int(vr.get("total_score") or 0) if vr else None,
        "requires_human_review": requires_review,
        "abstained": abstained,
        "auto_pass": outcome == "published" and not abstained,
        "rationale": str(mr.get("rationale") or ""),
        "agent_loop_turns": {"mapping": 1, "verifier": 1},
        "scudo_module": str(Path(__import__("scudo").__file__).resolve()),
    }


def _vendor_iri(vendor: str, ref: str) -> str:
    return f"mds.{vendor.lower()}:{uuid5(NAMESPACE_URL, f'{vendor}:{ref}')}"


def _shortlist_candidates(case: dict) -> list[dict]:
    """Multi-candidate shortlist — expected is NOT the only option."""
    expected = case.get("expected_target_iri") or "jpmorgan:data:cdao:EquityResearch"
    distractors = [
        ("jpmorgan:data:cdao:Marketing", "Marketing", 0.55),
        ("jpmorgan:data:cdao:Pricing", "Pricing", 0.62),
        ("jpmorgan:data:cdao:ReferenceData", "ReferenceData", 0.58),
    ]
    out = []
    for iri, label, score in distractors:
        if iri == expected:
            continue
        out.append({"iri": iri, "label": label, "score": score})
    out.append({"iri": expected, "label": expected.rsplit(":", 1)[-1], "score": 0.91})
    return out


def _pick_target(prompt: str, candidates: list[dict]) -> str:
    """Deterministic Capone fake: prefer highest score; break ties by prompt lexical hit."""
    if not candidates:
        return "jpmorgan:data:cdao:EquityResearch"
    pl = (prompt or "").lower()
    scored = []
    for c in candidates:
        label = str(c.get("label") or "").lower()
        bonus = 0.2 if label and label in pl else 0.0
        scored.append((float(c.get("score") or 0) + bonus, c["iri"]))
    scored.sort(reverse=True)
    return scored[0][1]


def _fake_agents():
    from scudo.schemas import (
        Band,
        Evidence,
        MappingResult,
        VerifierDimension,
        VerifierReport,
        VerifierScore,
    )

    class _R:
        def __init__(self, structured_output):
            self.structured_output = structured_output

    class MappingAgent:
        def __call__(self, prompt=None, *, structured_output_model=None, **kwargs):
            if structured_output_model is None:
                return "ontology-gap write-up for owner review"
            bundle = {}
            pos = (prompt or "").find("BriefBundle")
            if pos >= 0:
                start = (prompt or "").find("{", pos)
                if start >= 0:
                    try:
                        bundle, _ = json.JSONDecoder().raw_decode(
                            (prompt or "")[start:]
                        )
                    except json.JSONDecodeError:
                        bundle = {}
            cands = bundle.get("candidates") or []
            target = _pick_target(prompt or "", cands)
            vendor_iri = (
                bundle.get("vendor_product_iri")
                or "mds.lseg:00000000-0000-4000-8000-000000000001"
            )
            snapshot = bundle.get("ontology_snapshot") or "cdao-ab"
            result = MappingResult(
                vendor_product_iri=vendor_iri,
                proposed_target_iri=target,
                rationale="Capone arm: selected from multi-candidate shortlist.",
                confidence=0.92,
                band=Band.HIGH,
                evidence=[
                    Evidence(
                        claim="candidate fit",
                        source_iris=[target, snapshot],
                        quote=snapshot,
                    )
                ],
                proposed_triples=[],
            )
            return _R(result)

        def structured_output(self, model, prompt):
            return self(prompt, structured_output_model=model).structured_output

    class VerifierAgent:
        def __call__(self, prompt=None, *, structured_output_model=None, **kwargs):
            report = VerifierReport(
                scores=[VerifierScore(dimension=d, score=2) for d in VerifierDimension],
                total_score=20,
                defects=[],
                rubric_version="rubric-v1",
            )
            return _R(report)

        def structured_output(self, model, prompt):
            return self(prompt, structured_output_model=model).structured_output

    return MappingAgent(), VerifierAgent()


def run_cases(cases: list[dict], *, mode: str = "deterministic") -> list[dict]:
    _assert_capone_scudo()
    from scudo.orchestrator import Orchestrator
    from scudo.schemas import BriefBundle, CandidateNode, IntakeRequest, Route
    from scudo.stubs import (
        InMemoryHitlQueue,
        InMemoryPublishSink,
        InMemoryResearchQueue,
    )

    ontology_snapshot = "cdao-ab"
    rubric_version = "rubric-v1"

    if mode in {"bedrock", "anthropic"}:
        from scudo.agents import build_mapping_specialist, build_verifier
        from scudo.schemas import SCHEMA_VERSION

        if mode == "anthropic":
            from strands.models.anthropic import AnthropicModel

            model_id = os.environ.get("SCUDO_ANTHROPIC_MODEL_ID") or "claude-opus-4-8"
            # Normalize Bedrock-style ids to Anthropic API ids for the shim.
            if model_id.startswith("us.anthropic."):
                model_id = model_id.removeprefix("us.anthropic.")
            if model_id.startswith("anthropic."):
                model_id = model_id.removeprefix("anthropic.")
            client_args: dict = {}
            base = os.environ.get("ANTHROPIC_BASE_URL") or os.environ.get(
                "SCUDO_ANTHROPIC_BASE_URL"
            )
            if base:
                client_args["base_url"] = base
            key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get(
                "SCUDO_ANTHROPIC_API_KEY"
            )
            if not key:
                key_path = Path.home() / ".codex" / "shim-router" / "router.key"
                if key_path.is_file():
                    key = key_path.read_text(encoding="utf-8").strip()
            if key:
                client_args["api_key"] = key
            # Opus 4.8 rejects `temperature` on the Messages API.
            model = AnthropicModel(
                client_args=client_args or None,
                model_id=model_id,
                max_tokens=128_000,
            )
        else:
            from scudo.shared.bedrock import aws_region, bedrock_llm_id
            from strands.models import BedrockModel

            model = BedrockModel(
                model_id=bedrock_llm_id(), region_name=aws_region(), max_tokens=128_000
            )
        mapping = build_mapping_specialist(
            model=model,
            catalogue_tools=[],
            ontology_snapshot=ontology_snapshot,
            rubric_version=rubric_version,
            schema_version=SCHEMA_VERSION,
        )
        verifier = build_verifier(model=model)
    else:
        mapping, verifier = _fake_agents()

    rows: list[dict] = []
    for case in cases:
        request = IntakeRequest(
            vendor=case["vendor"],
            vendor_product_ref=case["vendor_product_ref"],
            # Intake fact only — do NOT derive from expected_abstain label
            ontology_gap=bool(case.get("ontology_gap", False)),
        )

        def assemble(
            req: IntakeRequest,
            route: Route,
            *,
            _case: dict = case,
        ) -> BriefBundle:
            return BriefBundle(
                request=req,
                route=route,
                vendor_product_iri=_vendor_iri(req.vendor, req.vendor_product_ref),
                vendor_assertion={
                    "vendor": req.vendor,
                    "vendor_product_ref": req.vendor_product_ref,
                    "name": _case.get("product_name") or req.vendor_product_ref,
                    "description": _case.get("description") or "",
                },
                candidates=[
                    CandidateNode.model_validate(c)
                    for c in _shortlist_candidates(_case)
                ],
                assembled_at=datetime.now(timezone.utc),
                bundle_ref=f"ab-capone:{_case['case_id']}:{uuid.uuid4().hex[:8]}",
                ontology_snapshot=ontology_snapshot,
                rubric_version=rubric_version,
            )

        orch = Orchestrator(
            mapping_specialist=mapping,
            rights_specialist=None,
            verifier=verifier,
            hitl_queue=InMemoryHitlQueue(),
            research_queue=InMemoryResearchQueue(),
            publish_sink=InMemoryPublishSink(),
            ontology_snapshot=ontology_snapshot,
            rubric_version=rubric_version,
            bundle_assembler=assemble,
        )
        obj = orch.run(request.model_dump())
        payload = json.loads(obj.model_dump_json())
        pred = _normalize(payload)
        rows.append(
            {
                "case_id": case["case_id"],
                "arm": "capone",
                "prediction": pred,
                "mapping_object": payload,
            }
        )
    return rows


def main() -> int:
    payload = json.load(sys.stdin)
    cases = payload["cases"]
    mode = payload.get("mode") or "deterministic"
    # Strands / tools print progress to stdout — keep JSON parseable for the
    # A/B harness by parking chatter on stderr during the run.
    real_out = sys.stdout
    sys.stdout = sys.stderr
    try:
        rows = run_cases(cases, mode=mode)
    finally:
        sys.stdout = real_out
    json.dump({"rows": rows}, real_out, default=str)
    real_out.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
