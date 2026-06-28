#!/usr/bin/env bash
# Build the SCUDO matching dashboard and vendor its dist into MatchMaker.
#
# The dashboard lives in a separate pnpm-workspace repo. This builds it with
# `pnpm build:matching` (matching mode, base:"/demo/") and copies the output to
# MatchMaker/dashboard-dist/, which the CodeBuild buildspec + the CloudShell
# deploy script publish to S3.
#
# Run locally (needs node + pnpm + the understand-anything repo checked out):
#   bash infra/build_dashboard_dist.sh
#
# Override the dashboard location with DASHBOARD_DIR=/path/to/packages/dashboard.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DASHBOARD_DIR="${DASHBOARD_DIR:-$ROOT/../../Understand-Anything/understand-anything-plugin/packages/dashboard}"
OUT="$ROOT/dashboard-dist"

if [ ! -d "$DASHBOARD_DIR" ]; then
  echo "ERROR: dashboard not found at $DASHBOARD_DIR" >&2
  echo "Set DASHBOARD_DIR to the understand-anything packages/dashboard path." >&2
  exit 1
fi

echo "[build_dashboard_dist] syncing matching graph fixture → dashboard public/"
bash "$ROOT/backend/scudo/scripts/sync_matching_graph_to_dashboard.sh"

echo "[build_dashboard_dist] building dashboard (pnpm build:matching)"
( cd "$DASHBOARD_DIR" && pnpm install --frozen-lockfile && pnpm build:matching )

echo "[build_dashboard_dist] vendoring dist → $OUT"
rm -rf "$OUT"
cp -R "$DASHBOARD_DIR/dist" "$OUT"

echo "[build_dashboard_dist] done. $(find "$OUT" -type f | wc -l | tr -d ' ') files in dashboard-dist/"
echo "  index.html references /demo/ assets — publish under the demo/ S3 prefix."
