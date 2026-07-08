"""P3 — ODRL rights evaluator and check_scope integration (WS6)."""

from __future__ import annotations


import json
from pathlib import Path

from scudo_mapping_mcp.frames import check_scope
from scudo_mapping_mcp.models import VendorProductRef
from scudo_mapping_mcp.rights_odrl import (
    clear_policies,
    evaluate_odrl_scope,
    load_policy,
    load_policy_from_jsonld,
)


def _ref(vendor: str = "LSEG") -> VendorProductRef:
    return VendorProductRef(vendor=vendor, product_id="P1", name="Test Product")


def setup_function():
    clear_policies()


def test_missing_policy_priority_vendor_permitted():
    result = evaluate_odrl_scope(_ref("LSEG"))
    assert result.allowed is True


def test_missing_policy_out_of_scope_vendor_denied():
    result = evaluate_odrl_scope(_ref("NotAVendor"))
    assert result.allowed is False
    assert "outside the in-scope set" in result.reason


def test_permission_grants():
    load_policy(
        "target-1",
        {
            "permission": [
                {
                    "action": "use",
                    "target": {"@id": "target-1"},
                    "assignee": {"@id": "https://example.com/vendor/lseg"},
                }
            ]
        },
    )
    result = evaluate_odrl_scope(_ref("LSEG"), target_iri="target-1")
    assert result.allowed is True
    assert "granted" in result.reason.lower()


def test_prohibition_denies():
    load_policy(
        "target-2",
        {
            "permission": [{"action": "use", "target": {"@id": "target-2"}}],
            "prohibition": [{"action": "use", "target": {"@id": "target-2"}}],
        },
    )
    result = evaluate_odrl_scope(_ref(), target_iri="target-2")
    assert result.allowed is False
    assert "prohibition" in result.reason.lower()


def test_unknown_constraint_fail_closed():
    load_policy(
        "target-3",
        {
            "permission": [
                {
                    "action": "use",
                    "target": {"@id": "target-3"},
                    "constraint": [
                        {
                            "leftOperand": "dateTime",
                            "operator": "gt",
                            "rightOperand": "2020",
                        }
                    ],
                }
            ]
        },
    )
    result = evaluate_odrl_scope(_ref(), target_iri="target-3")
    assert result.allowed is False
    assert "constraint" in result.reason.lower()


def test_evaluate_odrl_scope_fail_closed_on_internal_error(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("neptune down")

    monkeypatch.setattr(
        "scudo_mapping_mcp.rights_odrl._evaluate_policy",
        boom,
    )
    load_policy("x", {"permission": [{"action": "use", "target": {"@id": "x"}}]})
    result = evaluate_odrl_scope(_ref(), target_iri="x")
    assert result.allowed is False
    assert "ODRL evaluation failed" in result.reason


def test_check_scope_permitted_via_odrl():
    ref = _ref("LSEG")
    load_policy(
        ref.iri,
        {
            "permission": [
                {
                    "action": "use",
                    "target": {"@id": ref.iri},
                    "assignee": {"@id": "https://example.com/vendor/lseg"},
                }
            ]
        },
    )
    result = check_scope(ref)
    assert result.allowed is True


def test_check_scope_out_of_scope_vendor():
    result = check_scope(_ref("NotAVendor"))
    assert result.allowed is False


def test_check_scope_fail_closed_on_ref_access_error():
    class _Boom:
        @property
        def vendor(self):
            raise RuntimeError("simulated odrl lookup failure")

    result = check_scope(_Boom())  # type: ignore[arg-type]
    assert result.allowed is False
    assert "scope check failed" in result.reason


def test_load_policy_from_jsonld_single_dict_permission_indexed_and_evaluated(
    tmp_path: Path,
):
    policy = {
        "@context": "https://www.w3.org/ns/odrl/2/",
        "@type": "Set",
        "uid": "policy-single-perm",
        "permission": {
            "action": "use",
            "target": {"@id": "urn:example:dataset-1"},
            "assignee": {"@id": "https://example.com/vendor/lseg"},
        },
    }
    path = tmp_path / "policy.jsonld"
    path.write_text(json.dumps(policy), encoding="utf-8")

    assert load_policy_from_jsonld(path) == 1
    result = evaluate_odrl_scope(_ref("LSEG"), target_iri="urn:example:dataset-1")
    assert result.allowed is True
    assert "granted" in result.reason.lower()
