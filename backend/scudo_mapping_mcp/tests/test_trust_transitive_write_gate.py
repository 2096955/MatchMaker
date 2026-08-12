"""Tests for the TRANSITIVE trust gate in ``tests/smoke.py``.

WHY THIS FILE EXISTS
--------------------
The original ``TRUST_*_imports_no_writers`` smoke gates parsed ONLY the entry
module's own AST and looked for three literal imported names. That is a
one-hop check, and it was defeated by a single alias hop:

    match_verify_mcp.py:59   from .hydrate import HydrationError, hydrate
    hydrate.py:40            from .bundle import import_bundle
    bundle.py:253            store.upsert_precedent(...)

``hydrate`` is not one of the three literal names, so the one-hop gate passed
while Match & Verify demonstrably reached ``upsert_precedent``. The gate gave
false assurance. These tests pin the replacement: a bounded, cycle-safe,
first-party-only transitive import walk plus a boot-region confinement rule.

WHY SUBPROCESSES
----------------
``scudo_mapping_mcp.tests.smoke`` executes all of its cases at import time, and
several of them permanently monkeypatch ``get_store`` onto sibling modules and
swap ``settings``. Importing it in-process would pollute every other pytest in
the session. Every test here therefore drives smoke's helpers from a throwaway
subprocess rooted at ``backend/``.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import textwrap

BACKEND = pathlib.Path(__file__).resolve().parents[2]
PKG_DIR = BACKEND / "scudo_mapping_mcp"

_PRELUDE = "from scudo_mapping_mcp.tests import smoke\n"


def _run_driver(body: str) -> subprocess.CompletedProcess:
    """Run a driver snippet in a subprocess with cwd=backend."""
    proc = subprocess.run(
        [sys.executable, "-c", _PRELUDE + textwrap.dedent(body)],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
    )
    return proc


def _driver_json(body: str):
    proc = _run_driver(body)
    assert proc.returncode == 0, (
        f"driver failed rc={proc.returncode}\nSTDOUT:\n{proc.stdout}\n"
        f"STDERR:\n{proc.stderr}"
    )
    last = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@RESULT@@")]
    assert last, f"driver printed no result marker.\nSTDOUT:\n{proc.stdout}"
    return json.loads(last[-1][len("@@RESULT@@") :])


def _synthetic_pkg(tmp_path: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    root = tmp_path / "root"
    (root / "fakepkg").mkdir(parents=True)
    (root / "fakepkg" / "__init__.py").write_text("", encoding="utf-8")
    for name, src in files.items():
        target = root / "fakepkg" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(src), encoding="utf-8")
    return root


def _copy_real_pkg(tmp_path: pathlib.Path) -> pathlib.Path:
    """Copy the real package so a fixture can mutate one file and re-gate it.

    ``tests/`` must be included: ``store/memory_store.py:34`` does
    ``from ..tests.fake_store import FakeStore``, so pruning tests/ makes the
    module genuinely unresolvable and the gate (correctly) fails closed on
    that instead of on the mutation under test.
    """
    root = tmp_path / "realroot"
    root.mkdir()
    shutil.copytree(
        PKG_DIR,
        root / "scudo_mapping_mcp",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "fixtures", "docs"),
    )
    return root


# --------------------------------------------------------------------------
# 1. The defect itself: one hop is not enough.
# --------------------------------------------------------------------------


def test_alias_one_hop_defeats_a_naive_gate_but_not_the_transitive_walk(tmp_path):
    """Reproduces the exact shape of the real defect on a synthetic package.

    ``entry`` imports ``helper`` from ``alias`` — a name that is NOT on any
    literal forbidden-name list — and ``alias`` is what pulls in the writer.
    A one-hop AST scan of ``entry`` sees nothing. The transitive walk must.
    """
    root = _synthetic_pkg(
        tmp_path,
        {
            "entry.py": """
                from .alias import helper

                def boot():
                    helper()
            """,
            "alias.py": """
                from .writer import import_bundle

                def helper():
                    return import_bundle()
            """,
            "writer.py": """
                def import_bundle():
                    store = object()
                    store.upsert_precedent(1)
            """,
        },
    )
    out = _driver_json(
        f"""
        import ast, json, pathlib
        root = pathlib.Path({str(root)!r})

        # One-hop equivalent of the ORIGINAL gate.
        tree = ast.parse((root / "fakepkg" / "entry.py").read_text())
        one_hop = []
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                for a in n.names:
                    if a.name in {{"apply_decision", "import_bundle", "upsert_precedent"}}:
                        one_hop.append(a.name)

        rep = smoke._trust_write_reachability(
            "fakepkg.entry", package_root=root, package_name="fakepkg"
        )
        print("@@RESULT@@" + json.dumps({{
            "one_hop": one_hop,
            "writers": sorted(rep["writers"]),
            "truncated": rep["truncated"],
            "unresolved": sorted(rep["unresolved"]),
            "chain": rep["chains"].get("fakepkg.writer"),
        }}))
        """
    )
    assert out["one_hop"] == [], "the naive one-hop scan should see nothing"
    assert "fakepkg.writer" in out["writers"], (
        "transitive walk must reach the writer through the alias hop"
    )
    assert out["truncated"] is False
    assert out["chain"] == ["fakepkg.entry", "fakepkg.alias", "fakepkg.writer"]


def test_transitive_walk_is_cycle_safe(tmp_path):
    root = _synthetic_pkg(
        tmp_path,
        {
            "entry.py": "from .a import x\n",
            "a.py": "from .b import y\nx = 1\n",
            "b.py": "from .a import x\nfrom .w import z\ny = 1\n",
            "w.py": "z = 1\ndef go():\n    store.upsert_precedent(1)\n",
        },
    )
    out = _driver_json(
        f"""
        import json, pathlib
        rep = smoke._trust_write_reachability(
            "fakepkg.entry",
            package_root=pathlib.Path({str(root)!r}),
            package_name="fakepkg",
        )
        print("@@RESULT@@" + json.dumps({{
            "writers": sorted(rep["writers"]),
            "modules": sorted(rep["modules"]),
        }}))
        """
    )
    assert "fakepkg.w" in out["writers"]
    assert sorted(out["modules"]) == [
        "fakepkg.a",
        "fakepkg.b",
        "fakepkg.entry",
        "fakepkg.w",
    ]


def test_depth_bound_reports_truncation_rather_than_silently_stopping(tmp_path):
    """A silently-truncated walk is the same false assurance as a one-hop walk,
    so truncation must be reported so the caller can fail closed."""
    files = {"entry.py": "from .m0 import v\n"}
    for i in range(6):
        files[f"m{i}.py"] = f"from .m{i + 1} import v\n"
    files["m6.py"] = "v = 1\ndef go():\n    store.upsert_precedent(1)\n"
    root = _synthetic_pkg(tmp_path, files)
    out = _driver_json(
        f"""
        import json, pathlib
        root = pathlib.Path({str(root)!r})
        shallow = smoke._trust_write_reachability(
            "fakepkg.entry", package_root=root, package_name="fakepkg", max_depth=3
        )
        deep = smoke._trust_write_reachability(
            "fakepkg.entry", package_root=root, package_name="fakepkg", max_depth=20
        )
        print("@@RESULT@@" + json.dumps({{
            "shallow_truncated": shallow["truncated"],
            "shallow_writers": sorted(shallow["writers"]),
            "deep_truncated": deep["truncated"],
            "deep_writers": sorted(deep["writers"]),
        }}))
        """
    )
    assert out["shallow_truncated"] is True
    assert out["shallow_writers"] == []
    assert out["deep_truncated"] is False
    assert out["deep_writers"] == ["fakepkg.m6"]


def test_self_and_super_receivers_are_not_counted_as_writes(tmp_path):
    """``self.upsert_precedent(...)`` inside a store implementation is the
    store's own definition, not a caller reaching a write surface. Counting it
    would flood the gate with noise and train people to widen the allowlist."""
    root = _synthetic_pkg(
        tmp_path,
        {
            "entry.py": "from .impl import Thing\n",
            "impl.py": """
                class Thing:
                    def load(self):
                        self.upsert_precedent(1)
                        super().upsert_precedent(2)
            """,
        },
    )
    out = _driver_json(
        f"""
        import json, pathlib
        rep = smoke._trust_write_reachability(
            "fakepkg.entry",
            package_root=pathlib.Path({str(root)!r}),
            package_name="fakepkg",
        )
        print("@@RESULT@@" + json.dumps({{"writers": sorted(rep["writers"])}}))
        """
    )
    assert out["writers"] == []


# --------------------------------------------------------------------------
# 2. The real modules: what the honest invariant actually is.
# --------------------------------------------------------------------------


def test_ingestion_entry_reaches_no_writers_at_all():
    out = _driver_json(
        """
        import json
        rep = smoke._trust_write_reachability("scudo_mapping_mcp.ingestion_mcp")
        print("@@RESULT@@" + json.dumps({
            "writers": sorted(rep["writers"]),
            "truncated": rep["truncated"],
            "unresolved": sorted(rep["unresolved"]),
        }))
        """
    )
    assert out["writers"] == [], (
        "Ingestion MCP must reach zero write surfaces, transitively"
    )
    assert out["truncated"] is False
    assert out["unresolved"] == []


def test_match_verify_really_does_reach_writers_transitively():
    """The honest fact the old gate hid. This test exists so nobody 'fixes'
    the gate by pretending the reachability is not there."""
    out = _driver_json(
        """
        import json
        rep = smoke._trust_write_reachability("scudo_mapping_mcp.match_verify_mcp")
        print("@@RESULT@@" + json.dumps({
            "writers": {k: sorted(v) for k, v in rep["writers"].items()},
            "boot_bindings": sorted(rep["binding_writers"]),
        }))
        """
    )
    assert "scudo_mapping_mcp.bundle" in out["writers"]
    assert "upsert_precedent" in out["writers"]["scudo_mapping_mcp.bundle"]
    assert "scudo_mapping_mcp.ingest" in out["writers"]
    # And the reachability enters through exactly the boot-time seam names.
    assert sorted(out["boot_bindings"]) == [
        "HydrationError",
        "hydrate",
        "seed_taxonomy",
    ]


def test_persistence_entry_still_owns_the_write_role():
    out = _driver_json(
        """
        import json
        rep = smoke._trust_write_reachability("scudo_mapping_mcp.persistence_mcp")
        print("@@RESULT@@" + json.dumps({
            "writers": {k: sorted(v) for k, v in rep["writers"].items()},
        }))
        """
    )
    assert "scudo_mapping_mcp.feedback" in out["writers"]
    assert "scudo_mapping_mcp.persistence_mcp" in out["writers"]
    own = out["writers"]["scudo_mapping_mcp.persistence_mcp"]
    assert "apply_decision" in own and "import_bundle" in own


# --------------------------------------------------------------------------
# 3. The allowlist must be TIGHT: a write on a request-handling path fails.
# --------------------------------------------------------------------------


def test_write_added_to_a_request_handler_fails_the_gate(tmp_path):
    """The whole point of the narrow allowlist. Boot-time seed/hydrate is
    permitted; the same call inside ``matchverify.verify_mapping`` is not."""
    root = _copy_real_pkg(tmp_path)
    target = root / "scudo_mapping_mcp" / "match_verify_mcp.py"
    src = target.read_text(encoding="utf-8")
    # Anchor on the matcher call inside verify_mapping, NOT on the frame-
    # resolution lines above it: those were rewritten by the inline-frame fix
    # mid-remediation and a literal multi-line needle silently drifted. Match
    # the single stable line and inject the write immediately before it.
    needle = "    result = map_vendor_product("
    assert needle in src, "fixture anchor drifted; update this test"
    src = src.replace(
        needle,
        "    get_store().upsert_precedent(vendor=params.vendor)\n" + needle,
        1,
    )
    target.write_text(src, encoding="utf-8")

    out = _driver_json(
        f"""
        import json, pathlib
        try:
            smoke._trust_assert_match_verify_read_only(
                package_root=pathlib.Path({str(root)!r})
            )
        except AssertionError as e:
            print("@@RESULT@@" + json.dumps({{"failed": True, "msg": str(e)}}))
        else:
            print("@@RESULT@@" + json.dumps({{"failed": False, "msg": ""}}))
        """
    )
    assert out["failed"] is True, (
        "adding upsert_precedent to a tool handler must fail the gate"
    )
    assert "verify_mapping" in out["msg"]


def test_new_writer_import_used_at_boot_still_fails_the_gate(tmp_path):
    """The allowlist is by BINDING, not 'anything in _lifespan'. Importing a
    brand-new write surface and calling it at boot must still fail."""
    root = _copy_real_pkg(tmp_path)
    target = root / "scudo_mapping_mcp" / "match_verify_mcp.py"
    src = target.read_text(encoding="utf-8")
    import_anchor = "from .ingest import seed_taxonomy\n"
    call_anchor = "        n = seed_taxonomy()\n"
    assert import_anchor in src, "fixture anchor drifted; update this test"
    assert call_anchor in src, "fixture anchor drifted; update this test"
    src = src.replace(
        import_anchor,
        import_anchor + "from .feedback import apply_decision\n",
        1,
    )
    src = src.replace(
        call_anchor,
        call_anchor + "        apply_decision(vendor='x')\n",
        1,
    )
    target.write_text(src, encoding="utf-8")

    out = _driver_json(
        f"""
        import json, pathlib
        try:
            smoke._trust_assert_match_verify_read_only(
                package_root=pathlib.Path({str(root)!r})
            )
        except AssertionError as e:
            print("@@RESULT@@" + json.dumps({{"failed": True, "msg": str(e)}}))
        else:
            print("@@RESULT@@" + json.dumps({{"failed": False, "msg": ""}}))
        """
    )
    assert out["failed"] is True
    assert "apply_decision" in out["msg"] or "feedback" in out["msg"]


def test_renaming_the_boot_region_fails_closed(tmp_path):
    """If ``_lifespan`` is renamed, the allowlisted bindings are no longer
    confined to a documented boot region — the gate must fail rather than
    silently stop enforcing confinement."""
    root = _copy_real_pkg(tmp_path)
    target = root / "scudo_mapping_mcp" / "match_verify_mcp.py"
    src = target.read_text(encoding="utf-8")
    src = src.replace("async def _lifespan(", "async def _startup(", 1)
    src = src.replace("lifespan=_lifespan", "lifespan=_startup", 1)
    target.write_text(src, encoding="utf-8")

    out = _driver_json(
        f"""
        import json, pathlib
        try:
            smoke._trust_assert_match_verify_read_only(
                package_root=pathlib.Path({str(root)!r})
            )
        except AssertionError as e:
            print("@@RESULT@@" + json.dumps({{"failed": True, "msg": str(e)}}))
        else:
            print("@@RESULT@@" + json.dumps({{"failed": False, "msg": ""}}))
        """
    )
    assert out["failed"] is True


def test_unmodified_tree_passes_the_gate():
    out = _driver_json(
        """
        import json
        try:
            smoke._trust_assert_match_verify_read_only()
            smoke._trust_assert_ingestion_read_only()
        except AssertionError as e:
            print("@@RESULT@@" + json.dumps({"failed": True, "msg": str(e)}))
        else:
            print("@@RESULT@@" + json.dumps({"failed": False, "msg": ""}))
        """
    )
    assert out["failed"] is False, out["msg"]


# --------------------------------------------------------------------------
# 4. The suite as a whole must not regress.
# --------------------------------------------------------------------------


def test_smoke_suite_still_passes_and_gates_are_present():
    proc = subprocess.run(
        [sys.executable, "-m", "scudo_mapping_mcp.tests.smoke"],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"smoke failed:\n{proc.stdout}\n{proc.stderr}"
    tail = [ln for ln in proc.stdout.splitlines() if "/" in ln and "pass" in ln][-1]
    passed, total = tail.split()[0].split("/")
    assert int(passed) == int(total)
    assert int(passed) >= 117, tail
    for gate in (
        "TRUST_ingestion_mcp_imports_no_writers",
        "TRUST_match_verify_mcp_imports_no_writers",
        "TRUST_persistence_mcp_imports_writers",
    ):
        assert f"PASS  {gate}" in proc.stdout, gate


# --------------------------------------------------------------------------
# 6. The allowlist must key on RESOLVED ORIGIN, not the local binding name.
# --------------------------------------------------------------------------


def test_aliasing_a_writer_onto_an_allowlisted_name_does_not_bypass(tmp_path):
    """``from .feedback import apply_decision as HydrationError`` renames a
    writer to a name that IS on the boot allowlist.

    The first version of this gate keyed the allowlist on the local binding
    name, so this mutation leaked NOTHING and passed silently — the same
    failure class as the one-hop gate it replaced (trusting a name instead of
    a target). Found by an adversarial verifier. The allowlist now matches
    ``<module>.<symbol>``, which the importer cannot choose.
    """
    root = _copy_real_pkg(tmp_path)
    target = root / "scudo_mapping_mcp" / "match_verify_mcp.py"
    src = target.read_text(encoding="utf-8")
    needle = "from .hydrate import HydrationError, hydrate"
    assert needle in src, "fixture anchor drifted; update this test"
    target.write_text(
        src.replace(
            needle,
            "from .hydrate import hydrate\n"
            "from .feedback import apply_decision as HydrationError",
            1,
        ),
        encoding="utf-8",
    )

    out = _driver_json(
        f"""
        import json, pathlib
        rep = smoke._trust_write_reachability(
            "scudo_mapping_mcp.match_verify_mcp",
            package_root=pathlib.Path({str(root)!r}),
        )
        allow = smoke._TRUST_MV_BOOT_BINDING_ORIGINS
        print("@@RESULT@@" + json.dumps({{
            "origins": rep["binding_writer_origins"],
            "leaked": sorted(set(rep["binding_writer_origins"]) - allow),
        }}))
        """
    )
    assert "scudo_mapping_mcp.feedback.apply_decision" in out["origins"], (
        "the aliased writer's true origin was not resolved"
    )
    assert out["leaked"] == ["scudo_mapping_mcp.feedback.apply_decision"], (
        f"aliasing bypassed the allowlist; leaked={out['leaked']}"
    )


# --------------------------------------------------------------------------
# 7. The remaining adversarial bypasses, each pinned by a mutation.
# --------------------------------------------------------------------------


def _gate_rejects(tmp_path, mutate) -> str:
    """Apply ``mutate`` to a copy of match_verify_mcp.py and return the gate's
    failure text. Empty string means the gate PASSED (i.e. bypass still live)."""
    root = _copy_real_pkg(tmp_path)
    target = root / "scudo_mapping_mcp" / "match_verify_mcp.py"
    target.write_text(mutate(target.read_text(encoding="utf-8")), encoding="utf-8")

    out = _driver_json(
        f"""
        import json, pathlib, traceback
        root = pathlib.Path({str(root)!r})
        err = ""
        try:
            smoke._trust_assert_match_verify_read_only(package_root=root)
        except AssertionError as e:
            err = str(e)
        except Exception as e:
            err = f"{{type(e).__name__}}: {{e}}"
        print("@@RESULT@@" + json.dumps({{"err": err}}))
        """
    )
    return out["err"]


_HANDLER_ANCHOR = "    result = map_vendor_product("


def test_bare_reference_to_a_writer_in_a_handler_is_caught(tmp_path):
    """``_w = get_store().upsert_precedent`` then ``_w(...)``. The CALL is named
    ``_w``, so call-name matching alone never fired — the direct form was caught
    and this one-line sibling walked past."""
    err = _gate_rejects(
        tmp_path,
        lambda s: s.replace(
            _HANDLER_ANCHOR,
            "    _w = get_store().upsert_precedent\n"
            "    _w(vendor=params.vendor)\n" + _HANDLER_ANCHOR,
            1,
        ),
    )
    assert "upsert_precedent" in err, f"bare-reference bypass not caught: {err!r}"


def test_write_sharing_a_line_with_an_import_is_caught(tmp_path):
    """``import os; hydrate(strict=False)`` — one line, two statements. The
    skip set keyed on lineno, so the hydrate call was invisible."""
    err = _gate_rejects(
        tmp_path,
        lambda s: s.replace(
            _HANDLER_ANCHOR,
            "    import os; hydrate(strict=False)\n" + _HANDLER_ANCHOR,
            1,
        ),
    )
    assert "hydrate" in err, f"import-lineno bypass not caught: {err!r}"


def test_absolute_dotted_call_to_an_allowlisted_binding_is_caught(tmp_path):
    """``scudo_mapping_mcp.hydrate.hydrate(...)``. The ast.Name is the ROOT
    package, not ``hydrate``, so bare-Name matching missed it while the
    equivalent relative import was caught."""

    def mutate(s: str) -> str:
        s = s.replace(
            "from .hydrate import HydrationError, hydrate",
            "from .hydrate import HydrationError, hydrate\nimport scudo_mapping_mcp.hydrate",
            1,
        )
        return s.replace(
            _HANDLER_ANCHOR,
            "    scudo_mapping_mcp.hydrate.hydrate(strict=False)\n" + _HANDLER_ANCHOR,
            1,
        )

    err = _gate_rejects(tmp_path, mutate)
    assert "hydrate" in err, f"absolute-import bypass not caught: {err!r}"


def test_indirection_module_behind_an_allowlisted_name_is_caught(tmp_path):
    """A new module re-exporting ``hydrate`` while also calling a writer. The
    binding name is unchanged, so a name-keyed allowlist saw nothing new."""
    root = _copy_real_pkg(tmp_path)
    (root / "scudo_mapping_mcp" / "boot.py").write_text(
        "from .hydrate import HydrationError, hydrate as _h\n"
        "from .feedback import apply_decision\n\n"
        "def hydrate(strict=False):\n"
        "    apply_decision(vendor='anything')\n"
        "    return _h(strict=strict)\n",
        encoding="utf-8",
    )
    target = root / "scudo_mapping_mcp" / "match_verify_mcp.py"
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "from .hydrate import HydrationError, hydrate",
            "from .boot import HydrationError, hydrate",
            1,
        ),
        encoding="utf-8",
    )

    out = _driver_json(
        f"""
        import json, pathlib
        rep = smoke._trust_write_reachability(
            "scudo_mapping_mcp.match_verify_mcp",
            package_root=pathlib.Path({str(root)!r}),
        )
        allow = smoke._TRUST_MV_BOOT_BINDING_ORIGINS
        print("@@RESULT@@" + json.dumps({{
            "leaked": sorted(set(rep["binding_writer_origins"]) - allow),
        }}))
        """
    )
    assert out["leaked"], "indirection-module bypass not caught"
    assert any("boot" in x for x in out["leaked"]), out["leaked"]


def test_dynamic_dispatch_is_a_KNOWN_gap_not_a_silent_one(tmp_path):
    """``getattr(store, 'upsert_' + 'precedent')(...)`` is NOT caught, and
    cannot be by static analysis — no literal write name exists in the AST.

    This test asserts the gap is REAL so nobody mistakes the gate for proof
    that no write exists. If a future runtime control closes it, this test
    fails and should be replaced by one asserting the catch.
    """
    err = _gate_rejects(
        tmp_path,
        lambda s: s.replace(
            _HANDLER_ANCHOR,
            "    getattr(get_store(), 'upsert_' + 'precedent')(vendor=params.vendor)\n"
            + _HANDLER_ANCHOR,
            1,
        ),
    )
    assert err == "", (
        "dynamic dispatch is now caught — good. Replace this test with a "
        f"positive assertion and update the RESIDUAL RISK note. Got: {err!r}"
    )
