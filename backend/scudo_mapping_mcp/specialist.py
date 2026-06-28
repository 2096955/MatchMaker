"""Borderline specialist for the REST `/map` path (Codex A5).

The cost ladder's BORDERLINE band consults a ``SpecialistScorer`` —
``(ref, candidates) -> Optional[Candidate]`` — that re-scores the candidate set
and returns its single best pick (anchored to the surfaced candidates; it never
brings in off-list nodes). This adapter builds one from the existing
``opus_dense`` Bedrock scorer.

SAFETY: this runs INSIDE the synchronous REST request, so a Bedrock hiccup must
not 500 the route or hang past the ALB idle timeout. ``make_rest_specialist``
wraps the scorer with a hard timeout and returns ``None`` on any error/timeout —
which the gate treats as "specialist abstained" (fail-safe, not auto-map of a
hallucinated pick). Pair with the matcher's borderline-no-specialist policy.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Optional

from .models import Candidate, VendorProductRef

# Hard ceiling for the synchronous specialist call (seconds). Kept well under a
# typical ALB 60s idle timeout. Override with SCUDO_SPECIALIST_TIMEOUT_S.
_TIMEOUT_S = float(os.getenv("SCUDO_SPECIALIST_TIMEOUT_S", "20"))


def _best_pick(
    ref: VendorProductRef, candidates: list[Candidate]
) -> Optional[Candidate]:
    """Re-score each candidate with opus_dense and return the top one."""
    from .opus_dense import opus_dense_score

    if not candidates:
        return None
    best: Optional[Candidate] = None
    best_score = -1.0
    for c in candidates:
        score = opus_dense_score(
            query_label=ref.name or ref.product_id,
            query_desc=ref.description or "",
            candidate_label=c.node.label,
            candidate_desc="",  # taxonomy nodes carry no description today
        )
        if score > best_score:
            best_score = score
            best = Candidate(node=c.node, similarity=score)
    return best


def make_rest_specialist():
    """Return a timeout-and-error-guarded SpecialistScorer for REST `/map`.

    Returns ``None`` (abstain) on timeout or any exception, so the matcher's
    fail-safe borderline path takes over rather than the route erroring.
    """

    def specialist(
        ref: VendorProductRef, candidates: list[Candidate]
    ) -> Optional[Candidate]:
        # Do NOT use `with ThreadPoolExecutor(...)`: its __exit__ calls
        # shutdown(wait=True), which would re-block on a hung Bedrock call
        # right after the timeout fired — defeating the wall-clock guard. Manage
        # the executor manually and shut it down WITHOUT waiting on timeout, so
        # a slow specialist is truly abandoned (it can't hang past the ALB
        # idle timeout). The abandoned worker thread dies with the process.
        ex = ThreadPoolExecutor(max_workers=1)
        try:
            fut = ex.submit(_best_pick, ref, candidates)
            result = fut.result(timeout=_TIMEOUT_S)
            ex.shutdown(wait=False)
            return result
        except FutureTimeout:
            ex.shutdown(wait=False, cancel_futures=True)
            return None
        except Exception:  # noqa: BLE001 — abstain on any specialist failure
            ex.shutdown(wait=False, cancel_futures=True)
            return None

    return specialist
