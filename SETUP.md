# Attnd setup checklist

Do these steps once, in order. Each step says which values go where.
- **GH-web**, **GH-ios** and **GH-android** mean that repo's GitHub page, under **Settings → Secrets and variables → Actions**.
- **Heroku prod** is the app `attnd-web`. **Heroku dev** is the new app `attnd-dev`.
- Once everything is set up, pushing to `develop` deploys all three apps to dev and beta, and merging `develop` → `main` releases to production.

---

## 0. GitHub (5 min)

1. In **Attnd-iOS** and **Attnd-Android**, set the default branch to `main`: Settings → General → Default branch → switch icon → `main`. Attnd-Web is already on `main`.
2. Optional cleanup:
   - **Attnd-Android:** `master` is a single 2021 "initial commit" and can be deleted. The real code now lives on `main` and `develop`.
   - **Attnd-Web:** `master` is an old side line that is not part of `main`. Glance at it before deleting. Web's old 2021 `develop` history is still reachable, because it was merged into the new `develop` without its content.
3. In each of the three repos, create two environments under Settings → Environments: **`dev`** and **`production`**. On `production` you can also add yourself as a required reviewer if you want a one-tap approval before each production deploy.

## 1. Rotate the leaked API key (2 min) — required

```bash
heroku config:set API_KEY=$(openssl rand -hex 32) -a attnd-web
```
The old key is public in iOS git history. The server already rejects it, but this gives you a fresh one. Production app builds don't use this key at all, because they authenticate with App Attest or Play Integrity.

## 2. Heroku (15 min)

### Production (`attnd-web`)
```bash
heroku addons:create scheduler:standard -a attnd-web
heroku addons:open scheduler -a attnd-web
#   → Add job: "python manage.py run_scheduled_tasks", every 10 minutes
heroku config:set -a attnd-web OPENAI_API_KEY=sk-...        # if not already set
```

### Dev (`attnd-dev`)
```bash
heroku create attnd-dev
heroku addons:create heroku-postgresql:essential-0 -a attnd-dev
heroku addons:create scheduler:standard -a attnd-dev   # same job as production
heroku config:set -a attnd-dev \
  SECRET_KEY=$(openssl rand -hex 32) API_KEY=$(openssl rand -hex 32) \
  ATTESTATION_JWT_SECRET=$(openssl rand -hex 32) OPENAI_API_KEY=sk-... \
  APPLE_APP_BUNDLE_ID=com.arcu.Attnd APPLE_TEAM_ID=GQQX4L6TE2 \
  ANDROID_PACKAGE_NAME=com.arcu.attnd \
  GOOGLE_SERVICE_ACCOUNT_JSON="$(cat play-integrity-service-account.json)"
```
- Copy `GOOGLE_PLACES_API_KEY` from production if you use it.
- Leave `APPLE_ATTEST_ENVIRONMENT` unset. TestFlight builds use production App Attest.

### Let GitHub deploy to Heroku
```bash
heroku authorizations:create -d "GitHub Actions"   # copy the token
```
| Where | Name | Value |
|---|---|---|
| GH-web secret | `HEROKU_API_KEY` | that token |
| GH-web env `dev` variable | `HEROKU_APP` | `attnd-dev` |
| GH-web env `production` variable | `HEROKU_APP` | `attnd-web` |

## 3. Apple (30 min)

### 3a. Push notifications (APNs)
1. Go to [developer.apple.com](https://developer.apple.com) → Certificates, IDs & Profiles → **Identifiers** → `com.arcu.Attnd`. Make sure **Push Notifications** and **App Attest** are both enabled.
2. Go to **Keys → +**, name the key "Attnd APNs", tick **Apple Push Notifications service (APNs)**, then download `AuthKey_XXXXXXXXXX.p8`. Apple only lets you download it once, so keep it safe.
3. Set it on **both** Heroku apps:
   ```bash
   for app in attnd-web attnd-dev; do
     heroku config:set -a $app APNS_KEY_ID=XXXXXXXXXX APNS_AUTH_KEY="$(cat AuthKey_XXXXXXXXXX.p8)"
   done
   ```
   - The team ID and bundle ID default to `APPLE_TEAM_ID` and `APPLE_APP_BUNDLE_ID`.
   - TestFlight and App Store builds use the production push servers. Only builds run directly from Xcode need `APNS_USE_SANDBOX=1`.

### 3b. App Store Connect API key (signing and uploads from CI)
1. Go to [App Store Connect](https://appstoreconnect.apple.com) → Users and Access → **Integrations** → App Store Connect API → **Team Keys → +**.
2. Name it "GitHub CI" with access **Admin**. Admin is required so Xcode can create the signing certificate in the cloud. Download the `.p8` and note the **Key ID** and **Issuer ID**.
3. Add to GitHub:

| Where | Name | Value |
|---|---|---|
| GH-ios secret | `ASC_KEY_ID` | Key ID |
| GH-ios secret | `ASC_ISSUER_ID` | Issuer ID |
| GH-ios secret | `ASC_KEY_P8` | full contents of the `.p8` file |

### 3c. TestFlight
1. In App Store Connect → Attnd → **TestFlight → Internal Testing → +**, create a group named **Internal**. Add yourself, plus anyone else on your team.
2. Install the **TestFlight** app on your iPhone and accept the invite.
3. Add to GitHub:

| Where | Name | Value |
|---|---|---|
| GH-ios env `dev` variable | `ATTND_SERVER` | `attnd-dev.herokuapp.com` |
| GH-ios env `dev` variable | `TESTFLIGHT_GROUP` | `Internal` |
| GH-ios secret (optional) | `ATTND_API_KEY` | attnd-dev's `API_KEY`; this turns on the live API tests in CI |
| GH-ios env `production` variable (optional) | `WHATS_NEW` | release notes for the next App Store version |

### 3d. Before the first App Store submission from CI
- Update the privacy policy at `/privacy/` (`home/templates/home/privacy.html`). The app stores a random device ID and push tokens, and the current text says no device identifiers are collected. Also update the matching App Privacy answers in App Store Connect.
- Make sure the App Store listing (screenshots, description, review contact) is filled in. CI submits the build, but it can't fill in missing store metadata.

## 4. Google / Android (40 min)

### 4a. Firebase (push notifications)
1. In the [Firebase console](https://console.firebase.google.com), create a project or reuse your Google Cloud project.
2. Add Android apps for **`com.arcu.attnd`** and **`com.arcu.attnd.debug`**, then download `google-services.json`. It contains both apps.
3. Put the file at `Attnd-Android/app/google-services.json` for local builds. It's gitignored.
4. Set up the server's sending account. In Firebase → Project settings → **Service accounts** → *Generate new private key*, or use a Cloud service account with the **Firebase Cloud Messaging API Admin** role. Then:
   ```bash
   for app in attnd-web attnd-dev; do
     heroku config:set -a $app FCM_PROJECT_ID=<firebase-project-id> FCM_SERVICE_ACCOUNT_JSON="$(cat fcm-sa.json)"
   done
   ```

### 4b. Google Play publishing from CI
1. In [Google Cloud console](https://console.cloud.google.com), using the same project, enable the **Google Play Android Developer API**.
2. Create a service account named "github-play-publisher" and download a JSON key for it.
3. In **Play Console → Users and permissions → Invite new users**, enter that service account's email. Under App permissions → Attnd, grant **Release apps to testing tracks**, **Release to production**, and **Manage testing tracks**.
4. In Play Console → Attnd → **Testing → Internal testing → Testers**, add yourself, then open the **opt-in link** on your Android phone once.

### 4c. Signing and keys
- Upload keystore: use the same `.jks` and passwords as your current `signing.properties`.
- Maps key: in Cloud console → APIs & Services → Credentials, restrict the Maps key to Android apps:
  - `com.arcu.attnd` with the **App signing key** SHA-1 (Play Console → Test and release → App integrity)
  - `com.arcu.attnd` with the upload key SHA-1
  - `com.arcu.attnd.debug` with your debug SHA-1
- App Links: set the Play **App signing** SHA-256 on both Heroku apps so `/.well-known/assetlinks.json` is valid:
  ```bash
  heroku config:set -a attnd-web ANDROID_SHA256_FINGERPRINT=AA:BB:...
  ```

| Where | Name | Value |
|---|---|---|
| GH-android secret | `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` | publisher service-account JSON (4b) |
| GH-android secret | `ANDROID_KEYSTORE_BASE64` | `base64 -i upload.jks` |
| GH-android secret | `ANDROID_KEYSTORE_PASSWORD` / `ANDROID_KEY_ALIAS` / `ANDROID_KEY_PASSWORD` | from `signing.properties` |
| GH-android secret | `MAPS_API_KEY` | Maps key |
| GH-android secret | `GOOGLE_SERVICES_JSON` | contents of `google-services.json` |
| GH-android secret (optional) | `ATTND_API_KEY` | attnd-dev's `API_KEY`, for the live API tests |
| GH-android env `dev` variable | `ATTND_SERVER` | `attnd-dev.herokuapp.com` |
| GH-android env `production` variable (optional) | `PLAY_ROLLOUT` | e.g. `0.2` for a 20% staged rollout (default: 100%) |
| GH-android env `production` variable (optional) | `RELEASE_NOTES` | text shown in Play |

Note: the app now requires **Android 15 or later**. Play will stop offering updates to phones on older versions.

## 5. Local development keys

| App | File (gitignored) | Contents |
|---|---|---|
| iOS | `Attnd-iOS/Config/Secrets.xcconfig` | `ATTND_API_KEY = <attnd-dev API_KEY>` and `ATTND_SERVER = attnd-dev.herokuapp.com` |
| Android | `Attnd-Android/local.properties` | `ATTND_API_KEY=…`, `MAPS_API_KEY=…`, `ATTND_SERVER=attnd-dev.herokuapp.com` |
| Web | your shell | `DEBUG=1 API_KEY=dev-key uv run python manage.py runserver` |

## 6. Check it all works

1. Push anything to `develop` in each repo, or re-run the latest workflow from the Actions tab.
2. Watch **Actions**. The order is web → Heroku dev, iOS → TestFlight, Android → Play internal.
3. On your phone:
   - **iPhone:** TestFlight → Attnd → Install. The build is usually ready about 10–20 minutes after the run.
   - **Android:** open Play from the internal testing opt-in link, or Play Store → Attnd → update. Allow a few minutes after the run.
4. To release, merge `develop` → `main`. Web deploys to production, iOS is submitted to App Review, and Android goes to Play production.

### How the pipelines work (reference)

| Branch | Web | iOS | Android |
|---|---|---|---|
| any push / PR | tests (Postgres) | build + unit tests | unit tests, lint, debug build |
| `develop` | Heroku dev | TestFlight "Internal", using the dev server | Play internal testing, using the dev server |
| `main` | Heroku production | App Review → auto-release | Play production |

- Version numbers come from the run number, so you never bump them by hand.
  - iOS: `VERSION` file + run number, e.g. `2.0.57`.
  - Android: `2.0.<run>`, with version code `<run>000`.
- The pipelines use only GitHub's own actions and the official Apple and Google tools and APIs:
  - `scripts/ci/heroku_deploy.sh` (web)
  - `scripts/ci/asc.py` and `archive_upload.sh` (iOS)
  - `scripts/ci/play_publish.py` (Android)
- Dependencies:
  - Python: **Upgrade dependencies** runs every Monday (or run it by hand) and opens a PR into `develop`.
  - GitHub Actions and Gradle: Dependabot handles these.
