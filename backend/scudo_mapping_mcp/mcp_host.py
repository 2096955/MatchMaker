"""
SCUDO MCP HOST — round-robin transport layer between the agent and the
three-MCP trust gradient.

CONTRACT (mirrors arb-review-pack scudo-overview.mmd)
-----------------------------------------------------
The arb-review-pack diagram is explicit:

    AGENT  ->  HOST  ->  { INGESTION, MATCH_VERIFY, PERSISTENCE }

Until WS-C lands, agent.py wires its four @tool callables straight to
local Python — Strands and ScriptedMappingAgent both go in-process. That
works for tests but it short-circuits the diagram: the HOST node is the
visibility surface the SCUDO platform owns, the per-tier circuit breaker
lives there, and round-robin across multiple replicas of a given MCP
endpoint is meaningless without it.

This module is that HOST. It is intentionally narrow:

  * Pure transport. No matcher logic, no scope-gate, no signing — those
    stay on the MCP servers themselves so the trust gradient is sacrosanct.
  * Tier-aware. Endpoints are grouped by trust tier
    (ingestion / match_verify / persistence) and round-robin happens
    within a tier, never across tiers.
  * Dependency-light. Uses requests (already in the backend's transitive
    closure) with a small wrapper. No httpx, no aiohttp, no tornado.
  * Opt-in. Agent.use_mcp_host=False is the default so every existing
    in-process call path (and the 86/86 smoke suite) keeps working.

DESIGN NOTES
------------
1. Round-robin is per-tier, monotonic counter. We deliberately do NOT
   weight by health — a degraded endpoint is taken out of rotation by
   the circuit breaker, not by a moving load-average.
2. Circuit breaker is a per-endpoint 3-strikes-and-you're-out: after
   three consecutive failures the endpoint goes OPEN for 30 seconds.
   On the next round-robin pick we cycle past OPEN endpoints. If every
   endpoint in a tier is OPEN we raise McpHostUnavailable so the agent
   can surface it (rather than retry forever or fall back silently).
3. Concurrency per tier is bounded by a semaphore (default 16). This
   keeps a misbehaving agent from queuing thousands of in-flight calls
   against the persistence MCP, which is the sole writer.
4. Visibility surface is McpHost.metrics() — a snapshot dict the SCUDO
   admin UI / /api/visibility/mcp-host endpoint serialises directly.
   Per-tier: calls_total, calls_failed, in_flight, breaker_state per
   endpoint.

This module SUPERSEDES the earlier asyncio MCPHost prototype that lived
here. The earlier shape (per-MCP single endpoint, async invoke()) was
useful for prototyping but the arb-review-pack diagram pins round-robin
across endpoint replicas inside a tier, and that pin is what this module
implements.
"""
from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional


# ──────────────────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────────────────


class McpHostError(RuntimeError):
    """Base exception for all McpHost transport failures."""


class McpHostUnavailable(McpHostError):
    """Raised when every endpoint in a tier is OPEN at pick time.

    Distinct from a transport error so the caller can distinguish
    "the network blipped on one call" from "the entire tier is down".
    """


class McpHostUnknownTier(McpHostError):
    """Raised when the agent asks for a tier the host wasn't configured with."""


# ──────────────────────────────────────────────────────────────────────────
# Endpoint state — one per URL, owned by McpHost
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class _EndpointState:
    """Per-endpoint mutable bookkeeping.

    Wrapped in a dataclass (not raw dict) so reasoning about the
    breaker state stays explicit and the metrics() snapshot can copy
    fields rather than serialise mutexes by accident.
    """

    url: str
    consecutive_failures: int = 0
    open_until_ts: float = 0.0        # 0.0 means CLOSED
    calls_total: int = 0
    calls_failed: int = 0
    in_flight: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def is_open(self, now: float) -> bool:
        return self.open_until_ts > now

    def breaker_state(self, now: float) -> str:
        # Tri-state surface for the visibility endpoint. half_open is the
        # exact moment the breaker has just expired but no probe call has
        # landed yet — informational only; the picker treats it as CLOSED.
        if self.open_until_ts == 0.0:
            return "closed"
        if self.open_until_ts > now:
            return "open"
        return "half_open"


# ──────────────────────────────────────────────────────────────────────────
# Tier state — one per trust tier
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class _TierState:
    """Per-tier bookkeeping.

    Holds the endpoint roster, a monotonic round-robin counter, and a
    semaphore that caps in-flight calls. The semaphore protects the
    persistence tier (sole writer) from agent runaways.
    """

    tier: str
    endpoints: List[_EndpointState]
    rr_counter: "itertools.count[int]" = field(default_factory=itertools.count)
    semaphore: threading.BoundedSemaphore = field(
        default_factory=lambda: threading.BoundedSemaphore(16)
    )


# ──────────────────────────────────────────────────────────────────────────
# McpHost
# ──────────────────────────────────────────────────────────────────────────


# Transport signature: callable(method, url, json=..., timeout=...) returning
# (status_code, body). Kept as a plain callable rather than a Protocol so
# tests can pass a lambda without subclassing.
Transport = Callable[..., Any]


class McpHost:
    """Round-robin transport host for the three-MCP trust gradient.

    Construct with the per-tier endpoint roster, e.g.

        host = McpHost(
            endpoints_by_tier={
                "ingestion":    ["http://localhost:8001/mcp/ingest"],
                "match_verify": [
                    "http://localhost:8002/mcp/match",
                    "http://localhost:8002b/mcp/match",
                ],
                "persistence":  ["http://localhost:8003/mcp/persist"],
                "normalise":    ["http://localhost:8004/mcp/normalise"],
            },
        )

    The agent calls host.call(tier, tool_name, args) and gets back the
    decoded JSON response. The host owns transport — retries, breaker,
    concurrency cap — but nothing semantic; the MCP servers stay
    authoritative for scope-gate / matcher / signing / persistence.
    """

    # Defaults — kept on the class so tests can override without touching
    # the constructor. The breaker / semaphore numbers are pinned in the
    # arb-review-pack; change them there first.
    DEFAULT_MAX_CONCURRENT_PER_TIER = 16
    DEFAULT_BREAKER_THRESHOLD = 3
    DEFAULT_BREAKER_COOLDOWN_S = 30.0
    DEFAULT_TIMEOUT_S = 10.0

    # "normalise" is the read-only Normalise & Calibrate MCP (:8004). It is
    # a utility tier, not part of the write-trust gradient: no signing key,
    # no write surface (TRUST_normalise_mcp_imports_no_writers pins this).
    KNOWN_TIERS = ("ingestion", "match_verify", "persistence", "normalise")

    def __init__(
        self,
        endpoints_by_tier: Dict[str, Iterable[str]],
        *,
        max_concurrent_per_tier: int = DEFAULT_MAX_CONCURRENT_PER_TIER,
        breaker_threshold: int = DEFAULT_BREAKER_THRESHOLD,
        breaker_cooldown_s: float = DEFAULT_BREAKER_COOLDOWN_S,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        transport: Optional[Transport] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._max_concurrent = max_concurrent_per_tier
        self._breaker_threshold = breaker_threshold
        self._breaker_cooldown_s = breaker_cooldown_s
        self._timeout_s = timeout_s
        # Injected so tests can stub transport without monkey-patching
        # requests, and so callers can plug an httpx.Client on demand.
        # The shape is: callable(method, url, json=..., timeout=...)
        # returning (status_code, parsed_json_or_text).
        self._transport: Transport = transport or _default_requests_transport
        self._clock: Callable[[], float] = clock or time.monotonic

        self._tiers: Dict[str, _TierState] = {}
        for tier, urls in endpoints_by_tier.items():
            urls_list = list(urls)
            # Empty tier is legal (e.g. dev-only setups with only
            # ingestion wired) but it surfaces as McpHostUnavailable
            # when you actually try to call it.
            self._tiers[tier] = _TierState(
                tier=tier,
                endpoints=[_EndpointState(url=u) for u in urls_list],
                semaphore=threading.BoundedSemaphore(max_concurrent_per_tier),
            )

    # ── Public API ────────────────────────────────────────────────────

    def call(
        self,
        tier: str,
        tool_name: str,
        args: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Invoke a tool on a tier endpoint, returning the decoded result.

        Picks an endpoint via round-robin, skipping any that are OPEN.
        Bounded by the per-tier semaphore. Any non-2xx response or
        transport exception counts as a failure for breaker bookkeeping;
        three consecutive failures opens the breaker for 30s.
        """
        tier_state = self._tier(tier)
        if not tier_state.endpoints:
            raise McpHostUnavailable(
                f"tier {tier!r} has no endpoints configured"
            )

        endpoint = self._pick_endpoint(tier_state)
        acquired = tier_state.semaphore.acquire(timeout=self._timeout_s)
        if not acquired:
            # Semaphore exhaustion is treated as a transport failure for
            # the picked endpoint so the breaker can react to overload,
            # but we do not block forever — callers see a clear error.
            raise McpHostError(
                f"tier {tier!r} semaphore exhausted "
                f"(>= {self._max_concurrent} in flight)"
            )

        with endpoint.lock:
            endpoint.in_flight += 1
            endpoint.calls_total += 1
        try:
            status, body = self._transport(
                method="POST",
                url=endpoint.url,
                json={"tool": tool_name, "args": args or {}},
                timeout=self._timeout_s,
            )
            if status < 200 or status >= 300:
                self._record_failure(endpoint)
                raise McpHostError(
                    f"{tier}:{endpoint.url} returned HTTP {status}: {body!r}"
                )
            self._record_success(endpoint)
            return body
        except McpHostError:
            raise
        except Exception as e:  # noqa: BLE001 — transport-agnostic
            self._record_failure(endpoint)
            raise McpHostError(
                f"{tier}:{endpoint.url} transport error: "
                f"{type(e).__name__}: {e}"
            ) from e
        finally:
            with endpoint.lock:
                endpoint.in_flight = max(0, endpoint.in_flight - 1)
            tier_state.semaphore.release()

    def metrics(self) -> Dict[str, Any]:
        """Return a JSON-safe snapshot for /api/visibility/mcp-host.

        Shape (per tier):
            {
              "calls_total":  int,
              "calls_failed": int,
              "in_flight":    int,
              "breaker_state": "closed" | "open" | "half_open",  # tier rollup
              "endpoints": [
                {"url": str, "breaker_state": ...,
                 "calls_total": int, "calls_failed": int, "in_flight": int}
              ]
            }

        Plus a top-level "config" block so the admin UI can show the
        live thresholds without a separate config fetch.

        The tier-level breaker_state is a rollup the brief asks for:
        "open" iff every endpoint in the tier is open, "half_open" iff at
        least one is half_open and none are closed, otherwise "closed".
        That gives the SCUDO dashboard a single coloured pill per tier
        and the per-endpoint detail underneath.
        """
        now = self._clock()
        out: Dict[str, Any] = {
            "enabled": True,
            "config": {
                "max_concurrent_per_tier": self._max_concurrent,
                "breaker_threshold": self._breaker_threshold,
                "breaker_cooldown_s": self._breaker_cooldown_s,
                "timeout_s": self._timeout_s,
            },
            "tiers": {},
        }
        for tier, state in self._tiers.items():
            endpoints_view: List[Dict[str, Any]] = []
            t_total = 0
            t_failed = 0
            t_in_flight = 0
            ep_states: List[str] = []
            for ep in state.endpoints:
                with ep.lock:
                    ep_state_str = ep.breaker_state(now)
                    endpoints_view.append({
                        "url": ep.url,
                        "breaker_state": ep_state_str,
                        "calls_total": ep.calls_total,
                        "calls_failed": ep.calls_failed,
                        "in_flight": ep.in_flight,
                    })
                    t_total += ep.calls_total
                    t_failed += ep.calls_failed
                    t_in_flight += ep.in_flight
                    ep_states.append(ep_state_str)
            out["tiers"][tier] = {
                "calls_total": t_total,
                "calls_failed": t_failed,
                "in_flight": t_in_flight,
                "breaker_state": _rollup_breaker_state(ep_states),
                "endpoints": endpoints_view,
            }
        return out

    # ── Internals ─────────────────────────────────────────────────────

    def _tier(self, tier: str) -> _TierState:
        try:
            return self._tiers[tier]
        except KeyError as e:
            raise McpHostUnknownTier(
                f"tier {tier!r} not configured; "
                f"known tiers: {sorted(self._tiers)}"
            ) from e

    def _pick_endpoint(self, tier_state: _TierState) -> _EndpointState:
        """Round-robin pick, skipping endpoints whose breaker is OPEN.

        We walk at most len(endpoints) positions starting from the
        round-robin cursor. If every endpoint is OPEN we raise
        McpHostUnavailable; the agent surfaces it to the reviewer queue
        rather than retrying blindly.
        """
        now = self._clock()
        n = len(tier_state.endpoints)
        for _ in range(n):
            idx = next(tier_state.rr_counter) % n
            candidate = tier_state.endpoints[idx]
            if not candidate.is_open(now):
                return candidate
        raise McpHostUnavailable(
            f"tier {tier_state.tier!r} fully open — all {n} endpoint(s) "
            f"tripped their breaker"
        )

    def _record_success(self, endpoint: _EndpointState) -> None:
        with endpoint.lock:
            endpoint.consecutive_failures = 0
            endpoint.open_until_ts = 0.0

    def _record_failure(self, endpoint: _EndpointState) -> None:
        with endpoint.lock:
            endpoint.consecutive_failures += 1
            endpoint.calls_failed += 1
            if endpoint.consecutive_failures >= self._breaker_threshold:
                endpoint.open_until_ts = (
                    self._clock() + self._breaker_cooldown_s
                )

    # ── Test seam ─────────────────────────────────────────────────────

    def _force_open(self, tier: str, url: str) -> None:
        """Test helper — trip the breaker on a specific endpoint.

        Underscore-prefixed because production code has no business
        doing this; the smoke tests use it to assert round-robin skips
        OPEN endpoints without needing to actually fail real HTTP calls.
        """
        state = self._tier(tier)
        for ep in state.endpoints:
            if ep.url == url:
                with ep.lock:
                    ep.consecutive_failures = self._breaker_threshold
                    ep.open_until_ts = (
                        self._clock() + self._breaker_cooldown_s
                    )
                return
        raise KeyError(f"endpoint {url!r} not in tier {tier!r}")


def _rollup_breaker_state(states: List[str]) -> str:
    """Tier-level breaker rollup for the visibility dashboard.

    Rules:
      * No endpoints in this tier  -> "open"  (cannot call -> down)
      * Any endpoint is "closed"   -> "closed"
      * No closed, any "half_open" -> "half_open"
      * All "open"                 -> "open"
    """
    if not states:
        return "open"
    if "closed" in states:
        return "closed"
    if "half_open" in states:
        return "half_open"
    return "open"


# ──────────────────────────────────────────────────────────────────────────
# Default transport — kept at module scope so it's mockable from tests
# and the McpHost constructor stays dependency-light.
# ──────────────────────────────────────────────────────────────────────────


def _default_requests_transport(
    method: str,
    url: str,
    *,
    json: Optional[Dict[str, Any]] = None,
    timeout: float = 10.0,
):
    """Thin wrapper over requests so tests can stub the transport.

    Imported lazily so the McpHost module loads on machines that don't
    have requests installed (the package import surface stays clean for
    the FakeStore-only smoke runs).
    """
    import requests  # local import keeps top-level deps lean

    resp = requests.request(method, url, json=json, timeout=timeout)
    body: Any
    try:
        body = resp.json()
    except ValueError:
        body = resp.text
    return resp.status_code, body


# ──────────────────────────────────────────────────────────────────────────
# Module-level singleton helpers — the same pattern get_store() uses so
# routes and the agent don't each construct their own host instance.
# ──────────────────────────────────────────────────────────────────────────


_HOST_SINGLETON: Optional[McpHost] = None
_HOST_LOCK = threading.Lock()


def get_host() -> Optional[McpHost]:
    """Return the active singleton, or None if no host has been set."""
    return _HOST_SINGLETON


def set_host(host: Optional[McpHost]) -> None:
    """Install / clear the module singleton.

    Called once at app start when SCUDO_MCP_HOST_ENABLED is set, and
    cleared from tests that exercise the in-process path.
    """
    global _HOST_SINGLETON
    with _HOST_LOCK:
        _HOST_SINGLETON = host


__all__ = [
    "McpHost",
    "McpHostError",
    "McpHostUnavailable",
    "McpHostUnknownTier",
    "get_host",
    "set_host",
]
