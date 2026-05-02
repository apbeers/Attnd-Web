# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run development server
DEBUG=1 uv run python manage.py runserver

# Run all tests
bash scripts/test.sh
# or directly:
uv run python manage.py test events --verbosity=2

# Run a single test module
uv run python manage.py test events.tests
uv run python manage.py test events.tests_integration

# Run a single test class or method
uv run python manage.py test events.tests.EventLookupTestCase
uv run python manage.py test events.tests.EventLookupTestCase.test_lookup_by_exact_name

# Run database migrations
uv run python manage.py migrate

# Create a new migration after model changes
uv run python manage.py makemigrations

# Load fixtures for local testing
uv run python manage.py loaddata events/fixtures/dump.json
```

## Architecture

This is a Django 6 backend that serves a mobile event-discovery app (iOS + Android). The organizer-facing UI is the Django admin. There is no separate frontend framework.

### App structure

- **`attnd/`** — Project configuration: `settings.py`, root `urls.py`, `wsgi.py`/`asgi.py`.
- **`events/`** — Core domain. Contains all data models (`Event`, `Location`, `ScheduledItem`, `Notification`, `Device`) and the entire mobile REST API (`apis.py`). The admin (`admin.py`) is the organizer console — multi-tenant data isolation is enforced there via `UserScopedAdminMixin`.
- **`attestation/`** — Apple App Attest (iOS) and Google Play Integrity (Android) verification. On success it issues short-lived JWTs used as bearer tokens for the API.
- **`scrape/`** — "Try It Now" public endpoint. Crawls an event website with httpx + BeautifulSoup, sends the text to OpenAI (`gpt-4o-mini`) to extract structured data, optionally enriches coordinates via Google Places, and creates a trial `Event` owned by the `trialuser` account.
- **`console/`** — Organizer sign-up view. New accounts are automatically made `is_staff=True` so they can access the Django admin. Handles the trial-event "claim" flow (transfers a `trialuser`-owned event to the new account).
- **`join/`** — QR code deep-link redirect (`/join/<event_code>/`). Detects iOS/Android via User-Agent and redirects to the appropriate app store. Serves `/.well-known/apple-app-site-association` and `/.well-known/assetlinks.json` for universal/app links.
- **`home/`** — Marketing home page and sitemap.
- **`infra/`** — Terraform for AWS (ECS Fargate, RDS PostgreSQL, ALB, ECR, S3, CloudWatch). See `infra/MIGRATION.md` for the Heroku → AWS migration guide.

### API authentication

Every request to `api/v1/events/` requires `Authorization: Bearer <token>`. The `ApiKeyRequired` mixin (in `events/apis.py`) accepts two token types:

1. **Static `API_KEY`** — set via the `API_KEY` env var. Used by CI, dev, and legacy app builds.
2. **Attestation JWT** — issued by `/api/v1/attest/ios/` or `/api/v1/attest/android/` after verifying device integrity. TTL controlled by `ATTESTATION_JWT_TTL_HOURS` (default 24h).

### Multi-tenancy in the admin

`UserScopedAdminMixin` (in `events/admin.py`) is applied to `EventAdmin`, `LocationAdmin`, `ScheduledItemAdmin`, and `NotificationAdmin`. It filters all querysets and permissions to `request.user` — superusers bypass the filter and see everything. New objects are automatically assigned `user = request.user` on save. Staff users cannot see the `Device` model or the `User` admin.

### Trial / scrape flow

1. `POST /scrape/` (unauthenticated, rate-limited at 5/hour per IP) — crawls the URL, extracts event data via OpenAI, creates an `Event` owned by `trialuser` with `paid=False`.
2. The response includes a QR code and a `join_url` pointing to `/join/<event_code>/`.
3. On organizer sign-up, if a `?claim=<event_code>` query param is present, the event is transferred from `trialuser` to the new user.

### Environment-driven config

| Env var | Effect |
|---|---|
| `DEBUG=1` | Enables debug mode, allows all hosts, disables SSL redirect |
| `DATABASE_URL` | Switches from SQLite to PostgreSQL |
| `CLOUDINARY_URL` | Switches media storage from local filesystem to Cloudinary |
| `API_KEY` | Static bearer token for legacy API auth |
| `OPENAI_API_KEY` | Required for the scrape service |
| `GOOGLE_PLACES_API_KEY` | Enriches scraped event coordinates and location photos |
| `APPLE_APP_BUNDLE_ID`, `APPLE_TEAM_ID`, `APPLE_ATTEST_ENVIRONMENT` | iOS attestation config |
| `ANDROID_PACKAGE_NAME`, `GOOGLE_SERVICE_ACCOUNT_JSON` | Android attestation config |
| `ATTESTATION_JWT_SECRET`, `ATTESTATION_JWT_TTL_HOURS` | JWT signing config |
| `IOS_APP_STORE_URL` | App Store URL used in join redirects |

### Testing conventions

- Tests live in `events/tests.py` (unit) and `events/tests_integration.py` (integration).
- API tests use `RequestFactory` with `@override_settings(API_KEY=TEST_API_KEY)`.
- File-upload tests use `@override_settings(STORAGES=_IN_MEMORY_STORAGES)` to avoid touching the filesystem.
- Fixtures at `events/fixtures/dump.json` seed test data. Integration tests may use `events/fixtures/sample_data.json`.

### Deployment

- **Local/Heroku**: `uv run gunicorn attnd.wsgi` (see `Procfile`). Migrations run as a release command.
- **AWS ECS**: `bash scripts/deploy.sh [tag]` — builds a Docker image, pushes to ECR, runs migrations as a one-off Fargate task, then force-deploys the ECS service. Requires `terraform output` values for the ECR URL and cluster name.
- Static files are collected at Docker build time (`collectstatic --noinput`) and served by WhiteNoise.
