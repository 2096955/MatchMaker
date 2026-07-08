"""Deterministic ODRL 2.2 rights evaluation (WS6 / P3).

TOTAL contract: never raises; any exception or unknown constraint => DENY.
Missing policy falls back to the PRIORITY_VENDORS scope check.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .config import PRIORITY_VENDORS
from .models import ScopeResult, VendorProductRef

_POLICIES: dict[str, dict[str, Any]] = {}


def clear_policies() -> None:
    _POLICIES.clear()


def load_policy(key: str, policy: dict[str, Any]) -> None:
    """Register an ODRL policy document (JSON-LD shape)."""
    _POLICIES[key.strip()] = policy


def _action_name(action: Any) -> str:
    if isinstance(action, dict):
        return (
            str(action.get("@id") or action.get("id") or "").rsplit("/", 1)[-1].lower()
        )
    return str(action or "").rsplit("/", 1)[-1].lower()


def _matches_target(rule: dict, target_iri: str, vendor: str) -> bool:
    target = rule.get("target") or rule.get("odrl:target")
    if target is None:
        return True
    targets = target if isinstance(target, list) else [target]
    for t in targets:
        tid = t.get("@id") if isinstance(t, dict) else str(t)
        if tid and (tid == target_iri or vendor.lower() in tid.lower()):
            return True
    return False


def _matches_assignee(rule: dict, vendor: str) -> bool:
    assignee = rule.get("assignee") or rule.get("odrl:assignee")
    if assignee is None:
        return True
    items = assignee if isinstance(assignee, list) else [assignee]
    vendor_slug = vendor.lower().replace(" ", "")
    for a in items:
        aid = a.get("@id") if isinstance(a, dict) else str(a)
        if aid and vendor_slug in aid.lower().replace(" ", ""):
            return True
    return False


def _evaluate_policy(
    policy: dict,
    *,
    target_iri: str,
    vendor: str,
    action: str = "use",
) -> ScopeResult:
    permissions = policy.get("permission") or policy.get("odrl:permission") or []
    prohibitions = policy.get("prohibition") or policy.get("odrl:prohibition") or []
    if not isinstance(permissions, list):
        permissions = [permissions]
    if not isinstance(prohibitions, list):
        prohibitions = [prohibitions]

    for rule in prohibitions:
        if not isinstance(rule, dict):
            return ScopeResult(allowed=False, reason="unknown ODRL prohibition shape")
        if _matches_target(rule, target_iri, vendor) and _action_name(
            rule.get("action") or rule.get("odrl:action")
        ) in {action, "all"}:
            return ScopeResult(
                allowed=False, reason="ODRL prohibition overrides permission"
            )

    for rule in permissions:
        if not isinstance(rule, dict):
            return ScopeResult(allowed=False, reason="unknown ODRL permission shape")
        act = _action_name(rule.get("action") or rule.get("odrl:action"))
        if act not in {action, "all", ""}:
            continue
        if not _matches_target(rule, target_iri, vendor):
            continue
        if not _matches_assignee(rule, vendor):
            return ScopeResult(allowed=False, reason="ODRL assignee mismatch")
        for key in rule:
            if key.startswith("constraint") or key.startswith("odrl:constraint"):
                return ScopeResult(allowed=False, reason="unmatched ODRL constraint")
        return ScopeResult(allowed=True, reason="ODRL permission granted")

    return ScopeResult(allowed=False, reason="no matching ODRL permission")


def evaluate_odrl_scope(
    ref: VendorProductRef,
    *,
    target_iri: Optional[str] = None,
) -> ScopeResult:
    """Evaluate ODRL policies for a vendor product. Never raises."""
    try:
        keys = [ref.iri, target_iri or "", ref.vendor, ref.product_id]
        policy = None
        for k in keys:
            if k and k in _POLICIES:
                policy = _POLICIES[k]
                break
        if policy is None:
            if ref.vendor not in PRIORITY_VENDORS:
                return ScopeResult(
                    allowed=False,
                    reason=f"Vendor '{ref.vendor}' is outside the in-scope set this year.",
                )
            return ScopeResult(
                allowed=True, reason="no ODRL policy — priority-vendor fallback"
            )

        return _evaluate_policy(
            policy,
            target_iri=target_iri or ref.iri,
            vendor=ref.vendor,
        )
    except Exception as e:  # noqa: BLE001 — fail-closed by design
        return ScopeResult(
            allowed=False,
            reason=f"ODRL evaluation failed: {type(e).__name__}: {e}",
        )


def load_policy_from_jsonld(path: str | Path) -> int:
    """Load ODRL policies from a JSON-LD file into the in-memory store."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else [data]
    count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        uid = str(item.get("uid") or item.get("odrl:uid") or item.get("@id") or count)
        load_policy(uid, item)
        perms = item.get("permission") or item.get("odrl:permission") or []
        if not isinstance(perms, list):
            perms = [perms]
        for perm in perms:
            if not isinstance(perm, dict):
                continue
            targets = perm.get("target") or perm.get("odrl:target") or []
            if not isinstance(targets, list):
                targets = [targets]
            for t in targets:
                tid = t.get("@id") if isinstance(t, dict) else str(t)
                if tid:
                    load_policy(tid, item)
        count += 1
    return count
