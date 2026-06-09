#!/usr/bin/env bash
# SCUDO post-deploy smoke.
#
# Hits the four ALB-backed paths and dumps target-group health so we catch
# image-pull failures / health-check 5xx fast after a deploy.
#
# Usage:
#   ./scudo_post_deploy_smoke.sh                    # resolves ALB DNS from CFN
#   ./scudo_post_deploy_smoke.sh <alb-dns>          # explicit DNS
#
# Exit 0 if every probe returns a non-5xx code AND every target group
# reports >=1 healthy target. Exit 1 otherwise.

set -uo pipefail

REGION="${AWS_REGION:-eu-west-2}"
STACK="${STACK:-scudo-dev-deploy}"

if [ $# -ge 1 ] && [ -n "$1" ]; then
  ALB_DNS="$1"
else
  ALB_DNS=$(aws cloudformation describe-stacks \
    --stack-name "$STACK" --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`AlbDnsName`].OutputValue' \
    --output text 2>/dev/null)
fi

if [ -z "${ALB_DNS:-}" ] || [ "$ALB_DNS" = "None" ]; then
  echo "Could not resolve ALB DNS. Pass as arg or check stack '$STACK' outputs." >&2
  exit 2
fi

echo "ALB DNS : $ALB_DNS"
echo "Region  : $REGION"
echo "Stack   : $STACK"
echo

# ---------- HTTP probes ----------
echo "=== HTTP probes ==="
PATHS=(
  "/api/mapping/vendors"
  "/mcp/ingest"
  "/mcp/match"
  "/mcp/persist"
)

probe_ok=0
probe_fail=0
for p in "${PATHS[@]}"; do
  url="http://$ALB_DNS$p"
  code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "$url" 2>/dev/null || echo "000")
  # Accept 1xx-4xx as 'service is up and responding'. 5xx + 000 (no response) are fails.
  if [ "$code" != "000" ] && [ "$code" -lt 500 ] 2>/dev/null; then
    printf "  OK    %-40s  HTTP %s\n" "$url" "$code"
    probe_ok=$((probe_ok + 1))
  else
    printf "  FAIL  %-40s  HTTP %s\n" "$url" "$code"
    probe_fail=$((probe_fail + 1))
  fi
done
echo
echo "Probes: ${probe_ok} OK, ${probe_fail} FAIL"
echo

# ---------- Target-group health ----------
echo "=== Target-group health ==="
TG_NAMES=(
  scudo-dev-flask-tg
  scudo-dev-ingest-tg
  scudo-dev-match-tg
  scudo-dev-persist-tg
)

unhealthy_total=0
for tg_name in "${TG_NAMES[@]}"; do
  tg_arn=$(aws elbv2 describe-target-groups \
    --names "$tg_name" --region "$REGION" \
    --query 'TargetGroups[0].TargetGroupArn' \
    --output text 2>/dev/null)
  if [ -z "$tg_arn" ] || [ "$tg_arn" = "None" ]; then
    echo "  $tg_name : NOT FOUND"
    unhealthy_total=$((unhealthy_total + 1))
    continue
  fi

  # JSON output, parse states via python to keep this script colon-safe.
  states_json=$(aws elbv2 describe-target-health \
    --target-group-arn "$tg_arn" --region "$REGION" --output json 2>/dev/null)

  healthy=$(echo "$states_json" | python3 -c '
import json, sys
d = json.load(sys.stdin)
n = sum(1 for t in d.get("TargetHealthDescriptions", [])
        if t.get("TargetHealth", {}).get("State") == "healthy")
print(n)
' 2>/dev/null || echo 0)
  total=$(echo "$states_json" | python3 -c '
import json, sys
print(len(json.load(sys.stdin).get("TargetHealthDescriptions", [])))
' 2>/dev/null || echo 0)

  if [ "$healthy" -gt 0 ] 2>/dev/null; then
    printf "  OK    %-26s  healthy %s of %s\n" "$tg_name" "$healthy" "$total"
  else
    printf "  FAIL  %-26s  healthy %s of %s\n" "$tg_name" "$healthy" "$total"
    unhealthy_total=$((unhealthy_total + 1))

    # Dump per-target reasons so operators can see if it is a pull / health-check fail
    echo "$states_json" | python3 -c '
import json, sys
d = json.load(sys.stdin)
for t in d.get("TargetHealthDescriptions", []):
    target = t.get("Target", {}).get("Id", "?")
    th = t.get("TargetHealth", {})
    state = th.get("State", "?")
    reason = th.get("Reason", "")
    desc = th.get("Description", "")
    print(f"        target={target}  state={state}  reason={reason}  desc={desc[:160]}")
' 2>/dev/null
  fi
done
echo

# ---------- Verdict ----------
echo "=== Verdict ==="
if [ "$probe_fail" -eq 0 ] && [ "$unhealthy_total" -eq 0 ]; then
  echo "GREEN. Every probe returned non-5xx and every target group has >=1 healthy target."
  exit 0
fi
echo "RED. ${probe_fail} probe failures, ${unhealthy_total} target groups without healthy targets."
echo "Next steps:"
echo "  - Check service events: aws ecs describe-services --cluster scudo-dev --services scudo-dev-flask scudo-dev-ingestion scudo-dev-match-verify scudo-dev-persistence scudo-dev-falkor --region $REGION --query 'services[].{Name:serviceName,Running:runningCount,Desired:desiredCount,Events:events[0]}' --output table"
echo "  - Log streams: /aws/ecs/scudo-dev-backend in CloudWatch Logs"
exit 1
