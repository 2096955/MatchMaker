# SCUDO OKF Knowledge Bundle — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a navigable Open Knowledge Format (OKF) bundle at `docs/okf/scudo/` that consolidates ~39 scattered SCUDO/MatchMaker docs into 37 curated, typed, cross-linked concepts — produced reproducibly via the real `okf` CLI and gated by automated conformance, agent fan-out, and Codex review.

**Architecture:** A Python staging script (run under the OKF venv's interpreter, reusing `okf_toolkit`'s own `document`/`links` modules) reads a YAML manifest and emits a curated 7-folder tree into `build/okf-src/` (gitignored). A thin `build_bundle.sh` then runs `okf convert → validate → evals → visualize` to produce `docs/okf/scudo/`. Verification is layered: a self-verifying agent fan-out checks every concept's metadata against its content (loop-until-clean), a link-integrity loop fixes the load-bearing cross-links, and Codex reviews the finished bundle before a human pre-commit gate.

**Tech Stack:** Python 3.11+ (OKF venv), PyYAML (ships with `okf_toolkit`), the `okf` CLI (`okf convert/validate/evals/visualize`), bash, pytest, Codex (via the codex plugin), and the Workflow tool for the verification fan-out.

**Spec:** `docs/superpowers/specs/2026-06-27-scudo-okf-knowledge-bundle-design.md`

## Global Constraints

- **OKF toolkit is a SEPARATE repo/venv** at `/Users/anthonylui/OpenKnowledgeFormat`. Resolve via `OKF_BIN="${OKF_BIN:-/Users/anthonylui/OpenKnowledgeFormat/.venv/bin/okf}"` and `OKF_PY="$(dirname "$OKF_BIN")/python"`. Never hard-assume; assert existence + that `--help` works.
- **Originals are NEVER moved, edited, or deleted.** The bundle is *copies*. No edits to any source doc, deploy config (`backend/Dockerfile`, `.dockerignore`, `infra/*.yml`, `infra/*.yaml`), runbook, or import path.
- **Bundle output:** `docs/okf/scudo/`. **Staging scratch:** `build/okf-src/` — gitignored, never committed.
- **37 concepts, 7 folders:** `architecture/` (7), `reference/` (2), `skills/` (5), `specs/` (6), `plans/` (6), `deployment/` (3), `handovers/` (8).
- **Type vocabulary (exactly 8):** `Architecture`, `Decision Record`, `Reference`, `Skill`, `Spec`, `Plan`, `Runbook`, `Handover`.
- **Frontmatter: MERGE, never blind-prepend.** Several sources (the 5 `SKILL.md` files) already have a frontmatter block; OKF parses only the first. Parse-and-overlay curated keys.
- **Validate gate = plain `okf validate` (zero errors).** `--strict` is advisory only (it fails on OKF-tolerated broken links, SPEC §9).
- **Automated evals that must pass:** 01 (conformance), 02 (index coverage), 06 (claude.md coverage), 07 (log). Evals 03/04/05/08 are agent-graded (advisory).
- **Branch:** work on the current `scudo-phase0-foundations` branch (additive docs only). Commit frequently.
- **Codex** is the review gate: use the codex plugin (`mcp__codex__codex`, read-only sandbox) for the spec/bundle reviews; hand-verify any refutation that would reverse a load-bearing decision.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `docs/okf/build/manifest.yaml` | Data: the 37-concept manifest (out path, source(s), type, title, description, tags, staleness, supersedes/superseded_by, transform). |
| `docs/okf/build/stage.py` | Staging driver: reads manifest, reuses `okf_toolkit.bundle.document`/`links`, emits `build/okf-src/`. Handles merge/split/fold + frontmatter overlay + link repoint. |
| `docs/okf/build/test_stage.py` | Unit tests for `merge_frontmatter`, `repoint_links`, and the 3 special transforms. |
| `docs/okf/build_bundle.sh` | Orchestrator: resolve OKF bin → stage → clean → convert → validate → evals → visualize. |
| `docs/okf/README.md` | How to rebuild the bundle. |
| `docs/okf/scudo/` | **Generated** bundle output (committed). |
| `.gitignore` | Add `build/okf-src/`. |
| `README.md` (root) | Two additive pointers (§7 of spec). |

---

## Task 1: Scaffolding, gitignore, and OKF-binary guardrail

**Files:**
- Create: `docs/okf/build/.gitkeep`
- Create: `docs/okf/build_bundle.sh`
- Modify: `.gitignore` (append one line)
- Test: manual (shell assertions below)

**Interfaces:**
- Produces: `build_bundle.sh` with a `resolve_okf()` preamble that exports `OKF_BIN` and `OKF_PY` and aborts loudly if missing. Later tasks append pipeline stages to this script.

- [ ] **Step 1: Create the build dir placeholder and gitignore entry**

```bash
mkdir -p docs/okf/build docs/okf/scudo
touch docs/okf/build/.gitkeep
printf '\n# OKF bundle staging scratch (build artifact, never committed)\nbuild/okf-src/\n' >> .gitignore
```

- [ ] **Step 2: Write the OKF-binary guardrail preamble of `build_bundle.sh`**

```bash
cat > docs/okf/build_bundle.sh <<'SH'
#!/usr/bin/env bash
# Reproducible build of the SCUDO OKF knowledge bundle.
# Usage:  OKF_BIN=/path/to/okf ./docs/okf/build_bundle.sh
# The OKF toolkit lives in a SEPARATE repo/venv; this script never edits sources.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

OKF_BIN="${OKF_BIN:-/Users/anthonylui/OpenKnowledgeFormat/.venv/bin/okf}"
OKF_PY="$(dirname "$OKF_BIN")/python"

if [ ! -x "$OKF_BIN" ]; then
  echo "ERROR: okf CLI not found at: $OKF_BIN" >&2
  echo "  Install: cd /Users/anthonylui/OpenKnowledgeFormat && python3 -m venv .venv && .venv/bin/pip install -e ." >&2
  echo "  Or set OKF_BIN to your okf binary." >&2
  exit 1
fi
"$OKF_BIN" --help >/dev/null 2>&1 || { echo "ERROR: '$OKF_BIN --help' failed" >&2; exit 1; }
"$OKF_PY" -c "import okf_toolkit, yaml" 2>/dev/null || { echo "ERROR: OKF venv python lacks okf_toolkit/yaml: $OKF_PY" >&2; exit 1; }

STAGE_SRC="$REPO_ROOT/build/okf-src"
BUNDLE_OUT="$REPO_ROOT/docs/okf/scudo"
MANIFEST="$REPO_ROOT/docs/okf/build/manifest.yaml"
echo "OKF_BIN=$OKF_BIN"
echo "OKF_PY=$OKF_PY"
echo "guardrail: OK"
SH
chmod +x docs/okf/build_bundle.sh
```

- [ ] **Step 3: Verify the guardrail passes with a real okf and fails without one**

Run:
```bash
./docs/okf/build_bundle.sh
OKF_BIN=/nonexistent/okf ./docs/okf/build_bundle.sh; echo "exit=$?"
```
Expected: first run prints `guardrail: OK`; second prints the install hint and `exit=1`.

- [ ] **Step 4: Commit**

```bash
git add docs/okf/build_bundle.sh docs/okf/build/.gitkeep .gitignore
git commit -m "feat(okf): build script scaffold + OKF-binary guardrail + gitignore staging"
```

---

## Task 2: Frontmatter-merge helper (TDD)

**Files:**
- Create: `docs/okf/build/stage.py`
- Test: `docs/okf/build/test_stage.py`

**Interfaces:**
- Produces: `merge_frontmatter(raw_text: str, curated: dict) -> str` — returns a full markdown doc string with a single frontmatter block where `curated` keys overlay any existing parsed frontmatter (existing keys preserved unless overridden), body untouched. Reuses `okf_toolkit.bundle.document.OKFDocument`.

- [ ] **Step 1: Write the failing test**

```python
# docs/okf/build/test_stage.py
import sys
from pathlib import Path

# stage.py runs under the OKF venv python; tests do too (see build_bundle docs).
sys.path.insert(0, str(Path(__file__).parent))
from stage import merge_frontmatter  # noqa: E402


def test_merge_overlays_onto_existing_frontmatter_single_block():
    raw = "---\nname: taxonomy-mapping\ndescription: old\n---\n\n# Body\n\ntext\n"
    out = merge_frontmatter(raw, {"type": "Skill", "description": "new", "staleness": "current"})
    # Exactly one frontmatter block (two '---' delimiters at the doc head).
    assert out.count("\n---\n") == 1 and out.startswith("---\n")
    # Curated keys win; untouched existing keys survive; body intact.
    assert "type: Skill" in out
    assert "description: new" in out
    assert "name: taxonomy-mapping" in out
    assert "staleness: current" in out
    assert "# Body" in out
    assert "old" not in out.split("# Body")[0]  # old description not left in frontmatter


def test_merge_creates_block_when_none_present():
    raw = "# Title\n\nbody only, no frontmatter\n"
    out = merge_frontmatter(raw, {"type": "Plan", "title": "T"})
    assert out.startswith("---\n")
    assert "type: Plan" in out
    assert "body only, no frontmatter" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/anthonylui/OpenKnowledgeFormat/.venv/bin/python -m pytest docs/okf/build/test_stage.py -v`
Expected: FAIL — `ImportError: cannot import name 'merge_frontmatter'` (stage.py not written yet).

- [ ] **Step 3: Write the minimal implementation**

```python
# docs/okf/build/stage.py
"""Stage SCUDO source docs into a curated OKF source tree (build/okf-src/).

Runs under the OKF venv's interpreter so it can reuse okf_toolkit's own
document + link helpers — guaranteeing staging agrees with the converter and
validator. Never edits source docs; only writes copies into build/okf-src/.
"""
from __future__ import annotations

from okf_toolkit.bundle.document import OKFDocument


def merge_frontmatter(raw_text: str, curated: dict) -> str:
    """Overlay *curated* keys onto any existing frontmatter; return one doc string.

    Existing frontmatter keys are preserved unless a curated key overrides them.
    Curated keys with value None are dropped (so manifest nulls don't emit `key: null`).
    """
    doc = OKFDocument.parse(raw_text)
    merged = dict(doc.frontmatter)
    for k, v in curated.items():
        if v is None:
            continue
        merged[k] = v
    return OKFDocument(frontmatter=merged, body=doc.body, had_frontmatter=True).serialize()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/anthonylui/OpenKnowledgeFormat/.venv/bin/python -m pytest docs/okf/build/test_stage.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add docs/okf/build/stage.py docs/okf/build/test_stage.py
git commit -m "feat(okf): frontmatter-merge helper (parse-and-overlay, never double-block)"
```

---

## Task 3: Link helpers — repoint, related-section, superseded-banner (TDD)

**Files:**
- Modify: `docs/okf/build/stage.py`
- Modify: `docs/okf/build/test_stage.py`

**Why three helpers (not just repoint):** Many priority cross-links in the spec
(§4.4) — above all the band→`matching-data-provenance` link — appear in the sources as
**plain prose or bold text, not markdown links** (e.g. `README.md:99`, `AGENTS.md:16`,
the skill chain in `taxonomy-mapping/SKILL.md:32`). A repoint-only approach cannot
create a link that doesn't exist. So:
- `repoint_links` rewrites links that DO exist (a relocated doc's existing links).
- `add_related_section` **injects** the desired cross-links as a generated `## Related`
  section — deterministic, guaranteed to resolve, no fragile prose surgery.
- `add_superseded_banner` prepends a visible banner to superseded concepts so a reader
  who lands on an old doc is routed to its replacement (frontmatter alone is invisible
  in the body).

**Interfaces:**
- Consumes: `okf_toolkit.bundle.links.iter_markdown_links`, `split_target`, `is_external`.
- Produces:
  - `repoint_links(body, link_map: dict[str,str]) -> str` — for each EXISTING markdown
    link whose target (raw, normalized, or `./`-toggled form) is a key in `link_map`,
    replace the WHOLE `[text](target)` link: mapped value → `[text](mapped#anchor "title")`;
    sentinel `"@inline"` → `` `text` `` (de-linked). External/unmapped/code-span links
    untouched. Single pass over `iter_markdown_links` (which already skips code spans),
    so anchors/titles and code spans are handled correctly.
  - `add_related_section(body, related: list[dict]) -> str` — append `## Related` with one
    bullet `- [label](target)` per item (no-op on empty).
  - `add_superseded_banner(body, target: str|None) -> str` — prepend `> **Superseded.** See [name](target).` (no-op on falsy target).

- [ ] **Step 1: Write the failing tests**

```python
# append to docs/okf/build/test_stage.py
from stage import repoint_links, add_related_section, add_superseded_banner  # noqa: E402


def test_repoint_remaps_existing_link_to_bundle_absolute():
    body = "See [bands](../matching-data-provenance.md)."
    out = repoint_links(body, {"../matching-data-provenance.md": "/reference/matching-data-provenance.md"})
    assert "[bands](/reference/matching-data-provenance.md)" in out


def test_repoint_inline_delinks_even_with_anchor():
    body = "the [code](batch.py#x) here"
    out = repoint_links(body, {"batch.py": "@inline"})
    assert "`code`" in out
    assert "(batch.py" not in out


def test_repoint_leaves_external_and_unmapped_untouched():
    body = "[ext](https://x.com) and [keep](./other.md)."
    out = repoint_links(body, {"missing.md": "/x.md"})
    assert "[ext](https://x.com)" in out
    assert "[keep](./other.md)" in out


def test_repoint_ignores_links_in_code_spans():
    body = "`[not a link](foo.md)` real [a](foo.md)"
    out = repoint_links(body, {"foo.md": "/bar.md"})
    assert "`[not a link](foo.md)`" in out      # code span untouched
    assert "[a](/bar.md)" in out


def test_repoint_normalizes_dot_slash():
    body = "[a](x.md) [b](./x.md)"
    out = repoint_links(body, {"x.md": "/y.md"})
    assert out.count("(/y.md)") == 2            # both forms matched


def test_add_related_section_injects_links():
    out = add_related_section("# Doc\n\nbody\n", [
        {"label": "Bands (canonical)", "target": "/reference/matching-data-provenance.md"},
    ])
    assert "## Related" in out
    assert "[Bands (canonical)](/reference/matching-data-provenance.md)" in out


def test_add_related_section_noop_when_empty():
    assert add_related_section("body\n", []) == "body\n"


def test_add_superseded_banner_prepends():
    out = add_superseded_banner("# Old\n\nbody\n", "/architecture/diagrams-and-sources.md")
    assert out.startswith("> **Superseded.**")
    assert "(/architecture/diagrams-and-sources.md)" in out


def test_add_superseded_banner_noop_when_none():
    assert add_superseded_banner("body\n", None) == "body\n"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/anthonylui/OpenKnowledgeFormat/.venv/bin/python -m pytest docs/okf/build/test_stage.py -k "repoint or related or superseded" -v`
Expected: FAIL — `ImportError: cannot import name 'repoint_links'`.

- [ ] **Step 3: Write the implementation**

```python
# add to docs/okf/build/stage.py
import os

from okf_toolkit.bundle.links import iter_markdown_links, split_target, is_external


def _candidates(path: str) -> list[str]:
    """Raw, normalized, and ./-toggled forms so x.md and ./x.md coalesce."""
    cands = [path, os.path.normpath(path), path[2:] if path.startswith("./") else "./" + path]
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def repoint_links(body: str, link_map: dict) -> str:
    """Rewrite whole existing markdown links; de-link @inline targets. Code-span safe."""
    edits: list[tuple[int, int, str]] = []  # (whole_link_start, whole_link_end, replacement)
    for link in iter_markdown_links(body):
        path, anchor, title = split_target(link.raw_target)
        if not path or is_external(path):
            continue
        mapped = next((link_map[c] for c in _candidates(path) if c in link_map), None)
        if mapped is None:
            continue
        # link.start/end bound the TARGET; expand to the whole [text](...) link.
        rb = body.rfind("]", 0, link.start)   # closing bracket of [text]
        lb = body.rfind("[", 0, rb)           # opening bracket
        close = body.find(")", link.end)      # closing paren of the link
        if lb < 0 or rb < 0 or close < 0:
            continue
        text = body[lb + 1 : rb]
        replacement = f"`{text}`" if mapped == "@inline" else f"[{text}]({mapped}{anchor}{title})"
        edits.append((lb, close + 1, replacement))

    for start, end, replacement in sorted(edits, key=lambda e: e[0], reverse=True):
        body = body[:start] + replacement + body[end:]
    return body


def add_related_section(body: str, related: list) -> str:
    """Append a generated '## Related' section of markdown links (no-op if empty)."""
    if not related:
        return body
    lines = ["", "## Related", ""]
    for item in related:
        lines.append(f"- [{item['label']}]({item['target']})")
    return body.rstrip() + "\n" + "\n".join(lines) + "\n"


def add_superseded_banner(body: str, target) -> str:
    """Prepend a visible supersession banner (no-op if target is falsy)."""
    if not target:
        return body
    name = target.rsplit("/", 1)[-1]
    if name.endswith(".md"):
        name = name[:-3]
    return f"> **Superseded.** See [{name}]({target}).\n\n" + body
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/anthonylui/OpenKnowledgeFormat/.venv/bin/python -m pytest docs/okf/build/test_stage.py -v`
Expected: PASS (all green — Task 2's 2 + these 9 = 11).

- [ ] **Step 5: Commit**

```bash
git add docs/okf/build/stage.py docs/okf/build/test_stage.py
git commit -m "feat(okf): link helpers — repoint (whole-link, code-safe) + related/superseded injection"
```

---

## Task 4: Author the concept manifest

**Files:**
- Create: `docs/okf/build/manifest.yaml`

**Interfaces:**
- Produces: `manifest.yaml` — a top-level `concepts:` list of 37 entries. Keys per entry: `out` (bundle-relative path), `sources` (list of repo-relative source paths; >1 ⇒ merge), `type` (one of the 8), `title`, `description` (one sentence), `tags` (list), `staleness` (`current|historical|superseded`), `supersedes`/`superseded_by` (bundle-absolute path or `null`), `transform` (`null|merge|split_ingestion|fold_diagrams`), `link_rewrites` (map of an EXISTING source-link target → bundle-absolute path or `@inline`), `related` (list of `{label, target}` cross-links to INJECT as a `## Related` section).

**Cross-links: `related` vs `link_rewrites`.** Use `related` for the §4.4 priority
links — most sources mention the target as prose, not a link, so it must be injected.
Use `link_rewrites` only when the source already contains a markdown link that needs
relocating (or a code/path reference to de-link via `@inline`). The `superseded_by`
value auto-generates a banner; do not also put it in `related`.

**Authoring procedure (this is data-entry, not placeholders):** For each row in the structural table below, the `out`/`sources`/`type`/`staleness`/`supersedes`/`superseded_by`/`transform` values are fixed (given). Author `title` from the doc's first H1; `description` as one sentence (seed from the classification synthesis already produced, then confirm against the doc — the §6.2 fan-out re-verifies every one); `tags` as 1–3 topic words; `related` from the §4.4 priority links that originate in that concept.

- [ ] **Step 1: Write the manifest header and the worked examples (one per transform/type)**

```yaml
# docs/okf/build/manifest.yaml
# 37 SCUDO concepts → docs/okf/scudo/. Authored per the plan's structural table.
# out=bundle path; sources=repo-relative; transform=null|merge|split_ingestion|fold_diagrams
concepts:
  # --- normal copy, source already has NO frontmatter ---
  - out: architecture/overview.md
    sources: [README.md]
    type: Architecture
    title: "SCUDO MatchMaker — Project Overview"
    description: "Top-level overview of the vendor→CDAO mapping prototype: the five-rung cost ladder, three-MCP trust gradient, HMAC seal contract, repo layout, run/deploy paths, and an explicit what-is-NOT-done list."
    tags: [overview, architecture]
    staleness: current
    supersedes: null
    superseded_by: null
    transform: null
    link_rewrites: {}
    related:
      - {label: "Confidence bands & provenance (canonical)", target: /reference/matching-data-provenance.md}

  # --- normal copy, source ALREADY has frontmatter (merge overlay must not double-block) ---
  - out: skills/taxonomy-mapping.md
    sources: [backend/scudo/skills/taxonomy-mapping/SKILL.md]
    type: Skill
    title: "Taxonomy Mapping Specialist Skill"
    description: "The Mapping Specialist agent's procedure for mapping a vendor product to a JPMC CDAO taxonomy node — inputs, candidate retrieval, RDF-graph confirmation, per-route behaviour, and confidence calibration."
    tags: [skill, mapping, agent]
    staleness: current
    supersedes: null
    superseded_by: null
    transform: null
    link_rewrites: {}
    related:
      - {label: "GraphRAG retrieval (candidate discovery)", target: /skills/graphrag-retrieval.md}
      - {label: "Neptune I/O (authoritative confirmation)", target: /skills/neptune-io.md}
      - {label: "RDF serialisation (publish)", target: /skills/rdf-serialisation.md}
      - {label: "Confidence bands & provenance (canonical)", target: /reference/matching-data-provenance.md}

  # --- MERGE two sources into one concept ---
  - out: handovers/dashboard-repo-push.md
    sources: [infra/handoff/README.md, infra/DASHBOARD_REPO_PUSH_HANDOFF.md]
    type: Handover
    title: "Handover — Push the Dashboard Source Repo"
    description: "Point-in-time instructions for pushing the 7 stranded dashboard-source commits to Egonex-AI/Understand-Anything via the git-bundle workaround (author lacks push access)."
    tags: [handover, dashboard, git]
    staleness: historical
    supersedes: null
    superseded_by: null
    transform: merge

  # --- SPLIT: keep ETL spec §1–11, drop the duplicated diagram appendices ---
  - out: specs/ingestion-framework.md
    sources: [docs/ingestion_framework_spec.md]
    type: Spec
    title: "Ingestion Framework Spec"
    description: "Format-agnostic ETL ingestion spec (§1–11) for landing vendor source documents into SCUDO; the SCUDO-specific pipeline diagrams are kept in architecture/, not re-embedded here."
    tags: [spec, ingestion, etl]
    staleness: current
    supersedes: null
    superseded_by: null
    transform: split_ingestion

  # --- FOLD: two mapping_mcp READMEs → one architecture concept ---
  - out: architecture/diagrams-and-sources.md
    sources: [backend/scudo_mapping_mcp/docs/architecture/README.md, backend/scudo_mapping_mcp/docs/README.md]
    type: Architecture
    title: "SCUDO Architecture Diagrams & Sources"
    description: "Names the canonical .mmd diagram set (overview/match-verify/retrieval), what each covers, the supersession of the older diagram-* docs, the diagrams-win-over-prose rule, and a quick orientation for new readers."
    tags: [architecture, diagrams, source-of-truth]
    staleness: current
    supersedes: null
    superseded_by: null
    transform: fold_diagrams
```

- [ ] **Step 2: Append the remaining 32 entries per the structural table**

Author one entry per row below (same schema as Step 1). The fixed columns are given; author `title`/`description`/`tags`/`links` per the authoring procedure. **Structural table (out · sources · type · staleness · superseded_by):**

```
architecture/hooks.md                · hooks.md                                                      · Architecture    · current     · null
architecture/batch.md                · backend/scudo/BATCH.md                                        · Architecture    · current     · null
architecture/arb-review-pack.md      · backend/scudo_mapping_mcp/docs/architecture/arb-review-pack.md· Decision Record · current     · null
architecture/diagram-main-flow.md    · backend/scudo_mapping_mcp/docs/diagram-1-main-flow.md         · Architecture    · superseded  · /architecture/diagrams-and-sources.md
architecture/diagram-falkor-internals.md · backend/scudo_mapping_mcp/docs/diagram-2-falkor-internals.md · Architecture · superseded · /architecture/diagrams-and-sources.md
reference/matching-data-provenance.md· docs/superpowers/matching-data-provenance.md                 · Reference       · current     · null
reference/agents.md                  · AGENTS.md                                                     · Reference       · current     · null
skills/graphrag-retrieval.md         · backend/scudo/skills/graphrag-retrieval/SKILL.md              · Skill           · current     · null
skills/neptune-io.md                 · backend/scudo/skills/neptune-io/SKILL.md                      · Skill           · current     · null
skills/rdf-serialisation.md          · backend/scudo/skills/rdf-serialisation/SKILL.md              · Skill           · current     · null
skills/rights-odrl.md                · backend/scudo/skills/rights-odrl/SKILL.md                     · Skill           · current     · null
specs/self-improving-agent.md        · docs/superpowers/specs/2026-06-16-self-improving-agent-system-design.md · Spec · current · null
specs/matching-frontend.md           · docs/superpowers/specs/2026-06-24-scudo-matching-frontend-spec.md · Spec · current · null
specs/hitl-two-way-chat.md           · docs/superpowers/specs/2026-06-25-hitl-two-way-chat-spec.md   · Spec           · current     · null
specs/auth-gate-strip-inject.md      · infra/AUTH_GATE_SPEC_strip_inject.md                          · Spec           · current     · null
specs/i5-lift-preconditions.md       · backend/scudo_mapping_mcp/docs/i5-lift-preconditions.md       · Spec           · current     · null
plans/phase0-foundations.md          · docs/superpowers/plans/2026-06-18-scudo-phase0-foundations.md · Plan           · historical  · null
plans/matching-dashboard.md          · docs/superpowers/plans/2026-06-24-scudo-matching-dashboard.md · Plan           · historical  · null
plans/matching-frontend-and-deploy.md· docs/superpowers/plans/2026-06-24-scudo-matching-frontend-and-deploy.md · Plan · historical · null
plans/dense-arm-sdk-adoption.md      · backend/scudo_mapping_mcp/docs/dense-arm-sdk-adoption.md      · Plan           · current     · null
plans/dense-arm-swap.md              · backend/scudo_mapping_mcp/docs/dense-arm-swap.md              · Plan           · superseded  · /plans/dense-arm-sdk-adoption.md
plans/precedent-hydrator-workstream.md · backend/scudo_mapping_mcp/docs/precedent-hydrator-workstream.md · Plan · current · null
deployment/deploy.md                 · backend/scudo/DEPLOY.md                                       · Runbook         · current     · null
deployment/deploy-runbook-scudo-poc.md · infra/DEPLOY_RUNBOOK_scudo-poc.md                          · Runbook         · current     · null
deployment/demo-runbook.md           · backend/scudo_mapping_mcp/docs/demo-runbook.md               · Runbook         · historical  · null
handovers/aws-handoff.md             · backend/scudo/AWS_HANDOFF.md                                  · Handover        · historical  · null
handovers/hitl-bands-2026-06-26.md   · infra/HANDOVER_hitl_bands_2026-06-26.md                       · Handover        · historical  · null
handovers/code-review-fixes.md       · infra/CODE_REVIEW_FIXES.md                                    · Handover        · historical  · null
handovers/architecture-gap-analysis.md · docs/superpowers/2026-06-16-scudo-architecture-gap-analysis.md · Handover · historical · null
handovers/smoke-fixes-round1.md      · infra/SMOKE_FIXES_round1.md                                   · Handover        · historical  · null
handovers/smoke-upload-flow-live.md  · infra/SMOKE_upload_flow_live.md                               · Handover        · historical  · null
handovers/redeploy-branding.md       · infra/REDEPLOY_NOTE_branding.md                              · Handover        · historical  · null
```

For `supersedes` (the reverse direction): set `plans/dense-arm-sdk-adoption.md` `supersedes: /plans/dense-arm-swap.md` (the two diagram docs get their banner from `superseded_by` set in the table). Wire the priority cross-links (spec §4.4 #1–#8) via each concept's **`related`** list: the band-citing docs (`architecture/hooks.md`, `reference/agents.md`, `specs/i5-lift-preconditions.md`, both diagram docs, `architecture/arb-review-pack.md`, `specs/ingestion-framework.md`, `skills/taxonomy-mapping.md`) each get a `related` entry → `/reference/matching-data-provenance.md`; `specs/auth-gate-strip-inject.md` ↔ `/handovers/code-review-fixes.md`; `handovers/hitl-bands-2026-06-26.md` → `/deployment/deploy-runbook-scudo-poc.md`; the skill chain per §4.4 #8 (already shown for `taxonomy-mapping` in Step 1). Set `link_rewrites: {}` unless a source has an existing markdown link to relocate.

- [ ] **Step 3: Validate the manifest is well-formed and counts 37**

Run:
```bash
/Users/anthonylui/OpenKnowledgeFormat/.venv/bin/python - <<'PY'
import yaml
m = yaml.safe_load(open("docs/okf/build/manifest.yaml"))
c = m["concepts"]
assert len(c) == 37, f"expected 37, got {len(c)}"
TYPES = {"Architecture","Decision Record","Reference","Skill","Spec","Plan","Runbook","Handover"}
import collections
folders = collections.Counter(e["out"].split("/")[0] for e in c)
assert folders == {"architecture":7,"reference":2,"skills":5,"specs":6,"plans":6,"deployment":3,"handovers":8}, folders
for e in c:
    assert e["type"] in TYPES, e
    assert e["staleness"] in {"current","historical","superseded"}, e
    for s in e["sources"]:
        from pathlib import Path
        assert Path(s).is_file(), f"missing source: {s}"
print("manifest OK: 37 concepts,", dict(folders))
PY
```
Expected: `manifest OK: 37 concepts, {...}` with no assertion error.

- [ ] **Step 4: Commit**

```bash
git add docs/okf/build/manifest.yaml
git commit -m "feat(okf): 37-concept manifest (types, staleness, supersession, priority links)"
```

---

## Task 5: Stage driver — emit `build/okf-src/` (TDD for the 3 transforms)

**Files:**
- Modify: `docs/okf/build/stage.py`
- Modify: `docs/okf/build/test_stage.py`

**Interfaces:**
- Consumes: `merge_frontmatter`, `repoint_links`, the manifest.
- Produces: `main(repo_root, manifest_path, out_dir)` that writes all 37 concepts; helpers `_merge_sources(bodies, titles) -> str`, `_split_ingestion(body) -> str`, `_fold_diagrams(bodies) -> str`. Each source doc is read, body extracted via `OKFDocument.parse`, transformed, links repointed, frontmatter merged, written to `out_dir/<out>`.

- [ ] **Step 1: Write failing tests for the three transforms**

```python
# append to docs/okf/build/test_stage.py
from stage import _merge_sources, _split_ingestion, _fold_diagrams  # noqa: E402


def test_merge_sources_concatenates_with_headers():
    out = _merge_sources(["body A", "body B"], ["Apply Bundle", "Repo Push"])
    assert "body A" in out and "body B" in out
    assert "Apply Bundle" in out and "Repo Push" in out
    assert out.index("body A") < out.index("body B")  # order preserved


def test_split_ingestion_drops_appendices():
    body = "## 1. Scope\nkeep me\n## 11. Done\nkeep\n## Appendix A\nDROP DIAGRAM\n"
    out = _split_ingestion(body)
    assert "keep me" in out
    assert "DROP DIAGRAM" not in out
    assert "Appendix A" not in out


def test_fold_diagrams_keeps_both_unique_sections():
    out = _fold_diagrams(["SUPERSEDES mapping here", "Quick orientation bullets"])
    assert "SUPERSEDES mapping here" in out
    assert "Quick orientation bullets" in out
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/anthonylui/OpenKnowledgeFormat/.venv/bin/python -m pytest docs/okf/build/test_stage.py -k "merge_sources or split_ingestion or fold_diagrams" -v`
Expected: FAIL — import error for the three helpers.

- [ ] **Step 3: Implement the transforms and `main`**

```python
# add to docs/okf/build/stage.py
import sys
from pathlib import Path

import yaml


def _merge_sources(bodies: list[str], titles: list[str]) -> str:
    parts = []
    for title, body in zip(titles, bodies):
        parts.append(f"## {title}\n\n{body.strip()}\n")
    return "\n---\n\n".join(parts)


def _split_ingestion(body: str) -> str:
    """Keep everything before the first '## Appendix' heading (drop duplicated diagrams)."""
    lines = body.splitlines()
    cut = len(lines)
    for i, ln in enumerate(lines):
        if ln.strip().lower().startswith("## appendix"):
            cut = i
            break
    kept = "\n".join(lines[:cut]).rstrip()
    return kept + (
        "\n\nFor the SCUDO mapping-pipeline diagrams, see "
        "[architecture diagrams & sources](/architecture/diagrams-and-sources.md).\n"
    )


def _fold_diagrams(bodies: list[str]) -> str:
    """Fold the two mapping_mcp READMEs; first source (architecture/README) leads."""
    return "\n\n---\n\n".join(b.strip() for b in bodies) + "\n"


def _read_body(repo_root: Path, src: str) -> tuple[str, str]:
    raw = (repo_root / src).read_text(encoding="utf-8")
    doc = OKFDocument.parse(raw)
    title = ""
    for ln in doc.body.splitlines():
        if ln.startswith("# "):
            title = ln[2:].strip()
            break
    return doc.body, title


def main(repo_root: str, manifest_path: str, out_dir: str) -> int:
    repo = Path(repo_root)
    out = Path(out_dir)
    if out.exists():
        import shutil
        shutil.rmtree(out)
    out.mkdir(parents=True)
    manifest = yaml.safe_load((repo / manifest_path).read_text(encoding="utf-8"))
    n = 0
    for e in manifest["concepts"]:
        bodies, titles = [], []
        for s in e["sources"]:
            b, t = _read_body(repo, s)
            bodies.append(b)
            titles.append(t)
        transform = e.get("transform")
        if transform == "merge":
            body = _merge_sources(bodies, titles)
        elif transform == "split_ingestion":
            body = _split_ingestion(bodies[0])
        elif transform == "fold_diagrams":
            body = _fold_diagrams(bodies)
        else:
            body = bodies[0]
        body = repoint_links(body, e.get("link_rewrites") or {})
        body = add_superseded_banner(body, e.get("superseded_by"))
        body = add_related_section(body, e.get("related") or [])
        curated = {
            "type": e["type"],
            "title": e["title"],
            "description": e["description"],
            "tags": e.get("tags"),
            "staleness": e["staleness"],
            "supersedes": e.get("supersedes"),
            "superseded_by": e.get("superseded_by"),
        }
        # Reuse the first source's raw text so existing frontmatter (skills) merges.
        first_raw = (repo / e["sources"][0]).read_text(encoding="utf-8")
        first_doc = OKFDocument.parse(first_raw)
        merged_doc = merge_frontmatter(
            OKFDocument(frontmatter=first_doc.frontmatter, body=body, had_frontmatter=True).serialize(),
            curated,
        )
        dest = out / e["out"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(merged_doc, encoding="utf-8")
        n += 1
    print(f"staged {n} concepts → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2], sys.argv[3]))
```

- [ ] **Step 4: Run unit tests + a full staging dry run**

Run:
```bash
/Users/anthonylui/OpenKnowledgeFormat/.venv/bin/python -m pytest docs/okf/build/test_stage.py -v
/Users/anthonylui/OpenKnowledgeFormat/.venv/bin/python docs/okf/build/stage.py "$PWD" docs/okf/build/manifest.yaml build/okf-src
find build/okf-src -name '*.md' | wc -l        # expect 37
# skill files must have exactly ONE frontmatter block:
for f in build/okf-src/skills/*.md; do test "$(grep -c '^---$' "$f")" -eq 2 || echo "DOUBLE BLOCK: $f"; done
```
Expected: all unit tests PASS; `37`; no `DOUBLE BLOCK` lines.

- [ ] **Step 5: Commit**

```bash
git add docs/okf/build/stage.py docs/okf/build/test_stage.py
git commit -m "feat(okf): stage driver — 37 concepts with merge/split/fold + link repoint"
```

---

## Task 6: Wire convert → validate → evals → visualize into `build_bundle.sh`

**Files:**
- Modify: `docs/okf/build_bundle.sh`

**Interfaces:**
- Consumes: the guardrail preamble (Task 1) + `stage.py` (Task 5).
- Produces: a complete `build_bundle.sh` whose successful run yields `docs/okf/scudo/` passing `okf validate` (0 errors) and the 4 automated evals.

- [ ] **Step 1: Append the pipeline stages to `build_bundle.sh`**

```bash
cat >> docs/okf/build_bundle.sh <<'SH'

echo "=== stage ==="
"$OKF_PY" "$REPO_ROOT/docs/okf/build/stage.py" "$REPO_ROOT" docs/okf/build/manifest.yaml "$STAGE_SRC"

echo "=== clean output ==="
rm -rf "$BUNDLE_OUT"

echo "=== convert ==="
"$OKF_BIN" convert "$STAGE_SRC" --out "$BUNDLE_OUT" --default-type Document

echo "=== validate (gate: zero errors) ==="
"$OKF_BIN" validate "$BUNDLE_OUT"

echo "=== validate --strict (advisory: warnings inform the link loop) ==="
"$OKF_BIN" validate "$BUNDLE_OUT" --strict || echo "(advisory warnings above — not a gate)"

echo "=== evals (gate: 01,02,06,07 must pass) ==="
"$OKF_BIN" evals run "$BUNDLE_OUT"

echo "=== visualize ==="
"$OKF_BIN" visualize "$BUNDLE_OUT"

echo "=== BUILD COMPLETE → $BUNDLE_OUT ==="
SH
```

- [ ] **Step 2: Run the full build end-to-end**

Run: `./docs/okf/build_bundle.sh`
Expected: prints `staged 37 concepts`, convert summary, `validate` summary ending `PASS` (0 errors), eval lines `[PASS] 01`, `[PASS] 02`, `[PASS] 06`, `[PASS] 07`, and `BUILD COMPLETE`.

- [ ] **Step 3: Assert the gates programmatically**

Run:
```bash
OKF_BIN="${OKF_BIN:-/Users/anthonylui/OpenKnowledgeFormat/.venv/bin/okf}"
"$OKF_BIN" validate docs/okf/scudo | tail -1                       # expect: PASS
"$OKF_BIN" evals run docs/okf/scudo >/tmp/okf_evals.txt 2>&1; echo "exit=$?"   # expect exit=0
grep -E '^\[(PASS|FAIL)\] 0[1267]' /tmp/okf_evals.txt              # all PASS
test -f docs/okf/scudo/index.md && test -f docs/okf/scudo/log.md && echo "index+log OK"
```
Expected: `PASS`, `exit=0`, four `[PASS]` lines, `index+log OK`.

- [ ] **Step 4: Commit (bundle + script)**

```bash
git add docs/okf/build_bundle.sh docs/okf/scudo
git commit -m "feat(okf): generate SCUDO bundle — convert+validate+evals+visualize pipeline"
```

---

## Task 7: Content-accuracy fan-out (self-verifying loop, spec §6.2)

**Files:**
- Modify: `docs/okf/build/manifest.yaml` (corrections only), regenerate `docs/okf/scudo/`

**Interfaces:**
- Consumes: the built bundle + manifest.
- Produces: a manifest whose every concept's `type`/`description`/`staleness` matches its content, and correct supersession direction — verified by an independent agent per concept, looped until clean.

- [ ] **Step 1: Dispatch the verification fan-out**

Use the Workflow tool. One agent per concept (37), each reads the concept file in `docs/okf/scudo/<out>` and returns a structured verdict: does `type` fit the 8-vocab? is `description` a faithful one-sentence summary? is `staleness` right (commit-pinned/dated snapshots ⇒ historical; replaced ⇒ superseded)? is each `supersedes`/`superseded_by` direction correct? Schema: `{out, type_ok, description_ok, staleness_ok, supersession_ok, suggested_fixes}`. Pipeline shape so each verdict returns as soon as its read completes; collect all, then filter to failures.

- [ ] **Step 2: Apply corrections to the manifest and re-stage**

For each failing verdict, hand-check the suggestion against the doc (verifiers can be wrong on technicalities), edit the corresponding `manifest.yaml` entry, then rerun `./docs/okf/build_bundle.sh`.

- [ ] **Step 3: Re-run the fan-out until clean (loop-until-dry)**

Repeat Steps 1–2 until a full round reports zero failures (or two consecutive rounds surface nothing new). Record the final clean round.

- [ ] **Step 4: Commit corrections (if any)**

```bash
git add docs/okf/build/manifest.yaml docs/okf/scudo
git commit -m "fix(okf): content-accuracy fan-out corrections (type/description/staleness verified)"
```

---

## Task 8: Link-integrity loop (spec §6.3)

**Files:**
- Modify: `docs/okf/build/manifest.yaml` (`links` maps), regenerate `docs/okf/scudo/`

**Interfaces:**
- Produces: all 8 priority cross-links resolve to existing bundle concepts; zero residual *in-bundle* dangling links; out-of-bundle references de-linked to inline code.

- [ ] **Step 1: Enumerate broken in-bundle links**

Run:
```bash
OKF_BIN="${OKF_BIN:-/Users/anthonylui/OpenKnowledgeFormat/.venv/bin/okf}"
"$OKF_BIN" validate docs/okf/scudo --strict 2>&1 | grep -i "broken internal link" || echo "no broken in-bundle links"
```

- [ ] **Step 2: Confirm the 8 priority links resolve**

Run:
```bash
/Users/anthonylui/OpenKnowledgeFormat/.venv/bin/python - <<'PY'
from pathlib import Path
B = Path("docs/okf/scudo")
required = [
  "reference/matching-data-provenance.md","reference/agents.md","specs/matching-frontend.md",
  "architecture/diagrams-and-sources.md","plans/dense-arm-sdk-adoption.md","plans/dense-arm-swap.md",
  "specs/i5-lift-preconditions.md","specs/auth-gate-strip-inject.md","handovers/code-review-fixes.md",
  "handovers/hitl-bands-2026-06-26.md","deployment/deploy-runbook-scudo-poc.md",
  "skills/taxonomy-mapping.md","skills/graphrag-retrieval.md","skills/neptune-io.md","skills/rdf-serialisation.md",
]
missing = [r for r in required if not (B/r).is_file()]
print("MISSING:", missing or "none")
# spot-check that provenance is actually referenced somewhere
hits = sum("/reference/matching-data-provenance.md" in (p.read_text()) for p in B.rglob("*.md"))
print("provenance referenced by", hits, "concepts")
PY
```
Expected: `MISSING: none`; provenance referenced by ≥3 concepts.

- [ ] **Step 3: Repoint any residual in-bundle dangles, re-stage, re-check (loop)**

For each broken in-bundle link from Step 1, add a mapping to the originating concept's `links` in `manifest.yaml` (`→ /folder/concept.md` if it has a home, else `@inline`), rerun `./docs/okf/build_bundle.sh`, repeat Step 1 until `no broken in-bundle links`.

- [ ] **Step 4: Commit**

```bash
git add docs/okf/build/manifest.yaml docs/okf/scudo
git commit -m "fix(okf): link-integrity loop — priority cross-links resolve, in-bundle dangles cleared"
```

---

## Task 9: Bundle docs + README pointers

**Files:**
- Create: `docs/okf/README.md`
- Modify: `README.md` (root) — two additive insertions

**Interfaces:**
- Produces: a rebuild guide and two discoverability pointers; no existing README content removed.

- [ ] **Step 1: Write `docs/okf/README.md`**

```markdown
# OKF Knowledge Bundles

`scudo/` is an [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf) bundle — a navigable, index-first mirror of the SCUDO/MatchMaker docs. **Start at [`scudo/index.md`](scudo/index.md)** and navigate via indices, not grep.

## Rebuilding

The bundle is generated from `build/manifest.yaml` by the OKF toolkit (a separate repo).

```bash
# OKF toolkit (one-time): cd /Users/anthonylui/OpenKnowledgeFormat && python3 -m venv .venv && .venv/bin/pip install -e .
OKF_BIN=/Users/anthonylui/OpenKnowledgeFormat/.venv/bin/okf ./docs/okf/build_bundle.sh
```

- Sources are **copied**, never moved — editing a source does not update the bundle until you rebuild.
- `build/okf-src/` is gitignored staging scratch.
- Gate: `okf validate docs/okf/scudo` (0 errors) + automated evals 01/02/06/07.
```

- [ ] **Step 2: Add the `## Repo layout` pointer in root README**

Find the `## Repo layout` section and add a `docs/okf/scudo/` entry in its file tree/list, described as: "navigable OKF knowledge bundle (start at `index.md`)". Match the section's existing list format exactly.

- [ ] **Step 3: Add the `## Architecture source of truth` pointer**

In the `## Architecture source of truth` section, append one line: "An index-first OKF mirror of the scattered docs lives at [`docs/okf/scudo/`](docs/okf/scudo/index.md) — navigate via `index.md`, not grep. Rebuild with `docs/okf/build_bundle.sh`."

- [ ] **Step 4: Verify README still renders and links resolve**

Run:
```bash
grep -n "docs/okf/scudo" README.md            # expect 2 hits
test -f docs/okf/scudo/index.md && echo "link target exists"
```
Expected: 2 hits; `link target exists`.

- [ ] **Step 5: Commit**

```bash
git add docs/okf/README.md README.md
git commit -m "docs(okf): rebuild guide + root README pointers to the bundle"
```

---

## Task 10: Codex bundle review gate (spec §6.4) + Gate B pre-commit approval

**Files:**
- Modify: as directed by triaged Codex findings (manifest/stage/bundle), regenerate as needed.

**Interfaces:**
- Produces: a bundle with no unaddressed load-bearing Codex findings, approved by the user before final integration.

- [ ] **Step 1: Run the Codex bundle review**

Invoke `mcp__codex__codex` (cwd = repo root, sandbox `read-only`, approval `never`). Ask it to verify, against the originals: (a) taxonomy coherence + the 8-type vocabulary applied consistently; (b) the merge (dashboard handovers), split (ingestion appendices dropped), and fold (two READMEs) preserved all load-bearing info and lost nothing; (c) staleness + supersession metadata correct; (d) frontmatter quality (every concept has type/title/description; skill files single-block); (e) the root README diff is additive and accurate. Require a verdict (APPROVE / APPROVE-WITH-FIXES / REWORK) + numbered findings tagged [CONFIRMED-OK]/[ERROR]/[RISK]/[SUGGESTION] with file evidence.

- [ ] **Step 2: Triage and fix**

For each [ERROR]/[RISK]: hand-verify against the cited source before acting (a refutation that would reverse a load-bearing decision must be checked — verifiers can wrongly reject true findings). Fix via `manifest.yaml`/`stage.py`, rerun `./docs/okf/build_bundle.sh`, and re-run Tasks 7–8 gates if metadata/links changed. Loop until Codex returns APPROVE (or only [SUGGESTION]s you consciously accept).

- [ ] **Step 3: Final gate sweep**

Run:
```bash
OKF_BIN="${OKF_BIN:-/Users/anthonylui/OpenKnowledgeFormat/.venv/bin/okf}"
"$OKF_BIN" validate docs/okf/scudo | tail -1                 # PASS
"$OKF_BIN" evals run docs/okf/scudo >/dev/null; echo "evals exit=$?"   # 0
/Users/anthonylui/OpenKnowledgeFormat/.venv/bin/python -m pytest docs/okf/build/test_stage.py -q   # all pass
git status --short
```
Expected: `PASS`, `evals exit=0`, pytest green, clean-ish working tree (only intended bundle/manifest changes).

- [ ] **Step 4: Gate B — present to user for pre-commit approval**

Show the user: the bundle tree (`find docs/okf/scudo -name '*.md' | sort`), the `viz.html` path, the validate/evals results, the Codex verdict, and the root README diff (`git diff --staged README.md` or `git show`). **Wait for explicit approval** before the final commit/integration. Do not push or open a PR unless the user asks.

- [ ] **Step 5: Final commit (after approval)**

```bash
git add docs/okf README.md .gitignore
git commit -m "feat(okf): SCUDO knowledge bundle — Codex-reviewed, evals green, user-approved"
```

---

## Self-Review

**1. Spec coverage:**
- §2 deploy safety → no deploy files touched (Global Constraints) + verified in spec; the build only writes under `docs/` + `build/` (gitignored). ✓
- §3 staging approach → Tasks 2–6. ✓
- §4 taxonomy (37 concepts, 7 folders, 8 types) → Task 4 manifest + Task 5 stage + Task 4 Step 3 assertion. ✓
- §4.2 merge-not-prepend → Task 2 + Task 5 Step 4 double-block check. ✓
- §4.3 dedup/merge/split/fold → Task 4 (sources) + Task 5 transforms. ✓
- §4.4 8 priority cross-links → Task 4 `links` + Task 8 verification. ✓
- §5 pipeline (OKF_BIN, clean, convert, validate plain, evals, visualize) → Tasks 1, 6. ✓
- §6.2 content fan-out loop → Task 7. ✓
- §6.3 link loop → Task 8. ✓
- §6.4 Codex gate → Task 10. ✓
- §6.5 Gate B → Task 10 Step 4. ✓
- §7 README → Task 9. ✓
- §8 deliverables (bundle, build_bundle.sh, docs/okf/README.md, README pointers, gitignore) → Tasks 1, 6, 9. ✓
- §10 success criteria → Task 6 Step 3 + Task 10 Step 3. ✓

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N". The manifest's per-doc `title`/`description` is a defined data-entry procedure with a verification gate (Task 7), not a placeholder; worked examples for all 4 transforms + full structural table provided.

**3. Type consistency:** `merge_frontmatter`, `repoint_links`, `add_related_section`, `add_superseded_banner`, `_candidates`, `_merge_sources`, `_split_ingestion`, `_fold_diagrams`, `_read_body`, `main` — names identical across definition (Tasks 2, 3, 5) and use (Task 5 `main`, Task 6 script). Manifest keys (`out/sources/type/title/description/tags/staleness/supersedes/superseded_by/transform/link_rewrites/related`) consistent across Task 4 schema, Task 4 examples, and Task 5 consumption (`main()` reads `link_rewrites`, `superseded_by`, `related`). `OKF_BIN`/`OKF_PY` consistent across Tasks 1, 6, 8, 10 (standalone shell blocks each re-resolve `OKF_BIN`).

**4. Cross-link feasibility (Codex finding 6):** priority links are injected via `related` (`## Related` section), not assumed to pre-exist as prose links — so they are created deterministically and resolve. `repoint_links` is reserved for genuinely existing links.
