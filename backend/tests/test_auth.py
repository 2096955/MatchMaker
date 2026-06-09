"""
Unit tests for the auth accessor — pure Python, no Flask, no DB.

Run directly:
    python -m tests.test_auth          (from backend/)
    python tests/test_auth.py          (from backend/)

The accessor is the load-bearing trust boundary for /api/*. These gates
prove the resolution policy is fail-closed and that the order of precedence
matches the documented contract — the policy can be retuned later (header
-> JWT) without breaking the route layer.
"""
from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from auth import AuthError, Principal, resolve_principal  # noqa: E402


class _FakeHeaders:
    """Mimics flask.request.headers — just the .get(key, default) shape."""

    def __init__(self, mapping=None) -> None:
        self._m = dict(mapping or {})

    def get(self, key, default=""):
        return self._m.get(key, default)


_RESULTS = []


def case(name):
    def deco(fn):
        try:
            fn()
            _RESULTS.append((name, True, ""))
        except AssertionError as e:
            _RESULTS.append((name, False, f"assertion: {e}"))
        except Exception as e:  # noqa: BLE001
            _RESULTS.append((name, False, f"{type(e).__name__}: {e}\n"
                                          f"{traceback.format_exc()}"))
        return fn
    return deco


def _clear_env():
    for k in ("SCUDO_AUTH_ALLOW_DEV", "SCUDO_AUTH_DEV_PRINCIPAL",
              "SCUDO_AUTH_PRINCIPAL_HEADER"):
        os.environ.pop(k, None)


@case("missing_header_and_no_dev_fallback_is_rejected")
def _():
    _clear_env()
    try:
        resolve_principal(_FakeHeaders())
    except AuthError as e:
        assert "no authenticated principal" in str(e)
    else:
        raise AssertionError("expected AuthError")


@case("header_from_gateway_resolves")
def _():
    _clear_env()
    p = resolve_principal(_FakeHeaders({"X-Authenticated-User": "alice@jpmc"}))
    assert isinstance(p, Principal)
    assert p.user_id == "alice@jpmc"
    assert p.source == "gateway-header"


@case("custom_header_name_via_env")
def _():
    _clear_env()
    os.environ["SCUDO_AUTH_PRINCIPAL_HEADER"] = "X-JPMC-User"
    try:
        # Default header set but custom not set -> miss.
        try:
            resolve_principal(_FakeHeaders({"X-Authenticated-User": "alice"}))
        except AuthError:
            pass
        else:
            raise AssertionError("custom header should not fall back to default")
        p = resolve_principal(_FakeHeaders({"X-JPMC-User": "bob"}))
        assert p.user_id == "bob"
        assert p.source == "gateway-header"
    finally:
        _clear_env()


@case("dev_fallback_requires_explicit_allow_flag")
def _():
    _clear_env()
    # Dev principal set but ALLOW_DEV unset -> not honoured.
    os.environ["SCUDO_AUTH_DEV_PRINCIPAL"] = "dev@local"
    try:
        try:
            resolve_principal(_FakeHeaders())
        except AuthError:
            pass
        else:
            raise AssertionError("dev principal must NOT resolve without ALLOW_DEV")
    finally:
        _clear_env()


@case("dev_fallback_resolves_when_explicitly_allowed")
def _():
    _clear_env()
    os.environ["SCUDO_AUTH_ALLOW_DEV"] = "1"
    os.environ["SCUDO_AUTH_DEV_PRINCIPAL"] = "dev@local"
    try:
        p = resolve_principal(_FakeHeaders())
        assert p.user_id == "dev@local"
        assert p.source == "dev-env"
    finally:
        _clear_env()


@case("dev_takes_precedence_over_header_when_allowed")
def _():
    """Intentional precedence: a misconfigured prod deploy that left
    ALLOW_DEV=1 by accident gets a dev-env identity in audit logs and is
    visible at first request, instead of silently using the gateway header."""
    _clear_env()
    os.environ["SCUDO_AUTH_ALLOW_DEV"] = "yes"
    os.environ["SCUDO_AUTH_DEV_PRINCIPAL"] = "dev@local"
    try:
        p = resolve_principal(_FakeHeaders({"X-Authenticated-User": "alice"}))
        assert p.user_id == "dev@local"
        assert p.source == "dev-env"
    finally:
        _clear_env()


@case("empty_header_value_is_treated_as_missing")
def _():
    _clear_env()
    try:
        resolve_principal(_FakeHeaders({"X-Authenticated-User": "  "}))
    except AuthError:
        pass
    else:
        raise AssertionError("whitespace-only header value must NOT authenticate")


@case("principal_is_frozen_dataclass")
def _():
    p = Principal(user_id="x", source="gateway-header")
    try:
        p.user_id = "y"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("Principal should be immutable")


def main() -> int:
    for name, ok, detail in _RESULTS:
        if ok:
            print(f"  PASS  {name}")
        else:
            print(f"  FAIL  {name}\n        {detail}")
    failed = [r for r in _RESULTS if not r[1]]
    print(f"\n{len(_RESULTS) - len(failed)}/{len(_RESULTS)} pass")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
