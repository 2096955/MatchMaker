# SCUDO Lambda + API Gateway Deployment

PoC scope: open HTTPS endpoint, shared-secret header, in-memory mocks for the
catalogue and Neptune, real Bedrock calls to Opus 4.8 in `us-east-1`.

## What gets created

- One Lambda function (`<stack>-orchestrator`, container image, 3 GB RAM, 90 s timeout)
- One API Gateway HTTP API with two routes:
  - `GET /health` — no auth, returns model/region pins
  - `POST /run` — requires `x-api-key` header, runs the full SCUDO loop
- A CloudWatch log group (`/aws/lambda/<stack>-orchestrator`, 14-day retention)
- An ECR repository (managed by SAM) for the container image

## Prerequisites (all on CloudShell — already there)

- `aws --version` → `2.x`
- `sam --version` → SAM CLI present
- `docker --version` → Docker available (it is in CloudShell)
- `aws sts get-caller-identity` → returns your assumed-role
- Bedrock model access for `us.anthropic.claude-opus-4-8` (you already verified this)

## One-time generate an API key

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Save the output. You'll paste it during `sam deploy --guided`.

## First deploy

From the `backend/scudo/` directory (or wherever this `template.yaml` lives):

```bash
sam build
sam deploy --guided --stack-name scudo-poc --capabilities CAPABILITY_IAM
```

You'll be prompted for:
- **Stack name** → `scudo-poc` (or whatever)
- **Region** → `us-east-1`
- **ApiKey** → paste the secret you generated above
- **BedrockModelId** → accept default (`us.anthropic.claude-opus-4-8`) or change
- **OntologySnapshot** → accept default
- **RubricVersion** → accept default
- **Confirm changes before deploy?** → `y`
- **Allow IAM role creation?** → `y`
- **Save arguments to samconfig.toml?** → `y`

Build takes ~2 minutes, deploy takes ~3–5 minutes (most of it ECR image push).

## Subsequent deploys

The first run wrote `samconfig.toml` — every subsequent change is just:

```bash
sam build && sam deploy
```

## Test the deployed endpoint

After the deploy finishes, SAM prints `ApiUrl`, `RunUrl`, `HealthUrl`. Try:

```bash
# Health check (no auth, ~50 ms)
curl https://<your-api-id>.execute-api.us-east-1.amazonaws.com/health

# Mapping run (with auth, ~30 s — Opus 4.8 isn't fast)
curl -X POST https://<your-api-id>.execute-api.us-east-1.amazonaws.com/run \
  -H "x-api-key: <your-api-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "vendor": "lseg",
    "vendor_product_ref": "LSEG-IBES-EST-001",
    "vendor_product": {
      "title": "I/B/E/S Estimates - Global Equities",
      "description": "Consensus analyst estimates across global equities.",
      "theme": "Investment Data",
      "asset_class": "Equities"
    },
    "has_precedent": false,
    "has_conflict": false,
    "ontology_gap": false,
    "candidates_term": "estimates"
  }'
```

You should see a JSON response with `mapping_object.outcome` ∈ `{published, hitl, retry}`.

## Share with third parties

The `RunUrl` works from anywhere on the internet — share that plus the
`x-api-key` value. Anyone with both can POST to the endpoint.

CORS is `*` so a browser-side fetch will work too. (If you stand up a
static frontend next, that's all wired.)

## Watch what's happening

```bash
# Tail the Lambda logs in real time
sam logs --stack-name scudo-poc --tail

# Or with the AWS CLI
aws logs tail /aws/lambda/scudo-poc-orchestrator --follow
```

## Tear down (when the PoC is over)

```bash
sam delete --stack-name scudo-poc
```

This removes the Lambda, the API Gateway, the IAM role, the log group, and
the ECR image. Bedrock charges stop when the Lambda stops being called —
nothing keeps running idle.

## Things you'd tighten for prod (not a PoC concern)

- Replace `Resource: '*'` on the Bedrock policy with the specific inference
  profile ARN
- Replace `API_KEY` env var with a Secrets Manager lookup
- Swap the shared-secret check for Cognito or AWS_IAM auth
- Add a WAF in front of the API Gateway for rate-limiting beyond the
  per-stage throttle (currently 5 rps / 5 burst)
- Switch to `Architectures: [arm64]` (Graviton) for ~20% cheaper compute
