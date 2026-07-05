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

import logging
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Callable, Optional

from .models import Candidate, VendorProductRef

SpecialistScorer = Callable[[VendorProductRef, list[Candidate]], Optional[Candidate]]

log = logging.getLogger("scudo.specialist")

# Hard ceiling for the synchronous specialist call (seconds). Kept well under a
# typical ALB 60s idle timeout. Override with SCUDO_SPECIALIST_TIMEOUT_S.
_TIMEOUT_S = float(os.getenv("SCUDO_SPECIALIST_TIMEOUT_S", "20"))


def _pool():
    """urllib3 PoolManager for the remote specialist backend (patched in tests)."""
    import urllib3  # type: ignore

    return urllib3.PoolManager()


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


def make_rest_specialist() -> SpecialistScorer:
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


def make_http_specialist() -> SpecialistScorer:
    """SpecialistScorer that delegates the borderline decision to a remote
    specialist service over HTTP (SCUDO_SPECIALIST_URL) — e.g. the Lambda
    orchestrator. POSTs the vendor ref + the anchored candidate IRIs and maps
    the returned IRI back onto that candidate set. Returns None (abstain) on any
    error/timeout, or when the service returns an off-list IRI — so the matcher's
    fail-safe borderline path and the anchored-candidate invariant both hold."""

    def specialist(
        ref: VendorProductRef, candidates: list[Candidate]
    ) -> Optional[Candidate]:
        import json

        url = os.getenv("SCUDO_SPECIALIST_REST_URL") or os.getenv(
            "SCUDO_SPECIALIST_URL"
        )
        if not url or not candidates:
            return None
        try:
            import urllib3  # type: ignore

            resp = _pool().request(
                "POST",
                url,
                body=json.dumps(
                    {
                        "vendor": ref.vendor,
                        "product_id": ref.product_id,
                        "name": ref.name,
                        "candidates": [c.node.iri for c in candidates],
                    }
                ).encode("utf-8"),
                headers={"content-type": "application/json"},
                timeout=urllib3.Timeout(total=_TIMEOUT_S),
            )
            if getattr(resp, "status", 200) >= 400:
                return None
            data = json.loads(resp.data.decode("utf-8"))
        except Exception:  # noqa: BLE001 — abstain on any remote failure
            return None

        chosen = {c.node.iri: c for c in candidates}.get(data.get("iri"))
        if chosen is None:
            return None  # off-list / missing pick -> abstain (fail-safe)
        conf = data.get("confidence")
        sim = float(conf) if isinstance(conf, (int, float)) else chosen.similarity
        sim = max(0.0, min(1.0, sim))
        return Candidate(node=chosen.node, similarity=sim)

    return specialist


def make_strands_specialist() -> SpecialistScorer:
    """SpecialistScorer backed by the in-process Zone-4 Strands specialist+
    verifier orchestrator. RESERVE: the cross-package in-process bridge is not
    wired into the mapping runtime yet, so this adapter uses DEFERRED OPTIONAL
    deps — it imports the bridge only when invoked and fail-soft ABSTAINS
    (returns None) if it is unavailable or errors, deferring to the matcher's
    borderline fallback rather than importing strands at module load or 500-ing
    the request. The public ``strands`` backend name is stable: drop in a
    ``strands_specialist.strands_pick(ref, candidates)`` module to light it up."""

    def specialist(
        ref: VendorProductRef, candidates: list[Candidate]
    ) -> Optional[Candidate]:
        try:
            from .strands_specialist import strands_pick  # optional; not built yet
        except Exception:  # noqa: BLE001 - optional bridge/deps may be absent
            log.debug("strands specialist bridge unavailable; abstaining")
            return None
        try:
            picked = strands_pick(ref, candidates)
            if picked is None:
                return None
            if picked.node.iri not in {c.node.iri for c in candidates}:
                return None
            return picked
        except Exception:  # noqa: BLE001 — abstain on any orchestrator failure
            return None

    return specialist


_BACKENDS = ("local", "strands", "rest")


def resolve_specialist(
    backend: Optional[str] = None, *, default: str = "local"
) -> Optional[SpecialistScorer]:
    """Select the borderline SpecialistScorer by name (env-overridable).

    ``SCUDO_SPECIALIST_BACKEND`` is used when ``backend`` is not supplied, so a
    deployment can switch the borderline arm with no code change:

      local   -> in-process opus_dense specialist (make_rest_specialist),
                 timeout-guarded, abstains on error (the default).
      strands -> in-process Strands specialist+verifier orchestrator
                 (make_strands_specialist); fail-soft reserve, abstains until the
                 bridge module is wired.
      rest    -> HTTP delegate to the orchestrator at SCUDO_SPECIALIST_URL
                 (make_http_specialist); abstains on missing URL / error /
                 off-list IRI.

    The off-list fail-closed invariant lives in matching.py and is untouched:
    every backend returns either an anchored candidate or None (abstain).
    """
    selected = backend if backend is not None else os.getenv("SCUDO_SPECIALIST_BACKEND")
    backend = (selected or default).strip().lower()
    if backend == "local":
        return make_rest_specialist()
    if backend == "strands":
        return make_strands_specialist()
    if backend == "rest":
        return make_http_specialist()
    raise ValueError(
        f"SCUDO_SPECIALIST_BACKEND={backend!r} unknown; expected one of {_BACKENDS}"
    )


def specialist_from_env() -> Optional[SpecialistScorer]:
    """Seam entry for call sites that default to NO specialist (the agent and
    MCP paths): returns the SCUDO_SPECIALIST_BACKEND-selected scorer when that
    env var is set, else None — preserving each site's deterministic default
    while still honouring an explicit backend choice. Flask's /map instead calls
    resolve_specialist() directly, keeping its make_rest_specialist default."""
    if not os.getenv("SCUDO_SPECIALIST_BACKEND"):
        return None
    return resolve_specialist()
