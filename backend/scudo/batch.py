"""Durable, self-verifying batch matching over the per-product Orchestrator.

The Orchestrator (orchestrator.py) maps ONE vendor product: maker → independent
verifier → deterministic gate → Outcome. It has no batch path, no retry budget,
no quarantine. This module is the batch layer — the *self-verifying-loop* (plan →
execute → verify → requeue, loop until a pass rejects nothing or the retry budget
is hit, quarantine what never passes) wrapped around that orchestrator, with the
*loops* skill's durable properties: idempotent publish (via IdempotentPublishSink
at the sink the orchestrator was built with) and a checkpointed ledger + resume
here.

Why this is the right home for the pattern: SCUDO's per-unit claim — "vendor
product X maps to CDAO node Y" — is mechanically checkable (the verifier rubric +
the orchestrator's pre-verify defects + IRI determinism), and the verifier is
already structurally independent of the maker (a separate Strands agent, separate
context, sees only the result + rubric — see agents.build_verifier). That is the
regime where this loop buys a real guarantee, not just confident-sounding output.

Non-goals (v1): sequential, not concurrent (so no per-key race / double-publish);
the bundle is re-assembled each pass (a future optimisation could cache it).
"""

from __future__ import annotations

import logging
from typing import Optional

from .orchestrator import Orchestrator
from .schemas import BatchLedger, LedgerPass, Outcome, RejectedUnit
from .stubs import LedgerStore

log = logging.getLogger("scudo.batch")


def unit_key(payload: dict) -> str:
    """Collision-safe key for a request: NUL cannot appear in either field, so
    ('ab','c') and ('a','bc') can never collide."""
    return f"{payload['vendor']}\x00{payload['vendor_product_ref']}"


class BatchMatcher:
    """Run N mapping requests through the orchestrator as a self-verifying loop.

    Outcome → ledger mapping:
      • PUBLISHED       → passed     (terminal, verified clean)
      • RESEARCH_QUEUED → research   (terminal, escalated to ontology owner)
      • HITL            → quarantined(terminal, needs a human; orchestrator gave
                          it no retry path — floor breach / self-flag)
      • RETRY           → requeued with the reason, up to `retry_budget` passes;
                          on exhaustion the unit is enqueued to the *existing*
                          HITL queue and recorded as quarantined (never dropped).

    `retry_budget` defaults to 2: pass 1 is the initial attempt, pass 2 the single
    retry the orchestrator's own "retry once then HITL" comment intends. Raise it
    deliberately for a more persistent loop.

    Durability is supplied by the caller's wiring:
      • build the orchestrator with an IdempotentPublishSink → a replayed publish
        is a no-op;
      • pass a `ledger_store` → the ledger is checkpointed after every pass and a
        resumed run skips units already terminal (no re-call of the LLM).
    """

    def __init__(
        self,
        orchestrator: Orchestrator,
        *,
        ledger_store: Optional[LedgerStore] = None,
        retry_budget: int = 2,
    ) -> None:
        if retry_budget < 1:
            raise ValueError(f"retry_budget must be >= 1; got {retry_budget}")
        self.orchestrator = orchestrator
        self.ledger_store = ledger_store
        self.retry_budget = retry_budget

    def run_batch(
        self, payloads: list[dict], *, run_id: str, resume: bool = False
    ) -> BatchLedger:
        by_key = {unit_key(p): p for p in payloads}

        # Resume: reuse a prior ledger if asked and present; its terminal records
        # are the source of truth for what to skip (the loops checkpoint property).
        prior = (
            self.ledger_store.load(run_id)
            if resume and self.ledger_store is not None
            else None
        )
        if prior is not None:
            ledger = prior
            done = ledger.terminal_keys()
        else:
            ledger = BatchLedger(run_id=run_id, retry_budget=self.retry_budget)
            done = set()

        pending = {k: p for k, p in by_key.items() if k not in done}
        last_reason: dict[str, str] = {}
        existing_passes = len(ledger.passes)

        for i in range(self.retry_budget):
            if not pending:
                break
            pass_no = existing_passes + i + 1
            passed_n = 0
            requeued: dict[str, str] = {}
            rejected: list[RejectedUnit] = []

            for key, payload in pending.items():
                obj = self.orchestrator.run(
                    payload, prior_rejection=last_reason.get(key)
                )
                reason = obj.outcome_reason or obj.outcome.value
                if obj.outcome is Outcome.PUBLISHED:
                    ledger.passed.append(key)
                    passed_n += 1
                elif obj.outcome is Outcome.RESEARCH_QUEUED:
                    ledger.research.append(key)
                elif obj.outcome is Outcome.HITL:
                    ledger.quarantined.append(
                        RejectedUnit(
                            unit_key=key, reason=reason, last_outcome=obj.outcome
                        )
                    )
                elif obj.outcome is Outcome.RETRY:
                    requeued[key] = reason
                    rejected.append(
                        RejectedUnit(
                            unit_key=key, reason=reason, last_outcome=obj.outcome
                        )
                    )
                else:  # defensive: an unknown outcome is quarantined, never dropped
                    ledger.quarantined.append(
                        RejectedUnit(
                            unit_key=key,
                            reason=f"unhandled outcome {obj.outcome.value}",
                            last_outcome=obj.outcome,
                        )
                    )

            ledger.passes.append(
                LedgerPass(
                    pass_no=pass_no,
                    checked=len(pending),
                    passed=passed_n,
                    requeued=len(requeued),
                    rejected=rejected,
                )
            )
            if self.ledger_store is not None:
                self.ledger_store.save(run_id, ledger)
            log.info(
                "scudo batch run=%s pass=%d checked=%d passed=%d requeued=%d",
                run_id,
                pass_no,
                len(pending),
                passed_n,
                len(requeued),
            )

            # Next pass works only the requeued units, carrying their reasons back.
            last_reason = requeued
            pending = {k: by_key[k] for k in requeued}

        # Budget exhausted with units still requeued → quarantine through the
        # existing HITL queue. Reuse the human channel; never invent a parallel
        # one, and never drop the unit.
        for key, reason in last_reason.items():
            ticket = self.orchestrator.hitl.enqueue(
                mapping_result=None,
                verifier_report=None,
                reason=f"batch retry budget ({self.retry_budget}) exhausted: {reason}",
            )
            ledger.quarantined.append(
                RejectedUnit(
                    unit_key=key,
                    reason=f"retry budget exhausted ({self.retry_budget} passes); "
                    f"HITL {ticket}: {reason}",
                    last_outcome=Outcome.RETRY,
                )
            )
        if last_reason and self.ledger_store is not None:
            self.ledger_store.save(run_id, ledger)

        return ledger


__all__ = ["BatchMatcher", "unit_key"]
