# Monorepo Migration Plan

This document covers merging `Attnd-Web`, `Attnd-iOS`, and `Attnd-Android` into
a single repository with path-filtered CI/CD pipelines for all three.

---

## Why a Monorepo

- API changes in `web/` and the mobile client changes that depend on them ship in one PR, making it impossible to merge a breaking API change without updating the clients simultaneously
- The integration test pipeline (see `PIPELINES.md`) can gate every release candidate on all three components passing together
- One place for infra, secrets, and deployment config
- `API.md` and `CLAUDE.md` stay in sync automatically — there's one copy

---

## Target Structure

```
attnd/                           ← renamed from Attnd-Web, or new repo
├── .github/
│   └── workflows/               ← all active pipelines
│       ├── ci.yml               ← existing web CI (add path filter)
│       ├── deploy-web-dev.yml   ← renamed from deploy-dev.yml
│       ├── deploy-web-prod.yml  ← renamed from deploy-prod.yml
│       ├── ci-ios.yml
│       ├── ci-android.yml
│       ├── distribute-ios-dev.yml
│       ├── distribute-ios-prod.yml
│       ├── distribute-android-dev.yml
│       ├── distribute-android-prod.yml
│       └── integration-tests.yml
├── android/                     ← Attnd-Android repo root moves here
├── ios/                         ← Attnd-iOS repo root moves here
├── web/                         ← Attnd-Web source moves here (not infra/)
└── infra/                       ← stays at repo root, no path changes
```

---

## Migration Steps

### Step 1 — Rename and restructure the web repo

Rename `Attnd-Web` → `attnd` on GitHub (Settings → Repository name). This
preserves all existing Terraform state references, ECR image tags, and GitHub
secrets — nothing on AWS changes.

```bash
git clone git@github.com:apbeers/Attnd-Web.git attnd
cd attnd

# Move web source into web/ (infra/ stays at root)
git mv attnd console events home join scrape attestation \
       manage.py pyproject.toml uv.lock Dockerfile Procfile \
       .dockerignore .python-version app.json version.txt \
       static public scripts _set_heroku_env.command \
       web/

git commit -m "chore: move web source into web/ for monorepo"
```

### Step 2 — Import iOS with history

```bash
git remote add ios git@github.com:apbeers/Attnd-iOS.git
git fetch ios

# --squash collapses iOS history into one commit to keep the log readable.
# Remove --squash if you want the full iOS commit history preserved.
git subtree add --prefix=ios ios/develop --squash

git remote remove ios
```

### Step 3 — Import Android with history

```bash
git remote add android git@github.com:apbeers/Attnd-Android.git
git fetch android
git subtree add --prefix=android android/develop --squash
git remote remove android
```

### Step 4 — Update internal path references

| File | What to change |
|------|----------------|
| `infra/github-workflows/ci.yml` | Add `paths: [web/**]` filter; change `uv sync` / `manage.py` commands to run in `working-directory: web` |
| `infra/github-workflows/deploy-dev.yml` | Change Docker build context from `.` to `web`; rename file to `deploy-web-dev.yml` |
| `infra/github-workflows/deploy-prod.yml` | Same; rename to `deploy-web-prod.yml` |
| `infra/ecs.tf` | No changes — Dockerfile path is supplied by the workflow, not Terraform |
| `ios/Attnd.xcodeproj` | No changes |
| `android/app/build.gradle` | No changes |

The `Dockerfile` uses `COPY . .` which works when the build context is `web/`:

```yaml
# In deploy-web-dev.yml / deploy-web-prod.yml, change the build step:
- name: Build & push image
  run: |
    docker buildx build \
      --platform linux/amd64 \
      --context web \           ← was: .
      --tag $ECR_URL:$SHA \
      --push \
      .
```

### Step 5 — Add path filter to existing web CI

Open `.github/workflows/ci.yml` (after copying from `infra/github-workflows/ci.yml`)
and add `paths` filters so it only runs when web code changes:

```yaml
on:
  pull_request:
    branches: [main, develop]
    paths:
      - 'web/**'
      - 'infra/**'
  push:
    branches: [develop]
    paths:
      - 'web/**'
      - 'infra/**'
```

### Step 6 — Activate all new workflows

```bash
# Copy updated web workflows
cp infra/github-workflows/ci.yml                   .github/workflows/ci.yml
cp infra/github-workflows/deploy-dev.yml           .github/workflows/deploy-web-dev.yml
cp infra/github-workflows/deploy-prod.yml          .github/workflows/deploy-web-prod.yml

# Copy new mobile + integration workflows
cp infra/github-workflows/ci-ios.yml               .github/workflows/
cp infra/github-workflows/ci-android.yml           .github/workflows/
cp infra/github-workflows/distribute-ios-dev.yml   .github/workflows/
cp infra/github-workflows/distribute-ios-prod.yml  .github/workflows/
cp infra/github-workflows/distribute-android-dev.yml  .github/workflows/
cp infra/github-workflows/distribute-android-prod.yml .github/workflows/
cp infra/github-workflows/integration-tests.yml    .github/workflows/
```

Commit and push to `develop`. Verify all CI jobs go green before proceeding.

### Step 7 — Add new GitHub Environments

The existing `dev` and `prod` environments cover the web backend. Add four more:

| Environment | Pipeline | Required reviewer |
|-------------|----------|-------------------|
| `ios-dev` | distribute-ios-dev | No |
| `ios-prod` | distribute-ios-prod | Yes — blocks App Store submission |
| `android-dev` | distribute-android-dev | No |
| `android-prod` | distribute-android-prod | Yes — blocks Play Store production |

### Step 8 — Add new GitHub Secrets

See `PIPELINES.md` for the full list of iOS and Android secrets.

### Step 9 — Archive old repos

Once the monorepo pipelines are green and the first app builds have been
successfully distributed:

1. Add a notice to the top of each old repo's README pointing to the monorepo
2. Archive `Attnd-iOS` and `Attnd-Android` on GitHub (Settings → Archive repository)
3. Leave `Attnd-Web` archived or redirect — the rename in Step 1 already handles routing

---

## Branch Strategy (unchanged)

| Branch | Purpose |
|--------|---------|
| `develop` | Integration branch; all feature work merges here; triggers dev deploys + dev distribution |
| `main` | Production-ready; triggers prod deploy + prod distribution + full integration test gate |
| `feature/*` | Short-lived, off `develop` |

Path filters ensure only the changed component's pipelines run on each push.
A pure iOS change on `develop` triggers `ci-ios` and `distribute-ios-dev` only —
web and Android pipelines don't run.
