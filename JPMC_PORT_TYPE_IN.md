# jpmc-port — changes to type in by hand

**For an agent (or a person) working inside `jpmc-port/` on the JPMC side.**
No git pull, no copy-paste from another machine: everything you need is in this
file.

These changes were made and verified in the Capone tree (`backend/`) first.
`jpmc-port/` has the **same defects**, confirmed by inspection of its own files —
line numbers below are `jpmc-port`'s, not Capone's.

**Companion document:** `JPMC_LOCAL_RUN_HANDOVER.md` in the Capone tree. Read
its §7 (runtime agents + publish gate) for *why* these changes exist and the
measured before/after; read §1–§6 if you also need the local run working
without Docker. This file is the *how to type it in* half; that one is the
*what and why* half.

**Work in order. Stop after any TIER and you still have a working system.**

| tier | what you get | lines to type | stop here? |
|---|---|---|---|
| **1** | Agents are told the rules they are judged by | 16 | yes — highest value per line |
| **2** | The publish gate actually enforces those rules | 24 | yes |
| **2.5** | Both models share ONE definition of the 10-dim rubric | 1 file copy + 6 | yes — see note |
| **3** | Local run with no Docker / no PostgreSQL | 1 + 1 file | yes |
| **4** | Tests pinning tiers 1–2 | 46 | optional |

Counts are the exact number of lines inside the code blocks, counted
mechanically.

---

## Why tier 1 is first

The deterministic gate in tier 2 **rejects** a bad result. That is safe, but
expensive: every violation is a wasted Bedrock call plus a HITL ticket. Telling
the model the rule converts silent rejections into compliance.

Doing tier 2 *without* tier 1 is the bad ordering — you would be failing the
model for a rule it was never given.

---

# TIER 1 — tell the agents the rules (16 lines)

## 1.1 `jpmc-port/scudo/prompts.py`

**Problem, verified:** `grep -c vendor_product_iri jpmc-port/scudo/prompts.py`
returns **0**. The prompt never mentions the field, but tier 2 will hard-reject
a mismatch. The model cannot comply with a rule it is not given.

> **Anchor checked against `jpmc-port/scudo/prompts.py` on 2026-08-06.** This
> port's prompt is structured differently from Capone's — it ends with an
> `AGENTIC LOOP:` paragraph rather than a `Return a MappingResult that:` list.
> The anchor below is `jpmc-port`'s own text.

FIND this line — it is the LAST line of `mapping_prompt`'s return block
(around line 139):

```
        "Leave proposed_triples empty. Rights/licensing terms → requires_human_review."
```

TYPE this immediately AFTER it, so it becomes the final instruction the model
reads (note the added `\n\n` on the line above — the existing line has no
trailing newline, so you must add one):

```python
        "\n\nHARD REQUIREMENTS — enforced by deterministic code AFTER you "
        "answer, not by the verifier's judgement. Violating one means the "
        "result is REJECTED outright, so there is nothing to gain by guessing:\n"
        f"  1. `vendor_product_iri` MUST be exactly "
        f"{bundle.vendor_product_iri or '<unset>'} — copy it verbatim from the "
        "bundle. It is minted deterministically upstream and is the primary "
        "key of the published record. Do NOT invent or re-derive it.\n"
        "  2. `proposed_target_iri` MUST be one of the IRIs in "
        "bundle.candidates. You may not introduce a node that was not offered, "
        "however plausible. If none fit, that is a legitimate answer — set "
        "requires_human_review=true and say why. An empty candidate list means "
        "nothing can be published; say so rather than proposing something."
```

The existing `"Select from bundle.candidates"` line already asks for rule 2 —
but as a request, not as a stated consequence, and it says nothing at all about
rule 1. That is the gap this closes.

Note the two `f`-prefixed lines — the IRI is interpolated so the model sees its
**exact** expected value, not an abstract instruction. That is the point of the
change; a generic "echo the IRI" instruction is much weaker.

**Check it worked:**

```bash
cd jpmc-port && python3 -c "
from scudo.prompts import mapping_prompt
import inspect; src = inspect.getsource(mapping_prompt)
print('HARD REQUIREMENTS present:', 'HARD REQUIREMENTS' in src)
print('IRI interpolated:', 'bundle.vendor_product_iri' in src)"
```

Both must print `True`.

## 1.2 Agent refusal discipline (only if `jpmc-port` has its own agent prompt)

```bash
grep -rn "SYSTEM_PROMPT" jpmc-port/scudo/*.py
```

If that returns nothing, **skip this** — `jpmc-port` uses the Capone agent and
the change is already made there. If it returns a prompt, add to the end of its
rules:

```python
        "REFUSALS ARE ANSWERS, NOT ERRORS TO WORK AROUND:\n"
        "  A tool may return {\"error\": \"frame_not_found\"} when no ingested\n"
        "  vendor frame exists. Report it and recommend needs_review. Do NOT\n"
        "  retry, and do NOT substitute a name of your own — a refusal means\n"
        "  the input is missing upstream, and papering over it produces a\n"
        "  confident mapping of data that was never ingested.\n"
```

---

# TIER 2 — make the gate enforce, not suggest (24 lines)

**Problem, verified by execution in the Capone tree:**
`_pre_verify_defects` returns a `list[str]` that is only **concatenated into
the verifier LLM's prompt**. Nothing enforces it. A verifier that scores well
and ignores the injected text publishes anyway. Measured before the fix:

```
specialist proposes a node never in bundle.candidates
  -> OUTCOME: PUBLISHED | published: 1
```

`jpmc-port/scudo/orchestrator.py:196-200` has the identical prompt-only check,
including the **fail-open** `and iris and` guard — with an empty candidate list
it publishes anything.

FIND this block in `_gate_and_decide` (around line 252):

```python
        for t in result.proposed_triples:
            if not t.graph:
                raise PublishGateError("triple missing named graph")
            if not _IRI_DETERMINISM.match(t.subject):
                raise PublishGateError(f"non-deterministic IRI: {t.subject!r}")
```

TYPE this immediately AFTER that loop (before `named_graph = ...`):

```python
        # Enforced HERE, not in _pre_verify_defects: that function's output is
        # only pasted into the verifier's PROMPT and enforces nothing. Both of
        # these were proven to publish bad data before promotion.
        expected_iri = bundle.vendor_product_iri
        if expected_iri and result.vendor_product_iri != expected_iri:
            raise PublishGateError(
                f"vendor_product_iri {result.vendor_product_iri!r} does not "
                f"match the minted {expected_iri!r} — the specialist must echo "
                "the bundle's identity, never mint one."
            )
        # Fail CLOSED on the degenerate shapes. `if x and iris and ...` was
        # fail-OPEN: an EMPTY candidate list published anything the model
        # proposed — exactly where it has the least grounding.
        candidate_iris = {c.iri for c in bundle.candidates}
        if not result.proposed_target_iri:
            raise PublishGateError("proposed_target_iri is empty")
        if not candidate_iris:
            raise PublishGateError(
                "bundle.candidates is empty — the specialist's "
                f"{result.proposed_target_iri!r} is ungrounded by construction"
            )
        if result.proposed_target_iri not in candidate_iris:
            raise PublishGateError(
                f"proposed_target_iri {result.proposed_target_iri!r} was not in "
                "bundle.candidates — select from the candidates given."
            )
```

**Leave `_pre_verify_defects` alone.** It still usefully tells the verifier what
to look at; it just is not the enforcement point.

**Check it worked** — both directions, not just the happy path:

```bash
cd jpmc-port && python3 -c "
import inspect
from scudo.orchestrator import Orchestrator
src = inspect.getsource(Orchestrator._gate_and_decide)
print('IRI echo enforced      :', 'must echo' in src)
print('candidate gate enforced:', 'bundle.candidates is empty' in src)"
```

Both must print `True`. Then run `python3 run_e2e.py` (or the port's smoke) and
confirm a legitimate run still PUBLISHES — a gate that blocks everything is not
a fix.

---

# TIER 2.5 — one shared definition of the rubric (1 file copy + 6 lines)

**Problem, verified in `jpmc-port/scudo/schemas.py:129-139`:** `VerifierDimension`
is ten bare enum names — `semantic_fit`, `candidate_coverage`,
`conflict_handling`… — with **no definition anywhere in the port**. Two
consequences:

1. The **verifier** invents what each name means on every call. Its
   `total_score` drives a hard publish / retry / HITL gate, so scores that are
   not comparable between runs make that gate arbitrary.
2. The **specialist** is never shown the dimensions at all. It is graded on a
   rubric it cannot see.

The tell is already in `jpmc-port/scudo/prompts.py:160-161`:
`"Then score all 10 dimensions (0/1/2). taxonomy_freshness=2 iff snapshot
appears in evidence."` — one dimension defined by hand because the list is not
shared.

## 2.5a Copy the rubric block

Copy **lines 13–117** of `backend/scudo/prompts.py` from the Capone tree — the
`# The rubric, defined ONCE…` comment header through the end of
`rubric_text()`, stopping before `MAPPING_SYSTEM = (`. That is ~105 lines
including the rationale comment: **a straight copy, do not retype it.**

Paste it into `jpmc-port/scudo/prompts.py` after the
`from .zone_context import system_context_text` import line.

*(Line range verified 2026-08-06 by applying it to a scratch copy of
`jpmc-port` and confirming the result parses and renders — but line numbers
drift, so anchor on the comment text if they have moved.)*

Then extend the port's import at `jpmc-port/scudo/prompts.py:7`:

```python
from .schemas import BriefBundle, MappingResult, VerifierDimension
```

## 2.5b Show the specialist the rubric

In `mapping_prompt`, FIND the HARD REQUIREMENTS block you added in tier 1 and
TYPE this immediately after it (before the closing `)`):

```python
        + "\n\n"
        + rubric_text(audience="specialist")
```

## 2.5c Give the verifier the definitions

In `verifier_prompt`, FIND:

```
        "Then score all 10 dimensions (0/1/2). "
```

REPLACE that one line with:

```python
        + rubric_text(audience="verifier")
        + "\n\nCompute total_score = sum of the ten dimension scores. "
```

**Check it worked:**

```bash
cd jpmc-port && python3 -c "
from scudo.prompts import mapping_prompt, rubric_text
from scudo.schemas import VerifierDimension
t = rubric_text(audience='verifier')
print('all 10 defined:', all(d.value in t for d in VerifierDimension))
print('guard fires on a gap:', end=' ')
import scudo.prompts as p
saved = dict(p._RUBRIC); p._RUBRIC.pop(VerifierDimension.SEMANTIC_FIT)
try:
    p.rubric_text(audience='verifier'); print('NO — guard missing')
except ValueError: print('yes')
p._RUBRIC.update(saved)"
```

Both must print affirmatively.

## On gaming — read before you decide

Telling a model its grading rubric invites writing-to-the-scorer. This was
accepted deliberately, and the reasoning is in the copied comment. Three things
bound it: the verifier is a **separate model**, the deterministic
`_gate_and_decide` checks from tier 2 cannot be talked out of, and every
definition rewards a **verifiable property** of the output rather than a
rhetorical one. The specialist is also told explicitly *"do not write to the
scorer"*.

If you disagree with that trade-off, apply **2.5c only** (verifier gets
definitions, specialist stays blind). That still fixes the
scores-not-comparable problem, which is the half that affects the gate.

---

# TIER 3 — local run with no Docker (1 line + 1 file)

Only needed if `jpmc-port` serves the Providers / Datasets / Admin pages. Check:

```bash
grep -rln "get_conn\|psycopg" jpmc-port/ | grep -v test
```

If that returns nothing, **skip this tier entirely** — `jpmc-port` has no
relational DB and there is nothing to fall back from.

If it does: copy `backend/db_sqlite_fallback.py` from the Capone tree (276 lines,
standard library only — do not retype it, it is a straight file copy), then add
the call-time hook to the port's `db.py`:

```python
def _sqlite_enabled() -> bool:
    return os.environ.get("CONSOLE_DB_BACKEND", "").strip().lower() == "sqlite"
```

...and at the top of each connection function:

```python
    if _sqlite_enabled():
        from db_sqlite_fallback import connect as _sqlite_connect

        return _sqlite_connect()
```

Read at **call** time, not import time, so a deployed run that never sets the
var is byte-for-byte unchanged.

---

# TIER 4 — tests (46 lines, optional)

Create `jpmc-port/scudo/tests/test_publish_gate.py`:

```python
"""The publish gate must ENFORCE, not suggest.

_pre_verify_defects output only reaches the verifier's PROMPT. These pin that
the two identity rules are hard raises in _gate_and_decide instead.
"""
import inspect

import pytest

from scudo.orchestrator import Orchestrator, PublishGateError


def _gate_source() -> str:
    return inspect.getsource(Orchestrator._gate_and_decide)


def test_iri_echo_is_enforced_in_the_gate():
    assert "must echo" in _gate_source(), (
        "vendor_product_iri echo check is not in the deterministic gate — "
        "a check that only reaches the verifier prompt enforces nothing"
    )


def test_candidate_membership_is_enforced_in_the_gate():
    assert "bundle.candidates" in _gate_source()


def test_candidate_gate_fails_closed_on_empty():
    """`if x and iris and ...` was fail-OPEN: an empty candidate list
    published anything the model proposed."""
    src = _gate_source()
    assert "bundle.candidates is empty" in src
    assert "proposed_target_iri is empty" in src


def test_prompt_states_the_hard_requirements():
    from scudo.prompts import mapping_prompt

    src = inspect.getsource(mapping_prompt)
    assert "HARD REQUIREMENTS" in src, (
        "the specialist is judged by rules its prompt never states"
    )
    assert "bundle.vendor_product_iri" in src, (
        "the required IRI must be interpolated, not described abstractly"
    )
```

Run: `cd jpmc-port && python3 -m pytest scudo/tests/test_publish_gate.py -q`
→ expect **4 passed**.

---

# Verification state in the Capone tree

These changes are green there:

```
backend/scudo/tests          309 passed / 2 failed
backend/scudo_mapping_mcp    422 passed
mapping smoke                117/117
offline smoke                SCUDO SMOKE OK
```

The 2 failures are the pre-existing `test_provenance.py` Marketing failures
documented in CLAUDE.md. They fail at HEAD, are unrelated, and must be **left
failing** — they are the baseline.

---

# Rules for whoever types this in

- Do **not** commit or push unless asked.
- Do **not** run `git checkout` / `stash` / `reset` / `clean` — the Capone
  worktree is dirty with in-flight work.
- Type tier 1 before tier 2. Reversing them means rejecting the model for a
  rule it was never told.
- After each tier, run its check block. If a check fails, stop and fix it
  before continuing — each tier is independently useful, so a half-applied
  tier is worse than a skipped one.
- If a FIND anchor does not match your file, **stop and report** rather than
  guessing at a nearby line. The anchors above were read out of `jpmc-port`'s
  own files, but that tree changes independently.

## Not applicable to this port (checked, so you do not have to)

- **The FalkorDB default.** In Capone, `STORE_BACKEND` defaulted to
  `"falkordb"`, so any entry point that set no environment tried to open a
  connection on :6379 — that is why "FalkorDB keeps being asked for" kept
  recurring. Fixed there by defaulting to `local_file` (safe: every deployed
  path sets the var explicitly) and by making `start_all.sh` delegate to
  `start_local.py` instead of running `app.py` bare.

  `jpmc-port` has **no `config.py` and no store factory of its own**, and the
  only file mentioning Falkor is `scudo/matcher_bridge.py`. Nothing to change
  here — but if this port ever grows its own config, do not copy the
  `"falkordb"` default.

## Deliberately NOT in this file

Security hardening (write tokens, seal v3 provenance, MCP auth gates) is
**parked by decision** — this is an in-house demo, not a hardening exercise.
Those findings are recorded in `/tmp/scudo-adjacent-findings.md` in the Capone
tree if that changes.

One item worth knowing even for a demo: the Lambda HITL approve path
(`scudo/handler.py` in this port, `backend/scudo/lambda_handler.py:596` in
Capone) writes `mapping_result` from the request body straight to the catalogue
**without** running the publish gate. So a malformed IRI can still reach the
projection table by a route the auto-publish path would reject. Tier 2 does not
close that path — it is a separate ingress. Data-consistency nit here, not a
vulnerability.
