"""A/B comparison: Capone ``backend/scudo`` vs ``jpmc-port`` agents.

Arms must run in separate processes (both packages are named ``scudo``).
This module holds pure compare logic + the in-process port arm.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5


def load_ab_cases(path: str | Path) -> list[dict]:
    cases: list[dict] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid golden JSON at {path}:{line_no}: {exc}"
                ) from exc
            if not row.get("case_id"):
                raise ValueError(f"missing case_id at {path}:{line_no}")
            cases.append(row)
    if not cases:
        raise ValueError(f"no cases in {path}")
    return cases


def normalize_prediction(mapping_object: dict) -> dict:
    """Normalize Capone/port MappingObject dumps for pairwise compare."""
    mr = mapping_object.get("mapping_result") or {}
    vr = mapping_object.get("verifier_report") or {}
    outcome = str(mapping_object.get("outcome") or "")
    target = mr.get("proposed_target_iri") or mr.get("mapped_node_iri")
    confidence = float(mr.get("confidence") or 0.0)
    band = mr.get("band")
    if hasattr(band, "value"):
        band = band.value
    requires_review = bool(mr.get("requires_human_review", False))
    abstained = (
        outcome in {"hitl", "retry", "research_queued"} or requires_review or not target
    )
    auto_pass = outcome == "published" and not abstained
    return {
        "target_iri": target,
        "confidence": confidence,
        "band": str(band) if band is not None else None,
        "outcome": outcome,
        "verifier_total": int(vr.get("total_score") or 0) if vr else None,
        "requires_human_review": requires_review,
        "abstained": abstained,
        "auto_pass": auto_pass,
        "rationale": str(mr.get("rationale") or ""),
        "agent_loop_turns": {
            "mapping": (mapping_object.get("invocation_pins") or {}).get(
                "mapping_loop_turns"
            ),
            "verifier": (mapping_object.get("invocation_pins") or {}).get(
                "verifier_loop_turns"
            ),
        },
    }


def pairwise_compare(rows: list[dict]) -> dict:
    """Compare aligned {case_id, capone, port} prediction dicts."""
    n = len(rows)
    if n == 0:
        return {
            "n_cases": 0,
            "target_agreement": 0.0,
            "outcome_agreement": 0.0,
            "mean_abs_confidence_delta": 0.0,
            "mean_verifier_total_delta": 0.0,
            "disagreements": [],
        }
    target_ok = outcome_ok = 0
    conf_deltas: list[float] = []
    ver_deltas: list[float] = []
    disagreements: list[dict] = []
    for row in rows:
        a = row["capone"]
        b = row["port"]
        reasons: list[str] = []
        if a.get("target_iri") == b.get("target_iri"):
            target_ok += 1
        else:
            reasons.append("target")
        if a.get("outcome") == b.get("outcome"):
            outcome_ok += 1
        else:
            reasons.append("outcome")
        conf_deltas.append(
            abs(float(a.get("confidence") or 0) - float(b.get("confidence") or 0))
        )
        va, vb = a.get("verifier_total"), b.get("verifier_total")
        if va is not None and vb is not None:
            ver_deltas.append(abs(float(va) - float(vb)))
        if reasons:
            disagreements.append(
                {
                    "case_id": row["case_id"],
                    "reasons": reasons,
                    "capone": a,
                    "port": b,
                }
            )
    return {
        "n_cases": n,
        "target_agreement": target_ok / n,
        "outcome_agreement": outcome_ok / n,
        "mean_abs_confidence_delta": sum(conf_deltas) / n,
        "mean_verifier_total_delta": (sum(ver_deltas) / len(ver_deltas))
        if ver_deltas
        else 0.0,
        "disagreements": disagreements,
    }


def score_vs_golden(cases: list[dict], predictions_by_id: dict[str, dict]) -> dict:
    """Lightweight exact-match / abstention metrics against golden labels.

    ``exact_match_rate`` is over positive (non-abstain) cases only.
    """
    n = exact = positive_n = abstain_ok = abstain_n = false_auto = 0
    for case in cases:
        pred = predictions_by_id.get(case["case_id"])
        if pred is None:
            continue
        n += 1
        expect_abstain = bool(case.get("expected_abstain"))
        if expect_abstain:
            abstain_n += 1
            if pred.get("abstained") or pred.get("outcome") == "research_queued":
                abstain_ok += 1
            if pred.get("auto_pass"):
                false_auto += 1
        else:
            positive_n += 1
            if pred.get("target_iri") == case.get("expected_target_iri"):
                exact += 1
            if pred.get("auto_pass") and pred.get("target_iri") != case.get(
                "expected_target_iri"
            ):
                false_auto += 1
    return {
        "n_scored": n,
        "n_positive": positive_n,
        "exact_match_rate": (exact / positive_n) if positive_n else 0.0,
        "abstention_recall": (abstain_ok / abstain_n) if abstain_n else 1.0,
        "false_auto_pass_rate": (false_auto / n) if n else 0.0,
    }


def _vendor_iri(vendor: str, ref: str) -> str:
    return f"mds.{vendor.lower()}:{uuid5(NAMESPACE_URL, f'{vendor}:{ref}')}"


def _shortlist_candidates(case: dict) -> list[dict]:
    """Multi-candidate shortlist — expected is not the only option.

    Deterministic arms must select by score/lexical fit; planting the golden
    IRI as the sole candidate makes exact_match tautological.
    """
    expected = case.get("expected_target_iri") or "jpmorgan:data:cdao:EquityResearch"
    distractors = [
        ("jpmorgan:data:cdao:Marketing", "Marketing", 0.55),
        ("jpmorgan:data:cdao:Pricing", "Pricing", 0.62),
        ("jpmorgan:data:cdao:ReferenceData", "ReferenceData", 0.58),
    ]
    out: list[dict] = []
    for iri, label, score in distractors:
        if iri == expected:
            continue
        out.append({"iri": iri, "label": label, "score": score})
    out.append({"iri": expected, "label": expected.rsplit(":", 1)[-1], "score": 0.91})
    return out


def run_port_arm(
    cases: list[dict],
    *,
    mode: str = "deterministic",
    ontology_snapshot: str = "cdao-ab",
    rubric_version: str = "rubric-v1",
) -> list[dict]:
    """In-process jpmc-port arm with shared multi-candidate shortlists."""
    from . import local_state
    from .agents import get_agents
    from .orchestrator import Orchestrator
    from .schemas import (
        BriefBundle,
        CandidateNode,
        IntakeRequest,
        Route,
    )
    from .stubs import InMemoryHitlQueue, InMemoryPublishSink, InMemoryResearchQueue

    local_state.reset()
    mapping, verifier, rights, _ = get_agents(
        ontology_snapshot=ontology_snapshot,
        rubric_version=rubric_version,
        mode=mode,
    )

    rows: list[dict] = []
    for case in cases:
        # Intake fact only — do NOT copy expected_abstain into ontology_gap.
        request = IntakeRequest(
            vendor=case["vendor"],
            vendor_product_ref=case["vendor_product_ref"],
            ontology_gap=bool(case.get("ontology_gap", False)),
        )

        def assemble(
            req: IntakeRequest,
            route: Route,
            *,
            _case: dict = case,
        ) -> BriefBundle:
            cands = [
                CandidateNode.model_validate(c) for c in _shortlist_candidates(_case)
            ]
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
                candidates=cands,
                assembled_at=datetime.now(timezone.utc),
                bundle_ref=f"ab-port:{_case['case_id']}:{uuid.uuid4().hex[:8]}",
                ontology_snapshot=ontology_snapshot,
                rubric_version=rubric_version,
            )

        orch = Orchestrator(
            mapping_specialist=mapping,
            rights_specialist=rights,
            verifier=verifier,
            hitl_queue=InMemoryHitlQueue(),
            research_queue=InMemoryResearchQueue(),
            publish_sink=InMemoryPublishSink(),
            ontology_snapshot=ontology_snapshot,
            rubric_version=rubric_version,
            bundle_assembler=assemble,
        )
        obj = orch.run(request.model_dump())
        payload = obj.model_dump(mode="json")
        if orch.last_mapping_loop:
            payload.setdefault("invocation_pins", {})["mapping_loop_turns"] = (
                orch.last_mapping_loop.turns
            )
        if orch.last_verifier_loop:
            payload.setdefault("invocation_pins", {})["verifier_loop_turns"] = (
                orch.last_verifier_loop.turns
            )
        rows.append(
            {
                "case_id": case["case_id"],
                "arm": "jpmc-port",
                "prediction": normalize_prediction(payload),
                "mapping_object": payload,
            }
        )
    return rows


def write_predictions_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, default=str) + "\n")


def merge_ab_report(
    *,
    cases: list[dict],
    capone_rows: list[dict],
    port_rows: list[dict],
    mode: str,
) -> dict:
    by_c = {r["case_id"]: r for r in capone_rows}
    by_p = {r["case_id"]: r for r in port_rows}
    paired = []
    for case in cases:
        cid = case["case_id"]
        if cid not in by_c or cid not in by_p:
            continue
        paired.append(
            {
                "case_id": cid,
                "capone": by_c[cid]["prediction"],
                "port": by_p[cid]["prediction"],
            }
        )
    pairwise = pairwise_compare(paired)
    return {
        "arm_a": "capone",
        "arm_b": "jpmc-port",
        "mode": mode,
        "n_cases": pairwise["n_cases"],
        "pairwise": pairwise,
        "capone_vs_golden": score_vs_golden(
            cases, {r["case_id"]: r["prediction"] for r in capone_rows}
        ),
        "port_vs_golden": score_vs_golden(
            cases, {r["case_id"]: r["prediction"] for r in port_rows}
        ),
        "architecture_notes": {
            "capone": (
                "backend/scudo Orchestrator via python -P "
                "backend/scudo/scripts/ab_capone_arm.py (must not import jpmc-port); "
                "deterministic FakeAgent or live Bedrock/Anthropic"
            ),
            "jpmc_port": (
                "agentic multi-turn loop + Mapping tools + investigative Verifier "
                "tools; teach→learn CONSULT; live Opus only when mode=anthropic|bedrock"
            ),
            "shortlist": (
                "multi-candidate with distractors; ontology_gap is an intake fact, "
                "not copied from expected_abstain"
            ),
        },
        "evidence_provenance": (
            "deterministic"
            if mode == "deterministic"
            else ("anthropic-opus" if mode == "anthropic" else "bedrock")
        ),
    }
