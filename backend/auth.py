"""
Request-principal accessor for ``/api/*``.

Single-swap-point auth: routes ask for "the authenticated principal" via
``resolve_principal``; the resolution policy lives HERE, and only here. Today
that policy reads a trusted upstream header; a future switch to signed-token
validation (JWT) is one file's worth of change. The matcher / feedback /
store packages stay Flask-free and have no notion of "auth" at all.

Resolution order (first match wins):

  1. ``SCUDO_AUTH_DEV_PRINCIPAL`` env var, when ``SCUDO_AUTH_ALLOW_DEV`` is
     truthy. Local development only — a production deploy MUST leave at least
     one of these unset.

  2. The trusted upstream header configured by
     ``SCUDO_AUTH_PRINCIPAL_HEADER`` (default ``X-Authenticated-User``).

  3. None — caller raises ``AuthError`` and the ``app.py`` ``before_request``
     hook turns that into HTTP 401.

TRUST BOUNDARY — read before deploying:

  Header-from-gateway is sound ONLY when BOTH of the following hold:

    a) The service is reachable EXCLUSIVELY through the gateway. Any path
       around it (NodePort, direct EC2 attach, a sidecar with port-forward)
       lets a caller forge ``X-Authenticated-User`` and write a precedent
       under any identity they choose. The mock-side prototype runs in a
       VDI/VPC-isolated environment that meets this, but the assumption is
       LOAD-BEARING and must be re-confirmed on every deploy.

    b) The gateway STRIPS any client-supplied copy of the header BEFORE
       setting its own value from the authenticated session. A gateway that
       merely "adds" the header (without stripping inbound) lets the client
       overwrite it. JPMC's edge configuration must be verified to do the
       former, not the latter.

  If either assumption cannot be guaranteed — or the edge already hands out
  signed JWTs — replace the header read inside ``_resolve_from_header`` with
  JWT verification. The route surface and the rest of the codebase do not
  change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass(frozen=True)
class Principal:
    """The authenticated identity attached to one request."""

    user_id: str
    source: str  # "gateway-header" | "dev-env"


class AuthError(Exception):
    """Raised when no principal can be resolved for the current request."""


class _HeaderLike(Protocol):
    """Minimal Flask-request shape so this module can be unit-tested
    without importing flask. The before_request hook in app.py passes the
    real flask.request.headers object, which conforms."""

    def get(self, key: str, default: str = "") -> Optional[str]: ...


_TRUTHY = {"1", "true", "yes", "on"}


def _dev_allowed() -> bool:
    return os.getenv("SCUDO_AUTH_ALLOW_DEV", "").strip().lower() in _TRUTHY


def _dev_writes_allowed() -> bool:
    """Whether a ``dev-env`` principal may perform a durable HITL write.

    OFF by default, independent of ``SCUDO_AUTH_ALLOW_DEV``. Opt in with
    ``SCUDO_AUTH_ALLOW_DEV_WRITES`` on a developer machine (run_local.py).
    """
    return os.getenv("SCUDO_AUTH_ALLOW_DEV_WRITES", "").strip().lower() in _TRUTHY


def can_write_decision(principal: Principal) -> bool:
    """Policy: may this principal make a durable HITL decision write?

    Defense-in-depth for the finding-1 exposure. ``SCUDO_AUTH_DEV_PRINCIPAL``
    is a SHARED, unauthenticated identity: if a deploy leaves it set on a
    reachable endpoint, every anonymous visitor resolves to it and could
    otherwise record a HITL precedent (durable + self-reinforcing) under one
    person's name. So a ``dev-env`` principal is READ-ONLY for decisions
    unless ``SCUDO_AUTH_ALLOW_DEV_WRITES`` is explicitly enabled. A real
    ``gateway-header`` identity always may. Fail-closed: a misconfigured prod
    deploy blocks the write (403) instead of forging an audited precedent.
    """
    if principal.source == "dev-env":
        return _dev_writes_allowed()
    return True


def _resolve_from_dev() -> Optional[Principal]:
    if not _dev_allowed():
        return None
    user = (os.getenv("SCUDO_AUTH_DEV_PRINCIPAL", "") or "").strip()
    if not user:
        return None
    return Principal(user_id=user, source="dev-env")


def _resolve_from_header(headers: _HeaderLike) -> Optional[Principal]:
    name = (
        os.getenv("SCUDO_AUTH_PRINCIPAL_HEADER", "") or ""
    ).strip() or "X-Authenticated-User"
    value = headers.get(name, "") or ""
    value = value.strip()
    if not value:
        return None
    return Principal(user_id=value, source="gateway-header")


def resolve_principal(headers: _HeaderLike) -> Principal:
    """Return the authenticated principal for one request, or raise.

    The function is deliberately a pure function of the headers + env. No
    Flask import; no global state. ``app.py`` calls this from a
    ``before_request`` hook and attaches the result to ``flask.g.principal``.

    Args:
        headers: The request's headers mapping (anything supporting
            ``.get(key, default)``).

    Raises:
        AuthError: if no principal can be resolved from any configured
            source. Translates to HTTP 401 at the app layer.
    """
    principal = _resolve_from_dev()
    if principal is not None:
        return principal
    principal = _resolve_from_header(headers)
    if principal is not None:
        return principal
    raise AuthError("no authenticated principal in request")
