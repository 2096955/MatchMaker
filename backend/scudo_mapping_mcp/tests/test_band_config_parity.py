"""Confidence-band parity: code default vs. every infra deploy template.

The product contract (CLAUDE.md) pins the matching ladder's bands at
**passCut 0.80 / failCut 0.70**. Those two numbers are derived, not literal:
``config.pass_threshold()`` = ``round(CONFIDENCE_FLOOR + BORDERLINE_HALF_WIDTH, 2)``
and ``config.borderline_threshold()`` = ``round(CONFIDENCE_FLOOR - BORDERLINE_HALF_WIDTH, 2)``.

That indirection is exactly how the bands drifted: the CloudFormation task
definitions set ``CONFIDENCE_FLOOR=0.80`` (with the default half-width 0.05),
which resolves to a **0.85 / 0.75** ladder — one full step tighter than the
documented contract — so the deployed ECS services silently disagreed with both
the code default and the docs.

These tests parse the deploy artifacts off disk so the drift cannot come back
without a red test. Scope is the CloudFormation templates under ``infra/``
**and** ``backend/Dockerfile``.

The Dockerfile is load-bearing, not incidental: it declares ``CONFIDENCE_FLOOR``
without ``BORDERLINE_HALF_WIDTH``, and the ``ingestion-mcp`` container
(``infra/scudo-dev-deploy.yaml``, ~line 468) declares **no** band vars at all —
so that service inherits the image default. "Every ECS container overrides it"
is false, which is precisely how a fourth drift site survived a fix that
corrected the three explicit ones.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from scudo_mapping_mcp import config

# The product contract. Do not soften these without an explicit owner decision;
# CLAUDE.md ("Confidence bands: passCut 0.80 / failCut 0.70") is the source.
CONTRACT_PASS_CUT = 0.80
CONTRACT_FAIL_CUT = 0.70

_FLOOR_VAR = "CONFIDENCE_FLOOR"
_HALF_VAR = "BORDERLINE_HALF_WIDTH"
_BAND_VARS = (_FLOOR_VAR, _HALF_VAR)


def _repo_root() -> Path:
    """Walk up from this file until the directory holding ``infra/`` is found.

    Independent of pytest's cwd (root, ``backend/``, or the test dir all work).
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "infra").is_dir() and (parent / "backend").is_dir():
            return parent
    raise AssertionError(
        "could not locate the repo root (a parent containing both infra/ and backend/)"
    )


def _infra_templates() -> list[Path]:
    infra = _repo_root() / "infra"
    return sorted(
        p
        for p in list(infra.rglob("*.yaml")) + list(infra.rglob("*.yml"))
        if "__pycache__" not in p.parts
    )


# Inline flow style: ``- { Name: CONFIDENCE_FLOOR, Value: '0.80' }``
_INLINE = re.compile(
    r"\{\s*Name:\s*['\"]?(?P<name>[A-Za-z_][A-Za-z0-9_]*)['\"]?\s*,"
    r"\s*Value:\s*['\"]?(?P<value>[^'\"},]+?)['\"]?\s*\}"
)
# Block style: ``- Name: CONFIDENCE_FLOOR`` then ``  Value: '0.80'``
_BLOCK_NAME = re.compile(
    r"^\s*-?\s*Name:\s*['\"]?(?P<name>[A-Za-z_][A-Za-z0-9_]*)['\"]?\s*$"
)
_BLOCK_VALUE = re.compile(r"^\s*Value:\s*['\"]?(?P<value>[^'\"#]*?)['\"]?\s*$")
_ENV_HEADER = re.compile(r"^\s*Environment:\s*$")


class _CfnLoader(yaml.SafeLoader):
    """SafeLoader that tolerates CloudFormation short-form tags.

    ``scudo-dev-deploy.yaml`` uses ~135 of them (``!ImportValue``, ``!Ref``,
    ``!Sub``, ``!GetAtt``). We do not need their semantics — only the
    Environment mappings — so every unknown tag resolves to a placeholder.
    """


def _cfn_any_tag(loader, tag_suffix, node):  # pragma: no cover - trivial
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


_CfnLoader.add_multi_constructor("!", _cfn_any_tag)


def _parse_band_declarations_structural(path: Path) -> list[dict]:
    """Parse band vars by walking the loaded YAML, not by matching text.

    The regex scanner below pairs ``Name:``/``Value:`` in source order, so the
    equally-legal ``Value:`` before ``Name:`` form parsed as ZERO records and
    drift sailed through the gate. (Found by an adversarial verifier, who
    measured it against the real template rather than asserting it.) A real
    parse is key-order independent by construction.
    """
    try:
        doc = yaml.load(path.read_text(encoding="utf-8"), Loader=_CfnLoader)
    except yaml.YAMLError:  # pragma: no cover - malformed template
        return []

    out: list[dict] = []

    def walk(node):
        if isinstance(node, dict):
            env = node.get("Environment")
            if isinstance(env, list):
                found: dict[str, str] = {}
                for item in env:
                    if not isinstance(item, dict):
                        continue
                    name, value = item.get("Name"), item.get("Value")
                    if name in _BAND_VARS and value is not None:
                        found[str(name)] = str(value).strip()
                if found:
                    out.append({"file": path, "line": 0, "vars": found})
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(doc)
    return out


def _parse_band_declarations(path: Path) -> list[dict]:
    """Return one record per ``Environment:`` block that declares a band var.

    Each record: ``{"file", "line", "vars": {NAME: raw_value}}``. Grouping by
    the enclosing ``Environment:`` block is what makes the floor/half pairing
    meaningful — a per-file aggregate would mask one container drifting.

    Line numbers come from this text scan (useful in failure messages); the
    structural parse above is what makes coverage key-order independent. The
    two are unioned by ``_infra_band_declarations``.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks: dict[int, dict] = {}
    current_env_line = -1
    pending_name: str | None = None

    for idx, raw in enumerate(lines, start=1):
        if _ENV_HEADER.match(raw):
            current_env_line = idx
            pending_name = None
            continue

        found: list[tuple[str, str]] = [
            (m.group("name"), m.group("value").strip()) for m in _INLINE.finditer(raw)
        ]

        if not found:
            name_match = _BLOCK_NAME.match(raw)
            if name_match:
                pending_name = name_match.group("name")
            elif pending_name is not None:
                value_match = _BLOCK_VALUE.match(raw)
                if value_match:
                    found = [(pending_name, value_match.group("value").strip())]
                    pending_name = None
                elif raw.strip():
                    pending_name = None

        for name, value in found:
            if name not in _BAND_VARS:
                continue
            key = current_env_line
            record = blocks.setdefault(
                key,
                {"file": path, "line": key if key > 0 else idx, "vars": {}},
            )
            record["vars"][name] = value

    return [blocks[k] for k in sorted(blocks)]


# Dockerfile ENV continuation line: ``    CONFIDENCE_FLOOR=0.75`` (with or
# without a trailing ``\``), optionally preceded by ``ENV ``.
_DOCKER_ENV = re.compile(
    r"^\s*(?:ENV\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>[^\s\\]+)"
)


def _parse_dockerfile_bands(path: Path) -> list[dict]:
    """Band vars declared as image-level ENV defaults.

    All ENV defaults in one Dockerfile share a single scope, so they collapse
    into one record — matching how a container that overrides neither var sees
    them.
    """
    if not path.is_file():
        return []
    found: dict[str, str] = {}
    first_line = 0
    for idx, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = _DOCKER_ENV.match(raw)
        if not match or match.group("name") not in _BAND_VARS:
            continue
        found[match.group("name")] = match.group("value").strip().strip("'\"")
        first_line = first_line or idx
    return [{"file": path, "line": first_line, "vars": found}] if found else []


def _infra_band_declarations() -> list[dict]:
    """Band declarations from the CloudFormation templates under ``infra/``.

    Union of the structural (YAML) and textual (regex) scans. The structural
    scan is authoritative for COVERAGE — it cannot be evaded by key order —
    while the text scan contributes line numbers. Any var pair either sees is
    gated; a record the text scan misses is still caught structurally.
    """
    out: list[dict] = []
    for template in _infra_templates():
        structural = _parse_band_declarations_structural(template)
        textual = _parse_band_declarations(template)
        # Same var-pair from both scans -> prefer the textual record (it has a
        # line number). Structural-only records are appended so key-order
        # variants cannot hide.
        textual_sigs = {tuple(sorted(r["vars"].items())) for r in textual}
        out.extend(textual)
        out.extend(
            r
            for r in structural
            if tuple(sorted(r["vars"].items())) not in textual_sigs
        )
    return out


def _dockerfile_band_declarations() -> list[dict]:
    return _parse_dockerfile_bands(_repo_root() / "backend" / "Dockerfile")


def _all_band_declarations() -> list[dict]:
    return _infra_band_declarations() + _dockerfile_band_declarations()


def _resolve_bands(record: dict) -> tuple[float, float]:
    """Resolve one declaration to (pass_cut, borderline_cut).

    A record that declares only one of the two vars inherits the code default
    for the other, exactly as ``config.Settings.from_env()`` does.
    """
    floor = float(record["vars"].get(_FLOOR_VAR, config.CONFIDENCE_FLOOR))
    half = float(record["vars"].get(_HALF_VAR, config.BORDERLINE_HALF_WIDTH))
    return config.pass_threshold(floor, half), config.borderline_threshold(floor, half)


def _drift_offenders(records: list[dict]) -> list[str]:
    offenders: list[str] = []
    for record in records:
        pass_cut, fail_cut = _resolve_bands(record)
        if (pass_cut, fail_cut) != (CONTRACT_PASS_CUT, CONTRACT_FAIL_CUT):
            offenders.append(
                f"{record['file'].name}:{record['line']} "
                f"({_FLOOR_VAR}={record['vars'].get(_FLOOR_VAR, config.CONFIDENCE_FLOOR)}, "
                f"{_HALF_VAR}={record['vars'].get(_HALF_VAR, config.BORDERLINE_HALF_WIDTH)}) "
                f"-> pass={pass_cut} borderline={fail_cut}, "
                f"want pass={CONTRACT_PASS_CUT} borderline={CONTRACT_FAIL_CUT}"
            )
    return offenders


def test_code_default_bands_match_product_contract() -> None:
    """config.py defaults must resolve to the documented 0.80 / 0.70 ladder."""
    assert config.pass_threshold() == CONTRACT_PASS_CUT
    assert config.borderline_threshold() == CONTRACT_FAIL_CUT


def test_parser_finds_the_known_band_declarations() -> None:
    """Guard against a silently-matching-nothing parser making parity vacuous.

    ``infra/scudo-dev-deploy.yaml`` declares the pair on three ECS containers
    (flask, match-verify-mcp, persistence-mcp).
    """
    declarations = _all_band_declarations()
    assert declarations, (
        "parser found no CONFIDENCE_FLOOR/BORDERLINE_HALF_WIDTH sites in infra/"
    )

    dev_deploy = [d for d in declarations if d["file"].name == "scudo-dev-deploy.yaml"]
    assert len(dev_deploy) >= 3, (
        "expected >=3 Environment blocks declaring band vars in scudo-dev-deploy.yaml, "
        f"got {len(dev_deploy)}"
    )
    for record in declarations:
        assert record["vars"], (
            f"empty declaration record at {record['file']}:{record['line']}"
        )


def test_parser_finds_the_dockerfile_band_declaration() -> None:
    """The Dockerfile ENV default must be parsed, not silently skipped.

    Without this the Dockerfile parser could match nothing and parity over it
    would pass vacuously -- the exact way this site was missed the first time.
    """
    records = _parse_dockerfile_bands(_repo_root() / "backend" / "Dockerfile")
    assert len(records) == 1, (
        f"expected exactly one Dockerfile band record, got {len(records)}"
    )
    assert _FLOOR_VAR in records[0]["vars"], (
        f"{_FLOOR_VAR} not parsed from backend/Dockerfile"
    )


@pytest.mark.parametrize("var", _BAND_VARS)
def test_declared_band_values_are_parseable_floats(var: str) -> None:
    for record in _all_band_declarations():
        if var not in record["vars"]:
            continue
        raw = record["vars"][var]
        try:
            float(raw)
        except ValueError:  # pragma: no cover - only on a malformed template
            pytest.fail(
                f"{record['file']}:{record['line']} declares {var}={raw!r}, not a float"
            )


def test_infra_templates_yield_the_contract_bands() -> None:
    """Every infra Environment block must resolve to pass 0.80 / borderline 0.70.

    A block that declares only one of the two vars inherits the code default for
    the other -- resolved the same way ``config.Settings.from_env()`` does -- so
    half-only drift is caught too.
    """
    offenders = _drift_offenders(_infra_band_declarations())
    assert not offenders, "infra confidence-band drift:\n  " + "\n  ".join(offenders)


def test_dockerfile_env_default_yields_the_contract_bands() -> None:
    """The image-level ENV default must also resolve to the contract bands.

    This is not redundant with the infra gate. The ``ingestion-mcp`` container in
    ``infra/scudo-dev-deploy.yaml`` declares no band vars at all, so it inherits
    whatever the image sets -- "every ECS container overrides it" is false.
    """
    records = _dockerfile_band_declarations()
    assert records, "backend/Dockerfile declares no band vars -- parser or file changed"
    offenders = _drift_offenders(records)
    assert not offenders, "Dockerfile confidence-band drift:\n  " + "\n  ".join(
        offenders
    )


def test_ingestion_mcp_container_inherits_the_image_default() -> None:
    """Pin the reason the Dockerfile matters: ingestion-mcp overrides nothing.

    If someone later adds explicit band vars to that container, this test fails
    and the xfail above can be re-evaluated on real evidence rather than memory.
    """
    template = _repo_root() / "infra" / "scudo-dev-deploy.yaml"
    lines = template.read_text(encoding="utf-8").splitlines()

    start = next(
        (i for i, ln in enumerate(lines) if "- Name: ingestion-mcp" in ln), None
    )
    assert start is not None, (
        "ingestion-mcp container not found in scudo-dev-deploy.yaml"
    )
    end = next(
        (
            i
            for i, ln in enumerate(lines[start + 1 :], start=start + 1)
            if "- Name: " in ln
        ),
        len(lines),
    )

    declared = [
        (i + 1, ln.strip())
        for i, ln in enumerate(lines[start:end], start=start)
        if any(var in ln for var in _BAND_VARS)
    ]
    assert not declared, (
        "ingestion-mcp now declares band vars; update the Dockerfile xfail rationale: "
        f"{declared}"
    )


def test_key_order_cannot_hide_a_band_declaration(tmp_path):
    """``Value:`` before ``Name:`` is legal YAML with identical CloudFormation
    semantics. The original regex scanner paired keys in SOURCE ORDER and
    returned ZERO records for that form, so drift written that way passed the
    gate silently. Found by an adversarial verifier who measured it against the
    real template rather than asserting it.
    """
    canonical = tmp_path / "canonical.yaml"
    canonical.write_text(
        "Resources:\n"
        "  Svc:\n"
        "    Properties:\n"
        "      ContainerDefinitions:\n"
        "        - Name: svc\n"
        "          Environment:\n"
        "            - Name: CONFIDENCE_FLOOR\n"
        "              Value: '0.80'\n",
        encoding="utf-8",
    )
    reordered = tmp_path / "reordered.yaml"
    reordered.write_text(
        "Resources:\n"
        "  Svc:\n"
        "    Properties:\n"
        "      ContainerDefinitions:\n"
        "        - Name: svc\n"
        "          Environment:\n"
        "            - Value: '0.80'\n"
        "              Name: CONFIDENCE_FLOOR\n",
        encoding="utf-8",
    )

    for path in (canonical, reordered):
        records = _parse_band_declarations_structural(path)
        assert records, f"{path.name}: structural parse found no band vars"
        assert records[0]["vars"][_FLOOR_VAR] == "0.80"
        # And the drift is actually reported, not merely parsed.
        assert _drift_offenders(records), f"{path.name}: 0.80 drift not flagged"


def test_structural_parser_sees_the_real_templates():
    """Anti-vacuity: a loader that silently failed on CloudFormation's ~135
    short-form tags (!Ref, !ImportValue, !Sub) would return [] for every file
    and make the key-order defence useless."""
    total = sum(
        len(_parse_band_declarations_structural(t)) for t in _infra_templates()
    )
    assert total >= 3, (
        f"structural parse found {total} band declarations in infra/; "
        "expected >=3 (flask, match-verify-mcp, persistence-mcp)"
    )
