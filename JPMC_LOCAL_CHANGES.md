# SCUDO — local-run changes to type in by hand

One file, everything in it. Written for manual typing: no git pull, no copy-paste.

**Work in order. Stop after any TIER and you still have a working system** —
each tier ends with a check you can run to prove it worked before continuing.

| tier | what you get | lines to type | can you stop here? |
|---|---|---|---|
| **1** | The app runs. UI opens. Pages load. Agent runs offline. | 213 | yes — this fixes the reported problems |
| **2** | It remembers human decisions after a restart | 281 | yes |
| **3** | Providers/Datasets/Admin/Ingestion pages (needs Docker) | 28 | yes |
| **4** | Two lines in a requirements file, then run the tests | 4 | optional |
| **5** | FalkorDB + Neptune visibly commented out | 20 | optional — changes no behaviour |

Those counts are measured, not estimated: they are the exact number of lines
inside the code blocks of each tier, counted mechanically. About a quarter of
them are `# JPMC-LOCAL:` comment lines or blanks you may skip (see below), so
the real typing load is lower: 155 lines of actual code in Tier 1, 212 in
Tier 2, 27 in Tier 3, 4 in Tier 4, 9 in Tier 5.

Bedrock is TIER 0 below — it is environment variables only, nothing to type
into a file.

Throughout, comments beginning `# JPMC-LOCAL:` are **explanation, not code**.
Skip them if you are short on time; the code works without them. They are
included because the next person to read this will not have this document.

---

## First: the actual diagnosis

Three of the six reported problems have the same single cause.

`start_all.sh` runs `python3 app.py`. That sets **none** of the local
environment variables, and `app.py` rejects every `/api/*` request without
them — HTTP 401 on all of them.

Measured on your codebase:

| request | `python3 app.py` | with the env set |
|---|---|---|
| `/api/catalogue/products` | **401** | **200, real data** |
| `/api/mapping/vendors` | **401** | **200** |
| `/` | 404 raw JSON | 200 index page |

So "cannot open the UI" and "only one page opens" are **not** MySQL and **not**
FalkorDB. They are five missing environment variables. Tier 1 fixes that.

And on the other three:

- **MySQL** — **already gone, before you pulled it.** The console database was
  ported MySQL → Aurora PostgreSQL in commit `bf2f50c`, which is on `main`.
  `db.py` is psycopg v3; `init_db.sql` says "Aurora PostgreSQL" on line 1.
  There is no MySQL driver in the Python code at all. The word survives in
  four CloudFormation templates and one stale test docstring, none of which is
  read when you run locally. **Nothing to comment out — it is not there.**
  Check for yourself:
  `grep -rnE "pymysql|mysql.connector|MySQLdb" backend/ --include="*.py"`
  returns nothing.
- **FalkorDB** — not needed. Just do not install the pip package (Tier 1).
  **But do not delete `store/falkordb_store.py`** — the default scoring path
  imports `_jaro_winkler` from that file (`store/memory_store.py:35` and
  `opus_dense.py:149`). Removing the *file* breaks matching; removing the
  *package* does not. The file's only use of the package is a lazy import
  inside a method (`falkordb_store.py:212`) that nothing calls locally.
- **Neptune** — not needed, not loaded. Same story: `requests-aws4auth` is
  used only by `store/neptune_store.py`.

I tested that last pair rather than assuming it. I made `import falkordb` and
`import requests_aws4auth` raise `ModuleNotFoundError`, started the app, and
matched a product: **Market Data at 0.6623** — the identical score to the run
with both packages installed. "Do not install them" is a measured statement,
not a hopeful one.

(0.6623 is the score for the Tier 2 walkthrough's payload, which sends `name`
only. Send a `description` as well and the number changes — the score depends
on which fields you supply, so use the walkthrough's exact payload when
checking your own run against this document.)

**Nothing in Tiers 1–4 requires commenting out FalkorDB or Neptune**, because
nothing local loads them: they are lazy imports behind an `if`, and the
default path never reaches either branch. That is why the tiers above leave
them alone — and why **five AWS deploy configs set `STORE_BACKEND=falkordb`**
in eight places (`backend/Dockerfile`, `backend/scudo/template.yaml`,
`backend/scudo/build-pipeline.yaml`, `infra/scudo-poc-app.yaml`,
`infra/scudo-dev-deploy.yaml`) and would break if the branches were deleted.

**If you want them commented out anyway, TIER 5 below does it** — the
instruction was explicit and it is your copy of the code. It is last because
it fixes nothing that is currently broken, and because in a hand-typed local
copy that never merges back, the AWS objection does not apply. Read Tier 5's
warning before typing it.

---

## TIER 0 — Bedrock (no typing, just environment variables)

```bash
export SCUDO_AGENT_BACKEND=bedrock
export AWS_REGION=us-east-1                                  # your region
export SCUDO_BEDROCK_MODEL_ID=us.anthropic.claude-opus-4-8   # match your region
```

**The gotcha:** the built-in default is `eu.anthropic.claude-opus-4-8`
(`backend/scudo_mapping_mcp/agent.py:123`) — an **EU** inference profile. On a
US account the default fails with an access error that does not mention the
region. The `eu.` / `us.` prefix must match `AWS_REGION`.

Credentials come from boto3 as normal (`~/.aws/credentials`, env, or role).

Two separate switches — do not confuse them:

| variable | controls | default |
|---|---|---|
| `SCUDO_AGENT_BACKEND` | whether the LLM narrates the reasoning | `scripted` |
| `SCUDO_DENSE_BACKEND` | whether the LLM does the **scoring** | `jaro_winkler` |

Leave the second alone. Scoring stays deterministic and auditable; the same
input always gives the same score. Worth knowing so nobody claims the model is
making the decisions — it is not, the confidence gate is.

---

## TIER 1 — make it run  (213 lines)

### 1a. New file: `backend/requirements-local.txt`

Same as `requirements.txt` minus the two packages that will not install or are
never used locally. (Tier 4 later appends two test-only packages to the same
file, so the finished file is "minus two runtime packages, plus two test
dependencies" — but type it exactly as below for now.)

```
flask==3.1.0
flask-cors==5.0.1
psycopg[binary]==3.3.4
pandas==2.2.3
openpyxl==3.1.5
xlrd==2.0.1
pyarrow==18.1.0
lxml==5.3.0
python-dotenv==1.0.1
pydantic>=2,<3
boto3>=1.34
strands-agents>=0.1
mcp>=1.2.0
gunicorn>=22.0
requests>=2.32
rdflib>=7.0.0
pyshacl>=0.26.0
urllib3>=2.0
```

Dropped on purpose: `falkordb>=1.0.8` (this is what makes `pip install` fail)
and `requests-aws4auth>=1.2` (Neptune only). Note `requests>=2.32` must stay —
URL ingestion needs it, and it used to arrive only as a dependency of
`requests-aws4auth`. `boto3` stays too: it serves **both** Bedrock and S3
vendor frames. It is lazy-imported, so with `FRAME_SOURCE=mock` and the
scripted agent it is installed but never called.

```bash
pip install -r backend/requirements-local.txt
```

### 1b. New file: `start_local.py` (repo root)

Replaces `start_all.sh`, and runs on Windows.

```python
#!/usr/bin/env python3
"""JPMC-LOCAL: cross-platform starter (Windows / macOS / Linux).

start_all.sh is zsh-only AND launches `python3 app.py` with no environment,
so every /api/* call returns 401 -- that is why "only one page opens".
This sets the environment FIRST, then starts both servers.

    python start_local.py            # backend + frontend
    python start_local.py --backend  # backend only (no Node needed)
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
BACKEND = BASE / "backend"
FRONTEND = BASE / "frontend"

LOCAL_ENV = {
    # TIER 1: type "memory" for BOTH of these. Change them to "local_file"
    # ONLY when you have finished Tier 2 -- that store does not exist yet, and
    # config.py's allow-list rejects the name, so the app will not start.
    "STORE_BACKEND": "memory",
    "SCUDO_PERSIST_TARGET": "memory",
    "SCUDO_AUTH_ALLOW_DEV": "1",
    "SCUDO_AUTH_DEV_PRINCIPAL": "demo@local",
    "SCUDO_AUTH_ALLOW_DEV_WRITES": "1",
    "SCUDO_VERDICT_ALLOW_DEV": "1",
    "FRAME_SOURCE": "mock",
    # Preselect the offline narrator in the Matching Test dropdown. WITHOUT
    # this the UI defaults to "bedrock", and an explicitly-chosen provider
    # overrides SCUDO_AGENT_BACKEND -- so the "offline" demo would call AWS
    # and fail with no credentials. Set this to "bedrock" once Bedrock works.
    "SCUDO_AGENT_PROVIDER_DEFAULT": "scripted",
}


def main() -> int:
    env = os.environ.copy()
    for key, value in LOCAL_ENV.items():
        env.setdefault(key, value)

    backend_only = "--backend" in sys.argv

    # Honour PORT so nothing printed below can lie about where the app is.
    port = env.get("PORT", "5000")

    print("SCUDO local startup")
    print("-" * 60)
    for key in sorted(LOCAL_ENV):
        print(f"  {key}={env[key]}")
    print("-" * 60)

    procs = []
    print(f"Starting Flask backend on :{port} ...")
    procs.append(subprocess.Popen([sys.executable, "app.py"], cwd=BACKEND, env=env))

    if not backend_only:
        npm = "npm.cmd" if os.name == "nt" else "npm"
        # `npm run dev` exits instantly if dependencies were never installed,
        # and the parent used to wait on the backend first -- so the UI was
        # simply dead with nothing on screen explaining why. Check up front.
        if not (FRONTEND / "node_modules").is_dir():
            print()
            print("  !! frontend/node_modules is missing -- the UI cannot start.")
            print("     Run this once:   cd frontend && npm install")
            print("     Continuing with the backend only.")
            backend_only = True
        else:
            print("Starting React frontend on :3000 ...")
            try:
                procs.append(
                    subprocess.Popen([npm, "run", "dev"], cwd=FRONTEND, env=env)
                )
            except FileNotFoundError:
                print(f"  npm not found -- backend only. Open http://localhost:{port}/")
                backend_only = True

    print()
    print(f"  Backend   http://localhost:{port}/       (JSON index; proves it is up)")
    if not backend_only:
        print("  Frontend  http://localhost:3000/       <-- THE UI")
    print()
    print("Press Ctrl+C to stop.")

    # Wait on whichever child exits FIRST, not on the backend specifically:
    # if the UI dies you want to know immediately, not sit looking at a
    # half-running system waiting for a backend that never exits.
    try:
        while procs:
            for proc in list(procs):
                if proc.poll() is not None:
                    print(f"\n  child pid {proc.pid} exited ({proc.returncode}).")
                    procs.remove(proc)
                    for other in procs:
                        other.terminate()
                    return proc.returncode or 0
            time.sleep(0.4)
    except KeyboardInterrupt:
        for proc in procs:
            proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

**If you type only one thing from this document, type this file.**
Type `LOCAL_ENV` exactly as shown -- both store values are `memory`, which is
what Tier 1 needs. Step 2d changes them to `local_file` once the store from
Tier 2 exists. Setting them to `local_file` early stops the app from starting.

### 1c. Edit `backend/app.py` — add a root route

Without this, `http://127.0.0.1:5000/` returns a raw 404 and looks broken.

Find `@app.get("/healthz")` (line 104 in a fresh clone) and insert **above** it:

```python
# JPMC-LOCAL: without this, http://127.0.0.1:5000/ returns a raw 404 JSON blob
# and looks broken. It is not broken -- the backend is an API, the UI is served
# by Vite on :3000. This route says so, and lists the endpoints that need no
# database so you can confirm the backend works before touching Postgres.
@app.get("/")
def _index():
    """Human-readable landing page for the API process."""
    return (
        jsonify(
            {
                "service": "SCUDO backend API",
                "ui": "http://localhost:3000  <-- open THIS in the browser",
                "note": "This process serves /api/* only. It is working if you can see this.",
                "no_database_needed": [
                    "/healthz",
                    "/readyz",
                    "/api/catalogue/products",
                    "/api/mapping/vendors",
                ],
                "needs_postgres": ["/api/providers", "/api/datasets", "/api/admin/users"],
            }
        ),
        200,
    )
```

`jsonify` is already imported. This route sits outside `/api/*`, so the auth
gate lets it through — that is intentional and it exposes no data.

Then go to the **bottom of the same file** and make the port configurable.
Replace:

```python
if __name__ == "__main__":
    # Run in debug mode on port 5000 for local development.
    app.run(debug=True, port=5000)
```

with:

```python
if __name__ == "__main__":
    # JPMC-LOCAL: port made configurable. 5000 was hard-coded, so if anything
    # already held that port the process died with "Address already in use"
    # and there was no way out without editing code. macOS AirPlay Receiver
    # squats on 5000 by default, and locked-down desktops often have their own
    # agent there. Now: PORT=5050 python start_local.py (and point the UI at
    # it with VITE_API_PROXY=http://localhost:5050). Default is unchanged.
    app.run(debug=True, port=int(os.getenv("PORT", "5000")))
```

`os` is already imported. `VITE_API_PROXY` is read by `frontend/vite.config.js`
as it stands — nothing to change there.

### 1d. Edit `frontend/src/App.jsx` — fix the landing page

The app currently opens on `/providers`, which needs PostgreSQL, so the first
thing anyone sees is "Failed to load providers". `/catalogue` needs no database.

Find line 23:

```jsx
<Route path="/" element={<Navigate to="/providers" replace />} />
```

Replace with:

```jsx
{/* JPMC-LOCAL: was Navigate to="/providers", which needs Postgres and
    so greeted every new user with "Failed to load providers". /catalogue
    needs no database, so the app now opens on a page that works. */}
<Route path="/" element={<Navigate to="/catalogue" replace />} />
```

### 1e. Edit `backend/routes/mapping.py` — add an offline provider

**Without this, the "offline" demo calls AWS and fails.** The Matching Test
page has a Provider dropdown that offered only Bedrock and Azure, and
`get_agent()` treats an explicitly-chosen provider as an **override** of
`SCUDO_AGENT_BACKEND` — deliberately, so the dropdown never lies about which
runtime ran. Consequence: the UI sent `bedrock` on every run regardless of
your backend setting, and with no credentials there was no way to drive the
demo offline from the browser at all.

In `describe_agent()` (line 1363 in a fresh clone), find:

```python
    providers = [
        {
            "id": "bedrock",
```

and insert a new entry **before** the bedrock one:

```python
    providers = [
        # JPMC-LOCAL: without this entry the dropdown offered ONLY cloud
        # runtimes, and get_agent() treats an explicit provider as an override
        # -- so picking "bedrock" called AWS even when SCUDO_AGENT_BACKEND was
        # "scripted". There was no way to drive the demo offline from the UI.
        {
            "id": "scripted",
            "label": "Local scripted narrator (no AWS)",
            "enabled": True,
        },
        {
            "id": "bedrock",
```

`start_local.py` sets `SCUDO_AGENT_PROVIDER_DEFAULT=scripted`, so the dropdown
preselects it. Deploys are unaffected — they set that variable or inherit the
existing `bedrock` default.

**Two more edits are required, and the menu entry above is useless without
them.** I originally wrote here that `get_agent()` needed no change because it
"falls through to scripted". That was wrong, and an independent review caught
it. Both problems were reproduced by running the code:

**(i) `backend/scudo_mapping_mcp/agent.py`** — find `normalized_provider` in
`get_agent()` (line 1234 in a fresh clone). After the `bedrock` branch and
**before** the `backend = (os.getenv("SCUDO_AGENT_BACKEND")...` line, insert:

```python
    # JPMC-LOCAL: "scripted" is an override too, for the same reason the two
    # above are. Without this branch it fell through to SCUDO_AGENT_BACKEND,
    # so on a Bedrock-configured host the "Local scripted narrator (no AWS)"
    # option returned BedrockMappingAgent -- the one runtime it promises not
    # to be. Every named provider must be honoured, or the dropdown lies.
    if normalized_provider == "scripted":
        return ScriptedMappingAgent(use_mcp_host=use_host)
```

Without this, selecting the offline option on a machine with
`SCUDO_AGENT_BACKEND=bedrock` calls AWS anyway.

**(ii) `backend/routes/mapping.py`** — find the `run_agent` allow-list
(line 1416 in a fresh clone):

```python
    if agent_provider is not None and agent_provider not in ("bedrock", "azure"):
```

and replace it with:

```python
    # JPMC-LOCAL: "scripted" added. /agent/describe offers it, so rejecting it
    # here made the offline runtime a menu entry that 400s. Keep this tuple in
    # step with the provider list in describe_agent() below -- a provider the
    # UI can pick must be a provider the route accepts. It must NOT be kept in
    # step with scudo/lambda_handler.py, whose tuple deliberately differs; see
    # the note under this block before "fixing" the two to match.
    if agent_provider is not None and agent_provider not in (
        "bedrock",
        "azure",
        "scripted",
    ):
```

Without this, picking the offline option returns **HTTP 400** — the dropdown
offers a runtime the route refuses. Note the allow-list still rejects unknown
providers; only the one name is added.

> There is a third copy of this list in `backend/scudo/lambda_handler.py`
> (`if agent_provider not in ("bedrock", "azure")`). **Leave it alone.** It is
> the deployed AWS Lambda path, so it does not affect local running — and
> adding `"scripted"` there would be actively harmful. A few lines above it,
> `_get_agents_for_provider()` routes *everything except* `"azure"` to
> `_build_bedrock_agents()`, so a Lambda that accepted `"scripted"` would call
> Bedrock while reporting it ran the offline narrator. The 400 it returns
> today is the safer answer. The local Flask route is safe precisely because
> `get_agent()` has a real `"scripted"` branch (step 1e(i)) — the Lambda has
> no equivalent.
>
> (An earlier draft of this document told you to add it there. That was wrong,
> and the independent review caught it.)

### 1f. Edit `frontend/src/pages/matching/MatchingTest.jsx` — fail closed

Two small changes to the same file. Steps 1c–1e make the backend safe; this
makes the *page* safe, and without it the offline demo can still call AWS.

Find `const [provider, setProvider] = useState('bedrock')` (line 7 in a fresh
clone, the only match in the file) and change the value to `'scripted'`:

```jsx
  const [provider, setProvider] = useState('scripted')
```

The page asks `/api/mapping/agent/describe` for the real default and replaces
this a moment later — but until that answers, or if it never does, whatever is
here is what Run sends. `bedrock` was therefore a live AWS call on a page
advertised as offline. `scripted` is safe in both cases.

Then, a few lines below, that same request swallows its own failure. Find
`.catch(() => {})` (also the only match) and replace it with:

```jsx
      .catch(() => setError(
        'Could not read /api/mapping/agent/describe — provider list unavailable. '
        + 'Falling back to the offline "scripted" runtime.'
      ))
```

An empty dropdown with no message is the hardest kind of thing to debug on a
locked-down machine; now the page tells you which call failed.

### CHECK — Tier 1

```bash
python start_local.py --backend
```

In another terminal. **Every `curl` in this document assumes the default port
5000** — if you started with `PORT=5050`, substitute it throughout, or set
`SCUDO=http://127.0.0.1:5050` once and use `$SCUDO/...` in place of the host
below. Testing the wrong port is the easiest way to conclude that something is
broken when it is not:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5000/
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5000/api/catalogue/products
```

Both must print **200**. The second one printed 401 before this work — that is
the fix. Then check the agent runs offline:

```bash
curl -s http://127.0.0.1:5000/api/mapping/agent/describe
```

`default_provider` must be **`scripted`** and the provider list must contain
`scripted`. If it still says `bedrock`, step 1e or the `LOCAL_ENV` line is
missing and the demo will try to reach AWS.

Then `python start_local.py` and open **http://localhost:3000**.

> If `npm run dev` has never been run here, do `cd frontend && npm install`
> once first. `start_local.py` now detects the missing `node_modules` and says
> so rather than leaving a dead port.

> `/readyz` returning 503 immediately after start is **normal** — the taxonomy
> loads lazily on the first mapping request. It flips to ready after that.

---

## TIER 2 — memory that survives a restart  (281 lines)

### Why this tier exists

SCUDO learns from human review: approve a match, and next time that product is
matched it uses your decision instead of re-scoring. That already works. But
with `STORE_BACKEND=memory` the decisions live in a Python dictionary that dies
with the process — so you can never restart and *see* that it remembered. The
learning is real but invisible, which is a bad way to show it to people who
have not seen an agent system before.

In AWS these records live in Aurora (`backend/scudo/aurora_memory.py`). This
tier is the laptop equivalent: an append-only JSONL file you can open in VS
Code and read.

### 2a. New file: `backend/scudo_mapping_mcp/store/local_file_store.py`

```python
"""Local file-backed store -- the laptop equivalent of Aurora memory.

Precedents (human review decisions) are appended to a readable JSONL file and
replayed on startup through the SAME upsert_precedent the live path uses, so
the invariants cannot drift between the two paths.

Enable with STORE_BACKEND=local_file.
"""

from __future__ import annotations

import errno
import json
import os
import threading
import time
from pathlib import Path

from ..models import TaxonomyNode, VendorProductRef
from .memory_store import MemoryStore

# Directory fsync is not supported everywhere. These errnos mean "this
# filesystem does not do that" -- not "the write failed" -- and are the only
# ones tolerated below. EIO/ENOSPC and friends are real and must propagate.
_FSYNC_UNSUPPORTED = frozenset(
    {errno.EINVAL, errno.EPERM, errno.EACCES, errno.ENOTSUP, errno.EISDIR}
)

DEFAULT_MEMORY_PATH = (
    Path(__file__).resolve().parents[2] / "local_memory" / "precedents.jsonl"
)


def memory_path() -> Path:
    """Where the journal lives. Override with SCUDO_MEMORY_PATH."""
    raw = (os.getenv("SCUDO_MEMORY_PATH", "") or "").strip()
    return Path(raw) if raw else DEFAULT_MEMORY_PATH


class LocalFileStore(MemoryStore):
    """MemoryStore + a durable, human-readable precedent journal."""

    def __init__(self, path: Path | None = None) -> None:
        super().__init__()
        self._path = Path(path) if path is not None else memory_path()
        self._replaying = False
        # Flask serves requests on threads, so two reviewers can decide on the
        # same product at once. Without this lock the in-memory update and the
        # file append are separate steps: live state could end at B while the
        # file records B then A, and the next restart would replay A last and
        # silently REVERSE the decision. One lock covers both steps, so the
        # journal order always matches the order the memory saw.
        self._lock = threading.RLock()
        self._replay()

    def _replay(self) -> None:
        """Rebuild in-memory precedents from the journal, oldest first."""
        if not self._path.exists():
            return
        self._replaying = True
        # Held for the whole rebuild so no live decision can interleave.
        try:
            with self._lock:
                for lineno, line in enumerate(
                    self._path.read_text(encoding="utf-8").splitlines(), start=1
                ):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        self.upsert_precedent(
                            ref=VendorProductRef(**rec["ref"]),
                            node=TaxonomyNode(**rec["node"]),
                            decision=rec["decision"],
                            decided_by=rec["decided_by"],
                            confidence=rec["confidence"],
                            provisional=rec.get("provisional", False),
                            decided_at_ms=rec.get("decided_at_ms"),
                        )
                    except Exception as exc:  # noqa: BLE001
                        # One bad line must not cost you every prior decision.
                        print(
                            f"[local_file_store] skipped {self._path}:{lineno}: {exc}",
                            flush=True,
                        )
        finally:
            self._replaying = False

    def upsert_precedent(
        self,
        *,
        ref,
        node,
        decision,
        decided_by,
        confidence,
        provisional=False,
        decided_at_ms=None,
    ):
        # Stamp REAL wall-clock time for a live decision. The inherited
        # FakeStore stamps a fake counter that starts at 2023-11-14, which is
        # fine for tests but would write a fictional date into an audit
        # journal a human is meant to read. Replay passes the recorded value
        # straight through, so a decision keeps its original timestamp
        # forever instead of being re-dated on every restart.
        if decided_at_ms is None and not self._replaying:
            decided_at_ms = int(time.time() * 1000)
        # Replay holds the lock for the whole file, so re-entering here is
        # normal -- hence RLock rather than Lock.
        with self._lock:
            self._write_locked(
                ref=ref,
                node=node,
                decision=decision,
                decided_by=decided_by,
                confidence=confidence,
                provisional=provisional,
                decided_at_ms=decided_at_ms,
            )

    def _write_locked(
        self,
        *,
        ref,
        node,
        decision,
        decided_by,
        confidence,
        provisional,
        decided_at_ms,
    ):
        """Append to the journal FIRST, then update memory -- one atomic unit.

        The order is deliberate. If memory were updated first and the append
        then failed (disk full, read-only mount), the running process would
        keep serving a decision no restart can recover: the reviewer sees
        "approved", the journal has no record of it, and the disagreement
        only surfaces when a restart silently reverses it. Journalling first
        fails the recoverable way -- the caller gets the OSError, memory is
        untouched, and the two can never disagree.
        """
        if self._replaying:
            # Replay must update memory but not re-append what it just read.
            return super().upsert_precedent(
                ref=ref,
                node=node,
                decision=decision,
                decided_by=decided_by,
                confidence=confidence,
                provisional=provisional,
                decided_at_ms=decided_at_ms,
            )
        rec = {
            "ref": {
                "vendor": ref.vendor,
                "product_id": ref.product_id,
                "name": ref.name,
                "description": ref.description,
                "source_content_hash": ref.source_content_hash,
                "source_file_audit_id": ref.source_file_audit_id,
            },
            "node": {"iri": node.iri, "label": node.label},
            "decision": decision,
            "decided_by": decided_by,
            "confidence": confidence,
            "provisional": provisional,
            "decided_at_ms": decided_at_ms,
            # Not read back on replay -- purely so a human scanning the file
            # can see WHEN something was learned without converting millis.
            "decided_at": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime((decided_at_ms or 0) / 1000)
            ),
        }
        created = not self._path.exists()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            # Lead with a newline if a previous crash left a half-written line
            # with no terminator. Without this, the next append would join onto
            # that torn tail, fusing two records into one unparseable line --
            # so the crash would cost the NEXT decision as well as its own.
            # A stray blank line is skipped by _replay and costs nothing.
            if self._path.stat().st_size and not self._ends_with_newline():
                fh.write("\n")
            # ensure_ascii=False keeps non-English vendor names readable.
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        if created and os.name != "nt":
            # fsync above makes the CONTENTS durable, but on the very first
            # write the file's directory ENTRY may still be unflushed -- a
            # power cut can then lose the whole journal even though the bytes
            # were synced. Only needed when the file is newly created; later
            # appends do not touch the directory.
            #
            # Windows cannot open a directory handle at all, so it is skipped
            # outright rather than by catching the failure -- otherwise a real
            # I/O error and "this platform does not do that" look identical.
            #
            # On POSIX a REAL failure here (EIO, ENOSPC) is propagated: the
            # decision has not been made durable, and this file's whole
            # contract is that it never reports a decision it could not
            # persist. But "this filesystem does not support fsync on a
            # directory" is not a failure -- corporate VDI home directories are
            # often network mounts that answer EINVAL/EPERM, and turning that
            # into a failed HITL decision would break the feature for the
            # people it was written for.
            try:
                dir_fd = os.open(self._path.parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError as exc:
                if exc.errno not in _FSYNC_UNSUPPORTED:
                    raise
        # Durable on disk -- only now is it safe to act on.
        super().upsert_precedent(
            ref=ref,
            node=node,
            decision=decision,
            decided_by=decided_by,
            confidence=confidence,
            provisional=provisional,
            decided_at_ms=decided_at_ms,
        )

    def _ends_with_newline(self) -> bool:
        """True if the journal's last byte is a newline (i.e. not torn).

        Only called when the file is known to be non-empty. An empty file
        cannot seek to -1, which is a legitimate "nothing to fuse onto" --
        every other read error is NOT: if the tail cannot be read, the
        torn-line guard above is blind, and answering True would let the next
        record fuse onto a half-written one. Fail loudly instead; the caller
        gets the OSError before memory is touched, which is the same
        recoverable direction as a failed append.
        """
        with self._path.open("rb") as fh:
            try:
                fh.seek(-1, os.SEEK_END)
            except OSError:
                return True  # empty file -- nothing to fuse onto
            return fh.read(1) == b"\n"
```

### 2b. Edit `backend/scudo_mapping_mcp/store/factory.py`

Find the `memory` branch inside `get_store()` and add a branch after it, and
extend the error message:

```python
    # JPMC-LOCAL: same scoring as 'memory', but HITL precedents are journalled
    # to a readable JSONL file so the learning loop survives a restart.
    if backend == "local_file":
        from .local_file_store import LocalFileStore

        return LocalFileStore()
    raise ValueError(
        f"Unknown STORE_BACKEND '{backend}'. Use 'falkordb' (local), "
        f"'neptune' (prod), 'memory' (laptop demo), or "
        f"'local_file' (laptop demo with durable memory)."
    )
```

### 2c. Edit `backend/scudo_mapping_mcp/config.py`

**This one is easy to miss and the app will not start without it.**
`persist_target` defaults to `store_backend`, and there is a separate
allow-list that will reject `local_file` with:

```
ValueError: SCUDO_PERSIST_TARGET='local_file' not in ('falkordb', 'neptune', 'none', 'memory')
```

Find line 78:

```python
_ALLOWED_PERSIST_TARGETS: tuple[str, ...] = ("falkordb", "neptune", "none", "memory")
```

Replace with:

```python
# JPMC-LOCAL: 'local_file' added -- durable laptop memory (see store/local_file_store.py).
_ALLOWED_PERSIST_TARGETS: tuple[str, ...] = (
    "falkordb",
    "neptune",
    "none",
    "memory",
    "local_file",
)
```

### 2d. Turn it on — `start_local.py`

**Nothing above changes behaviour until you do this.** In `LOCAL_ENV`, change
the two values you typed as `memory` in Tier 1:

```python
    "STORE_BACKEND": "local_file",
    "SCUDO_PERSIST_TARGET": "local_file",
```

Both. `SCUDO_PERSIST_TARGET` defaults to `STORE_BACKEND`, but `start_local.py`
sets it explicitly, so changing only the first leaves the other on `memory`.

If you get `ValueError: SCUDO_PERSIST_TARGET='local_file' not in (...)` at
startup, step 2c is missing.

### CHECK — Tier 2: watch it learn

This is the demo worth showing people.

> **The UI cannot drive this.** The Matching Test page has no
> approve/override/reject control — nothing in `frontend/src` calls
> `/api/mapping/decision`. Adding that button is the obvious next piece of
> work and it is **not done**. Use `curl` for now.

Start the app, then:

**1. Match it.** Nothing decided yet:

```bash
curl -s -X POST http://127.0.0.1:5000/api/mapping/map \
  -H 'Content-Type: application/json' \
  -d '{"vendor":"LSEG","product_id":"LSEG-CARBON-029","name":"Carbon Data"}'
```

→ `confidence 0.6623`, `status needs_review`, node **Market Data**.
Below the 0.70 floor, so it asks a human. Note the guess: Market Data.

**2. A human decides.** The machine guessed Market Data; the right answer is
`Pricing`. Rejecting the suggestion in favour of a different node is an
**`override`**, not an `approve` — `approve` means "the suggested node was
right", and using it here would record Pricing as though the matcher had
proposed it:

```bash
curl -s -X POST http://127.0.0.1:5000/api/mapping/decision \
  -H 'Content-Type: application/json' \
  -d '{"vendor":"LSEG","product_id":"LSEG-CARBON-029","decision":"override",
       "node_iri":"jpmorgan:data:cdao:subdomain:pricing",
       "name":"Carbon Data"}'
```

→ `status overridden`, node **Pricing**, `confidence 1.0`. An override is a
human assertion, so it carries full confidence; `suggested_confidence` is
read only for `approve` and ignored here.

**3. Match again** (same command as step 1) → `status overridden`, node
**Pricing**, rationale **`precedent`**, confidence `1.0`. It did not re-score;
it returned your answer.

**4. Stop the server, start it again, match a third time** → still
`Pricing` / `precedent`. *That* is the part that did not work before this tier.

Now open `backend/local_memory/precedents.jsonl`:

```json
{"ref": {"vendor": "LSEG", "product_id": "LSEG-CARBON-029", "name": "Carbon Data", ...},
 "node": {"iri": "jpmorgan:data:cdao:subdomain:pricing", "label": "Pricing"},
 "decision": "override", "decided_by": "demo@local", "confidence": 1.0,
 "provisional": false, "decided_at_ms": 1785865075842,
 "decided_at": "2026-08-04T17:37:55Z"}
```

(That is a real line, copied from a run, not an illustration.)

**That file is the memory.** One line per decision, who made it and when.
Delete the file and the system forgets — *from the next restart*; the running
process still holds what it already loaded. Vendor case matters: `LSEG` works,
`lseg` returns `status: out_of_scope`.

### 2e. One line in `.gitignore`

The journal holds product descriptions, reviewer identity and source audit
ids. It is local operational state, not source:

```
backend/local_memory/
```

---

## TIER 3 — the database pages  (28 lines, needs Docker)

Four route groups need PostgreSQL: **Providers, Datasets, Admin and the
Ingestion console** (`backend/routes/{providers,datasets,admin,ingest}.py`).
Matching, Catalogue and Matching Test do not. If Docker is blocked on your
machine, **skip this tier entirely** — everything else still runs, and those
pages simply show an error.

### 3a. New file: `docker-compose.yml` (repo root)

```yaml
services:
  postgres:
    image: postgres:16
    container_name: scudo-postgres
    environment:
      POSTGRES_USER: scudo
      POSTGRES_PASSWORD: scudo_local_dev
      POSTGRES_DB: scudo_console
    ports:
      - "5432:5432"
    volumes:
      - ./backend/init_db.sql:/docker-entrypoint-initdb.d/01-init.sql:ro
      - scudo_pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U scudo -d scudo_console"]
      interval: 5s
      timeout: 5s
      retries: 10

volumes:
  scudo_pgdata:
```

The user / database / port match `backend/db.py`'s defaults exactly, so no
other configuration is needed — **except the password**:

```bash
docker compose up -d
export CONSOLE_DB_PASSWORD=scudo_local_dev    # Windows: set CONSOLE_DB_PASSWORD=...
python start_local.py
```

Three things that will otherwise cost you an hour:

- **`CONSOLE_DB_PASSWORD` is mandatory.** `db.py` defaults it to an empty
  string for localhost, but the Postgres image demands a real password, and
  the failure does not tell you that.
- **`init_db.sql` runs on FIRST START ONLY** — Postgres only executes
  `/docker-entrypoint-initdb.d/*` against an empty data directory. Changed the
  schema? `docker compose down -v` (this erases the data) and start again.
- **`init_db.sql` begins with `DROP TABLE`** for `tp_provider`, `tp_dataset`
  and `tp_dataset_col`. Running it by hand against a populated database
  destroys those three tables.

It seeds an admin user: `admin` / `Admin1234`.

> **Honesty note:** Docker would not start on my machine, so this compose file
> is reviewed against `db.py` and `init_db.sql` by hand — it is **not** a
> config I was able to boot and prove. Everything else in this document was
> executed and verified. If it fails, the likely culprit is the password.

### 3b. Optional: one stale line that will confuse the next person

`backend/tests/test_providers.py:6` still says the tests require **MySQL**.
They do not — that line predates the PostgreSQL port and is the only place in
the Python code where the word appears. Anyone who greps for "mysql" will find
it and reasonably conclude MySQL is still wired in. Replace line 6:

```python
Requires PostgreSQL running locally (the `console` and `ingestion` schemas).
JPMC-LOCAL: this line used to say MySQL. It was stale -- the console DB was
ported to Aurora PostgreSQL (see db.py, psycopg v3) and no MySQL driver
remains anywhere in the Python code. `docker compose up -d` provides it.
```

It is a comment. It changes nothing. It is here because "there is no MySQL"
is much easier to believe when grep agrees with it.

### CHECK — Tier 3

Open the Providers page. It should list providers instead of
"Failed to load providers".

---

## TIER 4 — tests and documentation  (4 lines, optional)

### 4a. `backend/scudo/tests/test_local_file_store.py`

**This file is not reproduced here** — it is 311 lines, and unlike everything
above, nothing stops working if you skip it. Type it only if you intend to
*change* `local_file_store.py`; if you just need the system running, the
Tier 2 CHECK already proved the same thing by hand.

If you *do* write your own version of it, start the file with
`os.environ.setdefault("STORE_BACKEND", "memory")` and
`os.environ.setdefault("FRAME_SOURCE", "mock")` **before** any
`scudo_mapping_mcp` import — every other test module in that directory does
the same, and for the same reason.

`settings` is a frozen dataclass built once at the first import of
`config.py`, and `STORE_BACKEND` defaults there to `falkordb`. Tests that
construct `LocalFileStore` directly never need the variable themselves — but
importing such a module first freezes the setting for the whole pytest
process, and a *later* test that exercises the Flask app then tries to reach
Redis on `:6379` and fails with a baffling "Working outside of request
context". The damage lands on someone else's test, which is why it is worth
knowing about in advance.

Fifteen tests: the precedent survives a new store instance, the journal is one
readable line per decision, replay does not duplicate the journal, a reject
survives as a negative, the single-positive-precedent invariant holds across a
restart, a corrupt line does not cost the other decisions, timestamps are real
wall-clock and are not re-dated on restart, non-ASCII vendor names stay
readable, a **torn** (crash-truncated) last line does not swallow the next
decision, a **failed journal write leaves no live precedent**, concurrent
writes leave memory and journal agreeing, a missing file is a clean empty
start, a **real** directory-fsync failure (EIO) is not swallowed, an
**unsupported** one (EINVAL, as network-mounted VDI home directories answer)
is tolerated, and an unreadable journal tail fails rather than risking a fused
record.

Six of those pay for themselves — the torn line, the failed write, the
concurrent writes, and the three fsync/tail cases. They are the tests for the
durability bugs the reviews found. Each was confirmed to fail when its fix is
removed, which is the only evidence that a test is testing anything. That
check earned its keep: the tail test *passed* against the broken code on its
first draft, because it patched the method it was meant to be testing. Only
the removal run exposed it.

If you do skip it, run the rest of the suite anyway — it tells you whether
what you typed broke something that already worked. **First** add these two
lines to the end of `backend/requirements-local.txt` — this is the whole of
Tier 4's typing:

```
pytest>=8.0
openai>=1.0
```

Neither is needed to *run* the app, and `requirements.txt` never listed
either. `pytest` is the runner itself. `openai` is stranger: it is never
called, but `test_agent_provider.py` does `@patch("openai.AzureOpenAI")`, and
`@patch` has to import the real module before it can replace it — so three
tests **error** without it while making no network request. Then:

```bash
pip install -r backend/requirements-local.txt
python -m pytest backend/scudo/tests/ -q
```

Typing in **only what this document tells you to**, expect **258 passed, 2
failed** — a pristine clone gives the same 258/2, because nothing in Tiers 1–3
adds or removes a test. Write your own `test_local_file_store.py` on top and,
if you write all fifteen tests listed above, it becomes **273 passed, 2
failed**.

(My working copy reports 276/2. The extra three are tests I wrote for the
step-1e provider work — `test_agent_provider.py`'s `scripted` cases — which
this document does not ask you to type, because the CHECK blocks already prove
the same behaviour by hand and they are 50 more lines. If you see 258 or 273
rather than 276, nothing is wrong.)

The two failures are in `test_provenance.py` and **pre-date this work** —
verified by extracting a pristine copy of the original code and re-running:
the same two fail there. Do not go hunting them.

All these counts assume `pytest` **and** `openai` are installed. Without `openai`
you will see 3 extra errors that are not your fault; without `pytest` the
command does not run at all. That gap was caught by the independent review —
the earlier version of this document quoted the counts without saying how to
get a machine that can produce them.

### 4b. `docs/LOCAL_RUN.md`

The explainer: what SCUDO does, where the agent is (and what it is not), how
the learning loop works, how to see it. Written for someone who has never
built an agent system. It is documentation — it changes no behaviour, so type
it last or not at all.

---

## TIER 5 — comment out FalkorDB and Neptune  (20 lines, optional)

**Read this first.** This tier changes **nothing** about whether the app runs.
Tiers 1–4 already work with both packages uninstalled — proven by making their
imports raise `ModuleNotFoundError` and matching a product anyway. What this
tier buys you is *legibility*: after it, someone reading `factory.py` can see
at a glance that the two external graph databases are switched off, instead of
having to reason about lazy imports.

**Do not carry these lines back to the shared repo.** Five AWS deploy configs
set `STORE_BACKEND=falkordb`; both live accounts break if this reaches them.
It is safe here because your copy is typed in by hand and never merges back.

### 5a. `backend/scudo_mapping_mcp/store/factory.py`

Find `if backend == "falkordb":` inside `get_store()` — it is the first `if`
in that function, and the only line in the file that matches. (Do not go by
line number: it sits at line 20 in a fresh clone but moves down if you did
Tier 2, which inserts a branch above it.) Comment out that branch and the
Neptune one below it, and add the guard so the failure is a clear message
instead of a confusing one:

```python
    # JPMC-LOCAL: FalkorDB and Neptune commented out for local running. They
    # were never reachable here (STORE_BACKEND is 'memory' or 'local_file'),
    # so this changes no behaviour -- it just makes "switched off" visible.
    # RESTORE BOTH BLOCKS BEFORE ANY AWS DEPLOY: five deploy configs set
    # STORE_BACKEND=falkordb and will hit the ValueError below without them.
    # if backend == "falkordb":
    #     from .falkordb_store import FalkorDBStore
    #     return FalkorDBStore(url=settings.falkordb_url, graph_name=settings.graph_name)
    # if backend == "neptune":
    #     from .neptune_store import NeptuneStore
    #     return NeptuneStore(endpoint=settings.neptune_endpoint, graph_name=settings.graph_name)
    if backend in ("falkordb", "neptune"):
        raise ValueError(
            f"STORE_BACKEND='{backend}' is commented out for local running. "
            f"Use 'memory' or 'local_file'; see factory.py to restore it."
        )
```

Leave `store/falkordb_store.py` and `store/neptune_store.py` **on disk**. The
first one is not optional: the default scoring path imports `_jaro_winkler`
from it (`memory_store.py:35`, `opus_dense.py:149`). Delete it and matching
stops working entirely — which is exactly the trap this document exists to
keep you out of.

### CHECK — Tier 5

```bash
python start_local.py --backend
```

Then, in another terminal, the same match as Tier 2 — it must be unchanged:

```bash
curl -s -X POST http://127.0.0.1:5000/api/mapping/map \
  -H 'Content-Type: application/json' \
  -d '{"vendor":"LSEG","product_id":"LSEG-CARBON-029","name":"Carbon Data"}'
```

Still `0.6623`, still Market Data. If anything changed, you commented out a
line you should not have.

---

## What I verified, and what I did not

Verified by running it:

- 401→200 on `/api/catalogue/products` — the core diagnosis
- The full match works with `falkordb`, `requests-aws4auth`, `boto3` and
  `botocore` all absent and no database running. Not assumed — I forced
  `import falkordb` and `import requests_aws4auth` to raise
  `ModuleNotFoundError`, booted the app, and matched a product: **Market Data
  at 0.6623**, the identical score to the run with both installed.
- The precedent loop **through the real HTTP routes**, using the exact payloads
  printed in the Tier 2 walkthrough: `/mapping/map` → 0.6623 `needs_review`
  (Market Data) → `/mapping/decision` override to Pricing → `/mapping/map` →
  **`overridden`, node Pricing, `rationale: precedent`, confidence 1.0** (the
  human's answer returned verbatim, not a re-score). The walkthrough previously
  printed `approve` with confidence 0.5294; that number matched none of the ten
  candidate scores on a live server (Market Data 0.6623, Reference Data 0.7121,
  Pricing 0.4560), which is how it was caught. The numbers above are captured
  from a real run.
- **That precedent survives a restart, across separate OS processes**, and the
  journal is not re-appended on replay (still 1 line)
- `/api/mapping/agent/describe` returns `default_provider: scripted` with
  `scripted` in the provider list, under `start_local.py`
- `get_agent(provider="scripted")` → `ScriptedMappingAgent`;
  `provider="bedrock"` → `BedrockMappingAgent` (so the dropdown cannot lie)
- `pip install -r requirements-local.txt` resolves (dry run, exit 0)
- `python start_local.py --backend` → `/` 200, `/api/catalogue/products` 200,
  `/api/providers` 500 (Postgres absent; handled, no crash)
- **276 tests pass** with Tier 4 typed in (261 without it); the 2 failures
  pre-exist — confirmed by extracting a pristine copy of the original code and
  re-running: the same two fail there
- Each of the six new durability tests was confirmed to **fail when its fix
  is removed** — they test the defect, not the implementation. This is not a
  formality: the tail-read test *passed* against the broken code on its first
  draft, because it monkeypatched the very method under test and so never
  exercised the real path. The removal run showed 1 failure where 2 were
  expected, which is the only reason it was caught. Rewritten to fail the
  underlying `Path.open("rb")` instead, removal now gives 2 failed / 13 passed
  and the restored fix gives 15 passed.
- The new test file was checked for **test-isolation damage**, not just for
  passing: running it alongside a Flask test in the same process originally
  broke *the other* test (frozen `STORE_BACKEND=falkordb` → a connection
  attempt to Redis on `:6379`, reported as "Working outside of request
  context"). Fixed with the two `os.environ.setdefault` lines described in 4a,
  and the fix confirmed load-bearing by removing it again: 1 failed / 24
  passed without, 25 passed with. The full-suite count is 276/2 either way,
  which is exactly why it was worth checking the pair as well as the whole
- **The tiers were tested for real independence**: I extracted a pristine copy
  of the repo at HEAD, typed in *only* Tier 1, and ran it — then added Tier 2
  on top and ran the whole learning loop including a restart. Both work. This
  found a genuine defect, described below.
- **The whole document was applied to a pristine tree by a script that reads
  the fences out of this file**, so what was tested is literally what is
  printed above — no hand-editing in between. All 13 find/replace anchors
  matched **exactly once**, including Tier 5's, which has to still match after
  Tier 2 has inserted a branch above it. The resulting tree: `/` 200,
  `/api/catalogue/products` 200, `agent/describe` → `default_provider:
  scripted` with `scripted` in the list, the four-step Tier 2 walkthrough
  reproducing `0.6623 needs_review Market Data` → `overridden 1.0 Pricing` →
  `precedent 1.0` → **the same after a full process restart**, one journal line
  and still one after replay, and the Tier 5 CHECK scoring `0.6623 Market Data`
  unchanged with both graph backends commented out. `pytest`: **258 passed, 2
  failed**, the same as pristine HEAD measured in the same run, and **273/2**
  once the Tier 4 test file is added — the three numbers this document quotes.
- **Tier 1 was re-run from a pristine tree after the final edits**, applying
  only the blocks in this document and nothing else. Every "find this / replace
  it with that" anchor in Tier 1 matched the original code **exactly once** —
  so the instructions cannot land in the wrong place or silently miss. The
  result: `/` 200, `/api/catalogue/products` 200, `agent/describe` →
  `default_provider: scripted`, and the Tier 2 CHECK payload scored
  **0.6623 needs_review, Market Data** — the number this document quotes. That
  run was deliberately done on a host with `SCUDO_AGENT_BACKEND=bedrock` and
  the store still on `memory`: the scripted agent returned
  `agent_backend: scripted` and never called AWS, which is the whole point of
  step 1e. It also ran on `PORT=5055`, so the Tier 1 port change is tested
  rather than merely written down.

**Not** verified:

- **The Docker Postgres path was never booted** — the daemon would not start
  on my machine (timed out twice). Statically reviewed against `db.py` and
  `init_db.sql` only.
- **Bedrock was not called.** No credentials here. The region/model-id gotcha
  comes from reading `agent.py:123`, not from a live call.
- **Nothing was exercised through the browser.** All verification was HTTP.
- Nothing was committed, pushed or deployed.

### What an independent review caught

I had Codex review the whole change set. It returned **Block**, and it was
right about two things I had asserted without checking through the UI:

1. **The "offline" demo called AWS.** The Provider dropdown defaulted to
   Bedrock and offered no local option, and an explicit provider overrides
   `SCUDO_AGENT_BACKEND`. Fixed in step 1e — this is a real defect that would
   have hit the engineer on day one.
2. **The learning-loop walkthrough described UI controls that do not exist.**
   `recordMappingDecision` has zero callers in `frontend/src`. The walkthrough
   is now `curl`-based and says plainly that the button is missing.

It also found three real durability bugs in the new store: a missing lock, a
crash-torn line silently eating the *next* decision, and a **write-ordering
bug** — memory was updated before the journal, so if the append failed (disk
full, read-only mount) the running process kept serving a decision that no
restart could recover. The reviewer sees "approved", the audit file has no
record of it, and nothing surfaces the disagreement until a restart silently
reverses it. Journalling first fails the recoverable way instead. All three
are fixed and tested, each verified to fail when its fix is removed.

A second review round found the tier table itself lying about the code: the
`scripted` provider was offered by `/agent/describe` but **rejected with a 400
by `/agent/run`**, and `get_agent(provider="scripted")` fell through to
`SCUDO_AGENT_BACKEND` — so on a Bedrock-configured host, picking "Local
scripted narrator (no AWS)" returned the Bedrock agent. Both fixed; three
tests now pin it, including one asserting an unknown provider is *still*
rejected, so the fix cannot decay into a pass-through.

**And one I found by rebuilding the tree from scratch, which is worth
recording because it is the failure this document exists to prevent.** The
"stop after any tier" promise was false: `start_local.py` set
`STORE_BACKEND=local_file`, but the store and the allow-list that accept that
name are both Tier 2. An engineer who typed Tier 1 and stopped got

```
ValueError: SCUDO_PERSIST_TARGET='local_file' not in ('falkordb', 'neptune', 'none', 'memory')
```

— the app dead on startup, on the tier whose entire job is to make it run.
Reading the code, this looked fine; it only surfaced by typing Tier 1 into a
clean copy of the repo and running it. Tier 1 now says `memory` and Tier 2
step 2d flips it. Both paths re-tested end to end from a pristine tree.

**A third round found four more, and the first is the worst thing in this
document's history.** It caught me *creating* a defect while fixing another
one. Adding `"scripted"` to the local Flask route was correct; I also added it
to the same allow-list in `backend/scudo/lambda_handler.py` "for parity", and
told you to do the same. But that file's `_get_agents_for_provider()` sends
every provider except `"azure"` to `_build_bedrock_agents()` — so a deployed
Lambda would have accepted `"scripted"`, called Bedrock, and reported the
offline narrator. A silent wrong answer where there had been an honest 400.
Reverted, with a comment in the file explaining why it must stay that way, and
the instruction above now says leave it alone.

Three more, all fixed:

- **No way out of a port clash.** `app.py` hard-coded port 5000, so if
  anything already held it the app died with `Address already in use` and no
  recourse without editing code. It now reads `PORT`, and `start_local.py`
  prints the port it actually used rather than assuming 5000.
- **The test command could not run as documented.** Neither `pytest` nor
  `openai` was in any requirements file, so the advertised suite either would
  not start or threw three errors. Both are now in
  `requirements-local.txt` — that is Tier 4's two lines.
- **First-write durability.** The journal append is `fsync`ed, but the
  *directory entry* for a newly created file was not, so a power cut could
  lose the whole file despite the bytes being synced. Now fsynced on creation
  only, skipped outright on Windows (which cannot open a directory handle at
  all).

A later round found two more in that same fsync, and both are fixed:

- **The directory fsync swallowed every error.** `except (OSError,
  AttributeError): pass` meant EIO and ENOSPC — the exact failures the fsync
  exists to detect — were ignored and memory was updated anyway. That is the
  same fail-open bug the write-ordering fix was written to prevent, one line
  further down. Real errors now propagate; only a fixed allow-list of
  "this filesystem does not do that" errnos (`EINVAL`/`EPERM`/`EACCES`/
  `ENOTSUP`/`EISDIR`) is tolerated, because corporate VDI home directories are
  frequently network mounts that answer `EINVAL` and failing their HITL
  decisions would break the feature for the people it is for.
- **The torn-line guard failed open.** `_ends_with_newline` returned `True` on
  any read error, so if the tail could not be read the guard went blind and
  the next record could fuse onto a half-written one — precisely what the
  guard exists to prevent. Only the empty-file case (a seek that cannot go to
  -1) still answers `True`; everything else raises before memory is touched.

It also flagged the tier line counts, correctly: they were estimates
(~150/~185/~50/~200) and were wrong. They are now measured by a script that
counts the lines inside each tier's code fences, and re-measured after the
last edit — 213/281/28/4/20.

Some findings I checked and rejected. `frontend/` is the correct UI (it is the
one in the reported screenshot) — the review argued Tier 1 launches "the
prohibited generic console" and that the real dashboard is the `/demo/` bundle,
but that bundle is a read-only visualisation, not the console with the
Providers and Matching Test pages your screenshot shows. There is no
`reports.py` requiring Postgres. And the claim that test counts were
unverified came from a sandbox that could not run pytest — the suite does run
here, 276/2 (which the same review then reproduced once it installed the two
packages, confirming the counts).

The review also asked why MySQL, FalkorDB and Neptune were not commented out,
given that was the brief. The answer is in the diagnosis above and I checked it
before writing it: MySQL is already gone from the Python code (ported in
`bf2f50c`, which is in what you pulled), and the other two are lazy imports
that five AWS deploy configs still depend on. Commenting out code that is not
running, to fix a problem caused by five missing environment variables, would
break both live AWS accounts and fix nothing. What was actually blocking you is
Tier 1.
