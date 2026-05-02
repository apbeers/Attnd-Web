# OpenAI → AWS Bedrock Migration

Replaces the `openai` Python package with `boto3` + AWS Bedrock so the event
scraper runs entirely within AWS infrastructure using IAM-based auth.
No API key is stored anywhere — the ECS task role grants access automatically.

---

## What Changes

| Area | Before | After |
|------|--------|-------|
| Python dependency | `openai>=2.8.0` | `boto3>=1.38` |
| Auth | `OPENAI_API_KEY` env var / secret | ECS task IAM role |
| Model | `gpt-4o-mini` | `claude-3-5-haiku` (via Bedrock) |
| Structured output | `response_format` JSON schema | Bedrock tool use (`toolChoice: forced`) |
| GitHub secret | `DEV_OPENAI_API_KEY`, `PROD_OPENAI_API_KEY` | None |
| Terraform variable | `openai_api_key` | None |
| IAM | No Bedrock access | `bedrock:InvokeModel` on `ecs_task` role |

---

## Why Bedrock

- No external API key to rotate or leak — IAM handles everything
- Stays inside the AWS VPC/trust boundary; the request never leaves AWS
- Billed on the same AWS account as the rest of the infra
- Claude 3.5 Haiku is faster and cheaper than `gpt-4o-mini` at this call volume

---

## Model

```
us.anthropic.claude-3-5-haiku-20241022-v1:0
```

This is the cross-region inference profile for Claude 3.5 Haiku, which provides
automatic failover across AWS regions. Model access must be enabled before first use.

**Enable model access (one-time, per AWS account):**
1. AWS Console → Bedrock → Model access
2. Select `Claude 3.5 Haiku` under Anthropic
3. Click Request access (it's instant for Haiku)

---

## Migration Steps

### Step 1 — Apply Terraform

The updated `infra/iam.tf` adds a `bedrock:InvokeModel` policy to the ECS task
role. The `infra/secrets.tf` and `infra/variables.tf` have had `openai_api_key`
removed. Just run the deploy pipeline — Terraform handles the rest.

### Step 2 — Swap the Python dependency

In `web/pyproject.toml` (or `pyproject.toml` before the monorepo move):

```toml
# Remove:
"openai>=2.8.0,<3.0.0"

# Add:
"boto3>=1.38.0"
```

Then:
```bash
uv lock
```

### Step 3 — Replace scrape/service.py

```bash
cp infra/app-changes/scrape_service_bedrock.py scrape/service.py
```

The new file is a drop-in replacement. The public `scrape_event()` signature
is unchanged. Only `_extract()` changes internally:
- `from openai import OpenAI` → `import boto3`
- `client.chat.completions.create(...)` → `client.converse(...)` with tool use
- No API key parameter — `boto3` picks up credentials from the IAM role
  (or `AWS_PROFILE` / environment variables locally)

### Step 4 — Remove the GitHub secrets (after deploy is stable)

Once the first successful deploy with Bedrock is confirmed:
1. Go to Settings → Secrets and variables → Actions
2. Delete `DEV_OPENAI_API_KEY` and `PROD_OPENAI_API_KEY`

### Step 5 — Local development

Locally, `boto3` resolves credentials in this order:
1. `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` env vars
2. `~/.aws/credentials` (via `AWS_PROFILE` or default profile)
3. Instance/container metadata (on ECS, EC2, etc.)

The easiest local setup:
```bash
aws configure sso   # or use an IAM user with bedrock:InvokeModel
export AWS_REGION=us-east-1
uv run python manage.py runserver
```

Make sure model access for `Claude 3.5 Haiku` is enabled in the account your
local credentials point to.

---

## Structured Output Approach

The OpenAI implementation used `response_format` with a strict JSON schema.
Bedrock's equivalent is **tool use with forced tool choice**:

```python
toolConfig={
    "tools": [{"toolSpec": {"name": "event_extraction", "inputSchema": {"json": EVENT_SCHEMA}}}],
    "toolChoice": {"tool": {"name": "event_extraction"}}  # forces the model to always call this tool
}
```

With `toolChoice` forced, Claude always responds with a tool call whose `input`
matches the schema exactly — equivalent to OpenAI's `strict: true` JSON schema mode.
The response is already parsed as a Python dict; no `json.loads()` needed.
