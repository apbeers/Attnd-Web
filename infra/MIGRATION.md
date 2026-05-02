# Heroku → AWS Migration Guide

Everything in this folder is self-contained. When you're ready to cut over,
follow the steps below. No manual AWS console work is required.

---

## Prerequisites

- AWS CLI configured with admin credentials (`aws configure`)
- Terraform >= 1.7 (`brew install terraform`)
- Docker with buildx support
- GitHub repository admin access (to set secrets and environments)

---

## Step 1 — Bootstrap (one-time, run locally)

This creates the Terraform state S3 bucket, DynamoDB lock table, GitHub OIDC
provider, and IAM deploy roles. Uses local state; safe to re-run.

```bash
bash infra/bootstrap.sh
```

Copy the two role ARNs from the output. You'll need them in Step 3.

---

## Step 2 — Create GitHub Environments

In GitHub: **Settings → Environments → New environment**

Create two environments: `dev` and `prod`.

For `prod`, add yourself as a required reviewer so every prod deploy needs
manual approval before it runs.

---

## Step 3 — Add GitHub Secrets

Go to **Settings → Secrets and variables → Actions → New repository secret**
and add all of these:

| Secret | Value |
|---|---|
| `AWS_DEV_ROLE_ARN` | From `bootstrap.sh` output |
| `AWS_PROD_ROLE_ARN` | From `bootstrap.sh` output |
| `DEV_DB_PASSWORD` | A strong password (alphanumeric + hyphens only) |
| `DEV_DJANGO_SECRET_KEY` | Generate: `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"` |
| `DEV_ATTESTATION_JWT_SECRET` | Your current Heroku `ATTESTATION_JWT_SECRET` value |
| `DEV_ATTND_API_KEY` | Your current Heroku `ATTND_API_KEY` value |
| `DEV_OPENAI_API_KEY` | Your OpenAI key (or leave empty) |
| `PROD_DB_PASSWORD` | Different strong password from dev |
| `PROD_DJANGO_SECRET_KEY` | Different from dev |
| `PROD_ATTESTATION_JWT_SECRET` | Your production value |
| `PROD_ATTND_API_KEY` | Your production value |
| `PROD_OPENAI_API_KEY` | Your OpenAI key (or leave empty) |

---

## Step 4 — Update environment tfvars (optional)

Edit `infra/environments/dev.tfvars` and `infra/environments/prod.tfvars`
if you want to change instance sizes, task counts, or alert email:

```hcl
# infra/environments/prod.tfvars
alert_email = "you@example.com"   # receives CloudWatch alarm emails
```

---

## Step 5 — Activate the GitHub Actions workflows

Move the three workflow files into `.github/workflows/`:

```bash
cp infra/github-workflows/ci.yml          .github/workflows/ci.yml
cp infra/github-workflows/deploy-dev.yml  .github/workflows/deploy-dev.yml
cp infra/github-workflows/deploy-prod.yml .github/workflows/deploy-prod.yml
```

Delete the disabled stub files:

```bash
# The stubs have `on: {}` so they won't trigger, but you can delete them
# after copying the real ones over (they'll be overwritten anyway).
```

Commit and push to `develop`. This triggers the first dev deploy:
1. Terraform applies the full dev infrastructure (VPC, RDS, ECS, ALB, S3, CloudWatch)
2. Builds and pushes the Docker image to ECR
3. Runs `python manage.py migrate`
4. Deploys the ECS service

---

## Step 6 — Point DNS

After the first dev deploy, Terraform prints the ALB DNS name. Add a CNAME:

```
dev.attnd.app  →  attnd-dev-alb-XXXX.us-east-1.elb.amazonaws.com
```

Terraform also prints the ACM certificate DNS validation records. Add those
too — the HTTPS listener won't work until the cert is validated (~1-2 min).

For prod, do the same after merging to `main`:
```
attnd.app  →  attnd-prod-alb-XXXX.us-east-1.elb.amazonaws.com
```

---

## Step 7 — Run `uv lock` after adding python-json-logger

Before the first deploy, update the lockfile locally:

```bash
uv lock
git add uv.lock && git commit -m "chore: add python-json-logger to lockfile"
```

---

## Step 8 — Decommission Heroku (when ready)

Once prod traffic is stable on AWS:
1. Scale Heroku dynos to 0 (or delete the app)
2. Export the Heroku Postgres database and import into RDS if you have
   existing data to preserve:
   ```bash
   heroku pg:backups:capture --app attnd-web
   heroku pg:backups:download --app attnd-web
   pg_restore --no-owner -d $DATABASE_URL latest.dump
   ```

---

## What's in `infra/`

```
infra/
├── bootstrap/              One-time setup: state bucket, OIDC, IAM roles
│   ├── main.tf
│   ├── variables.tf
│   └── outputs.tf
├── bootstrap.sh            Script to run bootstrap (one-time, locally)
├── backends/
│   ├── dev.hcl             S3 state key for dev environment
│   └── prod.hcl            S3 state key for prod environment
├── environments/
│   ├── dev.tfvars          Dev-specific sizing and config
│   └── prod.tfvars         Prod-specific sizing and config
├── github-workflows/       GitHub Actions pipelines (inactive until copied)
│   ├── ci.yml              PR checks: test + terraform validate + docker build
│   ├── deploy-dev.yml      Push to develop → infra apply → deploy dev
│   └── deploy-prod.yml     Push to main → infra apply → deploy prod
├── main.tf                 Provider + backend (parameterized per env)
├── variables.tf            All input variables
├── outputs.tf              Useful values printed after apply
├── vpc.tf                  VPC, subnets, security groups
├── ecr.tf                  Container registry
├── ecs.tf                  ECS Fargate cluster, task definition, service
├── rds.tf                  PostgreSQL RDS instance
├── alb.tf                  Application Load Balancer + ACM certificate
├── s3.tf                   S3 bucket for user media uploads
├── cloudwatch.tf           Log groups, metric filters, alarms, dashboard
├── iam.tf                  ECS task execution and task IAM roles
└── secrets.tf              Secrets Manager for all app secrets
```

## CloudWatch Logs

Django logs to stdout in JSON format. ECS ships all stdout to CloudWatch via
the awslogs driver. The log group is `/ecs/attnd-<env>`.

**CloudWatch Insights queries:**

```
# Recent errors
fields @timestamp, levelname, name, message
| filter levelname = 'ERROR'
| sort @timestamp desc
| limit 50

# Request latency (from gunicorn access logs)
fields @timestamp, @message
| parse @message '"* * *" * * *' as method, path, proto, status, bytes, duration
| filter status >= 400
| sort @timestamp desc
```

A pre-built dashboard is available at:
**CloudWatch → Dashboards → attnd-dev** (or attnd-prod)

Alarms fire to SNS → email for:
- Application ERROR log rate elevated
- ALB 5xx rate elevated
- ECS CPU > 80%
- RDS CPU > 80%
- RDS free storage < 5 GB
