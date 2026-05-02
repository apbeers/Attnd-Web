# Pipeline Architecture

This document describes all CI/CD pipelines for the Attnd monorepo: web, iOS,
Android, and the unified integration test suite that gates releases.

Workflow files are in `infra/github-workflows/` and are inactive until copied
to `.github/workflows/` (same pattern as the existing web pipelines).

---

## Pipeline Overview

```
PR opened
  └── path: web/**    → ci.yml             (Django tests, terraform validate, docker build)
  └── path: ios/**    → ci-ios.yml         (Xcode unit tests on simulator)
  └── path: android/** → ci-android.yml   (Gradle unit tests on JVM)

push → develop
  └── web/**    → deploy-web-dev.yml       (Terraform apply dev, build+push ECR, ECS deploy)
  └── ios/**    → distribute-ios-dev.yml   (xcodebuild archive → TestFlight internal)
  └── android/** → distribute-android-dev.yml  (Gradle AAB → Play Store internal track)
  └── (any)     → integration-tests.yml   (all three tested together against dev env)

push → main
  └── web/**    → deploy-web-prod.yml      (same as dev, targets prod env, manual approval)
  └── ios/**    → distribute-ios-prod.yml  (App Store submission, manual approval)
  └── android/** → distribute-android-prod.yml  (Play Store production, manual approval)
```

---

## iOS Pipelines

### CI (`ci-ios.yml`)

Trigger: PR to `main` or `develop` touching `ios/**`  
Runner: `macos-15`

1. Select Xcode version via `XCODE_VERSION` env
2. `xcodebuild test` against iPhone simulator
3. Fail PR if any test fails

### Dev Distribution (`distribute-ios-dev.yml`)

Trigger: push to `develop` touching `ios/**`  
Runner: `macos-15`  
Environment: `ios-dev`

1. Import signing certificate + provisioning profile into a temporary keychain
2. Run unit tests (same as CI)
3. `xcodebuild archive` with `Development` signing
4. `xcodebuild -exportArchive` with `development` export method
5. Upload IPA to TestFlight internal group using App Store Connect API key

### Prod Distribution (`distribute-ios-prod.yml`)

Trigger: push to `main` touching `ios/**`  
Runner: `macos-15`  
Environment: `ios-prod` (requires manual approval)

Same as dev with two differences:
- Uses `app-store` export method
- Uploads to App Store Connect for external TestFlight / App Review submission

### iOS Secrets Required

| Secret | Description |
|--------|-------------|
| `IOS_P12_BASE64` | Base64-encoded `.p12` distribution certificate |
| `IOS_P12_PASSWORD` | Password for the `.p12` |
| `IOS_DEV_PROVISIONING_PROFILE_BASE64` | Base64-encoded dev provisioning profile |
| `IOS_PROD_PROVISIONING_PROFILE_BASE64` | Base64-encoded App Store provisioning profile |
| `APP_STORE_CONNECT_API_KEY_ID` | App Store Connect API key ID |
| `APP_STORE_CONNECT_API_ISSUER_ID` | App Store Connect API issuer ID |
| `APP_STORE_CONNECT_API_KEY_BASE64` | Base64-encoded `.p8` private key |
| `IOS_ATTND_API_KEY` | API key baked into the app build (`ATTND_API_KEY` xcconfig value) |

### Generating the secrets

```bash
# Export distribution cert from Keychain Access as .p12, then:
base64 -i Certificates.p12 | pbcopy   # paste as IOS_P12_BASE64

# Export provisioning profile from ~/Library/MobileDevice/Provisioning Profiles/
base64 -i YourProfile.mobileprovision | pbcopy

# App Store Connect API key: Users & Access → Keys → + → download .p8
base64 -i AuthKey_XXXXXXXXXX.p8 | pbcopy
```

---

## Android Pipelines

### CI (`ci-android.yml`)

Trigger: PR to `main` or `develop` touching `android/**`  
Runner: `ubuntu-latest`

1. Set up JDK 21
2. Cache Gradle dependencies and build cache
3. Write `android/local.properties` with CI test keys
4. `./gradlew test` — JVM unit tests, no emulator needed

### Dev Distribution (`distribute-android-dev.yml`)

Trigger: push to `develop` touching `android/**`  
Runner: `ubuntu-latest`  
Environment: `android-dev`

1. Set up JDK 21 + Gradle cache
2. Decode keystore from `ANDROID_KEYSTORE_BASE64` secret into `android/keystore.jks`
3. Write `android/signing.properties` from secrets
4. Write `android/local.properties` with `ATTND_API_KEY` + `MAPS_API_KEY`
5. Run unit tests
6. Set `VERSION_CODE` to `github.run_number` (dev builds are ephemeral — no commit needed)
7. `./gradlew :app:bundleRelease` → produces `.aab`
8. Upload AAB to Google Play **internal test track** via service account JSON

### Prod Distribution (`distribute-android-prod.yml`)

Trigger: push to `main` touching `android/**`  
Runner: `ubuntu-latest`  
Environment: `android-prod` (requires manual approval)

Same as dev with two differences:
- Uses the `VERSION_CODE` from committed `version.properties` (not run number)
- After a successful Play Store upload, increments `VERSION_CODE` in `version.properties` and commits it back to `main`
- Uploads to Play Store **production track** (rolls out at 10% by default — adjust in workflow)

### Android Secrets Required

| Secret | Description |
|--------|-------------|
| `ANDROID_KEYSTORE_BASE64` | Base64-encoded release keystore `.jks` |
| `ANDROID_STORE_PASSWORD` | Keystore password |
| `ANDROID_KEY_ALIAS` | Key alias |
| `ANDROID_KEY_PASSWORD` | Key password |
| `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` | Full JSON of Google Play service account key |
| `ANDROID_ATTND_API_KEY` | API key baked into the app build |
| `ANDROID_MAPS_API_KEY` | Google Maps API key |

### Generating the secrets

```bash
# Keystore (if you don't have one yet):
keytool -genkey -v -keystore release.jks -alias attnd \
  -keyalg RSA -keysize 2048 -validity 10000
base64 -i release.jks | pbcopy   # paste as ANDROID_KEYSTORE_BASE64

# Google Play service account:
# Google Play Console → Setup → API access → Create service account
# Grant "Release manager" role → download JSON key
# Paste the entire JSON as GOOGLE_PLAY_SERVICE_ACCOUNT_JSON
```

---

## Integration Test Pipeline (`integration-tests.yml`)

### Purpose

Verify that the versions of web, iOS, and Android that are about to be released
all work correctly _together_ — not just in isolation.

### Trigger

- `workflow_dispatch` — manual trigger at any time
- `workflow_run` — automatically after `deploy-web-dev.yml` completes on `develop`
  (runs against the freshly deployed dev environment)

### Architecture

The suite has four jobs that run in sequence:

```
[web-tests] → [android-api-tests] → [ios-api-tests] → [smoke-tests]
                                                             ↓
                                                       post summary
```

#### Job 1: `web-tests` (ubuntu-latest)

Spins up Django + PostgreSQL in Docker using GitHub services. Runs the full
Django test suite (`python manage.py test`). This is the same job as the web
CI but runs against the monorepo's `web/` path.

#### Job 2: `android-api-tests` (ubuntu-latest)

Runs the Android unit test suite with `BASE_URL` pointed at the GitHub-hosted
web service from Job 1 (via `host.docker.internal` or a shared service network).

These tests exercise `APIClient` (Retrofit) against a real HTTP server with a
real database — catching serialization mismatches, missing fields, and changed
endpoint behavior that unit tests with mocked responses would miss.

No emulator is needed: Robolectric runs on the JVM.

#### Job 3: `ios-api-tests` (macos-15)

Runs iOS **logic tests** (XCTest, no simulator UI) with `ATTND_BASE_URL` set
to the dev environment URL. These test `APIClient.swift` and all
`Decodable` models against real API responses.

Logic tests don't require a booted simulator, so they complete in ~2 minutes.

#### Job 4: `smoke-tests` (ubuntu-latest)

A Python script (`infra/scripts/smoke_test.py`) that calls each API endpoint
and asserts:
- Response status codes match spec
- Required fields are present and the correct type
- The `nearby_events` endpoint returns results for a known test event
- Join/leave flow completes successfully
- Push subscribe/unsubscribe returns `{"status": "ok"}`

This script is the fastest way to catch a broken deploy and is easy to extend
when new endpoints are added.

### What Failure Means

| Failing job | Likely cause |
|-------------|-------------|
| `web-tests` | Django regression; block web deploy |
| `android-api-tests` | API contract broken for Android (field renamed, type changed, etc.) |
| `ios-api-tests` | API contract broken for iOS |
| `smoke-tests` | Deployed env is unhealthy or a key flow is broken end-to-end |

### Gating prod releases

The prod distribution workflows (`distribute-ios-prod`, `distribute-android-prod`,
`deploy-web-prod`) each have `needs: integration-tests` so a failing integration
suite blocks all three prod pipelines simultaneously. No single platform can
ship if the stack is broken.

---

## Full Secrets Inventory

All secrets are set at the repo level (Settings → Secrets and variables → Actions)
except where noted as environment-scoped.

### Existing (web)

`AWS_DEV_ROLE_ARN`, `AWS_PROD_ROLE_ARN`, `DEV_DB_PASSWORD`, `DEV_DJANGO_SECRET_KEY`,
`DEV_ATTESTATION_JWT_SECRET`, `DEV_ATTND_API_KEY`, `DEV_OPENAI_API_KEY`,
`PROD_DB_PASSWORD`, `PROD_DJANGO_SECRET_KEY`, `PROD_ATTESTATION_JWT_SECRET`,
`PROD_ATTND_API_KEY`, `PROD_OPENAI_API_KEY`

### New (iOS)

`IOS_P12_BASE64`, `IOS_P12_PASSWORD`, `IOS_DEV_PROVISIONING_PROFILE_BASE64`,
`IOS_PROD_PROVISIONING_PROFILE_BASE64`, `APP_STORE_CONNECT_API_KEY_ID`,
`APP_STORE_CONNECT_API_ISSUER_ID`, `APP_STORE_CONNECT_API_KEY_BASE64`,
`IOS_ATTND_API_KEY`

### New (Android)

`ANDROID_KEYSTORE_BASE64`, `ANDROID_STORE_PASSWORD`, `ANDROID_KEY_ALIAS`,
`ANDROID_KEY_PASSWORD`, `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON`,
`ANDROID_ATTND_API_KEY`, `ANDROID_MAPS_API_KEY`
