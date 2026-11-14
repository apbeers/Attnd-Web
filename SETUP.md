# Attnd setup checklist

Do these steps once, in order. Each step says which values go where.

## Progress

| Step | Status |
|---|---|
| 0. GitHub | ✅ Done. Default branch is `main` in all three repos (verified). The environments and secrets get verified by the first deploy run; a missing one fails with a message naming it. |
| 1. Rotate API key | ✅ Done. Run the check under step 1 to confirm. |
| 2. Heroku | ⏳ The dev app `attnd-dev` was created by Claude (Postgres, Scheduler and generated secrets are set). **You still need to:** set the two production secrets (below), add the OpenAI key to dev, and add the GitHub deploy token. |
| 3. Apple | ⬜ CI builds and tests pass. Upload waits for the `ASC_*` secrets (3b). |
| 4. Google / Android | ⏳ CI already builds a **signed** bundle, so the keystore secrets in GitHub work. `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` exists and signs in, but Play returns *permission denied*: invite that service account in Play Console (4b, step 3). |
| 5. Local dev keys | ⬜ |
| 6. Check it all works | ⬜ |

Notes:
- If the Heroku CLI says an update is available, run `heroku update` first. It's optional.
- Paste only the lines inside the command boxes. zsh doesn't treat `#` as a comment by default, so the boxes contain no comments.
- **GH-web**, **GH-ios** and **GH-android** mean that repo's GitHub page, under **Settings → Secrets and variables → Actions**.
- **Heroku prod** is the app `attnd-web`. **Heroku dev** is the new app `attnd-dev`.
- Once everything is set up, pushing to `develop` deploys all three apps to dev and beta, and merging `develop` → `main` releases to production.

---

## Where keys live

| Store | What it's for | Can you read keys back? |
|---|---|---|
| **Password manager** (Apple Passwords or 1Password) | The **master copy of every key**. Save each key here the moment you create it. | Yes |
| **GitHub environment secrets** | Keys the CI/CD pipelines use. | No, write-only |
| **Heroku config vars** | Keys the running server uses. | Yes (`heroku config:get`) |
| **Claude cloud environment** ("Attnd Cloud Environment") | Network secrets that give Claude API access. Currently: a Heroku API token for `api.heroku.com`. Claude never sees the value. | No, write-only |

Never keep keys only on your Mac. Once a key is in the password manager and wherever it's used, delete the downloaded file (`.p8`, `.jks`, `.json`).

The Claude environment allows `api.heroku.com`, `attnd.app` and `attnd-dev-7ab893f37020.herokuapp.com`. To give Claude access to another API later, add its token there as a network secret. Claude asks for these through a secure form, never in chat.

---

## 0. GitHub (5 min)

1. ✅ Set the default branch to `main` in **Attnd-iOS** and **Attnd-Android** (Settings → General → Default branch). Attnd-Web was already on `main`.
2. Optional cleanup:
   - **Attnd-Android:** `master` is a single 2021 "initial commit" and can be deleted. The real code now lives on `main` and `develop`.
   - **Attnd-Web:** `master` is an old side line that is not part of `main`. Glance at it before deleting. Web's old 2021 `develop` history is still reachable, because it was merged into the new `develop` without its content.
3. ✅ In each of the three repos, create two environments under Settings → Environments: **`dev`** and **`production`**. On `production` you can also add yourself as a required reviewer if you want a one-tap approval before each production deploy.

## 1. Rotate the leaked API key (2 min) — required

Also replace the old `ATTND_API_KEY` secret in Attnd-iOS and Attnd-Android. It's probably the leaked key. Use the dev app's new key (see section 5).

```bash
heroku config:set API_KEY=$(openssl rand -hex 32) -a attnd-web
```
The old key is public in iOS git history. The server already rejects it, but this gives you a fresh one. Production app builds don't use this key at all, because they authenticate with App Attest or Play Integrity.

**Check the new key works:**
```bash
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $(heroku config:get API_KEY -a attnd-web)" "https://attnd.app/api/v1/events/lookup/?q=FWFM2026"
```
- `200` or `404` means the key works. A 404 just means that test event doesn't exist in production.
- `401` means the key didn't take.

Requests with the old key now get `401`. Once this release is on `main`, the server also blocks the old key permanently by its hash.

## 2. Heroku (15 min)

### Production (`attnd-web`)
1. **Required before the first release to `main`:** production has no `SECRET_KEY` or `ATTESTATION_JWT_SECRET`. It has been running on a hardcoded fallback, and the new code refuses to start without a real key. Run this once:
   ```bash
   heroku config:set -a attnd-web SECRET_KEY=$(openssl rand -hex 32) ATTESTATION_JWT_SECRET=$(openssl rand -hex 32)
   ```
   This signs everyone out of the web console once. The apps re-attest automatically.
2. ✅ Scheduler job `python manage.py run_scheduled_tasks` (every 10 minutes) is added. It fails harmlessly until the first release to `main`, because the command doesn't exist in the old code.
3. ✅ The OpenAI and Places keys are already set on production.
4. **Play Integrity isn't configured on production** (there's no `GOOGLE_SERVICE_ACCOUNT_JSON`), so Android builds from the Play Store can't attest. Old Android builds fell back to the static key, which is now rotated, so the current Play Store version can't reach the API until this is set (step 4) and a new build ships. iOS builds use App Attest, which is configured.

### Dev (`attnd-dev`) — created by Claude
- ✅ App `attnd-dev` at `https://attnd-dev-7ab893f37020.herokuapp.com/`. Heroku now gives new apps a random hostname, so use this exact host wherever this guide says `ATTND_SERVER`.
- ✅ Postgres (essential-0), Scheduler, and generated `SECRET_KEY`, `API_KEY` and `ATTESTATION_JWT_SECRET`.
- **You:** copy the third-party keys over from production. The values never touch disk:
  ```bash
  heroku config:set -a attnd-dev OPENAI_API_KEY="$(heroku config:get OPENAI_API_KEY -a attnd-web)" GOOGLE_PLACES_API_KEY="$(heroku config:get GOOGLE_PLACES_API_KEY -a attnd-web)"
  ```
- After the first deploy to dev, add the same Scheduler job on `attnd-dev` (`heroku addons:open scheduler -a attnd-dev`).
- The dev `API_KEY` is what goes in the `ATTND_API_KEY` GitHub secrets and in your local dev config. Print it with `heroku config:get API_KEY -a attnd-dev`.

### Let GitHub deploy to Heroku
```bash
heroku authorizations:create -d "GitHub Actions"
```
Copy the token it prints into **GH-web secret `HEROKU_API_KEY`** and into your password manager. GitHub needs its own token: the one in the Claude environment can't be copied out.
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
| GH-ios env `dev` variable | `ATTND_SERVER` | `attnd-dev-7ab893f37020.herokuapp.com` |
| GH-ios env `dev` variable | `TESTFLIGHT_GROUP` | `Internal` |
| GH-ios secret + variables (optional) | `ATTND_API_KEY` secret = attnd-dev's `API_KEY`; variables `LIVE_API_TESTS=true`, `LIVE_API_SERVER=attnd-dev-7ab893f37020.herokuapp.com` | turns on the live API tests in CI |
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
| GH-android secret + variables (optional) | `ATTND_API_KEY` secret = attnd-dev's `API_KEY`; variables `LIVE_API_TESTS=true`, `LIVE_API_SERVER=attnd-dev-7ab893f37020.herokuapp.com` | turns on the live API tests in CI |
| GH-android env `dev` variable | `ATTND_SERVER` | `attnd-dev-7ab893f37020.herokuapp.com` |
| GH-android env `production` variable (optional) | `PLAY_ROLLOUT` | e.g. `0.2` for a 20% staged rollout (default: 100%) |
| GH-android env `production` variable (optional) | `RELEASE_NOTES` | text shown in Play |

Note: the app now requires **Android 15 or later**. Play will stop offering updates to phones on older versions.

## 5. Local development keys

| App | File (gitignored) | Contents |
|---|---|---|
| iOS | `Attnd-iOS/Config/Secrets.xcconfig` | `ATTND_API_KEY = <attnd-dev API_KEY>` and `ATTND_SERVER = attnd-dev-7ab893f37020.herokuapp.com` |
| Android | `Attnd-Android/local.properties` | `ATTND_API_KEY=…`, `MAPS_API_KEY=…`, `ATTND_SERVER=attnd-dev-7ab893f37020.herokuapp.com` |
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
