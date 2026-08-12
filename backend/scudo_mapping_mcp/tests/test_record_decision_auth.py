"""Trust-boundary gate on ``persist.record_decision``.

THE DEFECT THIS LOCKS DOWN
--------------------------
``persist.record_decision`` writes canonical state (``apply_decision`` ->
``store.upsert_precedent``) and, before this gate existed, performed NO
authentication and NO seal verification. ``verdict_seal.verify`` is called
in exactly one place in the package — ``commit_mapping`` — so the HITL
write path had no equivalent of the publish gate at all.

The docstring claimed the principal "comes from the gateway auth header,
never from the tool body". That is true of ``backend/routes/mapping.py``
(a SEPARATE Flask ingress that calls ``feedback.apply_decision`` directly
and never touches this tool), and false of the tool itself: ``decided_by``
is a caller-supplied ``DecisionInput`` field. The MCP server is published
through an internet-facing HTTP ALB, so "the gateway enforces it" was not
a property of this code path.

WHAT THE GATE IS
----------------
A shared-secret bearer check that FAILS CLOSED. The caller must present
``write_token`` matching ``SCUDO_PERSIST_WRITE_TOKEN``. With nothing
configured the tool refuses every write — the default is safe.

WHY NOT REQUIRE THE HMAC SEAL INSTEAD
-------------------------------------
Two reasons, both structural:
  1. The seal binds ``mapped_node_iri`` to the node MATCH & VERIFY chose.
     A ``override`` decision writes a DIFFERENT node by definition, so a
     seal can never bind an override — the seal would have to be ignored
     exactly where the human disagrees with the machine.
  2. ``verdict._max_age_seconds`` defaults to 300s. Human review routinely
     takes longer than five minutes, so a seal requirement would expire
     mid-review.
Independently: a seal proves M&V PRODUCED a verdict; it does not prove WHO
is calling. Authentication is the missing control, and the seal is not an
authentication primitive.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from scudo_mapping_mcp import feedback as feedback_mod
from scudo_mapping_mcp import persistence_mcp as pm
from scudo_mapping_mcp.models import TaxonomyNode
from scudo_mapping_mcp.tests.fake_store import FakeStore

VENDOR = "LSEG"
NODE_IRI = "jpmorgan:data:cdao:EquityPrices"

# Every env var that can influence the gate. Cleared before each test so a
# stray value from another module (or a developer shell) cannot make an
# authentication test pass for the wrong reason.
_GATE_ENV = (
    "SCUDO_PERSIST_WRITE_TOKEN",
    "SCUDO_PERSIST_ALLOW_DEV_WRITES",
    "SCUDO_VERDICT_ALLOW_DEV",
)


@pytest.fixture
def store(monkeypatch):
    """A FakeStore wired into the module ``apply_decision`` resolves through.

    ``feedback.apply_decision`` calls the module-level ``get_store`` imported
    into ``feedback``, so that is the name to patch. monkeypatch restores it,
    keeping this file free of the global store mutation smoke.py does.
    """
    fake = FakeStore(nodes=[TaxonomyNode(iri=NODE_IRI, label="Equity Prices")])
    monkeypatch.setattr(feedback_mod, "get_store", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def clean_gate_env(monkeypatch):
    for name in _GATE_ENV:
        monkeypatch.delenv(name, raising=False)


def _decision(**overrides):
    params = {
        "vendor": VENDOR,
        "product_id": "EQ-AUTH-1",
        "decision": "approve",
        "decided_by": "attacker@example.com",
        "node_iri": NODE_IRI,
        "name": "Equity Prices Real Time",
        "suggested_confidence": 0.95,
    }
    params.update(overrides)
    return pm.DecisionInput(**params)


def _call(params) -> dict:
    return json.loads(asyncio.run(pm.record_decision(params)))


def _wrote_anything(fake: FakeStore) -> bool:
    """True if any precedent (positive, provisional or negative) landed."""
    return bool(fake._precedents or fake._provisional or fake._negatives)


# ──────────────────────────────────────────────────────────────────────
# The defect: unauthenticated canon write
# ──────────────────────────────────────────────────────────────────────


def test_unauthenticated_caller_cannot_write_canon(store):
    """THE regression test. No token configured, none presented — the tool
    must refuse AND must not have written a precedent."""
    body = _call(_decision())

    assert body["committed"] is False, body
    assert body["refusal"]["reason"] == "write_not_authorized", body
    assert not _wrote_anything(store), (
        "unauthenticated record_decision wrote canonical state"
    )


def test_gate_fails_closed_when_no_token_is_configured(monkeypatch, store):
    """Unset secret must DENY, not allow. A deploy that forgets to inject
    SCUDO_PERSIST_WRITE_TOKEN gets a dead write path, never an open one."""
    body = _call(_decision(write_token="anything-at-all"))

    assert body["committed"] is False, body
    assert body["refusal"]["reason"] == "write_not_authorized", body
    assert not _wrote_anything(store)


def test_wrong_token_is_refused(monkeypatch, store):
    monkeypatch.setenv("SCUDO_PERSIST_WRITE_TOKEN", "s3cret-correct-value")

    body = _call(_decision(write_token="s3cret-wrong-value"))

    assert body["committed"] is False, body
    assert body["refusal"]["reason"] == "write_not_authorized", body
    assert not _wrote_anything(store)


def test_missing_token_is_refused_when_one_is_configured(monkeypatch, store):
    monkeypatch.setenv("SCUDO_PERSIST_WRITE_TOKEN", "s3cret-correct-value")

    body = _call(_decision())  # no write_token supplied

    assert body["committed"] is False, body
    assert body["refusal"]["reason"] == "write_not_authorized", body
    assert not _wrote_anything(store)


def test_correct_token_writes_canon(monkeypatch, store):
    """The gate must not break the legitimate path."""
    monkeypatch.setenv("SCUDO_PERSIST_WRITE_TOKEN", "s3cret-correct-value")

    body = _call(
        _decision(
            decided_by="reviewer@jpmc",
            write_token="s3cret-correct-value",
        )
    )

    assert body["committed"] is True, body
    assert body["result"]["status"] == "approved", body
    assert _wrote_anything(store), "authorised write did not reach the store"


# ──────────────────────────────────────────────────────────────────────
# Ordering — authorization is the FIRST gate
# ──────────────────────────────────────────────────────────────────────


def test_auth_is_checked_before_vendor_scope(store):
    """An unauthenticated caller must not be able to probe which vendors are
    in scope by reading back differing refusal reasons. Same discipline as
    ``GATE_refuses_forged_seal_before_anything_else`` for commit_mapping.
    """
    body = _call(_decision(vendor="NotAVendor"))

    assert body["refusal"]["reason"] == "write_not_authorized", body
    assert "NotAVendor" not in json.dumps(body), (
        "refusal leaked the scope-gate result to an unauthenticated caller"
    )


def test_auth_is_checked_before_decision_validation(store):
    """Likewise for the decision verb — an invalid verb from an
    unauthenticated caller reports the auth failure, not the verb."""
    body = _call(_decision(decision="not-a-verb"))

    assert body["refusal"]["reason"] == "write_not_authorized", body


# ──────────────────────────────────────────────────────────────────────
# Dev bypass — opt-in only, and absent from every infra template
# ──────────────────────────────────────────────────────────────────────


def test_dev_bypass_allows_the_local_loop(monkeypatch, store):
    """Local dev / the standalone smoke runner opt in explicitly. This is
    the same posture verdict.py takes for the dev HMAC key."""
    monkeypatch.setenv("SCUDO_PERSIST_ALLOW_DEV_WRITES", "1")

    body = _call(_decision(decided_by="reviewer@jpmc"))

    assert body["committed"] is True, body
    assert _wrote_anything(store)


def test_dedicated_dev_switch_opens_the_dev_bypass(monkeypatch, store):
    """The local-dev escape hatch works — via its OWN switch.

    This replaces an earlier test that asserted ``SCUDO_VERDICT_ALLOW_DEV``
    also opened the write gate. That coupling was unsafe: README.md and
    docs/demo-runbook.md instruct operators to export that var for a local
    run, so following the documented setup silently disabled the canonical
    write gate. See ``test_verdict_dev_flag_does_not_open_the_write_gate``.

    The smoke runner (``smoke.py:_ensure_dev_signing_key``) now sets both
    switches explicitly, so it keeps its coverage of this path.
    """
    monkeypatch.setenv("SCUDO_PERSIST_ALLOW_DEV_WRITES", "1")

    body = _call(_decision(decided_by="reviewer@jpmc"))

    assert body["committed"] is True, body


def test_dev_bypass_is_off_by_default():
    """The switches must be opt-in. Nothing set -> no bypass."""
    assert pm._dev_writes_allowed() is False


@pytest.mark.parametrize("falsy", ["", "0", "false", "no", "off", "maybe"])
def test_dev_bypass_ignores_falsy_values(monkeypatch, falsy):
    monkeypatch.setenv("SCUDO_PERSIST_ALLOW_DEV_WRITES", falsy)
    assert pm._dev_writes_allowed() is False


# ──────────────────────────────────────────────────────────────────────
# The false docstring
# ──────────────────────────────────────────────────────────────────────


def test_docstring_does_not_claim_the_gateway_enforces_the_principal():
    """The old docstring asserted the principal "comes from the gateway auth
    header, never from the tool body". ``decided_by`` IS a tool-body field,
    and the Flask route that does bind the principal never calls this tool.
    The docstring must describe what this function enforces."""
    doc = pm.record_decision.__doc__ or ""

    assert "never from the tool body" not in doc, (
        "docstring still claims the gateway supplies decided_by"
    )
    assert "write_token" in doc, "docstring must document the gate it enforces"


def test_decided_by_is_documented_as_bearer_asserted():
    """Honest description: the token holder ASSERTS the principal. The tool
    cannot independently verify it, and must not imply that it can."""
    doc = pm.record_decision.__doc__ or ""
    assert "assert" in doc.lower(), doc


# ──────────────────────────────────────────────────────────────────────
# MCP annotations are hints, not enforcement (noted, not relied upon)
# ──────────────────────────────────────────────────────────────────────


def test_annotations_are_not_the_enforcement_mechanism():
    """``readOnlyHint`` / ``destructiveHint`` are advisory metadata the MCP
    SDK ships to clients (mcp.types.ToolAnnotations: "all properties in
    ToolAnnotations are **hints** ... Clients should never make tool use
    decisions based on ToolAnnotations received from untrusted servers").
    They gate nothing server-side. Pinned here so nobody later mistakes the
    _RO/_RW dicts for access control.
    """
    assert pm._RW["readOnlyHint"] is False
    assert pm._RO["readOnlyHint"] is True

    # The real gate is a callable that reads configuration, not a dict.
    assert callable(pm._check_write_authorization)
    assert pm._check_write_authorization("") == "write_not_authorized"


# ──────────────────────────────────────────────────────────────────────
# EVERY write tool is gated, not just record_decision
# ──────────────────────────────────────────────────────────────────────
# Added after the original fix: record_decision was the briefed defect, but
# the same file exposes three other write surfaces on the same ALB path.
# persist.import_bundle bulk-writes CONFIRMED precedents (cheaper than
# forging one decision -- one call writes a bundle of them), and
# persist.publish_bundle writes the canonical S3 key that every Match &
# Verify container replays at boot. Both were ungated.


def _tool_source(fn) -> str:
    import inspect

    return inspect.getsource(inspect.unwrap(fn))


def test_import_bundle_is_write_gated():
    assert "_check_write_authorization" in _tool_source(pm.import_bundle_tool)


def test_publish_bundle_is_write_gated():
    assert "_check_write_authorization" in _tool_source(pm.publish_bundle_tool)


def test_export_bundle_is_not_gated_because_it_only_reads():
    """Negative control: proves the gate assertions above are discriminating
    and not merely matching any tool in the module."""
    assert "_check_write_authorization" not in _tool_source(pm.export_bundle_tool)


def test_every_rw_annotated_tool_checks_authorization():
    """The real gate: any tool annotated read-write must consult the
    authorization check. commit_mapping is exempt -- it is bound by the HMAC
    verdict seal instead, which is a stronger control for the agent path.

    If someone adds a new _RW tool without a gate, this fails.
    """
    import inspect

    exempt = {"commit_mapping"}
    ungated = []
    for name, fn in vars(pm).items():
        if not callable(fn) or name.startswith("_"):
            continue
        try:
            src = _tool_source(fn)
        except (OSError, TypeError):
            continue
        if not inspect.iscoroutinefunction(inspect.unwrap(fn)):
            continue
        if "**_RW" not in src and '"_RW"' not in src:
            continue
        if name in exempt:
            continue
        if "_check_write_authorization" not in src:
            ungated.append(name)
    assert not ungated, f"read-write MCP tools with no authorization gate: {ungated}"


# ──────────────────────────────────────────────────────────────────────
# The dev bypass must not ride on a documented convenience flag
# ──────────────────────────────────────────────────────────────────────


def test_verdict_dev_flag_does_not_open_the_write_gate(monkeypatch):
    """``SCUDO_VERDICT_ALLOW_DEV`` selects the dev HMAC SIGNING KEY. README.md
    and docs/demo-runbook.md both tell operators to export it for a local run,
    so if it also disabled the canonical-write gate, every operator following
    the documented setup would silently run an open write path.

    Found by an adversarial verifier. Keep these two concerns separate.
    """
    monkeypatch.setenv("SCUDO_VERDICT_ALLOW_DEV", "1")
    monkeypatch.delenv("SCUDO_PERSIST_ALLOW_DEV_WRITES", raising=False)
    monkeypatch.delenv("SCUDO_PERSIST_WRITE_TOKEN", raising=False)
    assert pm._check_write_authorization("") == "write_not_authorized"


def test_dedicated_dev_flag_still_works(monkeypatch):
    """Negative control: the intended local-dev escape hatch is unaffected."""
    monkeypatch.delenv("SCUDO_VERDICT_ALLOW_DEV", raising=False)
    monkeypatch.setenv("SCUDO_PERSIST_ALLOW_DEV_WRITES", "1")
    assert pm._check_write_authorization("") is None


def test_readme_dev_recipe_is_not_a_write_bypass():
    """Pin the actual documented recipe. If someone re-adds a README flag to
    the bypass list, this fails with a pointer to why that is wrong."""
    import inspect

    src = inspect.getsource(pm._dev_writes_allowed)
    assert "SCUDO_VERDICT_ALLOW_DEV" not in src.split('"""')[-1], (
        "the write bypass reads SCUDO_VERDICT_ALLOW_DEV again -- that flag is "
        "in README.md's local-run recipe and must not gate canonical writes"
    )


def test_non_ascii_token_refuses_instead_of_crashing(monkeypatch):
    """``hmac.compare_digest`` raises TypeError on non-ASCII str input.

    That raise was a deployment-posture ORACLE: unset secret -> clean refusal,
    configured secret -> unhandled TypeError, so a prober learned whether a
    secret exists — the exact distinction the opaque refusal exists to hide.
    Found by a completeness critic. Compare bytes, refuse totally.
    """
    monkeypatch.delenv("SCUDO_PERSIST_ALLOW_DEV_WRITES", raising=False)
    monkeypatch.delenv("SCUDO_VERDICT_ALLOW_DEV", raising=False)

    monkeypatch.setenv("SCUDO_PERSIST_WRITE_TOKEN", "s3cret")
    assert pm._check_write_authorization("café") == "write_not_authorized"
    assert pm._check_write_authorization("日本語") == "write_not_authorized"
    assert pm._check_write_authorization("s3cret") is None

    monkeypatch.delenv("SCUDO_PERSIST_WRITE_TOKEN", raising=False)
    assert pm._check_write_authorization("café") == "write_not_authorized"


def test_refusal_is_indistinguishable_between_postures(monkeypatch):
    """The anti-oracle property itself: same reason, no exception, whether or
    not a secret is configured."""
    monkeypatch.delenv("SCUDO_PERSIST_ALLOW_DEV_WRITES", raising=False)
    monkeypatch.delenv("SCUDO_VERDICT_ALLOW_DEV", raising=False)

    monkeypatch.delenv("SCUDO_PERSIST_WRITE_TOKEN", raising=False)
    unset = pm._check_write_authorization("café")
    monkeypatch.setenv("SCUDO_PERSIST_WRITE_TOKEN", "s3cret")
    configured = pm._check_write_authorization("café")
    assert unset == configured == "write_not_authorized"
