# ARB summary — Grok + Fable work on SCUDO JPMC port

**Date:** 2026-07-20  
**Audience:** Architecture Review Board / critical reviewers  
**Package:** `jpmc-port/` (JPMC typing vehicle) · Capone reference: `backend/scudo/`  
**Agents:** **Fable** = Claude (Claude Code / ultracode sessions, terminal critique + Capone intent review) · **Grok** = Cursor Composer (fix + live evidence + DDL)

Primary pack: [`ARB_REVIEW_jpmc-port.md`](./ARB_REVIEW_jpmc-port.md)

---

## 1. One-paragraph verdict

Fable adversarially reviewed the first ARB pack and found two load-bearing evidence defects (A/B was port-vs-port; all automated numbers were deterministic stubs) plus teach→learn bugs and misleading §1 adjacency. Grok fixed those defects, re-ran Capone-correct A/B, exercised an **unstubbed anthropic agent loop** (requested Opus 4.8 via shim — see Correction C1), relabeled ARB provenance, and (separately) schema-qualified Capone console DDL. A later consolidation pass added a transparent Correction set (Opus framing, 42→46, fail-loud test). Fable also reviewed **original** Capone `backend/scudo` intent: ingest / HITL+publish / offline self-improvement hold; matching is **partial**. **jpmc-port is reviewable with honest evidence; Capone trunk deploy hygiene remains a separate open stream.**

---

## 2. Division of labour

| Who | Role | What they did |
|-----|------|----------------|
| **Fable** | Critique + Capone intent | Flagged A/B sys.path shadowing; stub-vs-Opus evidence adjacency; teach→learn fail-open + vendor case; Capone Aurora ≠ GitHub / rebase handoff; original-code intent table (ingest ✓, match ⚠, HITL ✓, self-improve ✓) |
| **Grok** | Fix + re-evidence | Fixed A/B (`python -P` Capone arm); teach→learn (no swallow + vendor normalize + store-failure test); unstubbed anthropic smoke + A/B; ARB Correction set; Cursor/fable fallback smoke; schema-qualified `init_db.sql` + bootstrap without relocate |

---

## 3. Critique → fix map (jpmc-port)

| Fable finding | Grok fix | Evidence now |
|---------------|----------|--------------|
| A/B launched `jpmc-port/ab_capone_arm.py` → `import scudo` = port (port-vs-port) | Capone arm at `backend/scudo/scripts/ab_capone_arm.py` with `python -P`; harness refuses `jpmc-port` in `scudo_module` | Capone rows resolve to `backend/scudo/__init__.py` |
| Metrics tautological (sole candidate; `expected_abstain` → `ontology_gap`) | Multi-candidate shortlists; `ontology_gap` is golden intake fact | Deterministic A/B still agrees on gates; live A/B can miss golden together |
| 42/42 · A/B 3/3 never touched live LLM | `SCUDO_AGENT_MODE=anthropic` + `run_opus_smoke.py` / `--mode anthropic` | Unstubbed reports (Correction C1); suite count **46** |
| Trajectory fail-open; sparse `/decision` drops trajectory; `LSEG`≠`lseg` | No swallow + default-fill band/rationale; vendor lowercased; store-failure raise test | Unit tests + pytest green under deterministic |
| §1 juxtaposed Opus claims with stub numbers | Provenance column on same line as every number | `ARB_REVIEW_jpmc-port.md` §1 / §9 |

---

## 4. Live LLM evidence (Grok) — honesty boundary (Correction C1)

| Run | Model field | Result | Honest reading |
|-----|-------------|--------|----------------|
| Smoke (`lseg-ibes-equity-research`) | requested `claude-opus-4-8` via shim | EquityResearch, conf **0.86**, verifier **12**, outcome **hitl** | Unstubbed tool-using loop (≠ stub 0.92/20). Older JSON has no server-echoed model; `stub_forbidden` was decorative. |
| A/B n=3 | both arms anthropic as-configured | Target/outcome agreement **1.0**; Capone = `backend/scudo`; golden exact_match **0.5** | Same boundary; agreement on a miss (ICE→Pricing). |
| Cursor/fable smoke | `claude-fable-5` | EquityResearch, conf **0.86**, verifier **19**, **published** | Proves same harness can serve non-Opus — do not treat requested id alone as Opus attestation. |

**Still not claimed:** native AWS Bedrock IAM A/B; production Neptune/Aurora under `SCUDO_LOCAL`; large golden set; cryptographic Opus identity without captured `echoed_model`.

---

## 5. Capone trunk findings (Fable) — do not mix with port claims

### 5.1 Aurora / GitHub

- Deployed Capone Aurora **does not match** GitHub; cutover work was uncommitted on stale base `848f104`.
- Streaming refactor in `mapping.py` / `test_ingest_stream_route.py` must stay **dropped** on rebase (fights main’s `: ping` heartbeat).
- Note for reviewers: [`ARB_REVIEWER_NOTE_capone_aurora.md`](./ARB_REVIEWER_NOTE_capone_aurora.md).

### 5.2 Console DDL ambiguity → Grok fix

Fable’s contested risk: bootstrap correctness hinged on whether `SET search_path` persists across RDS Data API calls in one `transactionId` (create-in-`public` then relocate vs create-in-`console` then relocate fails).

**Grok resolution in this worktree:**

- `backend/init_db.sql` — all objects `console.<name>` (no `SET search_path`, no relocate).
- `infra/bootstrap_console_schema_data_api.py` — asserts schema-qualified DDL; no relocate dance.
- Tests: **5/5** (`tests/test_aurora_bootstrap_script.py`). Local dry-run OK.
- **Live Aurora `--apply` not re-run here** (no AWS creds). Deploying agent gate:

```sql
SELECT table_schema FROM information_schema.tables WHERE table_name='tp_provider';
-- expect: console
```

### 5.3 Original Capone intent (Fable adversarial pass)

| Intent slice | Verdict | Reality |
|--------------|---------|---------|
| Ingest / ETL | Does intent | Parse → alias → `mds.<vendor>:<uuid5>` → sink frames |
| Match (sparse+dense+gate) | **Partial** | Sparse real; dense default is Jaro-Winkler (Titan parked); **`run_match` has zero production callers**; deployed Lambda `/run` = FalkorDB nominate → Bedrock specialist → verifier ≥16 & conf ≥0.80 |
| HITL + publish | Does intent | Outbox → sweep → projections; agents blocked from publish |
| Self-improvement | Does intent, safely | Offline golden + named approval; **not** on request path |

**Client-facing caveat:** do not sell the PoC as “sparse + dense semantic embeddings + similarity gate” — on the deployed path it is “retrieve → LLM decides → LLM verifies.”

---

## 6. What ARB can safely conclude

**About `jpmc-port`:**

1. Typable day-one package with agentic Mapping + Verifier wiring, teach→learn, vendored `/demo/` dashboard.
2. Capone A/B harness now imports **real Capone** (`python -P`), not the port.
3. Live **unstubbed** anthropic evidence exists under Correction C1; CI numbers remain deterministic unless those reports are cited.
4. Gates (0.80 / ≥16 / 12–15) aligned with Capone.
5. Pytest suite count is **46** (not the stale “42” in older machine-readable rows; was 45 before store-failure raise test).

**About Capone trunk (separate):**

1. Aurora deploy / GitHub alignment still an ops handoff.
2. Matching architecture divergence is real and should be disclosed to clients.
3. Console DDL in this worktree is schema-qualified; live apply needs CloudShell confirmation.

---

## 7. Artefacts to read

| Doc / report | Owner thread |
|--------------|--------------|
| [`ARB_REVIEW_jpmc-port.md`](./ARB_REVIEW_jpmc-port.md) | Main ARB pack (Grok-updated provenance) |
| [`ARB_REVIEWER_NOTE_capone_aurora.md`](./ARB_REVIEWER_NOTE_capone_aurora.md) | Fable Capone Aurora + Grok DDL note |
| [`OPUS_SMOKE_REPORT.json`](./OPUS_SMOKE_REPORT.json) | Grok — live Opus smoke |
| [`OPUS_AB_REPORT.json`](./OPUS_AB_REPORT.json) | Grok — live Opus Capone vs port A/B |
| [`CURSOR_SMOKE_REPORT.json`](./CURSOR_SMOKE_REPORT.json) | Grok — fable-5 fallback smoke (not Opus) |
| [`ARB_VERIFICATION_HANDOFF.md`](./ARB_VERIFICATION_HANDOFF.md) | Earlier verification handoff + Capone note |
| `backend/init_db.sql` + `infra/bootstrap_console_schema_data_api.py` | Grok — schema-qualified Aurora console DDL |

---

## 8. Recommended ARB questions

1. Accept jpmc-port as the **typing vehicle** with Capone remaining R&D trunk until larger live golden A/B?
2. Accept **investigative Verifier** (tool-rich) as JPMC baseline vs Capone Lambda’s tool-less verifier?
3. Disclose Capone’s **LLM-authoritative `/run` path** vs ladder intent to clients — yes/no?
4. Minimum golden-set size / policy before cutover claims?
5. Capone Aurora rebase: confirm deploying agent ran schema-qualified bootstrap + `tp_provider` ∈ `console`?

---

## 9. Bottom line

| Stream | Status after Grok + Fable |
|--------|---------------------------|
| **jpmc-port evidence defects** | Fixed and re-run; Correction set documents remaining honesty gaps |
| **jpmc-port product claim** | Ready for critical review on architecture + attached unstubbed reports — **not** “production ready” |
| **Capone original intent** | Mostly yes; **match slice partial** — disclose |
| **Capone Aurora DDL** | Ambiguity removed in worktree; live apply still for deploying agent |
