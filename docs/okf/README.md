# OKF Knowledge Bundles

See [`SUMMARY.md`](SUMMARY.md) for implementation status, verification results, and Gate B notes.

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
