#!/usr/bin/env bash
# Refresh vendored matching dashboard from Capone MatchMaker/dashboard-dist.
# Prefer rebuilding Capone first: bash infra/build_dashboard_dist.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${1:-$ROOT/../dashboard-dist}"
DST="$ROOT/dashboard-dist"
if [[ ! -d "$SRC" ]]; then
  echo "source dashboard-dist not found: $SRC" >&2
  exit 1
fi
rm -rf "$DST"
cp -R "$SRC" "$DST"
echo "Synced $SRC → $DST"
ls -la "$DST" | head
