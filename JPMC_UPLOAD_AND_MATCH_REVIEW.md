# JPMC — Review brief: make file upload → agent matching work

**Audience:** the coding agent working inside the JPMC environment, plus the engineer
supervising it.
**Goal:** a user drops a vendor file into the UI, and the system matches its products
against the CDAO catalogue.
**Status of this brief:** every claim marked **VERIFIED** below was produced by running
the code, and the exact command is given so you can reproduce it. Anything not marked
VERIFIED is a pointer to look at, not a finding. Do not treat this document as ground
truth about *your* tree — the code you pulled may differ. **Re-run the probes first.**

---

## 0. How to use this document

This is a review brief, not a patch. It tells you **where to look**, **what the current
behaviour actually is**, and **which decisions are not yours to make**. Work in this
order:

1. **Section 1** — prove the environment runs at all. If this fails, nothing else matters.
2. **Section 2** — run the four probes. They produce the evidence you will reason from.
3. **Section 3** — the defects that block upload→match, ranked. Fix in order.
4. **Section 4** — decisions that need a human. Do not guess these; ask.
5. **Section 5** — traps. Things that look like defects and are not; changing them breaks
   the AWS deploys.
6. **Section 6** — how to prove you are done.

**Rule for this work:** do not change a line until you have run the probe that shows the
current behaviour. Several things in this codebase fail *silently* — an HTTP 200 with
zero results looks like success in the UI. If you patch by reading alone you will fix
the wrong layer.

---

## 1. Prove the environment runs

```bash
cd <repo root>
python start_local.py --backend
```

Expect the banner to print the environment, then Flask on `:5000` (or `$PORT`).

**Two things this script exists to prevent, both of which will bite you if you bypass it
and run `python app.py` directly:**

- **Auth.** `backend/auth.py` rejects every `/api/*` call with **HTTP 401** unless
  `SCUDO_AUTH_ALLOW_DEV=1` and `SCUDO_AUTH_DEV_PRINCIPAL` are set. This is the
  single most likely cause of "only one page opens" — the static shell loads,
  every data call 401s. `start_local.py` sets both; a bare `python app.py` sets neither.
- **Store backend.** `backend/scudo_mapping_mcp/config.py` builds `settings` as a
  **frozen dataclass at first import**, and `STORE_BACKEND` defaults to `falkordb`.
  If anything imports config before the env var is set, the process is pinned to
  FalkorDB for its whole life and will try to reach Redis on `:6379`.
  `start_local.py` sets `STORE_BACKEND=local_file` **before** importing `app.py`.
  Order matters; this is not superstition.

Health check, no UI needed:

```bash
curl -s localhost:5000/ | head -c 300
```

If you get 401 here, stop and fix the environment before reading further.

---

## 2. The four probes

Run all four. Paste the output into your working notes. They take about a minute total.

Set up once:

```bash
cd backend
export STORE_BACKEND=memory FRAME_SOURCE=mock \
       SCUDO_AUTH_ALLOW_DEV=1 SCUDO_AUTH_DEV_PRINCIPAL=demo@local
```

(`memory` rather than `local_file` deliberately — the probes should not write to the
durable journal.)

### Probe A — what the parser does with each sample file

```bash
python3 - <<'PY'
import sys; sys.path.insert(0, '.')
from pathlib import Path
from scudo_mapping_mcp.ingest import ingest_bytes
for p in ['../sample_data/provider/factset/company_fundamentals_v1.xlsx',
          '../sample_data/provider/sp_global/credit_ratings_v1.xml',
          '../sample_data/provider/bloomberg/equity_prices_v2.csv',
          '../sample_data/provider/reuters/news_sentiment_v1.json']:
    d = Path(p).read_bytes()
    try:
        fr = ingest_bytes('Bloomberg', Path(p).name, d, upsert=False)
        print(f'{Path(p).name:34s} -> {len(fr)} frames',
              [(f.product_id, f.name) for f in fr[:2]])
    except Exception as e:
        print(f'{Path(p).name:34s} -> RAISED {type(e).__name__}: {str(e)[:70]}')
PY
```

**VERIFIED result on this tree:**

| file | result |
|---|---|
| `company_fundamentals_v1.xlsx` | **RAISED `_csv.Error`** — "new-line character seen in unquoted field" |
| `credit_ratings_v1.xml` | **0 frames**, no error |
| `equity_prices_v2.csv` | **10 frames**, but every `name` is `''` |
| `news_sentiment_v1.json` | **0 frames**, no error |

### Probe B — what the HTTP route does with the same files

```bash
python3 - <<'PY'
import sys, io; sys.path.insert(0, '.')
import app as A
c = A.app.test_client()
for p, v in [('../sample_data/provider/factset/company_fundamentals_v1.xlsx', 'FactSet'),
             ('../sample_data/provider/sp_global/credit_ratings_v1.xml', 'S&P Global'),
             ('../sample_data/provider/bloomberg/equity_prices_v2.csv', 'Bloomberg')]:
    d = open(p, 'rb').read(); n = p.split('/')[-1]
    r = c.post('/api/mapping/ingest',
               data={'vendor': v, 'file': (io.BytesIO(d), n)},
               content_type='multipart/form-data')
    print(f'{n:34s} -> {r.status_code} {str(r.get_json())[:100]}')
PY
```

**VERIFIED result:**

| file | HTTP |
|---|---|
| `.xlsx` | **500** `{"error": "internal server error", "error_id": ...}` |
| `.xml` | **200** `{"ingested": 0, "products": []}` ← **silent zero** |
| `.csv` | **200** `{"ingested": 10, ...}` with empty names |

The `.xml` row is the dangerous one. The UI shows a successful upload and an empty
result, and nothing anywhere says why.

### Probe C — what the vendor gate accepts

```bash
python3 - <<'PY'
import sys, io; sys.path.insert(0, '.')
import app as A
c = A.app.test_client()
r = c.post('/api/mapping/ingest',
           data={'vendor': 'Reuters', 'file': (io.BytesIO(b'product_id,name\nA,B\n'), 'x.csv')},
           content_type='multipart/form-data')
print(r.status_code, r.get_json())
PY
```

**VERIFIED:** `400 {"error": "unknown vendor 'Reuters' (valid: LSEG, S&P Global,
Bloomberg, ICE, FactSet)"}`

Note that `sample_data/provider/` contains a **`reuters/`** directory. There is no
vendor named Reuters in the allowlist, so those three JSON files can never be
ingested under their own vendor name.

### Probe D — the shape that actually works

```bash
python3 - <<'PY'
import sys, io; sys.path.insert(0, '.')
import app as A
c = A.app.test_client()
csv = (b'product_id,name,description\n'
       b'BB-EQ-001,Equity Prices Historical Series,'
       b'EOD equity open high low close volume\n')
r = c.post('/api/mapping/ingest',
           data={'vendor': 'Bloomberg', 'file': (io.BytesIO(csv), 'p.csv')},
           content_type='multipart/form-data')
print('ingest ->', r.status_code, r.get_json())
r = c.post('/api/mapping/map', json={'vendor': 'Bloomberg', 'product_id': 'BB-EQ-001',
                                     'name': 'Equity Prices Historical Series'})
j = r.get_json()
print('map    ->', r.status_code,
      {k: j.get(k) for k in ('mapped_node_label', 'confidence', 'status', 'band')})
PY
```

**VERIFIED:** `ingest -> 200 {"ingested": 1, ...}` then
`map -> 200 {'mapped_node_label': 'Equity Prices', 'confidence': 0.8839,
'status': 'auto_mapped', 'band': 'pass'}`

**This is the single most important result in the document.** The matching engine is
not broken. Given a file in the right shape it matches at 0.88 and auto-maps. Every
defect below is about *getting files into that shape*, not about the matcher.

---

## 3. Defects, ranked

### D1 — `ingest_bytes` treats every non-JSON, non-Turtle file as CSV

**File:** `backend/scudo_mapping_mcp/ingest.py`, in `ingest_bytes` (~line 216 onward).

The format dispatch is:

```python
fmt = "json" if filename.lower().endswith(".json") else "csv"
```

There is no `.xlsx`, `.xml`, `.parquet`, or `.tsv`-beyond-delimiter branch. Everything
that is not `.json` and not Turtle is fed to `csv.DictReader`. That is why `.xlsx`
explodes (binary zip container through a text CSV reader) and `.xml` silently yields
zero rows (no commas, no recognised header).

**What to review, in order:**

1. **Decide the supported set.** See §4.1 — this is a scoping decision, not yours.
2. If the answer is "CSV and JSON only", the fix is **rejection, not parsing**:
   reject unsupported extensions up front with a clear 400 that names the supported
   formats. That is a handful of lines and removes both the 500 and the silent zero.
3. If the answer is "support xlsx/xml too", note that **the code already exists** —
   see `backend/ingestion/readers.py` (`XlsxIngester`, `XmlIngester`, `ParquetIngester`)
   and `backend/ingestion/factory.py`. **But read §5.1 before touching it.** Those
   engines belong to a different namespace with a different contract, and wiring them
   in naively will couple two subsystems that the code deliberately keeps apart.

### D2 — the `.xlsx` failure returns HTTP 500 instead of 400

**File:** `backend/routes/mapping.py`, `ingest_vendor_file` (~line 990).

The route catches:

```python
except (UnicodeDecodeError, ValueError) as e:      # -> 400
except (ConnectionError, TimeoutError, OSError) as e:  # -> 503
```

`csv.Error` is a subclass of `Exception`, **not** of `ValueError`, so it falls through
to the catch-all handler in `backend/app.py` and becomes a generic 500 with a
correlation id. A malformed upload is a **client** error; a 500 tells the user the
server is broken and tells the operator to go looking for a bug that isn't there.

Whatever you decide in D1, a bad file must produce a 4xx with a message that says what
was wrong. Check the other upload routes for the same gap: `/mapping/ingest/stream`
(~line 1111) and `/mapping/ingest/url` (~line 1254).

### D3 — silent zero-row ingest reports success

Covered by Probe B. `{"ingested": 0, "products": []}` with HTTP 200 is
indistinguishable in the UI from a file that legitimately contained nothing.

The pipeline already **counts rejects truthfully** — `_rows_to_frames`
(`ingest.py` ~line 145) returns `(frames, rejected)` and the stage callback emits
`validate(valid=..., rejected=...)`. That number exists and is correct; the
non-streaming route just never puts it in the response.

**Review:** should `/mapping/ingest` return `rejected` alongside `ingested`, and should
"parsed zero usable rows" be a 400 rather than a 200? Consider that a partially-bad
file (8 good rows, 2 rejects) should almost certainly still be a 200 — so the rule is
probably "zero usable rows is an error, some usable rows is a success with a count",
but confirm against §4.3.

### D4 — the shipped sample files cannot demonstrate the product

**VERIFIED inventory of `sample_data/provider/` — 12 files:**

| directory | files | vendor in allowlist? | ingests? |
|---|---|---|---|
| `bloomberg/` | 3 × `.csv` | yes | yes — but see below |
| `reuters/` | 3 × `.json` | **no** — Reuters is not a priority vendor | 0 frames |
| `sp_global/` | 3 × `.xml` | yes (`S&P Global`) | 0 frames (D1) |
| `factset/` | 3 × `.xlsx` | yes | 500 (D1/D2) |

The Bloomberg CSVs are the only ones that ingest, and they still do not demonstrate
matching. Their columns are:

```
date,ticker,open,close,volume,high
```

`ticker` is a recognised alias for `product_id` (so `AAPL`, `MSFT`… become product ids),
but there is **no** column matching any `name` alias, so every frame gets `name=''`.
**VERIFIED:** matching `AAPL` with an empty name scores **0.4643** against "Pricing" →
`needs_review`, `band='fail'`, rationale *"FAIL band — best candidate 'Pricing' at 0.46
< borderline threshold 0.70."*

**This is the root of "the demo doesn't match anything."** These files are *market data
observations* — a price series, a news feed, a ratings table. The matcher expects
**product catalogue metadata**: a row per vendor *product*, with a descriptive name.
They are different kinds of document, and no parser fix changes that.

The column aliases the parser accepts are in `ingest.py` `_COL_ALIASES` (~line 59):

```python
"product_id":  ("product_id", "id", "code", "ticker", "symbol", "sku")
"name":        ("name", "product", "product_name", "title", "label", "description_short")
"description": ("description", "desc", "details", "long_description", "notes")
```

Note `article_id` (the Reuters key) is **not** an alias for `product_id` — hence 0 frames
there even before the vendor gate rejects it.

**Review:** see §4.2. Adding sample files is cheap; picking the *right* ones is a
business question.

### D5 — durability defects in the local memory store (independent work stream)

`start_local.py` sets `STORE_BACKEND=local_file`, which uses
`backend/scudo_mapping_mcp/store/local_file_store.py` — the JSONL journal that makes
HITL decisions survive a restart.

Three findings against that file are **confirmed by two independent reviewers**, one of
which reproduced the failure end-to-end:

- **HIGH** — `_ends_with_newline` catches *every* `OSError` from `seek()` and returns
  `True` ("not torn"). A real I/O error therefore blinds the torn-line guard, and the
  next record is appended onto a half-written one. Reproduced: two JSON objects fused
  on one line, and on replay **both** precedents came back as `None` — one crash costs
  two decisions.
- **MED** — `_FSYNC_UNSUPPORTED` omits `errno.EOPNOTSUPP`. On Darwin `ENOTSUP=45` but
  `EOPNOTSUPP=102`; they are different values. A filesystem answering the latter fails
  a HITL decision that should have been tolerated. **Check this on your platform** —
  on Linux they are usually the same value, in which case the finding is inert there.
- **MED** — the `try` block spans `os.open(parent_dir)` as well as `os.fsync(fd)`, and
  `EACCES`/`EPERM` are in the tolerated set. A genuine permission failure *opening* the
  directory is misread as "this filesystem doesn't support directory fsync".

Plus a test defect: `test_a_real_directory_fsync_failure_is_not_swallowed` in
`backend/scudo/tests/test_local_file_store.py` has **no Windows skip**, but the
production code skips directory fsync entirely when `os.name == "nt"`. **VERIFIED by
simulation:** under `os.name == "nt"` the patched fsync is never reached, no `OSError`
is raised, and `pytest.raises(OSError)` fails. **If you are on Windows this test fails
and it is the test that is wrong, not your environment.**

These do not block upload→match. They block *trusting* the review decisions afterwards.
Fixes are being adjudicated separately — if you are picking this up cold, treat the
above as confirmed and fix them; do not re-derive.

---

## 4. Decisions that need a human

Do not guess these. Each one changes what you build.

### 4.1 Which upload formats must work?

- **CSV + JSON only** — smallest change, matches what the mapping pipeline was built
  for. Everything else gets a clear 400.
- **Add XLSX** — the engine exists in `backend/ingestion/readers.py`, but see §5.1.
- **Add XML** — same, plus someone must define how an arbitrary XML shape maps to
  product rows. `credit_ratings_v1.xml` uses `<record><issuer_id>…` — that mapping is a
  business decision, not a parsing one.

### 4.2 What should the sample files be?

Someone who knows the vendors must supply, or approve, a small set of **product
catalogue** files — one row per vendor product, with `product_id`, `name`, and ideally
`description`. Probe D shows the shape. Two or three files across two vendors is
enough to demonstrate the loop.

Also decide what happens to `sample_data/provider/reuters/` — either add Reuters to
`PRIORITY_VENDORS` (`backend/scudo_mapping_mcp/config.py` ~line 34) or drop/relabel the
directory. Leaving it is a guaranteed support question.

### 4.3 Is a zero-row upload an error?

See D3. Affects the HTTP contract and therefore the UI.

### 4.4 Which agent provider is the default here?

`backend/routes/mapping.py` `describe_agent` (~line 1346) reports the wired backend and
offers `scripted` / `bedrock` / `azure`. `start_local.py` pins
`SCUDO_AGENT_PROVIDER_DEFAULT=scripted` so the offline demo never calls AWS.

**Important semantics:** `get_agent(provider=…)` treats an **explicit** provider as an
*override* of `SCUDO_AGENT_BACKEND`. So if the UI sends `bedrock`, it calls AWS
regardless of the backend setting. Once Bedrock credentials are in place, flip
`SCUDO_AGENT_BACKEND=bedrock` **and** `SCUDO_AGENT_PROVIDER_DEFAULT=bedrock`; setting
only one produces a confusing half-state.

Note also that the agent **narrates**; the score is computed deterministically. Turning
Bedrock on does not change match confidence.

---

## 5. Traps — do not "fix" these

### 5.1 There are two things called "ingest" and they are deliberately separate

`backend/scudo_mapping_mcp/ingest.py` has an explicit **NAMESPACE NOTE** at the top:

> DO NOT consolidate with `backend/routes/ingest.py`. This module turns vendor-supplied
> files into `VendorProductRef` rows for the mapping pipeline. `routes/ingest.py` (the
> `ingest_bp` blueprint) is the separate ETL trigger that runs the format-specific
> engines in `backend/ingestion/` and writes to the `etl_run_log` table. They share the
> noun "ingest" and nothing else; merging them would couple the mapping package to the
> ETL orchestrator and break the transport-agnostic seam.

The `backend/ingestion/` engines are DB-backed and file-path-based (they read from disk
and write run logs). The mapping path is bytes-in, frames-out, no database. If §4.1
says "support XLSX", the right move is almost certainly to **borrow the parsing** (or
add a small bytes-level xlsx reader) — not to call `get_ingester()` from the mapping
route.

### 5.2 Do not delete `store/falkordb_store.py`

Even with FalkorDB unused. `opus_dense.py` imports `_jaro_winkler` **from that file**,
so the default scoring path needs it on disk. The falkordb *pip package* is not needed
— its import lives inside a method. This is already documented in
`store/factory.py`, which also explains why the `falkordb` and `neptune` branches are
left active: they are lazy imports that never load under `STORE_BACKEND=local_file`,
and the AWS deploys still use them.

### 5.3 Do not lower the confidence floor to make the demo match

The bands are a fixed contract: **passCut 0.80 / failCut 0.70**. The floor is read once
from `config.settings.confidence_floor` inside `matching.map_vendor_product`, and
`_validate_vendor` carries an explicit comment forbidding vendor-specific overrides —
adding one re-introduces a hidden second decision surface. If the demo files score 0.46,
the answer is §4.2 (better files), not a lower bar.

### 5.4 The catalogue is small on purpose

**VERIFIED:** `backend/scudo/fixtures/cdao_catalogue.json` holds **14 nodes**
(Market Data → Pricing → Equity Prices / Fixed Income Prices / FX Rates; Indices and
Benchmarks → Benchmark Index; Reference Data → Instrument Reference Data → Security
Master / Corporate Actions; Entity Reference Data; …). A product that has no plausible
home among those 14 *should* score low. Check the catalogue before concluding the
matcher is wrong.

Seeding is **lazy** — `_ensure_seeded()` in `routes/mapping.py` (~line 88) runs on first
request, not at startup, and retries on failure. An empty catalogue on the very first
call is expected, not a bug.

---

## 6. How to prove you are done

Do not report success on reasoning. Produce this evidence:

1. **Every file in `sample_data/provider/` either ingests with a non-zero count, or is
   rejected with a 4xx whose message says why.** No 500s. No silent 200-with-zero.
   Re-run Probe A and Probe B and paste the table.

2. **At least one shipped sample file matches in the pass band.** Re-run Probe D
   against a *shipped* file rather than an inline literal, and show
   `status: auto_mapped` with the confidence.

3. **The round trip through the UI.** Start `python start_local.py`, open
   `http://localhost:3000`, upload the file from (2) on the Matching Test page, and
   confirm the products and the match appear. A curl-only pass does not prove the UI
   wiring — the frontend posts multipart to `/api/mapping/ingest` via a Vite proxy to
   `:5000`, and any of those three hops can be the broken one.

4. **The test suite.** From the repo root:
   ```bash
   python -m pytest backend/scudo/tests/ -q
   ```
   Record the pass/fail counts **before** your changes as well as after, so a
   pre-existing failure is not mistaken for one you caused. Two failures in
   `test_provenance.py` are known and pre-existing. Bare `pytest` at the root collects
   nothing — you must give it the path.

5. **A negative control for each fix.** Revert the fix, show the test fails, restore it,
   show it passes. A fix that passes both ways is not load-bearing and you have not
   found the real cause.

6. **State what you did not do.** If §4 decisions were unanswered and you proceeded on
   an assumption, say which assumption, in the summary — not in a comment where it will
   be missed.

---

## Appendix A — endpoints on the upload/match path

All under `/api`, from `backend/routes/mapping.py`:

| method | path | purpose |
|---|---|---|
| POST | `/mapping/ingest` | multipart file upload → working set |
| POST | `/mapping/ingest/stream` | same, streaming real ETL stage events over SSE |
| POST | `/mapping/ingest/url` | fetch a URL server-side, synthesise one product row |
| GET | `/mapping/working_set` | what has been ingested |
| POST | `/mapping/map` | match one product against the catalogue |
| POST | `/mapping/similar` | candidate list |
| POST | `/mapping/decision` | record a HITL approve / override / reject |
| GET | `/mapping/agent/describe` | which agent backend is wired, which providers exist |
| POST | `/mapping/agent/run` | run the narrating agent over one product |

Blueprints are registered in `backend/app.py` (~line 73) all under `url_prefix="/api"`.

**HITL decision semantics** (`/mapping/decision`) — these trip people up:
`approve` means the suggested node was right and **requires** `suggested_confidence`;
`override` means the human picked a different node and **always** records confidence
1.0, ignoring `suggested_confidence`. Statuses are `approved` / `overridden` /
`rejected`, and the response field is `mapped_node_label`, not `mapped_node`.

## Appendix B — limits and environment variables

| variable | default | effect |
|---|---|---|
| `SCUDO_MAX_UPLOAD_BYTES` | 5 MB | Flask `MAX_CONTENT_LENGTH`; over it → **413** |
| `SCUDO_MAX_ROWS` | 10000 | parsed-row ceiling; over it → `ValueError` → 400 |
| `STORE_BACKEND` | **`falkordb`** | `local_file` \| `memory` \| `falkordb` \| `neptune`. Must be set **before** config import |
| `SCUDO_MEMORY_PATH` | `backend/local_memory/precedents.jsonl` | the durable journal |
| `SCUDO_AUTH_ALLOW_DEV` | off | without it every `/api/*` is **401** |
| `SCUDO_AUTH_ALLOW_DEV_WRITES` | off | without it HITL decisions are **403** |
| `SCUDO_AGENT_BACKEND` | `scripted` | `scripted` \| `bedrock` |
| `SCUDO_AGENT_PROVIDER_DEFAULT` | `bedrock` | what the UI preselects — see §4.4 |
| `FRAME_SOURCE` | — | `mock` reads bundled sample files instead of S3 |
| `SCUDO_TAXONOMY_SEED` | — | override the catalogue fixture path |
| `PORT` | 5000 | macOS AirPlay and some corporate agents squat on 5000 |

To delete everything the system has learned: stop the server, delete
`backend/local_memory/precedents.jsonl`, start it again. The file is plain JSONL — open
it in VS Code and you can read every decision.
