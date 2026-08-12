# Running the console UI with Node blocked

Your diagnosis is right and the analysis is good. One correction and one option
you did not have.

**Correction:** the two options you listed are not the only ones. There is a
third that needs neither a policy exception nor an outside build — **Flask can
serve the console itself.** That path is now wired and verified.

---

## The fix: serve the built bundle from Flask

Same pattern the repo already uses for `dashboard-dist/` at `/demo/`. No Node,
no Vite, no esbuild — only Flask, which is already running.

```bash
export SCUDO_SERVE_FRONTEND_DIST=1
python start_local.py
```

Open **<http://localhost:5000/app/>**

Verified end to end here:

```
/app/                        200   (index.html)
/app/assets/index-*.js       200   (303,730 bytes)
/app/providers               200   (SPA deep link → index.html)
/api/providers               200   (same origin)
```

Serving same-origin also removes the dev-server proxy from the picture
entirely: the bundle's relative `/api/*` fetches land on the same Flask
process, so **`VITE_API_PROXY` becomes irrelevant** and one of your remaining
requirements disappears.

### What changed

| File | Change |
|---|---|
| `backend/app.py` | `/app/` and `/app/<path>` routes behind `SCUDO_SERVE_FRONTEND_DIST`, with SPA fallback so refreshing a deep link works |
| `.gitignore` | `frontend/dist/` re-included so the bundle can be committed — same precedent as the tracked `dashboard-dist/` |
| `frontend/dist/` | rebuilt with `--base=/app/` |

Unset by default. Nothing changes for anyone running Vite normally.

### Rebuilding it (on a machine where Node works)

```bash
cd frontend && npx vite build --base=/app/
```

**The `--base` is load-bearing.** Without it assets emit as `/assets/*` and 404
under `/app/`. If the UI loads as an unstyled blank page, that is the cause.

---

## Your remaining items

**Backend packages — `pydantic` and `mcp` are already declared** in
`backend/requirements.txt` (lines 10 and 14). If they are missing, the install
did not complete rather than the file being wrong:

```bash
pip install -r backend/requirements.txt
```

**Port 5000** — no longer needed for the UI if you use `/app/`, since the API
and UI share the origin. If 5000 itself is taken (macOS AirPlay; some Windows
agents):

```bash
PORT=5055 SCUDO_SERVE_FRONTEND_DIST=1 python start_local.py
# then http://localhost:5055/app/
```

**`MatchingPanel.jsx` — please check this before committing it.** This tree has
no such file. It has:

```
frontend/src/pages/matching/MatchingTest.jsx      ← agent reasoning work is HERE
frontend/src/pages/ingestion/IngestionConsole.jsx
```

and `App.jsx:17` imports `MatchingTest` and routes it at `/matching-test`.

So `MatchingPanel.jsx` is a Citrix-side file that does not exist upstream. That
matters because **the readable agent-reasoning rendering, and the refusal-message
interceptor, went into `MatchingTest.jsx` and `api/index.js`** — if your panel
is a separate component, it will not have inherited either.

Worth resolving which is intended before adding it to source control. If
`MatchingPanel` is a wrapper that renders `MatchingTest`, nothing is lost. If it
is a parallel implementation, it needs the same two fixes or it will show
truncated JSON and bare `frame_not_found` messages.

**Directory casing** — `frontend/src/pages/ingestion/` is already lowercase
here, so normalising to that matches upstream.

---

## What to check once it loads

The bundle you are serving contains the frontend work described in
[`CITRIX_CHECK_FRONTEND.md`](CITRIX_CHECK_FRONTEND.md). Briefly:

1. **Upload** → stage list ticks `received → parse → validate → sink`.
2. **Run match** → result card with a confidence and a CDAO node label.
3. **Agent reasoning** → a card (`data-testid="agent-reasoning"`) showing
   `thinking / calls / returns` lines. If you see raw JSON truncated
   mid-sentence, you are running an older bundle.
4. **Run match with nothing uploaded** → the banner should say *ingest a file
   first*, not just `frame_not_found`.

---

## Honest limits

- **I have not opened this in a browser.** The routes, assets, SPA fallback and
  API were verified through the Flask test client. Layout and long-line
  wrapping in the reasoning card are unverified — that is the main thing worth
  your eyes.
- **The vendored bundle goes stale.** It is a build artefact in source control;
  if someone edits `frontend/src/` without rebuilding, the served UI silently
  lags. `dashboard-dist/` has the same property. Rebuild after frontend changes.
- **This does not fix `npm run dev`.** Hot reload still needs esbuild. This
  makes the app *runnable*, not *developable*, on that desktop — a policy
  exception is still the better long-term answer if anyone needs to iterate on
  the UI there.
