# Attnd Web — Backend & API

Django 6.0 backend serving the REST API consumed by the iOS and Android apps, plus a web console for event organizers. Deployed on Heroku.

## System Overview

Attnd is an event attendance app. This repo is the single backend for both mobile clients:

- **[attnd-ios](https://github.com/apbeers/Attnd-iOS)** — SwiftUI app
- **[attnd-android](https://github.com/apbeers/Attnd-Android)** — Kotlin/Compose app

Current mobile apps use **API v2** (`/api/v2/`, see `API_V2.md`): one `bundle/` request returns the event, locations, schedule, notifications and the server-resolved **display config**. `/api/v1/events/` stays for older app builds (shapes unchanged; drafts, hidden rows and location-less items are filtered out for them). There is no Django REST Framework — all API views are plain Django views returning `JsonResponse`.

## Platform model (the "full vision")

1. **AI import** — an organizer (or anonymous visitor on the homepage) gives URLs / a PDF or image / pasted text. An `IngestJob` crawls + extracts with OpenAI structured output and creates the event (`scrape/jobs.py`, `scrape/service.py`).
2. **Manual management** — the organizer console (`/console/`) edits everything. Any field a person edits is added to that row's `locked_fields`.
3. **AI refresh** — on demand or on a schedule, the AI re-reads the sources *with the current data* and returns its view; `scrape/diff.py` turns that into a `ChangeSet` of field-level `Change`s. Locked fields become conflicts (unticked), empty AI values never blank data, removals need cited evidence.
4. **Review** — the organizer accepts per change / per field; `apply_changeset` applies atomically, snapshots an `EventRevision` first (rollback from History), and bumps `content_version` once.
5. **Apps follow** — every content change bumps `Event.content_version` (signals in `events/signals.py`); apps poll `bundle/` with `If-None-Match` and can get a silent `content_updated` push.
6. **Adaptive layout** — `events/display.py` picks a preset (intimate / standard / festival) from size + spread; organizers override any key in **App layout**; the resolved dict ships in the bundle.

## Dependency policy

Avoid third-party libraries. Use Django and the Python standard library first; the organizer console uses
its own CSS (`console/static/console/console.css`), inline SVG icons (`console_tags.icon`) and vanilla JS
(`console.js`) — no CSS/JS frameworks or CDNs. Existing server dependencies are kept where the standard
library has no equivalent (Postgres driver, Pillow, OpenAI SDK, Google auth, HTTP/2 for APNs via
`httpx[http2]`, CBOR for App Attest). Stripe is the expected future exception. The marketing homepage
(`home/`) still uses its original vendored Bootstrap theme.

## Tech Stack

- **Python 3.12** managed with `uv`
- **Django 6.0** (no DRF)
- **PostgreSQL** in production via `dj-database-url`; SQLite in local dev
- **Cloudinary** for media storage in production (falls back to local filesystem)
- **Whitenoise** for static files
- **Gunicorn** (gthread, 2 workers × 4 threads) as WSGI server
- **Heroku** deployment via `Procfile`; Docker image also available

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

## Data Models (`events/models.py`)

| Model | Key fields |
|-------|------------|
| `Event` | name, event_code (unique), status, timezone, start/end_time, lat/lng (nullable), paid, public, flyer, address, display_config, accent_color, content_version, source_urls, refresh_schedule, auto_accept_minor, locked_fields |
| `Location` | name, snippet, description, category, sort_order, hidden, image/image_url, lat/lng (nullable), address, locked_fields — FK to Event |
| `ScheduledItem` | name, description, start/end_time, tags, hidden, locked_fields — FK to Event, optional FK to Location |
| `Notification` | title, body, send_time, sent, sent_at, delivered/failed counts — FK to Event |
| `Device` | id (UUID string PK), platform (ios/android), subscribed, push_token, app_version, last_seen, event FK |
| `EventMember` | extra organizers (editors) for an event |
| `EventRevision` | full content snapshot for rollback |
| `scrape.IngestJob` / `ChangeSet` / `Change` | AI runs and the reviewable edits they propose |

- `status`: `draft` (invisible to every API) → `published` → `archived` (still viewable, apps show "ended").
- `paid=False` means demo/trial — mobile clients must show a "Demo Event" banner.
- `public` = listed in nearby search. Unclaimed homepage trial events are published but unlisted, reachable by code/QR.
- Coordinates are nullable; `null` means unknown (never `0,0`).
- Times are stored in UTC; `Event.timezone` is the IANA zone used for entry and display.

## API Authentication

All API views mix in `ApiKeyRequired` (`events/apis.py`). Two accepted token forms:

1. **Static API key** — `Authorization: Bearer <API_KEY>` env var. For dev/CI builds only. Keys listed (by SHA-256) in `REVOKED_API_KEY_SHA256` in `events/apis.py` are always rejected.
2. **Attestation JWT** — short-lived JWT issued by `/api/v1/attest/ios/` or `/api/v1/attest/android/` after verifying Apple App Attest or Google Play Integrity. Used by production mobile builds.

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

```bash
# Install dependencies
uv sync

# Set required env vars
export DEBUG=1
export SECRET_KEY=dev-secret
export API_KEY=dev-key

# Run migrations
uv run python manage.py migrate

# Start dev server
uv run python manage.py runserver
```

## Background work

- **AI jobs** run in a daemon thread of the web dyno by default (`INGEST_RUN_INLINE=1`). To use a worker dyno instead: `heroku ps:scale worker=1` and set `INGEST_RUN_INLINE=0`.
- **Heroku Scheduler** (every 10 min): `python manage.py run_scheduled_tasks` — sends due notifications, starts scheduled AI refreshes, re-queues interrupted jobs, expires unclaimed trial events.
- **Push**: `events/push.py` (APNs token auth over HTTP/2, FCM HTTP v1). Platforms without credentials are skipped and logged.

## Running Tests

```bash
uv run python manage.py test
```

Tests live in `<app>/tests.py`, `events/tests_v2.py` (v2 API, display, push) and `events/tests_integration.py`.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | Yes | Django secret key (startup fails without it unless `DEBUG`/`CI`) |
| `DEBUG` | No | Set to any truthy value to enable debug mode |
| `API_KEY` | Yes | Static bearer token for mobile API auth |
| `DATABASE_URL` | Prod | PostgreSQL connection string |
| `CLOUDINARY_URL` | Prod | Cloudinary connection string |
| `GOOGLE_PLACES_API_KEY` | No | Location image fetching during scrape |
| `MAILGUN_SMTP_LOGIN` | No | Email sending |
| `MAILGUN_SMTP_PASSWORD` | No | Email sending |
| `APPLE_APP_BUNDLE_ID` | Prod | For App Attest verification |
| `APPLE_TEAM_ID` | Prod | For App Attest verification |
| `APPLE_ATTEST_ENVIRONMENT` | No | `production` (default) or `development` |
| `ANDROID_PACKAGE_NAME` | Prod | For Play Integrity verification |
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

## Deployment

Heroku `Procfile`:
```
release: python manage.py migrate --noinput && python manage.py collectstatic --noinput
web: gunicorn attnd.wsgi --worker-class gthread --workers 1 --threads 4 --timeout 300
worker: python manage.py process_ingest_jobs   # optional, scaled to 0 by default
```

Docker image uses `uv` for fast dependency installation (`Dockerfile`).
