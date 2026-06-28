#!/usr/bin/env bash
# Publish the vendored matching dashboard to the scudo-poc S3 + CloudFront.
#
# RUN FROM AWS CloudShell (us-east-1, account 954976331678) — the local laptop
# has no AWS credentials. Assumes `dashboard-dist/` has already been produced by
# infra/build_dashboard_dist.sh and is present in the checkout.
#
#   bash infra/deploy_dashboard_cloudshell.sh
#
# The dashboard is served at  https://<cloudfront-domain>/demo/
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dashboard-dist"

if [ ! -f "$DIST/index.html" ]; then
  echo "ERROR: $DIST/index.html missing. Build + vendor it first:" >&2
  echo "  bash infra/build_dashboard_dist.sh   # on a machine with node+pnpm" >&2
  exit 1
fi

# Resolve the scudo-poc frontend bucket + distribution from the deployed stack.
BUCKET="$(aws cloudformation list-exports \
  --query "Exports[?Name=='scudo-poc-frontend-bucket-name'].Value" --output text)"
DIST_ID="$(aws cloudformation list-exports \
  --query "Exports[?Name=='scudo-poc-frontend-distribution-id'].Value" --output text)"

if [ -z "$BUCKET" ] || [ -z "$DIST_ID" ]; then
  echo "ERROR: could not resolve scudo-poc frontend bucket/distribution exports." >&2
  echo "Is the scudo-poc-frontend stack deployed in this account/region?" >&2
  exit 1
fi

echo "[deploy] bucket=$BUCKET distribution=$DIST_ID"
echo "[deploy] syncing dashboard-dist/ → s3://$BUCKET/demo/"
aws s3 sync "$DIST/" "s3://$BUCKET/demo/" --delete

echo "[deploy] invalidating CloudFront /demo/*"
aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/demo/*" >/dev/null

DOMAIN="$(aws cloudfront get-distribution --id "$DIST_ID" \
  --query "Distribution.DomainName" --output text 2>/dev/null || true)"
echo "[deploy] done. Matching dashboard: https://${DOMAIN:-<cloudfront-domain>}/demo/"
