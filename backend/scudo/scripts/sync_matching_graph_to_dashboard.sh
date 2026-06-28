#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
DASH="${DASHBOARD_DIR:-$ROOT/../../Understand-Anything/understand-anything-plugin/packages/dashboard}"
cd "$ROOT/backend"
python -m scudo.build_matching_graph
cp "$ROOT/backend/scudo/fixtures/matching-graph.json" "$DASH/public/matching-graph.json"
cp "$ROOT/backend/scudo/fixtures/meta.json" "$DASH/public/meta.json"
echo "Synced to $DASH/public/"
