# Attnd Web — Backend & API

Django 6.1 backend serving the REST API consumed by the iOS and Android apps, plus a web console for event organizers. Deployed on Heroku.


## Branching (initial development)

- `main` = production (default branch), `develop` = dev/beta. Pushing to `develop` deploys to the dev server / TestFlight / Play internal testing; pushing to `main` deploys to production.
- **Prefer giving the user a command to paste** (`heroku`, `gh`, etc.) over click-by-click instructions. The user has the Heroku and GitHub CLIs installed.
- **ROADMAP.md lists agreed future work** (things needed before release, and later features). Add items there when the user parks an idea; remove them once built.
- **SETUP.md is the living setup checklist.** When the user reports completing a setup step, record it in SETUP.md (progress table and the step itself) instead of repeating instructions in chat. **At the start of each working session, remind the user of the unchecked "Open owner tasks" in SETUP.md.**
- **While there are no real users yet, commit and push straight to `develop`** — no feature branches or PRs needed. Merge `develop` → `main` to release. Revisit this once the apps have users.

## System Overview

Attnd is an event attendance app. This repo is the single backend for both mobile clients:

- **[attnd-ios](https://github.com/apbeers/Attnd-iOS)** — SwiftUI app
- **[attnd-android](https://github.com/apbeers/Attnd-Android)** — Kotlin/Compose app

Current mobile apps use **API v2** (`/api/v2/`, see `API_V2.md`): one `bundle/` request returns the event, locations, schedule, notifications and the server-resolved **display config**. `/api/v1/events/` stays for older app builds (shapes unchanged; drafts, hidden rows and location-less items are filtered out for them). There is no Django REST Framework — all API views are plain Django views returning `JsonResponse`.

## iPhone Duo

iPhone Duo is brand new (released late Oct 2026). For anything Duo-related in any of the three repos, always check online for the latest Apple docs and App Store Connect behaviour instead of relying on model training data (see attnd-ios `CLAUDE.md` → iPhone Duo for the current facts).

## Cross-repo coordination (this repo leads)

attnd-web is the coordinating repo for all three. Any feature that touches the API starts here. Follow `API_CHANGES.md`:
1. Add a tracker row and a log entry: the API change, additive or breaking, and exactly what iOS and Android must do.
2. Server first: implement it, update `API_V2.md`, refresh the snapshot with `UPDATE_CONTRACT=1 uv run python manage.py test events.tests_contract`, and add tests.
3. Then implement the app parts in attnd-ios and attnd-android (in the same session when all three repos are present). Reference the entry ID in commit messages and tick the tracker.

- **Never break installed apps.** Only additive changes go into v2. `events/tests_contract.py` fails CI on a removed or renamed key, endpoint or push type, or a changed type. A breaking change means a new `/api/v3/` alongside v2. Retire an old version only once `manage.py api_usage` (based on `Device.api_version`) shows its devices are gone.
- The mobile repos have no copy of the contract. `API_V2.md` and `API_CHANGES.md` live only here.

## Platform model (the "full vision")

1. **AI import** — an organizer (or anonymous visitor on the homepage) gives URLs / a PDF or image / pasted text. An `IngestJob` crawls + extracts with OpenAI structured output and creates the event (`scrape/jobs.py`, `scrape/service.py`).
2. **Manual management** — the organizer console (`/console/`) edits everything. Any field a person edits is added to that row's `locked_fields`. An event has an owner plus a team (`EventMember`: admins manage the team and settings, contributors edit content); invites by email work before the person has an account (`EventInvite`, claimed on sign-up/sign-in).
3. **Website watching + AI refresh** — `scrape/watch.py` fingerprints the event's pages (conditional GET + hash of visible text/JSON-LD; every 10 min during the event, hourly the week before, 6-hourly earlier) and runs the AI only on pages that changed, with a minimum gap and daily cap (`WATCH_MIN_AI_GAP_MINUTES`, `WATCH_MAX_AI_RUNS_PER_DAY`). `update_mode` review/minor/auto decides what applies itself (removals and locked fields always wait). Manual "Check for updates now" re-crawls everything. The AI re-reads the sources *with the current data* and returns its view; `scrape/diff.py` turns that into a `ChangeSet` of field-level `Change`s. Locked fields become conflicts (unticked), empty AI values never blank data, removals need cited evidence.
4. **Review** — the organizer accepts per change / per field; `apply_changeset` applies atomically, snapshots an `EventRevision` first (rollback from History), and bumps `content_version` once.
5. **Apps follow** — every content change bumps `Event.content_version` (signals in `events/signals.py`); a silent `content_updated` push (to every device with a token) tells apps to refetch `bundle/` with `If-None-Match`; they also poll slowly.
6. **Adaptive layout** — `events/display.py` picks a preset (intimate / standard / festival) from size + spread; organizers override any key in **App layout**; the resolved dict ships in the bundle.

## Claude cloud environment ("Attnd Cloud Environment")

- A Heroku API token is stored as a network secret for `api.heroku.com`. Call the Platform API directly (`Accept: application/vnd.heroku+json; version=3`); the proxy injects the token. There is no Heroku CLI login.
- Allowed hosts: `api.heroku.com`, `attnd.app` (production) and `dev.attnd.app` (dev app `attnd-dev`; its Heroku hostname `attnd-dev-7ab893f37020.herokuapp.com` is allowed too).
- Heroku apps: `attnd-web` = production (attnd.app), `attnd-dev` = dev (deployed from `develop`).
- Changing **production** config needs the user's go-ahead. Full key and setup checklist: `SETUP.md`.

## Dependency policy

Avoid third-party libraries. Use Django and the Python standard library first; the organizer console uses
its own CSS (`console/static/console/console.css`), inline SVG icons (`console_tags.icon`) and vanilla JS
(`console.js`) — no CSS/JS frameworks or CDNs. Existing server dependencies are kept where the standard
library has no equivalent (Postgres driver, Pillow, OpenAI SDK, Google auth, HTTP/2 for APNs via
`httpx[http2]`, CBOR for App Attest and passkeys; `cryptography`, installed with google-auth, verifies App Attest and passkey signatures). Stripe is the expected future exception. The public site (`home/`, `join/`) uses one hand-written
stylesheet (`home/static/home/site.css`) — no vendored frameworks, icon fonts or web fonts.
Upgrade Python packages with `uv run python scripts/upgrade_deps.py` (also runs weekly in CI).

## Tech Stack

- **Python 3.15** managed with `uv` (`.python-version`; Heroku reads the same file)
- **Django 6.1** (no DRF)
- **PostgreSQL 18 everywhere**: Heroku Postgres (essential-0), CI (`postgres:18` service) and local dev/tests (`scripts/dev_db.sh`, Docker). No SQLite: without `DATABASE_URL` the settings point at the local `postgres://attnd:attnd@localhost:5432/attnd`
- **Cloudinary** for media storage in production (falls back to local filesystem)
- **Whitenoise** for static files
- **Gunicorn** (gthread, 1 worker × 4 threads) as WSGI server
- **Heroku** (Cedar generation, `heroku-26` stack, Basic dynos) deployment via `Procfile`; Docker image also available

## Project Structure

```
attnd/          # Django project: settings, root urls.py, wsgi/asgi
events/         # Core models + entire REST API (apis.py, urls.py)
console/        # Organizer console (/console/): AI create, editors, review, layout, push, history, team
home/           # Marketing/landing pages
join/           # /join/<code>/ redirect + AASA & assetlinks well-known files
scrape/         # AI import/refresh: crawl + OpenAI extraction, jobs, diff/changesets
attestation/    # Apple App Attest + Google Play Integrity + JWT issuance
static/         # Project-level static assets
public/         # Files served at root by Whitenoise (robots.txt, etc.)
```

## Console UX

- **Live editing:** every attendee-visible change bumps `content_version` and schedules a debounced silent push (`events.push.content_changed`); it goes to every joined device with a push token, `subscribed` or not (API-007). Open apps refresh on that push, on resume and pull-to-refresh, and poll every 60 s only without a push token (5 min safety poll with one). App layout autosaves. The "Live on your phone" panel (QR) replaces the old phone mock-up (`_phone_preview.html` is unused for now).
- **Background tasks:** forms with `data-background` (AI import, Check for updates) submit with fetch; `/console/tasks/` is the feed; `console/static/console/sw.js` (served at `/console/sw.js`, scope `/console/`) watches running tasks and notifies; a corner tray and button spinners show progress. Web app manifest at `/console/manifest.webmanifest`.
- **Status badges** (`_status_badges.html`): draft/live/archived, paid/demo, happening now (`Event.timing`), ended, watching, unassigned.
- **Google Maps** (`console/static/console/maps.js`) when `GOOGLE_MAPS_BROWSER_KEY` is set: Places autocomplete on addresses, draggable pin map; otherwise the OpenStreetMap picker.
- **Brand colour** purple `#431f86` (from the app icon) across console, site and apps.
- **Demo data:** `python manage.py load_demo_event [--owner email]` builds the DEMO event (happening now, every feature populated).

## Accounts

People only ever see and sign in with their **email** (`console.auth_backends.EmailOrUsernameBackend`, case-insensitive). The account's `username` is an internal generated id (`u` + 10 hex, `console.accounts.new_username`), so an email can change (Account page, `/console/account/`) without losing the account. Emails are unique case-insensitively (Postgres index `auth_user_email_lower_uniq` + `email_in_use`). Create a site admin with `python manage.py create_admin you@example.com` (they set up two-factor sign-in at their first sign-in; see Security). Deleting an account never deletes events: they become unassigned.

## Security

- **HTTPS**: outside `DEBUG`, HTTP redirects to HTTPS, session/CSRF cookies are `Secure` (+ `HttpOnly`, `SameSite=Lax`), HSTS 1 year with subdomains (not preloaded). `SECURE_PROXY_SSL_HEADER` trusts Heroku's `X-Forwarded-Proto`.
- **Rate limits** (`attnd/ratelimit.py`, Django's cache = `LocMemCache`: fine with Heroku's single gthread web process; with more processes use `DatabaseCache` + `createcachetable`): failed sign-ins 5 per email and 20 per IP per 15 min (a success clears the email's count); password-reset requests 3 per email and 10 per IP per hour; signups 10 per IP per hour; wrong two-factor codes 5 per account per 15 min; failed passkey sign-ins 20 per IP per 15 min; emailed sign-in codes 3 per account per 15 min. Over the limit: the same page with a friendly message and HTTP 429, worded the same whether or not the email has an account. Client IP = the **last** `X-Forwarded-For` hop (the one Heroku's router appends). Override with `RATE_LIMIT_<NAME>=count/seconds` or `settings.RATE_LIMITS`. The admin's own login page redirects to the site's sign-in (`/accounts/login/`, `console.views_auth.SignInView`).
- **Sign-in methods** (`console/twofactor.py` = the policy, `TwoFactorMiddleware`, models `console.Passkey`, `console.SignInSecurity`, `console.TwoFactorDevice`):
  - **Passkeys (WebAuthn), the default sign-in.** `console/webauthn.py` is the relying party (stdlib + cbor2 + cryptography): ES256, EdDSA and RS256 keys; checks clientDataJSON type/challenge/origin, the RP ID hash, User Present + User Verified, the signature and the sign counter (both non-zero and not increasing → rejected and logged as a possible clone). Attestation is `none` and not verified (synced passkeys have none; it only says which make of authenticator made the key). Challenges are 32 random bytes in the session, 5 minutes, single use (a few may be outstanding: autofill + button + other tabs). `console/passkeys.py` runs the ceremonies, `console/views_passkeys.py` serves the JSON endpoints under `/accounts/passkeys/`, `console/static/console/passkeys.js` is the browser side (vanilla `navigator.credentials`; passkey UI stays hidden without WebAuthn support). The sign-in page offers usernameless sign-in (autofill via `autocomplete="username webauthn"` / conditional mediation, plus a "Sign in with a passkey" button); a passkey sign-in counts as **both** factors. Failed passkey sign-ins are limited per IP. Each account gets a random 32-byte WebAuthn user handle (`SignInSecurity.user_handle`). Passkeys belong to one domain: `WEBAUTHN_RP_ID` (default: `SITE_URL`'s host), origins `WEBAUTHN_ORIGINS` (default `SITE_URL`'s origin, + `http://localhost:8000` with `DEBUG`, where pages on localhost use the RP ID `localhost`). Dev (`dev.attnd.app`) and production (attnd.app) passkeys are separate.
  - **Password sign-in + second step** (`/accounts/2fa/`): required when the account has any second factor: a passkey, an authenticator app (`console/totp.py` RFC 6238, ±1 step, each step once) or **emailed codes** (6 digits, 10 min, single use, stored as an HMAC, sends limited per account). Recovery codes (10, SHA-256 hashes on `SignInSecurity`, made with the first second factor, removed with the last) work for any of them. Adding a passkey is refused while a second step is pending, so a password alone can't add one.
  - **Staff and superusers** must have a **strong** method (passkey or authenticator app); without one they are sent to enrollment (`/accounts/2fa/setup/`: "Add a passkey" first, the authenticator QR code as the alternative) before the console, admin or password change open. Emailed codes are organizers-only (never accepted for staff). Removing the last strong method is blocked for staff.
  - **Organizers**: nothing required. After a password sign-in on a browser with a built-in authenticator the console shows "Sign in faster with a passkey" once ("No thanks" is remembered per account). **Account** lists passkeys (name, added, last used, synced) with add/rename/remove, the authenticator app, emailed codes (only when email is configured) and new recovery codes (needs an authenticator code or the password).
  - Lockouts: `python manage.py reset_2fa <email>` removes every second factor (authenticator, passkeys, emailed and recovery codes) and signs the account out everywhere; superusers can also delete a device or passkey in the admin. Tests that sign in as staff use `console.testing.TwoFactorClient`; `console/tests_passkeys.py` drives a software authenticator.
- **Content Security Policy** (Django's built-in `ContentSecurityPolicyMiddleware`, `CSP_POLICY` in settings): `default-src 'self'`; scripts from self + a per-request nonce (+ Google Maps hosts when `GOOGLE_MAPS_BROWSER_KEY` is set, per Google's Maps JS CSP guidance); `img-src` self, `data:`, `blob:`, `https:` (organizer image URLs, Cloudinary, map tiles); `style-src` allows inline styles; `frame-ancestors 'none'`, `base-uri`/`form-action 'self'`, `object-src 'none'`; violations go to `/csp-report/` and are logged (`attnd.csp`). `CSP_MODE=enforce|report|off`. Rules for templates: every inline `<script>` gets `nonce="{{ csp_nonce }}"` (or move it to a static file), and **no inline event handlers** (`onclick=` …): use `data-confirm="…"`, `data-autosubmit`, `data-copy="…"` (handled in `console.js`) or `addEventListener`. `console/tests_security.py` checks every template.
- **Admin** lives at `ADMIN_URL_PATH` (default `manage-attnd/`); `/admin/` is a 404. Link to it with `{% url 'admin:index' %}`.

## Data Models (`events/models.py`)

| Model | Key fields |
|-------|------------|
| `Event` | name, event_code (unique), status, timezone, start/end_time, lat/lng (nullable), paid, public, flyer, address, display_config, accent_color, content_version, source_urls, refresh_schedule (off/watch), update_mode, sources_checked_at, locked_fields; `user` = owner, null = unassigned (owner deleted; admins reassign from Team) |
| `Location` | name, snippet, description, category, sort_order, hidden, image/image_url, lat/lng (nullable), address, locked_fields — FK to Event |
| `ScheduledItem` | name, description, start/end_time, tags, hidden, locked_fields — FK to Event, optional FK to Location |
| `Notification` | title, body, send_time, sent, sent_at, delivered/failed counts — FK to Event |
| `Device` | id (UUID string PK), platform (ios/android), subscribed, push_token, app_version, last_seen, event FK |
| `EventMember` / `EventInvite` | team: admin or contributor; pending invites by email |
| `scrape.SourcePage` | a watched page: etag/last-modified, content hash, pending hash |
| `EventRevision` | full content snapshot for rollback |
| `scrape.IngestJob` / `ChangeSet` / `Change` | AI runs and the reviewable edits they propose |

- `status`: `draft` (invisible to every API) → `published` → `archived` (still viewable, apps show "ended").
- `paid=False` means demo/trial — mobile clients must show a "Demo Event" banner.
- `public` = listed in nearby search. Unclaimed homepage trial events are published but unlisted, reachable by code/QR.
- Coordinates are nullable; `null` means unknown (never `0,0`).
- Times are stored in UTC; `Event.timezone` is the IANA zone used for entry and display.

## API Authentication

All API views mix in `ApiKeyRequired` (`events/apis.py`). Three accepted token forms:

1. **Static API key** — `Authorization: Bearer <API_KEY>` env var. For dev/CI builds only. Keys listed (by SHA-256) in `REVOKED_API_KEY_SHA256` in `events/apis.py` are always rejected.
2. **Attestation JWT** — short-lived JWT issued by `/api/v1/attest/ios/` or `/api/v1/attest/android/` after verifying Apple App Attest or Google Play Integrity. Used by production mobile builds. The secret must be ≥ 32 bytes (system check `attnd.W001`, an error with `check --deploy`).
3. **CI key** (API-008) — `CI_API_KEYS`, for the live integration tests. Works with the static key off, but only for the DEMO event: views opt in with `ci_scope` (`event` → other ids 404, `lookup`/`nearby` → results limited to DEMO, `device`); `ci-` device ids only; usage is answered normally and discarded; anything else 401. Devices with a `ci-` id are excluded everywhere counts are shown (`Device.objects.real()`) and from pushes.

On 401 the mobile clients invalidate their stored JWT and re-attest before retrying once.

## Key API Endpoints (v1 — legacy; see `API_V2.md` for v2)

Base: `https://attnd.app/api/v1/events/`

| Method | Path | Description |
|--------|------|-------------|
| GET | `nearby_events/<lat>/<lng>/` | Events near coordinates (±3–7 day window, relevance sorted) |
| GET | `lookup/?q=<query>` | Exact match by name or code (ignores public flag) |
| GET | `event_by_code_or_name/?data=<query>` | Fuzzy match, public events only |
| GET | `<id>/` | Single event |
| GET | `<id>/locations/` | All locations with nested scheduled_items |
| GET | `<id>/locations/<loc_id>/` | Single location |
| GET | `<id>/schedule/` | All scheduled items sorted by start_time |
| GET | `<id>/notifications/` | Sent notifications, newest first |
| POST | `<id>/join/<device_id>/` | Register device; body: `{"platform": "ios"\|"android"}` |
| POST | `<id>/leave/<device_id>/` | Unregister device |
| POST | `pushnotifications/subscribe/<device_id>/` | Opt into push |
| POST | `pushnotifications/unsubscribe/<device_id>/` | Opt out of push |

Full reference: `API.md`

## Attestation (`attestation/`)

- `ios_attest.py` — validates Apple App Attest CBOR attestation objects
- `android_attest.py` — calls Google Play Integrity API via service account
- `tokens.py` — issues/verifies short-lived JWTs signed with `ATTESTATION_JWT_SECRET`
- `views.py` — `/api/v1/attest/ios/` and `/api/v1/attest/android/` endpoints

Set `APPLE_ATTEST_ENVIRONMENT=development` locally to accept sandbox attestations from Xcode/TestFlight.

## Deep Linking (`join/`)

`/join/<event_code>/` detects OS via User-Agent and redirects:
- Android → Play Store with `referrer=event_code%3D<code>`
- iOS → App Store
- Desktop → landing page with both store links

Well-known files served from `join/views.py`:
- `/.well-known/apple-app-site-association` — iOS Universal Links + App Clips
- `/.well-known/assetlinks.json` — Android App Links

## Local Development

PostgreSQL 18 runs in Docker, the same major version as Heroku and CI:

```bash
uv sync
scripts/dev_db.sh                     # start Postgres 18 (Docker Desktop on Mac; `dockerd &` first in a Claude cloud session)
export DEBUG=1 SECRET_KEY=dev-secret API_KEY=dev-key
uv run python manage.py migrate
uv run python manage.py runserver
```

`scripts/dev_db.sh stop` stops it; `scripts/dev_db.sh reset` deletes it and its data.

## Background work

- **AI jobs** run in a daemon thread of the web dyno by default (`INGEST_RUN_INLINE=1`). To use a worker dyno instead: `heroku ps:scale worker=1` and set `INGEST_RUN_INLINE=0`.
- **Heroku Scheduler** (every 10 min): `python manage.py run_scheduled_tasks` — sends due notifications, checks watched event websites (AI only on change), re-queues interrupted jobs, expires unclaimed trial events.
- **Push**: `events/push.py` (APNs token auth over HTTP/2, FCM HTTP v1). Platforms without credentials are skipped and logged.

## Running Tests

```bash
scripts/dev_db.sh && DEBUG=1 SECRET_KEY=x API_KEY=k uv run python manage.py test
```
Tests always run on PostgreSQL 18 (locally via `scripts/dev_db.sh`, in CI via the `postgres:18` service). Add `--parallel auto` to run them across CPUs (CI does; ~3x faster).

Tests live in `<app>/tests.py`, `events/tests_ci_keys.py` (API-008), `events/tests_demo_facts.py` (the DEMO facts the live suites rely on), `events/tests_v2.py` (v2 API, display, push), `console/tests_security.py` (rate limits, 2FA, CSP, admin path), `console/tests_passkeys.py` (WebAuthn with a software authenticator, emailed codes, sign-in policy) and `events/tests_integration.py`.

Test settings (`TESTING` in `attnd/settings.py`): WhiteNoise uses the finders (no `staticfiles/` needed), `django.request` logs only errors (tests hit 4xx on purpose), and a 32+ byte test JWT secret. CI also runs `manage.py check` with `-W error::DeprecationWarning`; run the whole suite that way when upgrading Django/Python.

**Live integration tests** (`live_tests/`, stdlib only, not collected by `manage.py test`): `ATTND_IT_SERVER=<host> ATTND_IT_KEY=<key> python3 live_tests/run.py` checks a deployed server: the v2 edge cases (status codes per `API_V2.md`), the DEMO facts shared with the iOS/Android suites, `/join/` per user agent, well-known files, `/health/`, security headers, `/admin/` 404. Skipped without both variables. CI: "Integration · dev" after the dev deploy (`ATTND_DEV_API_KEY`), "Integration · prod" alongside the tests (`ATTND_PROD_CI_KEY`, informational). Device ids `ci-web-…`; user agent contains `attnd-ci`.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | Yes | Django secret key (startup fails without it unless `DEBUG`/`CI`) |
| `DEBUG` | No | Set to any truthy value to enable debug mode |
| `API_KEY` | Yes | Static bearer token for mobile API auth |
| `STATIC_API_KEY_ENABLED` | No | Default `1`; `0` on production after the first release (SETUP 4d) turns `API_KEY` off |
| `CI_API_KEYS` | No | Comma-separated CI keys (≥ 32 chars) for the live integration tests: DEMO event only, also with the static key off (API-008, SETUP "CI keys") |
| `DATABASE_URL` | Prod | PostgreSQL connection string |
| `CLOUDINARY_URL` | Prod | Cloudinary connection string |
| `GOOGLE_PLACES_API_KEY` | No | Location image fetching during scrape |
| `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | Email | Mailgun SMTP credentials for the sending domain (`postmaster@mg.attnd.app` on prod; dev uses its sandbox). No password = mail goes to the log. They feed Django 6.1's `MAILERS` (settings `EMAIL_ENABLED`, `EMAIL_SMTP_OPTIONS`); the deprecated `EMAIL_*` Django settings aren't used |
| `DEFAULT_FROM_EMAIL` | No | Default `Attnd <noreply@mg.attnd.app>` |
| `SITE_URL` | No | Base URL for links in emails, default `https://attnd.app` (dev: `https://dev.attnd.app`) |
| `ADMIN_EMAILS` | No | Comma-separated; get server error emails |
| `EMAIL_ALLOWED_RECIPIENTS` | No | Comma-separated addresses/@domains; when set, nobody else is emailed (for dev) |
| `MAILGUN_*` | — | Set by the Mailgun add-on (SMTP server/port, API key, sandbox domain) |
| `APPLE_APP_BUNDLE_ID` | Prod | For App Attest verification |
| `APPLE_TEAM_ID` | Prod | For App Attest verification |
| `APPLE_ATTEST_ENVIRONMENT` | No | `production` (default) or `development` |
| `ANDROID_PACKAGE_NAME` | No | Default `com.arcu.attnd`; Play Integrity, Play links |
| `IOS_APP_STORE_URL` | Prod | `https://apps.apple.com/app/id1470627406`; join links and the update screen |
| `APP_MIN_VERSION_IOS` / `_ANDROID` | No | Older builds get the blocking update screen (API-003) |
| `APP_LATEST_VERSION_IOS` / `_ANDROID` | No | Older builds get a dismissible "update available" banner |
| `APP_UPDATE_MESSAGE` | No | Custom text for the update screen and banner |
| `SUPPORTED_API_VERSIONS` | No | Default `1,2`; apps on other versions are told to update and `/api/vN/` returns 410 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Prod | Google service account for Play Integrity |
| `ATTESTATION_JWT_SECRET` | Prod | Secret for signing attestation JWTs |
| `ATTESTATION_JWT_TTL_HOURS` | No | JWT lifetime in hours, default 24 |
| `OPENAI_API_KEY` | Yes | AI import / refresh |
| `OPENAI_MODEL` | No | Extraction model, default `gpt-5-mini` |
| `INGEST_RUN_INLINE` | No | `1` (default) run AI jobs in web dyno; `0` = worker dyno only |
| `APNS_AUTH_KEY` / `APNS_KEY_ID` | Push | APNs .p8 key contents and key ID |
| `APNS_TEAM_ID` / `APNS_TOPIC` | No | Default to `APPLE_TEAM_ID` / `APPLE_APP_BUNDLE_ID` |
| `APNS_USE_SANDBOX` | No | Truthy for development-signed builds |
| `FCM_PROJECT_ID` | Push | Firebase project ID |
| `FCM_SERVICE_ACCOUNT_JSON` | No | Defaults to `GOOGLE_SERVICE_ACCOUNT_JSON` (needs Firebase Messaging permission) |
| `CSP_MODE` | No | `enforce` (default), `report` (Report-Only header, nothing blocked) or `off` |
| `ADMIN_URL_PATH` | No | Where the Django admin lives, default `manage-attnd/` (`/admin/` is a 404) |
| `RATE_LIMIT_<NAME>` | No | `count/seconds` overrides for `LOGIN_EMAIL` (5/900), `LOGIN_IP` (20/900), `RESET_EMAIL` (3/3600), `RESET_IP` (10/3600), `SIGNUP_IP` (10/3600), `OTP_USER` (5/900), `PASSKEY_IP` (20/900, failed passkey sign-ins), `EMAIL_CODE_USER` (3/900, emailed sign-in codes), `CSP_REPORT_IP` (60/60) |
| `RATE_LIMIT_TRUST_X_FORWARDED_FOR` | No | Default `1` (behind Heroku's router); `0` = use the socket address |
| `TWO_FACTOR_ISSUER` | No | Name shown in authenticator apps, default `Attnd` |
| `WEBAUTHN_RP_ID` | No | Passkey domain (relying party ID), default `SITE_URL`'s host (`attnd.app`; dev: `dev.attnd.app`). Changing it makes existing passkeys stop working |
| `WEBAUTHN_ORIGINS` | No | Comma-separated origins allowed to use passkeys, default `SITE_URL`'s origin (+ `http://localhost:8000` with `DEBUG`) |
| `WEBAUTHN_RP_NAME` | No | Name shown by the browser when creating a passkey, default `Attnd` |

## Deployment

CI/CD for all three repos (develop → dev, main → production) is documented in `SETUP.md`.

Heroku `Procfile`:
```
release: python manage.py migrate --noinput && python manage.py collectstatic --noinput
web: gunicorn attnd.wsgi --worker-class gthread --workers 1 --threads 4 --timeout 300
worker: python manage.py process_ingest_jobs   # optional, scaled to 0 by default
```

Docker image uses `uv` for fast dependency installation (`Dockerfile`).
