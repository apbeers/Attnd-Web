# Deployment pipelines

All three repositories follow the same flow:

| Branch | Web (attnd-web) | iOS (attnd-ios) | Android (attnd-android) |
|---|---|---|---|
| any push / PR | tests (Postgres) | build + unit tests | unit tests, lint, debug build |
| `develop` | → Heroku **dev** app | → **TestFlight** internal testers, using the dev server | → Play **internal testing**, using the dev server |
| `main` (Android: `master`) | → Heroku **production** | → **App Review** (auto-release when approved) | → Play **production** |

To ship: merge a feature branch into `develop`, wait for green, and test the TestFlight / internal build on your phone. Then merge `develop` into `main` (Android: `master`).

The pipelines use only GitHub's own actions and the vendors' official APIs: the Heroku git endpoint, `xcodebuild`, the App Store Connect API, and the Google Play Developer API. No third-party actions, fastlane or Gradle publishing plugins are involved.

---

## One-time setup

In each repo, open **Settings → Environments** and create two environments named `dev` and `production`. Put each setting below in the environment shown. The deploy jobs read from them, and the environment pages are also where you can require a manual approval before production deploys.

### 1. Web (attnd-web)

1. Create the dev app and give it the same add-ons as production:
   ```bash
   heroku create attnd-dev
   heroku addons:create heroku-postgresql:essential-0 -a attnd-dev
   heroku addons:create scheduler:standard -a attnd-dev
   heroku config:set -a attnd-dev SECRET_KEY=$(openssl rand -hex 32) API_KEY=$(openssl rand -hex 32) \
     OPENAI_API_KEY=… ATTESTATION_JWT_SECRET=$(openssl rand -hex 32) \
     APPLE_APP_BUNDLE_ID=com.arcu.Attnd APPLE_TEAM_ID=GQQX4L6TE2 ANDROID_PACKAGE_NAME=com.arcu.attnd \
     GOOGLE_SERVICE_ACCOUNT_JSON="$(cat play-integrity-sa.json)"
   ```
   Builds from `develop` are signed by Apple and Google just like production builds, so attestation works against the dev app as well.
2. Set up GitHub:

   | Where | Name | Value |
   |---|---|---|
   | Repository secret | `HEROKU_API_KEY` | `heroku authorizations:create -d "GitHub Actions"` |
   | Environment `dev` variable | `HEROKU_APP` | `attnd-dev` |
   | Environment `production` variable | `HEROKU_APP` | `attnd-web` |

3. Production's own setup (key rotation, Scheduler, push credentials) is in `ROLLOUT.md`.

### 2. iOS (attnd-ios)

1. In App Store Connect → Users and Access → Integrations → **App Store Connect API**, create a **Team key** with the **Admin** role. The Admin role is what allows Xcode to create and manage signing certificates in the cloud.
2. In App Store Connect → TestFlight, create an internal group (for example "Internal") and add yourself to it.
3. Turn on the **Push Notifications** capability for `com.arcu.Attnd` in the developer portal.
4. Set up GitHub:

   | Where | Name | Value |
   |---|---|---|
   | Repository secret | `ASC_KEY_ID` | Key ID |
   | Repository secret | `ASC_ISSUER_ID` | Issuer ID |
   | Repository secret | `ASC_KEY_P8` | Full contents of `AuthKey_XXXX.p8` |
   | Repository secret | `ATTND_API_KEY` | (optional) the **dev** app's `API_KEY`; turns on the live API tests in CI |
   | Environment `dev` variable | `ATTND_SERVER` | `attnd-dev.herokuapp.com` |
   | Environment `dev` variable | `TESTFLIGHT_GROUP` | `Internal` |
   | Environment `production` variable | `WHATS_NEW` | (optional) release notes for the next App Store release |

Versions are set automatically: `VERSION` + run number gives e.g. `2.0.57`. Edit `VERSION` to start a new major or minor line.

**Xcode Cloud** would also work, but the GitHub pipeline keeps all three apps in one place, so it isn't needed.

### 3. Android (attnd-android)

1. In Google Cloud, create a service account and download a JSON key for it. In **Play Console → Users and permissions**, invite that service account's email with the release permissions for Attnd: *Release apps to testing tracks*, *Release to production*, and *Manage testing tracks*.
2. Use the existing **upload keystore**. Play App Signing holds the real signing key.
3. Add internal testers in Play Console → Testing → Internal testing, then open the opt-in link on your phone once.
4. Set up GitHub:

   | Where | Name | Value |
   |---|---|---|
   | Repository secret | `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` | Service account JSON |
   | Repository secret | `ANDROID_KEYSTORE_BASE64` | `base64 -i upload.jks` |
   | Repository secret | `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD` | From `signing.properties` |
   | Repository secret | `MAPS_API_KEY` | Google Maps Android key |
   | Repository secret | `GOOGLE_SERVICES_JSON` | Contents of `google-services.json` (for FCM push) |
   | Repository secret | `ATTND_API_KEY` | (optional) the dev app's `API_KEY`, for the live API tests |
   | Environment `dev` variable | `ATTND_SERVER` | `attnd-dev.herokuapp.com` |
   | Environment `production` variable | `PLAY_ROLLOUT` | (optional) e.g. `0.2` for a 20% staged rollout |
   | Environment `production` variable | `RELEASE_NOTES` | (optional) |

Other ways to automate Android releases, and why this setup doesn't use them:
- **Firebase App Distribution:** it doesn't publish to Play testing tracks.
- **Google Cloud Build:** it would split the pipelines across two systems.
- **Gradle Play Publisher:** it's a third-party plugin.

---

## Keeping dependencies current

- **Python (web):** the **Upgrade dependencies** workflow runs every Monday and can also be started by hand from the Actions tab. It bumps every pin to the latest release, relocks, runs the tests, and opens a PR into `develop`. You can do the same locally with `uv run python scripts/upgrade_deps.py`, or add `--check` to only list what's out of date.
- **GitHub Actions (all repos) and Gradle (Android):** Dependabot opens weekly PRs into `develop`.
- **Front end:** there is nothing to upgrade. The site and console use our own CSS and JS, with no frameworks.
