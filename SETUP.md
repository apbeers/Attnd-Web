# Attnd setup checklist

Do these steps once, in order. Each step says which values go where. For any single key, see the **Key reference** at the end. It explains where to create the key and exactly where it goes.

## Progress

| Step | Status |
|---|---|
| 0. GitHub | ✅ Done. Default branch is `main` in all three repos (verified). The environments and secrets get verified by the first deploy run; a missing one fails with a message naming it. |
| 1. Rotate API key | ✅ Done. Run the check under step 1 to confirm. |
| 2. Heroku | ⏳ ✅ `attnd-dev` is created and configured, including the OpenAI and Places keys and its Scheduler job (which starts working after the first dev deploy). **Still to do:** the two production secrets (step 2), then the one-time GitHub setup under **GitHub from the command line**. |
| 3. Apple | ⏳ ✅ App ID capabilities checked: Push Notifications, App Attest and Associated Domains are enabled. **Next:** create the APNs key (3a, step 2). CI builds and tests pass; uploads wait for the `ASC_*` secrets (3b). |
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
- ✅ The OpenAI and Places keys have been copied from production. For reference, the command was:
  ```bash
  heroku config:set -a attnd-dev OPENAI_API_KEY="$(heroku config:get OPENAI_API_KEY -a attnd-web)" GOOGLE_PLACES_API_KEY="$(heroku config:get GOOGLE_PLACES_API_KEY -a attnd-web)"
  ```
- ✅ The Scheduler job is added on `attnd-dev`.
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
1. ✅ Go to [developer.apple.com](https://developer.apple.com) → Certificates, IDs & Profiles → **Identifiers** → `com.arcu.Attnd`. Check that **Push Notifications**, **App Attest** and **Associated Domains** are enabled. All three are.
   - Push shows **"Certificates (0)"**. That's expected. Attnd sends pushes with a token-based APNs **key** (step 2), not certificates, so don't click **Configure**.
   - Leave **Broadcast Capability** unticked. It's for live-broadcast push channels, which Attnd doesn't use.
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
4. Set up the server's Google service account, which covers both push and Play Integrity. See **Key reference → `GOOGLE_SERVICE_ACCOUNT_JSON`, `FCM_PROJECT_ID`**.

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

---

## Key reference

For every key: where to create it, where it goes, and what to do if it's lost. Save each one to your password manager first, then put it where it's used, then delete any downloaded file.

### How to store a key in each place

### GitHub from the command line (`gh`)

Run `gh auth login` once. After that, every GitHub setting in this guide can be set from Terminal. `gh secret set` reads the value from a pipe, so keys go straight from Heroku (or a file) into GitHub without being shown or saved.

**One-time GitHub setup.** Paste the whole block:
```bash
for r in Attnd-Web Attnd-iOS Attnd-Android; do gh api -X PUT repos/apbeers/$r/environments/dev >/dev/null; gh api -X PUT repos/apbeers/$r/environments/production >/dev/null; done
heroku authorizations:create -d "GitHub Actions" -S | gh secret set HEROKU_API_KEY -R apbeers/Attnd-Web
gh variable set HEROKU_APP -e dev -b attnd-dev -R apbeers/Attnd-Web
gh variable set HEROKU_APP -e production -b attnd-web -R apbeers/Attnd-Web
heroku config:get API_KEY -a attnd-dev | gh secret set ATTND_API_KEY -R apbeers/Attnd-iOS
heroku config:get API_KEY -a attnd-dev | gh secret set ATTND_API_KEY -R apbeers/Attnd-Android
gh variable set ATTND_SERVER -e dev -b attnd-dev-7ab893f37020.herokuapp.com -R apbeers/Attnd-iOS
gh variable set ATTND_SERVER -e dev -b attnd-dev-7ab893f37020.herokuapp.com -R apbeers/Attnd-Android
gh variable set TESTFLIGHT_GROUP -e dev -b Internal -R apbeers/Attnd-iOS
```

**Optional:** run the live API tests against dev in CI:
```bash
for r in Attnd-iOS Attnd-Android; do gh variable set LIVE_API_TESTS -b true -R apbeers/$r; gh variable set LIVE_API_SERVER -b attnd-dev-7ab893f37020.herokuapp.com -R apbeers/$r; done
```

**Secrets that come from downloaded files.** Run each one from the folder the file is in, then delete the file:
```bash
gh secret set ASC_KEY_P8 -R apbeers/Attnd-iOS < AuthKey_XXXXXXXXXX.p8
gh secret set ASC_KEY_ID -b XXXXXXXXXX -R apbeers/Attnd-iOS
gh secret set ASC_ISSUER_ID -b 00000000-0000-0000-0000-000000000000 -R apbeers/Attnd-iOS
gh secret set GOOGLE_PLAY_SERVICE_ACCOUNT_JSON -R apbeers/Attnd-Android < github-play-publisher.json
gh secret set GOOGLE_SERVICES_JSON -R apbeers/Attnd-Android < google-services.json
```

**Check what's set** (names only; values are never shown):
```bash
for r in Attnd-Web Attnd-iOS Attnd-Android; do echo "== $r"; gh secret list -R apbeers/$r; gh variable list -R apbeers/$r; for e in dev production; do gh secret list -e $e -R apbeers/$r; gh variable list -e $e -R apbeers/$r; done; done
```

**In the browser instead:**


**GitHub repository secret** (used by every workflow in that repo):
Repo → **Settings → Secrets and variables → Actions → Secrets tab → New repository secret**. Paste the name and value, then **Add secret**. To paste a file's contents without opening it, use `pbcopy < file` in Terminal, then ⌘V in the browser.

**GitHub environment secret or variable** (used only by deploys to that environment):
Repo → **Settings → Environments → `dev` or `production`** → **Add environment secret** for anything sensitive, or **Add environment variable** for plain settings like app names and hosts.

**Heroku config var** (used by the running server):
- From the Mac, without saving anything locally: `heroku config:set -a <app> NAME="value"`.
- From a file: `NAME="$(cat file)"`, then delete the file.
- Dashboard: app → **Settings → Reveal Config Vars → Add**.

Setting a config var restarts the app (a few seconds).

**Claude cloud environment** (gives Claude API access without showing it the value):
When Claude needs one, it opens a secure form for you; never paste keys into chat. To add one yourself: claude.ai → Claude Code → environment menu → **Attnd Cloud Environment → Edit → Network secrets**.

**Password manager:** add an entry named after the key, and attach the file if there is one (`.p8`, `.jks`, `.json`).

---

### Server keys (Heroku: `attnd-web` = production, `attnd-dev` = dev)

**`SECRET_KEY`, `ATTESTATION_JWT_SECRET`**: Django signing and app-token signing.
- **Create:** generate it inline with `$(openssl rand -hex 32)`. You never need to see it.
- **Put:** both Heroku apps, with a different value on each. See step 2.
- **If lost or leaked:** set a new value. Web sessions sign out and the apps re-attest automatically.
- **Password manager:** not needed.

**`API_KEY`**: static key for dev builds, CI and simulators.
- **Create:** `openssl rand -hex 32`. Already set on both apps.
- **Put:** Heroku (both apps). Also put the **dev** value in the `ATTND_API_KEY` repo secret in Attnd-iOS and Attnd-Android, and in your local dev config (step 5). Get it with `heroku config:get API_KEY -a attnd-dev | pbcopy`.
- **If leaked:** set a new value and update the GitHub secrets. To block a leaked value permanently, add its SHA-256 to `REVOKED_API_KEY_SHA256` in `events/apis.py`.

**`OPENAI_API_KEY`**: AI event import and refresh.
- **Create:** [platform.openai.com](https://platform.openai.com) → **API keys → Create new secret key**. Name it "Attnd server", pick the project, and set permissions to "Restricted" with Model capabilities = Write. Optionally set a monthly budget under Settings → Limits.
- **Put:** both Heroku apps. Use one key for both, or create a separate "Attnd dev" key to keep spending apart.
- **If leaked:** revoke it on the same page and create a new one.

**`GOOGLE_PLACES_API_KEY`**: map pins and photos for AI-imported locations.
- **Create:** [Google Cloud console](https://console.cloud.google.com) → APIs & Services → **Library**: enable **Places API (New)** and **Geocoding API**. Then **Credentials → Create credentials → API key → Edit**:
  - **API restrictions:** restrict the key to those two APIs.
  - **Application restrictions:** None. Heroku has no fixed IP address.
- **Put:** both Heroku apps.

**`APNS_KEY_ID`, `APNS_AUTH_KEY`**: iPhone push notifications.
- **Create:** [developer.apple.com](https://developer.apple.com/account/resources/authkeys/list) → **Keys → +**, name it "Attnd APNs", tick **Apple Push Notifications service (APNs)**, then **Continue → Register → Download**. The Key ID is shown on that page.
- **Put:** both Heroku apps:
  ```bash
  heroku config:set -a attnd-web APNS_KEY_ID=ABC123DEFG APNS_AUTH_KEY="$(cat AuthKey_ABC123DEFG.p8)"
  heroku config:set -a attnd-dev APNS_KEY_ID=ABC123DEFG APNS_AUTH_KEY="$(cat AuthKey_ABC123DEFG.p8)"
  ```
- **Password manager:** required. Attach the `.p8`, because Apple only lets you download it once.
- **If lost:** revoke it and create a new key; an account can have two APNs keys at a time.

**`GOOGLE_SERVICE_ACCOUNT_JSON`, `FCM_PROJECT_ID`**: Android device check (Play Integrity) **and** Android push (FCM). One service account covers both.
1. **Pick the Google Cloud project:** the one linked to Play: **Play Console → Test and release → App integrity → Play Integrity API → Link a Cloud project**. Use the same project for Firebase in the next step.
2. **Firebase:** [console.firebase.google.com](https://console.firebase.google.com) → **Add project → choose that existing Cloud project**. Then **Project settings → General → Your apps → Add app → Android**, twice: `com.arcu.attnd` and `com.arcu.attnd.debug`. `FCM_PROJECT_ID` is the **Project ID** shown on that page.
3. **Enable the APIs:** Cloud console → APIs & Services → Library → **Google Play Integrity API** and **Firebase Cloud Messaging API**.
4. **Service account:** Cloud console → **IAM & Admin → Service accounts → Create**. Name it "attnd-server" and give it the role **Firebase Cloud Messaging API Admin**. Then open it → **Keys → Add key → JSON**, which downloads the file.
5. **Put:**
   ```bash
   heroku config:set -a attnd-web FCM_PROJECT_ID=<project-id> GOOGLE_SERVICE_ACCOUNT_JSON="$(cat attnd-server.json)"
   heroku config:set -a attnd-dev FCM_PROJECT_ID=<project-id> GOOGLE_SERVICE_ACCOUNT_JSON="$(cat attnd-server.json)"
   ```
   The server uses this one account for push too, so you don't need a separate `FCM_SERVICE_ACCOUNT_JSON`.
- **If leaked:** delete the key under the service account's **Keys** tab and add a new one.

**`ANDROID_SHA256_FINGERPRINT`**: makes `attnd.app/join/…` links open the Android app.
- **Where:** Play Console → Attnd → **Test and release → App integrity → App signing** → *App signing key certificate* → **SHA-256 certificate fingerprint**.
- **Put:** both Heroku apps. This value is public, not secret.

---

### GitHub keys

**`HEROKU_API_KEY`** (Attnd-Web repo secret): lets the pipeline deploy.
- **Create:** `heroku authorizations:create -d "GitHub Actions"`, then copy the **Token**.
- **Put:** Attnd-Web repository secret.
- **If leaked:** `heroku authorizations` lists tokens; remove one with `heroku authorizations:revoke <id>`.

**`HEROKU_APP`** (Attnd-Web environment variable): `attnd-dev` on `dev`, `attnd-web` on `production`.

**`ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_KEY_P8`** (Attnd-iOS repo secrets): signing and App Store uploads.
- **Create:** [App Store Connect](https://appstoreconnect.apple.com/access/integrations/api) → **Users and Access → Integrations → App Store Connect API → Team Keys → +**. Name it "GitHub CI" with access **Admin**, then **Generate → Download** (one time only).
  - The **Issuer ID** is shown above the list of keys.
  - The **Key ID** is in the key's row.
- **Put:** the three Attnd-iOS repository secrets. For `ASC_KEY_P8`, paste the whole file including the BEGIN/END lines (`pbcopy < AuthKey_XXXX.p8`).
- **Password manager:** required (attach the `.p8`).
- **If leaked:** revoke it on the same page and create a new one.

**`ATTND_SERVER`** (Attnd-iOS and Attnd-Android, `dev` environment variable): `attnd-dev-7ab893f37020.herokuapp.com`. On `production`, leave it unset; it defaults to `attnd.app`.

**`TESTFLIGHT_GROUP`** (Attnd-iOS, `dev` environment variable): `Internal`. It has to match your TestFlight group's name exactly.

**`GOOGLE_PLAY_SERVICE_ACCOUNT_JSON`** (Attnd-Android repo secret): uploads builds to Play.
- **Status:** this secret already exists and signs in, but its account isn't invited in Play Console.
- **Find the account's email:** GitHub can't show you the secret. Look under Cloud console → IAM & Admin → **Service accounts**. If you can't tell which account it is, create a new one as below and replace the secret.
- **Create:** Cloud console → APIs & Services → Library → enable **Google Play Android Developer API**. Then IAM & Admin → **Service accounts → Create**, name it "github-play-publisher" (no roles needed), then **Keys → Add key → JSON**.
- **Grant access:** **Play Console → Users and permissions → Invite new users** → paste the service account's email → **App permissions → Add app → Attnd** → tick **Release apps to testing tracks**, **Release to production, exclude devices, and use Play App Signing**, and **Manage testing tracks and edit tester lists** → **Invite user**.
- **Put:** the Attnd-Android repository secret.

**`ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`** (Attnd-Android repo secrets): the upload key.
- **Status:** ✅ these already exist and work; CI built a signed bundle.
- **Password manager:** **required.** Attach the `.jks` and save all three values. Losing this key means asking Google support to reset it, which takes days.
- **If you ever need to re-add it:** `base64 -i upload.jks | pbcopy`, then paste into `ANDROID_KEYSTORE_BASE64`.

**`MAPS_API_KEY`** (Attnd-Android repo secret): Google Maps in the Android app.
- **Status:** ✅ it already exists.
- **Restrict it:** Cloud console → Credentials → that key:
  - **API restrictions:** Maps SDK for Android.
  - **Application restrictions:** Android apps. Add `com.arcu.attnd` with the **App signing** SHA-1 and the **Upload** SHA-1, both from Play Console → App integrity. Add `com.arcu.attnd.debug` with your debug SHA-1.

**`GOOGLE_SERVICES_JSON`** (Attnd-Android repo secret): Firebase configuration for the Android app (push).
- **Where:** Firebase → Project settings → General → Your apps → Android app → **google-services.json** download. Both Android apps are in the same file.
- **Put:** `pbcopy < google-services.json`, then paste into the Attnd-Android repository secret. This file isn't truly secret, but keeping it out of git keeps the repo clean.

**`ATTND_API_KEY`** (Attnd-iOS and Attnd-Android repo secrets): the **dev** app's `API_KEY`, used only for the optional live API tests. **Replace the old value**, which is likely the leaked production key. To turn the live tests on, also add the repo variables `LIVE_API_TESTS=true` and `LIVE_API_SERVER=attnd-dev-7ab893f37020.herokuapp.com`.

---

### Claude cloud environment keys

| Network secret | Host | Status |
|---|---|---|
| Heroku API token ("Heroku API token (Claude)") | `api.heroku.com` | ✅ saved; Claude can manage both Heroku apps |

Revoke it any time with `heroku authorizations` / `heroku authorizations:revoke <id>`, or from the environment's settings.
