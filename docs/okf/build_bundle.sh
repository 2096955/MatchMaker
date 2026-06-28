#!/usr/bin/env bash
# Reproducible build of the SCUDO OKF knowledge bundle.
# Usage:  OKF_BIN=/path/to/okf ./docs/okf/build_bundle.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

OKF_BIN="${OKF_BIN:-/Users/anthonylui/OpenKnowledgeFormat/.venv/bin/okf}"
OKF_PY="$(dirname "$OKF_BIN")/python"

if [ ! -x "$OKF_BIN" ]; then
  echo "ERROR: okf CLI not found at: $OKF_BIN" >&2
  echo "  Install: cd /Users/anthonylui/OpenKnowledgeFormat && python3 -m venv .venv && .venv/bin/pip install -e ." >&2
  exit 1
fi
"$OKF_BIN" --help >/dev/null 2>&1 || { echo "ERROR: '$OKF_BIN --help' failed" >&2; exit 1; }
"$OKF_PY" -c "import okf_toolkit, yaml" 2>/dev/null || { echo "ERROR: OKF venv python lacks okf_toolkit/yaml: $OKF_PY" >&2; exit 1; }

STAGE_SRC="$REPO_ROOT/build/okf-src"
BUNDLE_OUT="$REPO_ROOT/docs/okf/scudo"
MANIFEST="$REPO_ROOT/docs/okf/build/manifest.yaml"

echo "OKF_BIN=$OKF_BIN"
echo "OKF_PY=$OKF_PY"

echo "=== stage ==="
"$OKF_PY" "$REPO_ROOT/docs/okf/build/stage.py" "$REPO_ROOT" docs/okf/build/manifest.yaml "$STAGE_SRC"

echo "=== clean output ==="
rm -rf "$BUNDLE_OUT"

echo "=== convert ==="
"$OKF_BIN" convert "$STAGE_SRC" --out "$BUNDLE_OUT" --default-type Document

echo "=== validate (gate: zero errors) ==="
"$OKF_BIN" validate "$BUNDLE_OUT"

echo "=== validate --strict (advisory) ==="
"$OKF_BIN" validate "$BUNDLE_OUT" --strict || echo "(advisory warnings above — not a gate)"

echo "=== evals (gate: 01,02,06,07 must pass) ==="
"$OKF_BIN" evals run "$BUNDLE_OUT"

echo "=== visualize ==="
"$OKF_BIN" visualize "$BUNDLE_OUT"

echo "=== BUILD COMPLETE → $BUNDLE_OUT ==="
