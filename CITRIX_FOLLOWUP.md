# Follow-up on the Citrix apply

Four points on the state you reached. Everything below was re-run against the
repo just now — where I state a result, I ran it.

**Short version: the "remaining runtime note" is not a defect.** The 404 was a
wrong URL. And the Aurora dependency you hit is exactly what the SQLite
fallback removes — see §3, which is now the highest-value next step.

---

## 1. The 404s — wrong endpoint, server was fine

The health endpoint is **`/healthz`**, not `/health`. Measured:

```
200  /          <- root DOES serve; the reported 404 here is not reproducible
404  /health    <- does not exist, never has
200  /healthz   <- this is the one
200  /readyz    <- after warm-up; see below
```

So no fix is needed. If `/` 404'd for you it was almost certainly probed before
Flask finished binding, or against a different port.

### `/readyz` returns 503 on a cold start — also expected

`/readyz` reports whether the CDAO taxonomy has been seeded. Seeding is
triggered lazily by a `before_request` hook, so on a freshly-started process
nothing has seeded yet and the probe honestly says "not ready":

```
cold start                     -> 503
after any /api/* request       -> 200 {"ready":true}
```

Confirmed by running exactly that sequence. Treat 503-then-200 as correct
behaviour, not a failure. If you want it green immediately, hit
`/api/mapping/vendors` once as a warm-up.

---

## 2. `falcordb` vs `falkordb` — check the spelling before recording it as fact

The package is **`falkordb`** (k, not c). This repo's `backend/requirements.txt`
line 11 reads:

```
falkordb>=1.0.8
```

So `falcordb==1.0.8` is either a Citrix-side divergence or a transcription slip.
Worth resolving before it lands in a JPMC-facing note as a fact — if the file
there really says `falcordb`, that line was never installable, which would
explain "unavailable" for a reason other than the one recorded.

**Commenting it out is correct either way, but for a better reason than
"unavailable":** the `falkordb` pip package is genuinely not needed for a local
run. Its import lives inside a method, so it is only touched when
`STORE_BACKEND=falkordb`. Verified end-to-end with the package absent.

Do **not** delete `backend/scudo_mapping_mcp/store/falkordb_store.py` — the
default scoring path imports `_jaro_winkler` from that file, so the module must
stay on disk even though the database is unused. That is documented at
`store/factory.py:20-32`.

### Related: the FalkorDB default was the real reason it kept being asked for

`STORE_BACKEND` used to default to `"falkordb"`, so any entry point that set no
environment tried to open a connection on :6379. Now fixed in this repo:

- `config.py` defaults to `local_file` (safe — every deployed path sets the var
  explicitly: `Dockerfile:51`, `scudo-dev-deploy.yaml` ×4, `scudo-poc-app.yaml`,
  `template.yaml`)
- `start_all.sh` delegates to `start_local.py` instead of running `app.py` bare

With the whole environment stripped, `get_store()` now returns
`LocalFileStore`. Worth carrying across if the Citrix copy predates it.

---

## 3. You do not need Aurora to exercise the DB pages

> *"database-backed behaviour still needs an available Aurora instance and
> schema"*

This is the wall the SQLite fallback was built to remove. It is in this repo at
`backend/db_sqlite_fallback.py` (276 lines, **standard library only** — no pip
install, no daemon, no container) and does not appear to have made it into your
apply.

Turn it on with one environment variable:

```bash
export CONSOLE_DB_BACKEND=sqlite
```

Cold-start result, no PostgreSQL running, verified just now:

```
200  /healthz
200  /api/providers
200  /api/datasets
201  POST /api/providers      <- INSERT ... RETURNING round-trips
```

Data persists at `backend/.local/console.sqlite3` across restarts.

**No route code changes.** The hook is in `db.py`'s two connection functions and
is read at **call** time, so unset the variable and the psycopg/Aurora path is
byte-for-byte what it was. Your `[ALUI]` `db.py` work is unaffected — this sits
alongside it.

Why it was cheap: the route SQL is nearly dialect-free. Only `%s`→`?`,
`SERIAL`→`INTEGER`, `TIMESTAMPTZ`/`JSONB`→`TEXT`. **`RETURNING` and
`ON CONFLICT` are native in SQLite 3.35+** and pass through untouched.

Known limits, stated not hidden: no schemas (`console.`/`ingestion.` collapse
into one namespace); the PL/pgSQL `updated_at` trigger is skipped so that column
will not self-update; single writer. Sized for one person running a demo.

---

## 4. Port 5000 on macOS

`run_local.py` does set the auth env, so no 401s — good. But :5000 is where
macOS AirPlay Receiver squats. If you see connection oddities that look like the
server is up but unreachable:

```bash
PORT=5055 VITE_API_PROXY=http://localhost:5055 python start_local.py
```

Open the **UI on :3000**, not the backend port.

---

## Suggested next step

Apply §3 — it is one environment variable plus one file copy, and it unblocks
the only thing your note lists as still blocked. Full instructions with the
`db.py` hook are Tier 3 of `JPMC_PORT_TYPE_IN.md`; the *why* is §2 of
`JPMC_LOCAL_RUN_HANDOVER.md`.

## One thing worth not doing

Do not spend time making `/health` exist. Point the probe at `/healthz`. Adding
an alias is a change to a deployed contract to accommodate a typo in a test
command, and the 503-then-200 on `/readyz` is the probe working correctly.
