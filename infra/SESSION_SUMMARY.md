# Session Summary — Monorepo Migration Handoff

> **For the next Claude Code session**: This document captures everything planned and completed so far. Start here before touching any code or infra.

---

## Repos Involved

| Repo | Default Branch | Purpose |
|------|---------------|----------|
| `apbeers/Attnd-Web` | `main` | Django 6.0 backend + REST API + Terraform infra |
| `apbeers/Attnd-iOS` | `develop` | SwiftUI iOS 17+ app |
| `apbeers/Attnd-Android` | `develop` | Kotlin/Jetpack Compose Android app |
| `apbeers/Attnd` | `main` (empty) | **NEW** — destination monorepo |

All three original repos have `CLAUDE.md` files on their default branches documenting architecture, file structure, auth, and dev setup.

---

## What Has Been Completed

### 1. CLAUDE.md Files
All three repos have CLAUDE.md files committed to their default branches:
- `apbeers/Attnd-Web` `main` — Django app layout, all 5 models, dual auth, full API endpoint table, attestation module, deep linking, env vars
- `apbeers/Attnd-iOS` `develop` — Swift source files, app flow, auth priority (attestation JWT → static key), Universal Links, UserDefaults, demo banner requirement
- `apbeers/Attnd-Android` `develop` — Kotlin/Compose stack, project structure, secrets setup, app flow with Play Install Referrer, version management

### 2. Monorepo Migration Plan (`infra/MONOREPO.md`)
- Rename `Attnd-Web` → `attnd` (the new monorepo is `apbeers/Attnd`)
- Import web source into `web/` subdirectory
- Import iOS via `git subtree add --prefix=ios ios/develop --squash`
- Import Android via `git subtree add --prefix=android android/develop --squash`
- Path-filtered GitHub Actions workflows per subdirectory
- Add `ios-dev`, `ios-prod`, `android-dev`, `android-prod` GitHub Environments
- Archive old repos after migration

### 3. Pipeline Architecture (`infra/PIPELINES.md`)
Full pipeline design covering:
- iOS PR checks (`infra/github-workflows/ci-ios.yml`) — macos-15, xcodebuild test, no code signing
- Android PR checks (`infra/github-workflows/ci-android.yml`) — ubuntu, JDK 21, Gradle test
- iOS dev distribute → TestFlight internal (`infra/github-workflows/distribute-ios-dev.yml`)
- iOS prod distribute → App Store (`infra/github-workflows/distribute-ios-prod.yml`) — manual approval gate via `ios-prod` environment
- Android dev distribute → Play Store internal track (`infra/github-workflows/distribute-android-dev.yml`)
- Android prod distribute → Play Store production 10% rollout (`infra/github-workflows/distribute-android-prod.yml`) — manual approval gate, increments VERSION_CODE and commits back
- Unified integration test suite (`infra/github-workflows/integration-tests.yml`) — 4 jobs: web-tests → android-api-tests → ios-api-tests → smoke-tests
- API smoke test script (`infra/scripts/smoke_test.py`) — tests every endpoint, validates response shapes

All workflow files are in `infra/github-workflows/` — they need to be copied to `.github/workflows/` after the monorepo is assembled.

### 4. AWS Bedrock Migration (`infra/BEDROCK.md`)
Replacing the OpenAI API in the scraper with AWS Bedrock (Claude 3.5 Haiku):
- **Terraform changes already committed**: `infra/iam.tf`, `infra/secrets.tf`, `infra/variables.tf`, `infra/ecs.tf`
  - Added Bedrock IAM policy to ECS task role
  - Removed `OPENAI_API_KEY` from Secrets Manager, variables, and container env
- **Drop-in replacement**: `infra/app-changes/scrape_service_bedrock.py` → copy to `scrape/service.py`
- **Dependency swap**: `infra/app-changes/pyproject_bedrock_changes.txt` — replace `openai>=2.8.0,<3.0.0` with `boto3>=1.38.0`, then run `uv lock`
- **Deploy workflows updated**: `infra/github-workflows/deploy-dev.yml` and `deploy-prod.yml` — removed openai_api_key var, added path filters, Docker context changed from `.` to `web`
- **One manual step required**: Enable Bedrock model access for `us.anthropic.claude-3-5-haiku-20241022-v1:0` in the AWS console (us-east-1 region) — Models → Manage model access

### 5. AWS Credentials
- AWS credentials are configured in the Claude Code environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION=us-east-1`)
- Authenticated as `arn:aws:iam::383136686254:user/all-aws-admin`
- AWS CLI v2 needs to be installed in each new session: `curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "/tmp/awscliv2.zip" && unzip -q /tmp/awscliv2.zip -d /tmp && /tmp/aws/install --install-dir /tmp/aws-cli --bin-dir /tmp/aws-bin`
- Verify with: `/tmp/aws-bin/aws sts get-caller-identity`

---

## What Still Needs To Be Done

### Immediate — Monorepo Assembly
The new `apbeers/Attnd` repo exists and is empty. The next session needs to:
1. Clone `apbeers/Attnd-Web` locally as the base
2. Move web Django source into `web/` subdirectory
3. `git subtree add --prefix=ios https://github.com/apbeers/Attnd-iOS develop --squash`
4. `git subtree add --prefix=android https://github.com/apbeers/Attnd-Android develop --squash`
5. Move all `infra/` content to repo root level (it's currently nested under the web source)
6. Copy `infra/github-workflows/*.yml` → `.github/workflows/`
7. Push to `apbeers/Attnd` main

Full step-by-step in `infra/MONOREPO.md`.

### Infrastructure Bootstrap
Terraform has never been applied to AWS account `383136686254`. Before the app can deploy:
1. Run `infra/bootstrap.sh` — creates S3 state bucket, DynamoDB lock table, OIDC provider, and deploy IAM roles
2. Run `terraform init` then `terraform apply` in `infra/`
3. Set all required secrets in AWS Secrets Manager (see `infra/MIGRATION.md` and `infra/PIPELINES.md` secrets inventory)

### App Code — Bedrock Migration
These changes need to be applied to the web source before deploying:
1. Copy `infra/app-changes/scrape_service_bedrock.py` → `scrape/service.py`
2. In `pyproject.toml`: replace `openai>=2.8.0,<3.0.0` with `boto3>=1.38.0`
3. Run `uv lock` to regenerate the lockfile
4. Enable Bedrock model access in AWS console (us-east-1)

### GitHub Actions Secrets to Create
See `infra/PIPELINES.md` for the full inventory. Key ones:
- Web (existing): `DEV_AWS_ROLE_ARN`, `PROD_AWS_ROLE_ARN`, `DEV_DB_PASSWORD`, `PROD_DB_PASSWORD`, etc.
- iOS (new): `IOS_DISTRIBUTION_CERT_P12_BASE64`, `IOS_DIST_CERT_PASSWORD`, `IOS_PROVISIONING_PROFILE_BASE64`, `APPSTORE_API_KEY_BASE64`, `APPSTORE_API_KEY_ID`, `APPSTORE_API_ISSUER_ID`
- Android (new): `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`, `ANDROID_STORE_PASSWORD`, `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON`

### GitHub Environments to Create
In `apbeers/Attnd` repo settings → Environments:
- `ios-prod` — add required reviewers (manual approval gate)
- `android-prod` — add required reviewers (manual approval gate)
- `ios-dev`, `android-dev` — no approval required

---

## Key Technical Notes

### Authentication Architecture
- All API calls use `Authorization: Bearer <token>`
- Production: short-lived JWT issued after Apple App Attest (iOS) or Google Play Integrity (Android) verification
- Dev/CI: static `API_KEY` env var
- On 401: clients invalidate JWT and re-attest once before giving up

### AWS Infrastructure (ECS Fargate)
- Account: `383136686254`
- Region: `us-east-1`
- ECS Fargate + RDS PostgreSQL + ALB + ECR + S3 + CloudWatch + Secrets Manager
- GitHub Actions uses OIDC to assume deploy roles — **no stored AWS keys in GitHub secrets**
- The `all-aws-admin` IAM user keys are only for the one-time bootstrap

### Bedrock Model
- Model ID: `us.anthropic.claude-3-5-haiku-20241022-v1:0` (cross-region inference profile)
- Auth: IAM role (no API key)
- Structured output via forced tool use (`toolChoice: {tool: {name: "event_extraction"}}`)
- Region: `us-east-1`

### Monorepo Directory Layout (Target)
```
attnd/
  web/          ← Django source (was repo root of Attnd-Web)
  ios/          ← iOS source (from Attnd-iOS)
  android/      ← Android source (from Attnd-Android)
  infra/        ← Terraform + planning docs (was infra/ in Attnd-Web)
  .github/
    workflows/  ← All CI/CD workflows (copied from infra/github-workflows/)
```
