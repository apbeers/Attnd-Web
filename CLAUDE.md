# Attnd Web — Backend & API

Django 6.0 backend serving the REST API consumed by the iOS and Android apps, plus a web console for event organizers. Deployed on Heroku.

## System Overview

Attnd is an event attendance app. This repo is the single backend for both mobile clients:

- **[attnd-ios](https://github.com/apbeers/Attnd-iOS)** — SwiftUI app
- **[attnd-android](https://github.com/apbeers/Attnd-Android)** — Kotlin/Compose app

The mobile apps call `/api/v1/events/` exclusively. There is no Django REST Framework — all API views are plain Django class-based views returning `JsonResponse`.

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
console/        # Organizer web dashboard (login-protected)
home/           # Marketing/landing pages
join/           # /join/<code>/ redirect + AASA & assetlinks well-known files
scrape/         # AI-powered event scraper (OpenAI + BeautifulSoup)
attestation/    # Apple App Attest + Google Play Integrity + JWT issuance
static/         # Project-level static assets
public/         # Files served at root by Whitenoise (robots.txt, etc.)
```

## Data Models (`events/models.py`)

| Model | Key fields |
|-------|------------|
| `Event` | name, event_code (unique), latitude/longitude, start_time, paid, public, flyer, address |
| `Location` | name, snippet, description, image/image_url, lat/lng, address — FK to Event |
| `ScheduledItem` | name, description, start_time — FK to Location + Event |
| `Notification` | title, body, send_time, sent — FK to Event |
| `Device` | id (UUID string PK), platform (ios/android), subscribed, event FK |

`paid=False` means a demo/trial event created by the scraper — mobile clients must show a "Demo Event" banner. `public=False` events are hidden from search; organizers can still use `/lookup/` to find them.

## API Authentication

All API views mix in `ApiKeyRequired` (`events/apis.py`). Two accepted token forms:

1. **Static API key** — `Authorization: Bearer <API_KEY>` env var. Used in dev, CI, and the console.
2. **Attestation JWT** — short-lived JWT issued by `/api/v1/attest/ios/` or `/api/v1/attest/android/` after verifying Apple App Attest or Google Play Integrity. Used by production mobile builds.

On 401 the mobile clients invalidate their stored JWT and re-attest before retrying once.

## Key API Endpoints

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

## Running Tests

```bash
uv run python manage.py test
```

Tests live in `<app>/tests.py` (unit) and `events/tests_integration.py` (integration).

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | Yes | Django secret key |
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

## Deployment

Heroku `Procfile`:
```
release: python manage.py migrate --noinput && python manage.py collectstatic --noinput
web: gunicorn attnd.wsgi --worker-class gthread --workers 2 --threads 4 --timeout 300
```

Docker image uses `uv` for fast dependency installation (`Dockerfile`).
