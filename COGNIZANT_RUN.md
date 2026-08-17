# Running SCUDO on a Cognizant machine

**No Citrix, no port, no hand-typing.** Clone the repo and run two commands.

This is the full application: the React console, the matching dashboard, the
REST API, and the Streamlit matching console with the agent reasoning trace and
the reviewer Approve / Reject buttons.

The local stack needs no Node, npm, Docker, PostgreSQL, FalkorDB or Neptune.
The shipped one-click configuration uses Bedrock for the reasoning agent,
chat, candidate similarity and borderline specialist, so that mode needs a
working Bedrock bearer key or AWS credentials. An explicit offline mode is
available below.

---

## 1. Install (once)

```bash
pip install -r backend/requirements-local.txt
```

Everything in that file is a pure-Python or prebuilt wheel — no native build
steps. It deliberately **omits** `falkordb` and `requests-aws4auth`, neither of
which is used locally.

## 2. Run — one command

```bash
python run_demo.py
```

That starts **everything** and opens the browser at
<http://localhost:8501>. Nothing else to do.

| URL | What it is |
|---|---|
| <http://localhost:8501> | **Start here.** Upload → match → agent reasoning → Approve/Reject → **chat with the agent** |
| <http://localhost:5055/app/> | React console — Providers, Datasets, Admin, Ingestion, Catalogue |
| <http://localhost:5055/demo/> | Matching dashboard — the story view |
| <http://localhost:5055/healthz> | Liveness |

Ctrl-C stops both servers.

### Running only half of it

`run_demo.py` is the one to use. If you want just the Flask side (console,
dashboard, API) without Streamlit:

```bash
python run_cognizant.py            # Flask only, on :5055
streamlit run streamlit_app.py     # Streamlit only, on :8501
```

Both must agree on `STORE_BACKEND` and `SCUDO_SCIPY_SQLITE_PATH` or the two
halves learn into different stores — a decision approved in Streamlit would be
invisible to the API, which looks exactly like the memory not working.
`run_demo.py` guarantees they agree, and that is the main reason to prefer it.

---

## 3. The many-vendor demo (the one to show)

The point of the demo is that **several vendors' contracts can legitimately
match the same catalogue dataset** — and each keeps its own score and its own
review decision.

Two ready-made contract sets ship in `sample_data/demo/`. In step 01, open
**"Sample contract sets — load or download"** and load both:

| Set | Vendor | Contracts |
|---|---|---|
| Vendor Q | LSEG | Equity Prices Historical Series, Corporate Actions Feed, Reference Data Entity Master |
| Vendor P | Bloomberg | Equity Price History Daily Feed, Credit Ratings Data |

The contract list **accumulates**, so both vendors sit side by side, and the
picker shows `vendor · contract` so you always know which one you are matching.

One measured run against the same 14-node catalogue produced the following
results. Exact scores can change with the selected Bedrock model; the important
property is that both contracts can map independently to the same dataset.

| Vendor | Contract | Score | Dataset |
|---|---|---|---|
| LSEG | `Q-CONTRACT-X` | **0.8317** pass | Equity Prices |
| Bloomberg | `P-CONTRACT-Y` | **0.8377** pass | **Equity Prices** |

Then approve **only** the LSEG one and re-run both:

| Vendor | Status after | Rationale |
|---|---|---|
| LSEG | **`approved`** | `precedent` |
| Bloomberg | `auto_mapped` — **untouched** | normal PASS band |

That is the whole argument: the dataset is not consumed, the decisions are
independent, and the learning is per (vendor, contract).

## 3a. The five-minute demo

1. **Streamlit, step 01** — upload a vendor CSV. Use `product_id,name` columns;
   real stage counts appear (`received → parse → validate → sink`). Uploads
   accumulate across vendors, so load more than one.
   *Optional third upload point:* **Add catalogue datasets** in the same step
   (`iri`, `label`, `parent_iri`) to match against your own catalogue rather
   than the shipped 14-node fixture. `parent_iri` must already exist — the
   store validates the hierarchy on every write, and the panel lists the valid
   parents.
2. **Step 02** — pick the product, press **Run match**.
3. Watch the **agent reasoning trace** — `thinking` / `calls` / `returns`.
4. Read the result: confidence, band, and the mapped CDAO node.
5. Press **Approve**. Then **Run match again** — the status becomes
   `approved` and the rationale becomes `precedent`. **It remembered.**
6. Show the memory. Decisions live in the durable matching store
   (`backend/.local/scudo_matching.sqlite3`) — count them with:

   ```bash
   sqlite3 backend/.local/scudo_matching.sqlite3 \
     "select count(*) from positive_precedents;"
   ```

   Prefer a file you can simply open on screen? Run with
   `STORE_BACKEND=local_file` instead and every decision is one readable JSON
   line in `backend/local_memory/precedents.jsonl`.
7. **Step 04 — ask the agent.** Type a question: *"how does the scoring
   work?"*, *"what is in the catalogue?"*, *"why did that score 0.88?"*. Tool
   calls appear inline above the answer, so you can see it reaching for
   evidence rather than improvising.

Measured, two separate processes:

| | Result |
|---|---|
| First match | `0.8839`, `auto_mapped` |
| After approval, **new process** | `0.8839`, **`approved`**, rationale **`precedent`** |

---

## 4. Two behaviours to know before demoing

**You must ingest before you match.** Matching a product with no ingested frame
returns **404 on purpose** — the matcher refuses to invent a product name from
an identifier. Upload the file first.

**The store outlives the frames.** Reviewer decisions survive a restart; the
uploaded frames do not (they are in-process under `FRAME_SOURCE=mock`). After a
restart, **re-ingest the file** and the approval is reused. The decision is
remembered; the upload is not.

---

## 4a. The agent chat — what it can and cannot do

Step 04 in Streamlit is a free-text chat over the **same six tools** the
matching agent uses: `find_similar_products`, `get_taxonomy_node`,
`get_ontology_neighbourhood`, `analyse_taxonomy_candidates`,
`map_vendor_product_tool`, `describe_system_context`. It **does not score** —
if a conversation ends in a mapping, the number still comes from the matcher
via the tool.

The tool surface is the containment boundary **for the `bedrock` backend
only**, which can act no other way. The `scripted` responder in the table below
imports `get_store()` and `seed_taxonomy()` and calls them directly — and
`seed_taxonomy()` is a *write*, which the six read-only tools cannot even
express. Do not describe the tool list as the containment boundary for the
scripted path.

**Two backends, and the UI says which one you have:**

| Sidebar agent | Chat behaviour |
|---|---|
| `bedrock` (shipped launcher default) | Real Claude with a genuine tool-calling loop — open-ended questions, multi-step reasoning |
| `scripted` (explicit offline mode) | Keyword responder. Answers scoring, catalogue, vendors and how-to-match **using real data**, and states plainly it is not a model |

The scripted fallback exists because a chat box that errors on a machine with
no AWS account is a worse demo than one that answers honestly. **For a client
demo of agent reasoning, use `bedrock`** — the scripted responder is a
stand-in, not the story.

---

## 5. Bedrock key-drop checklist

`python run_demo.py` ships with all three model paths enabled:
`SCUDO_AGENT_BACKEND=bedrock`, `SCUDO_SPECIALIST_BACKEND=local`, and
`SCUDO_DENSE_BACKEND=opus`. The selected model is written to the shared
`SCUDO_BEDROCK_MODEL_ID`, so one picker controls the mapping agent, agent chat,
borderline specialist and dense candidate scorer.

1. Run `python run_demo.py` and open <http://localhost:8501>.
2. In the sidebar, choose **Agent = bedrock**.
3. Leave the default region as **eu-west-2**, unless the bearer key and model
   access belong to another configured region.
4. Paste the `bedrock-api-key-...` bearer token, choose a model, and press
   **Apply & test**.
5. Wait for **Ready — [model] responded.** The preflight calls and fully drains
   **ConverseStream**, the same streaming API used by the agent. A token that
   can call only non-streaming Converse is not sufficient.
6. Load or upload contracts in step 01, run a match, inspect the reasoning
   trace, then Approve or Reject and run it again to dogfood the learned
   precedent.

The bearer token is placed only in the running Streamlit process. It is masked,
not logged, not written to disk, and is lost when the process stops.

The key or its IAM policy needs both permissions:

- `bedrock:InvokeModelWithResponseStream` for the reasoning agent and chat
  (`ConverseStream`);
- `bedrock:InvokeModel` for the dense scorer and local borderline specialist.

The model now affects candidate similarity and can therefore change confidence,
band and selected target. On retrieval paths, one failed model call discards
the whole model-scored candidate batch and scores every nominee in that match
with Jaro-Winkler; model and fallback scores are never mixed in one ranking.

Recognise degraded operation in the sidebar: **Dense arm** changes from the
model backend to **jaro_winkler (degraded)**. The result area may also warn that
model narration is missing or that the score is provisional. A numeric result
alone does not prove Bedrock ran.

### Explicit offline mode

AWS is not required when all model paths are deliberately disabled:

```bash
SCUDO_AGENT_BACKEND=scripted \
SCUDO_DENSE_BACKEND=jaro_winkler \
python run_demo.py
```

Choose **Agent = scripted** in Streamlit. This is a deterministic local
dogfood path with Jaro-Winkler scoring, a local Jaro-Winkler specialist and
scripted narration; it is not evidence of Bedrock reasoning.

---

## 6. What each surface is for

| Surface | Use it for |
|---|---|
| **Streamlit** (:8501) | The matching story: upload → agent reasoning → score → review. The only place with Approve / Reject |
| **React console** (`/app/`) | Providers, Datasets, Admin, Ingestion, Catalogue — the data-management pages |
| **Dashboard** (`/demo/`) | The graph/story view of vendor → ETL → matcher → gate → persist |

---

## 7. If something does not start

| Symptom | Cause |
|---|---|
| `/app/` or `/demo/` 404s | `frontend/dist/` or `dashboard-dist/` missing — both are vendored in git, so this means a partial checkout. `run_cognizant.py` checks and tells you |
| Every `/api/*` returns 401 | You launched `backend/app.py` directly instead of `run_cognizant.py`. The auth env must be set **before** import |
| Port 5000 in use (macOS) | AirPlay Receiver. `run_cognizant.py` defaults to **5055** for this reason; override with `PORT=…` |
| Approve / Reject does nothing | Three separate dev-write gates must be set; `run_cognizant.py` sets all three. Launched another way, `POST /api/mapping/decision` returns 403 |
| Matches suddenly return 0 candidates | Restart Streamlit — a cached taxonomy count can go stale against a rebuilt store |

---

## Related

- [`CITRIX_STREAMLIT_HANDOVER.md`](CITRIX_STREAMLIT_HANDOVER.md) — the
  locked-down-desktop variant, and the Streamlit app's known issues
- [`JPMC_AURORA_BEDROCK_FILES.md`](JPMC_AURORA_BEDROCK_FILES.md) — moving to
  Aurora and Bedrock, with credential diagnostics
- [`README.md`](README.md) — where the agent, its tools and the engine live
