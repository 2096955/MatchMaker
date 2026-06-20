"""SCUDO batch self-verifying-loop smoke driver.

Exercises the BatchMatcher (self-verifying-loop) + the durable-runtime properties
(idempotent publish, checkpointed ledger, resume) with zero AWS/Bedrock and zero
catalogue MCP — a hermetic in-line bundle assembler + capturing fake agents drive
every branch deterministically.

Run from the backend dir:
  python -m scudo.tests.batch_smoke
"""

from __future__ import annotations

import logging
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from scudo.batch import BatchMatcher, unit_key
from scudo.orchestrator import Orchestrator
from scudo.schemas import (
    Band,
    BriefBundle,
    CandidateNode,
    Evidence,
    IntakeRequest,
    MappingResult,
    Outcome,
    ProposedTriple,
    Route,
    VerifierDimension,
    VerifierReport,
    VerifierScore,
)
from scudo.stubs import (
    IdempotentPublishSink,
    InMemoryHitlQueue,
    InMemoryLedgerStore,
    InMemoryPublishSink,
    InMemoryResearchQueue,
)

RUBRIC = "v1"
SNAPSHOT = "cdao-2026-05-19"
CANDIDATE_IRI = "jpmorgan:data:cdao:EquityResearch"
REATTEMPT_MARKER = "This is a re-attempt."


# ────────────────────────────────────────────────────────────────────────────
# Capturing fake agent — records call count + every prompt it received.
# ────────────────────────────────────────────────────────────────────────────
@dataclass
class _Result:
    structured_output: Any = None


@dataclass
class CapturingAgent:
    responder: Callable[[type, str], Any]
    calls: int = 0
    prompts: list[str] = field(default_factory=list)

    def structured_output(self, model_cls, prompt):
        return self.responder(model_cls, prompt)

    def __call__(self, prompt=None, *, structured_output_model=None, **kwargs):
        self.calls += 1
        self.prompts.append(prompt or "")
        if structured_output_model is not None:
            return _Result(
                self.structured_output(structured_output_model, prompt or "")
            )
        return "RESEARCH write-up (fake)."


# ────────────────────────────────────────────────────────────────────────────
# Hermetic bundle assembler — no MCP. One candidate (the target), version pins.
# ────────────────────────────────────────────────────────────────────────────
def _subject_for(ref: str) -> str:
    # Deterministic mds.<vendor>:<uuid> subject that matches _IRI_DETERMINISM.
    return f"mds.lseg:{uuid.uuid5(uuid.NAMESPACE_URL, ref)}"


def assembler(request: IntakeRequest, route: Route) -> BriefBundle:
    return BriefBundle(
        request=request,
        route=route,
        vendor_product_iri=_subject_for(request.vendor_product_ref),
        vendor_assertion={"title": request.vendor_product_ref},
        candidates=[
            CandidateNode(iri=CANDIDATE_IRI, label="Equity Research", score=0.9)
        ],
        precedent=None,
        conflicts=[],
        assembled_at=datetime(2026, 5, 19, 10, 0, 0, tzinfo=timezone.utc),
        bundle_ref=f"bundle-{request.vendor_product_ref}",
        ontology_snapshot=SNAPSHOT,
        rubric_version=RUBRIC,
    )


# ────────────────────────────────────────────────────────────────────────────
# Responders
# ────────────────────────────────────────────────────────────────────────────
def mapping_responder(
    known_refs: list[str], *, confidence: float, requires_review: bool = False
):
    """Return a unit-specific MappingResult so distinct units publish to distinct
    named graphs (otherwise the idempotent sink would treat them as replays)."""

    def _respond(model_cls, prompt):
        ref = next((r for r in known_refs if r in prompt), known_refs[0])
        subj = _subject_for(ref)
        graph = f"jpmorgan:data:cdao:graphs:enrichment-{ref}"
        return MappingResult(
            vendor_product_iri=subj,
            proposed_target_iri=CANDIDATE_IRI,
            rationale=f"Bundle candidate matches the vendor assertion for {ref}.",
            confidence=confidence,
            band=Band.for_confidence(confidence),
            requires_human_review=requires_review,
            evidence=[
                Evidence(
                    claim=f"{ref} maps to {CANDIDATE_IRI}",
                    source_iris=[CANDIDATE_IRI, subj],
                    quote=f"aligned per {SNAPSHOT}",
                )
            ],
            proposed_triples=[
                ProposedTriple(
                    subject=subj,
                    predicate="dcat:theme",
                    object=CANDIDATE_IRI,
                    graph=graph,
                )
            ],
        )

    return _respond


def verifier_with_total(total: int) -> VerifierReport:
    scores: list[VerifierScore] = []
    remaining = total
    for dim in VerifierDimension:
        s = min(2, remaining)
        remaining -= s
        scores.append(VerifierScore(dimension=dim, score=s))
    return VerifierReport(
        scores=scores, total_score=total, defects=[], rubric_version=RUBRIC
    )


def fixed_verifier(total: int):
    return lambda mc, p: verifier_with_total(total)


def stateful_verifier(totals: list[int]):
    """Return totals[0], totals[1], ... on successive calls; last value repeats."""
    state = {"i": 0}

    def _respond(mc, p):
        i = min(state["i"], len(totals) - 1)
        state["i"] += 1
        return verifier_with_total(totals[i])

    return _respond


def build_orchestrator(maker: CapturingAgent, verifier: CapturingAgent):
    inner = InMemoryPublishSink()
    sink = IdempotentPublishSink(inner)
    hitl = InMemoryHitlQueue()
    orch = Orchestrator(
        mapping_specialist=maker,
        rights_specialist=None,
        verifier=verifier,
        hitl_queue=hitl,
        research_queue=InMemoryResearchQueue(),
        publish_sink=sink,
        ontology_snapshot=SNAPSHOT,
        rubric_version=RUBRIC,
        bundle_assembler=assembler,
    )
    return orch, sink, inner, hitl


def _payload(ref: str, **flags) -> dict:
    return {
        "vendor": "lseg",
        "vendor_product_ref": ref,
        "has_precedent": flags.get("has_precedent", False),
        "has_conflict": flags.get("has_conflict", False),
        "ontology_gap": flags.get("ontology_gap", False),
    }


# ────────────────────────────────────────────────────────────────────────────
def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    # (a) all-pass — two units, both PUBLISHED on pass 1, distinct publishes.
    refs = ["LSEG-IBES-EST-001", "LSEG-PRICING-002"]
    maker = CapturingAgent(mapping_responder(refs, confidence=0.91))
    verifier = CapturingAgent(fixed_verifier(18))
    orch, sink, inner, _ = build_orchestrator(maker, verifier)
    led = BatchMatcher(orch, retry_budget=2).run_batch(
        [_payload(r) for r in refs], run_id="A"
    )
    assert set(led.passed) == {unit_key(_payload(r)) for r in refs}, led.passed
    assert not led.quarantined and not led.research, led
    assert len(led.passes) == 1, [
        p.pass_no for p in led.passes
    ]  # clean pass, early stop
    assert sink.publishes == 2 and sink.replays_deduped == 0, vars(sink)
    assert len(inner.published) == 2, inner.published
    print(
        f"[a] all-pass: passed={len(led.passed)} passes={len(led.passes)} publishes={sink.publishes}"
    )

    # (b) requeue-then-pass + reason threading + verifier-independence guard.
    maker = CapturingAgent(mapping_responder(["LSEG-IBES-EST-001"], confidence=0.91))
    verifier = CapturingAgent(stateful_verifier([14, 18]))  # RETRY then PUBLISH
    orch, sink, _, _ = build_orchestrator(maker, verifier)
    led = BatchMatcher(orch, retry_budget=2).run_batch(
        [_payload("LSEG-IBES-EST-001")], run_id="B"
    )
    assert led.passed == [unit_key(_payload("LSEG-IBES-EST-001"))], led.passed
    assert len(led.passes) == 2, [p.pass_no for p in led.passes]
    assert maker.calls == 2, maker.calls
    # The rejection reason rode back into the maker prompt on the retry…
    assert REATTEMPT_MARKER in maker.prompts[1], maker.prompts[1][:200]
    assert REATTEMPT_MARKER not in maker.prompts[0]
    # …and never leaked into the verifier prompt (independence preserved).
    assert all(REATTEMPT_MARKER not in p for p in verifier.prompts), (
        "reason leaked to verifier!"
    )
    print(
        f"[b] requeue→pass: passes={len(led.passes)} maker_calls={maker.calls} reason_threaded=True independence=OK"
    )

    # (c) never-passes → quarantined after budget, via the existing HITL queue.
    maker = CapturingAgent(mapping_responder(["LSEG-STUBBORN-003"], confidence=0.91))
    verifier = CapturingAgent(fixed_verifier(14))  # always retry band
    orch, sink, _, hitl = build_orchestrator(maker, verifier)
    led = BatchMatcher(orch, retry_budget=2).run_batch(
        [_payload("LSEG-STUBBORN-003")], run_id="C"
    )
    qkey = unit_key(_payload("LSEG-STUBBORN-003"))
    assert led.passed == [], led.passed
    assert [q.unit_key for q in led.quarantined] == [qkey], led.quarantined
    assert "retry budget exhausted" in led.quarantined[0].reason
    assert len(led.passes) == 2, [p.pass_no for p in led.passes]
    assert len(hitl.items) == 1 and "budget" in hitl.items[0]["reason"], hitl.items
    print(
        f"[c] never-pass: quarantined={len(led.quarantined)} passes={len(led.passes)} hitl_tickets={len(hitl.items)}"
    )

    # (d) self-flag → HITL → terminal quarantine immediately (no retry).
    maker = CapturingAgent(
        mapping_responder(["LSEG-FLAGGED-004"], confidence=0.91, requires_review=True)
    )
    verifier = CapturingAgent(fixed_verifier(20))  # high score, but maker self-flagged
    orch, sink, _, hitl = build_orchestrator(maker, verifier)
    led = BatchMatcher(orch, retry_budget=2).run_batch(
        [_payload("LSEG-FLAGGED-004")], run_id="D"
    )
    dkey = unit_key(_payload("LSEG-FLAGGED-004"))
    assert led.passed == [] and [q.unit_key for q in led.quarantined] == [dkey], led
    assert led.quarantined[0].last_outcome is Outcome.HITL, led.quarantined[0]
    assert len(led.passes) == 1, [p.pass_no for p in led.passes]  # no retry attempted
    assert maker.calls == 1, maker.calls
    assert sink.publishes == 0, vars(sink)  # self-flag never publishes
    print(
        f"[d] self-flag→HITL: quarantined={len(led.quarantined)} maker_calls={maker.calls} publishes={sink.publishes}"
    )

    # (e) resume same run_id → already-PUBLISHED unit skipped (no re-call, no re-publish).
    store = InMemoryLedgerStore()
    refs_e = ["LSEG-IBES-EST-001"]
    maker1 = CapturingAgent(mapping_responder(refs_e, confidence=0.91))
    orch1, sink1, _, _ = build_orchestrator(maker1, CapturingAgent(fixed_verifier(18)))
    led1 = BatchMatcher(orch1, ledger_store=store, retry_budget=2).run_batch(
        [_payload(r) for r in refs_e], run_id="E"
    )
    assert led1.passed == [unit_key(_payload(refs_e[0]))]
    assert sink1.publishes == 1 and maker1.calls == 1

    # Fresh orchestrator + fresh sink + fresh maker, SAME ledger store, resume=True.
    maker2 = CapturingAgent(mapping_responder(refs_e, confidence=0.91))
    orch2, sink2, _, _ = build_orchestrator(maker2, CapturingAgent(fixed_verifier(18)))
    led2 = BatchMatcher(orch2, ledger_store=store, retry_budget=2).run_batch(
        [_payload(r) for r in refs_e], run_id="E", resume=True
    )
    assert led2.passed == led1.passed, led2.passed
    assert maker2.calls == 0, f"resume re-called the maker: {maker2.calls}"
    assert sink2.publishes == 0, f"resume re-published: {vars(sink2)}"
    assert len(led2.passes) == 1, (
        "resume should add no new passes for a fully-terminal batch"
    )
    print(
        f"[e] resume: maker_calls_on_resume={maker2.calls} publishes_on_resume={sink2.publishes} (idempotent)"
    )

    print("\nSCUDO BATCH SMOKE OK")


if __name__ == "__main__":
    main()
