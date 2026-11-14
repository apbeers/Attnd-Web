# Attnd setup checklist

Do these steps once, in order. Each step says which values go where. For any single key, see the **Key reference** at the end. It explains where to create the key and exactly where it goes.

## Progress

| Step | Status |
|---|---|
| 0. GitHub | ✅ Done. Default branch is `main` in all three repos (verified). The environments and secrets get verified by the first deploy run; a missing one fails with a message naming it. |
| 1. Rotate API key | ✅ Done. Run the check under step 1 to confirm. |
| 2. Heroku | ⏳ ✅ Both apps are configured: secrets, OpenAI and Places keys, APNs key and Google key, Scheduler jobs and GitHub deploy settings. **Still to do:** add Cloudinary on both apps so uploads survive restarts, and set `CONTACT_EMAIL` on production. |
| 3. Apple | ⏳ ✅ App ID capabilities and App Attest fixed. ✅ 3a: APNs key `7Q3H2446BU` (Sandbox & Production) is set on both Heroku apps. ✅ 3c: TestFlight group. ❌ 3b: the CI key check (iOS run 78) reports that the `ASC_ISSUER_ID` secret is not the Issuer ID UUID. Re-set it with the command under 3b, then re-run the iOS workflow. |
| 4. Google / Android | ✅ 4a Firebase and server key, 4b Play publisher, 4c fingerprints (both Heroku apps serve them in `assetlinks.json`), Maps and Places keys restricted, keystore backed up. ✅ The first Play internal-testing upload succeeded (Android run 14). 4d (production lock-down) waits for the first release. |
| 5. Local dev keys | ⬜ |
| 6. Check it all works | ⏳ ✅ **Web dev works:** pushes to `develop` deploy to attnd-dev automatically. Health is OK with the database connected, migrations ran, and the console and API respond. ✅ **Android:** `develop` publishes to Play internal testing. Waiting on: iOS (3b `ASC_ISSUER_ID`) and the first release to `main`. |

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
1. ✅ **Required before the first release to `main`:** set real `SECRET_KEY` and `ATTESTATION_JWT_SECRET` values on production (done and verified). Production previously had neither. It has been running on a hardcoded fallback, and the new code refuses to start without a real key. Run this once:
   ```bash
   heroku config:set -a attnd-web SECRET_KEY=$(openssl rand -hex 32) ATTESTATION_JWT_SECRET=$(openssl rand -hex 32)
   ```
   This signs everyone out of the web console once. The apps re-attest automatically.
2. ✅ Scheduler job `python manage.py run_scheduled_tasks` (every 10 minutes) is added. It fails harmlessly until the first release to `main`, because the command doesn't exist in the old code.
3. ✅ The OpenAI and Places keys are already set on production.
4. **Play Integrity isn't configured on production** (there's no `GOOGLE_SERVICE_ACCOUNT_JSON`), so Android builds from the Play Store can't attest. Old Android builds fell back to the static key, which is now rotated, so the current Play Store version can't reach the API until this is set (step 4) and a new build ships. iOS builds use App Attest, which is configured.

5. **File uploads need permanent storage.** Production has no `CLOUDINARY_URL`, so flyers and location photos uploaded in the console are saved on Heroku's temporary disk and **lost on every restart or deploy**. Add Cloudinary's free plan to both apps:
   ```bash
   heroku addons:create cloudinary:starter -a attnd-web
   heroku addons:create cloudinary:starter -a attnd-dev
   ```
   That sets `CLOUDINARY_URL`, and the server switches to it automatically.

### Dev (`attnd-dev`) — created by Claude
- ✅ App `attnd-dev` at `https://attnd-dev-7ab893f37020.herokuapp.com/`. Heroku now gives new apps a random hostname, so use this exact host wherever this guide says `ATTND_SERVER`.
- ✅ Postgres (essential-0), Scheduler, and generated `SECRET_KEY`, `API_KEY` and `ATTESTATION_JWT_SECRET`.
- ✅ The OpenAI and Places keys have been copied from production. For reference, the command was:
  ```bash
  heroku config:set -a attnd-dev OPENAI_API_KEY="$(heroku config:get OPENAI_API_KEY -a attnd-web)" GOOGLE_PLACES_API_KEY="$(heroku config:get GOOGLE_PLACES_API_KEY -a attnd-web)"
  ```
- ✅ The Scheduler job is added on `attnd-dev`.
- The dev `API_KEY` is what goes in the `ATTND_API_KEY` GitHub secrets and in your local dev config. These commands send it straight to GitHub without showing it:
  ```bash
  heroku config:get API_KEY -a attnd-dev | gh secret set ATTND_API_KEY -R apbeers/Attnd-iOS
  heroku config:get API_KEY -a attnd-dev | gh secret set ATTND_API_KEY -R apbeers/Attnd-Android
  ```
  ✅ Done: both repos have the dev key as `ATTND_API_KEY`. To see the key itself (for example for local dev config), run `heroku config:get API_KEY -a attnd-dev`.

### Let GitHub deploy to Heroku
```bash
heroku authorizations:create -d "GitHub Actions"
```
Copy the token it prints into **GH-web secret `HEROKU_API_KEY`** and into your password manager. GitHub needs its own token: the one in the Claude environment can't be copied out.
| Where | Type | Name | Value |
|---|---|---|---|
| Attnd-Web → Settings → Secrets and variables → Actions | **Repository secret** (not an environment secret) | `HEROKU_API_KEY` | that token |
| Attnd-Web → Settings → Environments → `dev` | **Environment variable** (not a secret; it's just an app name) | `HEROKU_APP` | `attnd-dev` |
| Attnd-Web → Settings → Environments → `production` | **Environment variable** | `HEROKU_APP` | `attnd-web` |

✅ Done (with `gh`):
```bash
heroku authorizations:create -d "GitHub Actions" -S | gh secret set HEROKU_API_KEY -R apbeers/Attnd-Web
gh variable set HEROKU_APP -e dev -b attnd-dev -R apbeers/Attnd-Web
gh variable set HEROKU_APP -e production -b attnd-web -R apbeers/Attnd-Web
```

**Rule of thumb:** anything that grants access is a **secret**. Names, hosts and settings are **variables**. Secrets used by every deploy go at repository level; values that differ between dev and production go on the environment.

## 3. Apple (30 min)

### 3a. Push notifications (APNs)
1. ✅ Go to [developer.apple.com](https://developer.apple.com) → Certificates, IDs & Profiles → **Identifiers** → `com.arcu.Attnd`. Check that **Push Notifications**, **App Attest** and **Associated Domains** are enabled. All three are.
   - Push shows **"Certificates (0)"**. That's expected. Attnd sends pushes with a token-based APNs **key** (step 2), not certificates, so don't click **Configure**.
   - Leave **Broadcast Capability** unticked. It's for live-broadcast push channels, which Attnd doesn't use.
2. Go to **Keys → +**, name the key "Attnd APNs", tick **Apple Push Notifications service (APNs)**, and click **Configure**. Set:
   - **Environment:** **Sandbox & Production**. This can't be changed after saving. TestFlight and App Store builds use Production; Sandbox is only for builds run from Xcode.
   - **Key Restriction:** **Team Scoped (All Topics)**.

   Save, then **Continue → Register → Download** `AuthKey_XXXXXXXXXX.p8`. Apple only lets you download it once, so put it in your password manager right away. The **Key ID** is shown on the key's page.
3. ✅ Set it on **both** Heroku apps (done, key ID `7Q3H2446BU`):
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

### 3b-2. ✅ App Clip App ID
The App Clip (`com.arcu.Attnd.clip`) ships inside the app and now uses App Attest too. In [Identifiers](https://developer.apple.com/account/resources/identifiers/list), open **com.arcu.Attnd.clip** and tick **App Attest**. Leave the rest as is, then **Save**.

### 3c. TestFlight
1. ✅ The internal group already exists: **App Store Connect Users**, with you as a tester and TestFlight installed on your iPhone. New CI builds get added to this group.
2. Set the pipeline variables:
   ```bash
   gh variable set TESTFLIGHT_GROUP -e dev -b "App Store Connect Users" -R apbeers/Attnd-iOS
   gh variable set ATTND_SERVER -e dev -b attnd-dev-7ab893f37020.herokuapp.com -R apbeers/Attnd-iOS
   gh variable set ATTND_SERVER -e dev -b attnd-dev-7ab893f37020.herokuapp.com -R apbeers/Attnd-Android
   ```
3. Add to GitHub:

| Where | Name | Value |
|---|---|---|
| GH-ios env `dev` variable | `ATTND_SERVER` | `attnd-dev-7ab893f37020.herokuapp.com` |
| GH-ios env `dev` variable | `TESTFLIGHT_GROUP` | `App Store Connect Users` |
| GH-ios secret + variables (optional) | `ATTND_API_KEY` secret = attnd-dev's `API_KEY`; variables `LIVE_API_TESTS=true`, `LIVE_API_SERVER=attnd-dev-7ab893f37020.herokuapp.com` | turns on the live API tests in CI |
| GH-ios env `production` variable (optional) | `WHATS_NEW` | release notes for the next App Store version |

### 3d. Before the first App Store release from CI

Attnd is **already approved and live** on the App Store and Google Play, so CI ships **updates**; there's no first-time app setup. Version numbers continue upward automatically: iOS goes from `1.0.44` to `2.0.<run>`, and Android's version code goes from 223/224 to `<run>000`. Each App Store update still goes through normal App Review.

The new release also changes behaviour reviewers and users will notice:
- **iOS** now requires **iOS 26 or later**.
- **Android** now requires **Android 15 or later**, so older phones stop getting updates.
- Push notifications are new and **opt-in** (asked for when the user turns them on in Options).

- ✅ The privacy policy at `/privacy/` is rewritten to match what the apps and site actually collect. It goes live with the next production release. Set the contact email it shows:
  ```bash
  heroku config:set -a attnd-web CONTACT_EMAIL=apbeers@icloud.com
  ```
- **Update the App Store privacy answers.** In App Store Connect → Attnd → **App Privacy → Edit**:
  - **Identifiers → Device ID:** collected for *App Functionality*, **not** linked to the user's identity, **not** used for tracking. This covers the random app ID and the push token.
  - **Location:** answer **not collected**. It's sent only for a nearby-events search and isn't stored, which is how Apple defines data that isn't collected.
  - **Everything else:** not collected. That includes contact info, usage data, diagnostics and identifiers used for tracking.
- **Update the Google Play Data safety form.** Play Console → Attnd → **App content → Data safety**:
  - **Device or other IDs:** collected, not shared, used for *App functionality*, processed ephemerally = No, required.
  - **Approximate location:** the same, but optional, and processed ephemerally = Yes.
  - **Data is encrypted in transit:** Yes.
  - **Users can request data deletion:** Yes, through the contact on the privacy page.
- Optional: refresh the App Store screenshots and description to show the new layouts and push updates. Set the release notes with `gh variable set WHATS_NEW -e production -b "…" -R apbeers/Attnd-iOS` (Android: `RELEASE_NOTES` on Attnd-Android).

## 4. Google / Android (40 min)

### 4a. Firebase (push notifications)
1. ✅ The Firebase project **Attnd** already exists (Blaze plan) with the **Attnd - Android** app (`com.arcu.attnd`). Reuse it. Its old keys get rotated in step 4 below.
2. ✅ Both Android apps are in Firebase. Add the debug app: **Project Overview → + Add app → Android**, package `com.arcu.attnd.debug`, nickname "Attnd - Android (debug)". Skip the SDK steps, because the build already supports Firebase. Then download the updated `google-services.json` from **Project settings → General**; it now lists both apps. Store it:
   ```bash
   gh secret set GOOGLE_SERVICES_JSON -R apbeers/Attnd-Android < /Users/andrewbeers/Downloads/google-services.json
   ```
3. ✅ Put the file at `Attnd-Android/app/google-services.json` for local builds. It's gitignored.
4. **Give the server one Google key that covers both Android push and the Play Integrity device check.**
   1. ✅ **Check that Play Integrity uses this project.** It's linked to the **Attnd** Cloud project (number 271167704653), and all 7 integrity responses are turned on. The server ignores the extra ones. Go to Play Console → Attnd → **Protected with Play** (the shield icon). On the **Play Integrity API** row, click **Manage**. The linked Google Cloud project shown there should be the Firebase **Attnd** project. If it's a different project, or none, link the Attnd project.
      - **Automatic protection** (1 of 1 active) is fine to leave on. It blocks unofficial installs, which fits the server's rules.
      - The other optional Play Integrity services, such as the app access risk and Play Protect verdicts, aren't used by the server. You don't need to turn them on.
   2. ✅ **Turn on the two APIs.** Both are enabled. Open each link, choose the **Attnd** project at the top, and click **Enable**. If it says *Manage*, the API is already on.
      - https://console.cloud.google.com/apis/library/playintegrity.googleapis.com
      - https://console.cloud.google.com/apis/library/fcm.googleapis.com
   3. **Create a new key.** Firebase → **Project settings → Service accounts → Generate new private key**. This downloads `attnd-…-firebase-adminsdk-….json` to Downloads. The Firebase admin account can send pushes, and because it belongs to the linked project it can also decode Play Integrity tokens.
   4. ✅ **Find the Project ID.** It's `attnd-df28e`. It's on Firebase → **Project settings → General → Project ID**.
   5. ✅ **Put the key on both Heroku apps.** Done and verified: `FCM_PROJECT_ID` and `GOOGLE_SERVICE_ACCOUNT_JSON` are set, and both releases succeeded. Swap in your Project ID:
      ```bash
      heroku config:set -a attnd-web FCM_PROJECT_ID=attnd-df28e GOOGLE_SERVICE_ACCOUNT_JSON="$(cat /Users/andrewbeers/Downloads/attnd-df28e-firebase-adminsdk-fbsvc-2af30fbbb4.json)"
      heroku config:set -a attnd-dev FCM_PROJECT_ID=attnd-df28e GOOGLE_SERVICE_ACCOUNT_JSON="$(cat /Users/andrewbeers/Downloads/attnd-df28e-firebase-adminsdk-fbsvc-2af30fbbb4.json)"
      ```
   6. **Rotate out the old keys.** Go to https://console.cloud.google.com/iam-admin/serviceaccounts (Attnd project) → the **firebase-adminsdk** account → **Keys**. Delete every key except the one created today. Do the same on any other service account that has old keys you don't use.
   7. Back up the JSON in your password manager, then delete it:
      ```bash
      rm /Users/andrewbeers/Downloads/attnd-df28e-firebase-adminsdk-fbsvc-2af30fbbb4.json
      ```

### 4b. Google Play publishing from CI
1. In [Google Cloud console](https://console.cloud.google.com), using the same project, enable the **Google Play Android Developer API**.
2. ✅ Service account **github-play-publisher** created in the Attnd project, with no roles (Play access is granted in Play Console), and its JSON key downloaded. Store it in GitHub, then remove the file:
   ```bash
   gh secret set GOOGLE_PLAY_SERVICE_ACCOUNT_JSON -R apbeers/Attnd-Android < /Users/andrewbeers/Downloads/attnd-df28e-51b6a4e6e951.json
   rm /Users/andrewbeers/Downloads/attnd-df28e-51b6a4e6e951.json
   ```
3. ✅ In **Play Console → Users and permissions → Invite new users**, enter that service account's email. Under App permissions → Attnd, grant **Release apps to testing tracks**, **Release to production**, and **Manage testing tracks**.
4. ✅ In Play Console → Attnd → **Testing → Internal testing → Testers**, add yourself, then open the **opt-in link** on your Android phone once.

### 4c. Signing fingerprints and the Maps key

Android apps are identified by their package name **plus the certificate they're signed with**. Two different certificates matter:
- **Upload key:** your `.jks`. CI signs bundles with it before uploading.
- **App signing key:** Google holds this one and re-signs every build Play delivers to phones. It's what's on users' devices.

Three things need these fingerprints.

**1. Look up the fingerprints.** Play Console → Attnd → **Protected with Play** (**Test and release → App integrity** opens the same page). Expand the **Play Store protection** row with its ▾ arrow, then open **Play App Signing** (*Manage* / *Settings*). That page lists the app signing key and upload key certificates with their fingerprints. Note four values:
- **App signing key certificate:** SHA-256 and SHA-1
- **Upload key certificate:** SHA-1 (and SHA-256, for reference)

**2. ✅ App links and the signing-certificate pin (server).** Put the **App signing key SHA-256** on both Heroku apps:
```bash
heroku config:set -a attnd-web ANDROID_SHA256_FINGERPRINT=AA:BB:CC:...
heroku config:set -a attnd-dev ANDROID_SHA256_FINGERPRINT=AA:BB:CC:...
```
This one value does two jobs:
- `https://attnd.app/.well-known/assetlinks.json` lists it, so `attnd.app/join/<code>` links open the app directly instead of the browser.
- The device check rejects any Android app not signed with this certificate, even if its package name matches.

Check it afterwards: `curl -s https://attnd.app/.well-known/assetlinks.json` should show your fingerprint.

**3. ✅ Restrict the Maps API key.** It's restricted to Android apps (`com.arcu.attnd` ×2 and `com.arcu.attnd.debug`) and the Maps SDKs. Right now anyone who extracts the key from the app could use it and bill your account. Restricting it means only your signed app can use it.
1. Find the key: [Cloud console → APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials). Its value matches the `MAPS_API_KEY` in your `local.properties`. Check the project switcher at the top, because it may be in an older project.
2. Make sure **Maps SDK for Android** is enabled in that project (APIs & Services → Library).
3. Open the key:
   - **Application restrictions → Android apps → Add**, one row per signing certificate:

     | Package | Fingerprint | Used by | Where to get the SHA-1 |
     |---|---|---|---|
     | `com.arcu.attnd` | **App signing key** SHA-1 | Everything installed from Google Play, including internal testing | Play Console → **Protected with Play** → ▾ **Play Store protection** → **Play App Signing** → *App signing key certificate* → **SHA-1 certificate fingerprint** |
     | `com.arcu.attnd` | **Upload key** SHA-1 | Release builds signed with your `.jks` and installed directly (not through Play) | The same Play page → *Upload key certificate* → **SHA-1**. Or from the keystore (it asks for the store password): `keytool -list -v -keystore /path/to/upload.jks -alias YOUR_KEY_ALIAS \| grep SHA1` |
     | `com.arcu.attnd.debug` | **Debug** SHA-1 | Builds run from Android Studio on your Mac | `keytool -list -v -keystore ~/.android/debug.keystore -alias androiddebugkey -storepass android -keypass android \| grep SHA1`. Android Studio creates this keystore on the first debug build. |

     If `keytool` says *"Unable to locate a Java Runtime"*, your Mac has no system Java. Use the copy bundled with Android Studio instead:
     ```bash
     "/Applications/Android Studio.app/Contents/jbr/Contents/Home/bin/keytool" -list -v -keystore ~/.android/debug.keystore -alias androiddebugkey -storepass android -keypass android | grep SHA1
     ```

     A shortcut for the last two: in the Attnd-Android folder, run `./gradlew signingReport`. It prints the SHA-1 for the debug build, and for the release build when `signing.properties` is present.

     The debug SHA-1 is specific to each Mac. If you build on another computer, add that computer's debug SHA-1 as another `com.arcu.attnd.debug` row.
   - **API restrictions → Restrict key → Maps SDK for Android**.
   - **Save**. It can take a few minutes to apply.
4. Test: open the map in the next internal-testing build. A grey map with no streets means a fingerprint or package is wrong.

**4. ✅ Keystore backup (required).** The upload keystore secrets are already in GitHub and work: CI built a signed bundle. GitHub won't give them back, though, so keep your own copy. This command finds your `signing.properties` with Spotlight and copies one bundle to the clipboard: the passwords and alias, plus the keystore file as text. Paste it into a new secure note in your password manager:
```bash
P=$(mdfind -name signing.properties | grep -i attnd-android | head -1); S=$(grep '^STORE_FILE=' "$P" | cut -d= -f2-); case "$S" in /*) J="$S";; *) J="$(dirname "$P")/$S";; esac; { echo "Attnd Android upload keystore - backed up $(date +%F)"; echo; cat "$P"; echo; echo "Keystore file: $(basename "$J")"; echo "To restore: copy everything below this line, then run: pbpaste | base64 -D > upload.jks"; base64 -i "$J"; } | pbcopy && echo "Copied $(basename "$J") + signing.properties - paste into a secure note"
```
If it prints nothing useful, Spotlight didn't find the file. Run it from your Attnd-Android folder with `P=signing.properties` in place of the `mdfind …` part.

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

## 4d. Lock the production API to genuine apps (after the first release)

The server now only issues API access to:
- **iPhone:** the official Attnd app (App Store or TestFlight), verified by Apple App Attest on a genuine iPhone.
- **Android:** the official Attnd app, unmodified as Google Play distributed it, installed from Google Play, signed with our key, on a device that passes Play Integrity (not rooted, not an emulator, not tampered with).

The static API key is the remaining way around these checks, so turn it off on production **once the new app versions are live in both stores**:
```bash
heroku config:set -a attnd-web STATIC_API_KEY_ENABLED=0
```
Keep it on for `attnd-dev`: simulators, Xcode/Android Studio builds and CI need it there.

✅ The Android signing-certificate pin is already done. It's the same `ANDROID_SHA256_FINGERPRINT` you set in 4c step 2, and the server uses that one value for app links and for the pin.

Limits to know about:
- App Attest proves the app is genuine and unmodified, and that it runs on real Apple hardware. It isn't a full jailbreak detector.
- Play Integrity's device check is stronger.
- Someone with a genuine device could still copy a short-lived access token (valid up to 24 hours) and replay it. This stops scraping by bots, modified apps and emulators. It does not stop a determined person using a real phone.

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
gh variable set TESTFLIGHT_GROUP -e dev -b "App Store Connect Users" -R apbeers/Attnd-iOS
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

**`GOOGLE_PLACES_API_KEY`**: map pins and photos for AI-imported locations. ✅ Restricted to Geocoding API and Places API (New); application restriction **None** (correct for a server).
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
1. **Pick the Google Cloud project:** the one linked to Play: **Play Console → **Protected with Play** / App integrity → Play Integrity API → Link a Cloud project**. Use the same project for Firebase in the next step.
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

**`TESTFLIGHT_GROUP`** (Attnd-iOS, `dev` environment variable): `App Store Connect Users`. It has to match your TestFlight group's name exactly.

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
