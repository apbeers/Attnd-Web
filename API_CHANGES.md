# API changes and cross-repo tracker

**attnd-web is the coordinating repo.** Every feature that touches the API starts here. This file is the single place that says what changed and what the iOS and Android apps must do about it. The contract itself is [`API_V2.md`](API_V2.md). [`contract/v2.json`](contract/v2.json) is its machine-checked snapshot, and the iOS and Android repos have no copy of either.

## How a feature moves through the three repos

1. **Plan it here.** Add a row to the tracker below and an entry in the log. Say what changes in the API and exactly what each app must do. Classify the change as additive or breaking (rules below).
2. **Server first, in attnd-web.** Implement it, update `API_V2.md`, refresh the snapshot (`UPDATE_CONTRACT=1 uv run python manage.py test events.tests_contract`) and add tests. The server ships first, because an additive change is invisible to apps that don't know about it yet.
3. **Then the apps.** In attnd-ios and attnd-android, implement the "iOS" and "Android" parts of the log entry. Mention the entry ID in the commit message, for example `API-003: show the update prompt`.
4. **Track it.** Tick each column in the tracker as that part reaches `develop`, then again when it reaches production (`main` and the stores).

Server-only changes that don't change what the apps see, such as console features, AI import or internal fields, don't need an entry.

## Compatibility rules

Apps stay installed for months, so **every server release must keep working with every app build still in use.**

**Additive changes are safe and need no new version.** The apps ignore unknown keys and default missing ones (see `API_V2.md`). These are additive:
- a new key in any response or push payload;
- a new endpoint;
- a new optional request field;
- a new value for a `display` option, as long as the apps fall back to their default for values they don't know. Say this in the log entry.

**Breaking changes need a new version**, because they break app builds already installed:
- removing or renaming a key, endpoint or push `type`;
- changing a key's type or meaning (an id from integer to string, a time from UTC to local, a field that used to be nullable becoming required);
- requiring a new request field, or rejecting requests that worked before;
- changing auth, URLs or error semantics that the apps depend on.

The contract test (`events/tests_contract.py`) fails on anything it can detect: a removed key or endpoint, or a changed type. It can't detect a change in *meaning*, so classify that yourself.

## Breaking changes

Avoid them. There is almost always an additive alternative: add the new key next to the old one, keep filling the old one, and stop reading it in the apps. When a breaking change really is needed:

1. **New version alongside the old one.** Add `/api/v3/` (`events/urls_v3.py`, `API_V3.md`, `contract/v3.json`), sharing code with v2 wherever possible. v2 keeps its current shape, and its contract test keeps passing unchanged.
2. **Apps move to v3** in a normal release. Record the first app version that uses v3 in the tracker.
3. **Retire v2 only when usage says so.** Devices record the newest API version they call (`Device.api_version`). Check with:
   ```bash
   heroku run -a attnd-web python manage.py api_usage
   ```
   When v2 devices drop to near zero, or after a notice period you're happy with, remove v2 in its own logged entry. Until then v2 stays fully working.
4. If some devices never update, a minimum-version prompt (proposed in API-003) lets the server tell old builds to update instead of breaking silently.

**v1** (`API.md`) is the legacy API for app builds released before v2. It is frozen, and its devices are recorded as `api_version = 1`. Retire it the same way, once `api_usage` shows no v1 devices.

## Tracker

Status: ✅ done · 🚧 in progress · ⬜ not started · — not needed. "dev" means on `develop` / TestFlight / Play internal testing; "prod" means on `main` / the stores.

| ID | Change | Type | Web | iOS | Android |
|----|--------|------|-----|-----|---------|
| API-001 | API v2: bundle, display config, devices | additive (new version alongside v1) | ✅ dev · ⬜ prod | ✅ dev (TestFlight, App Attest verified on a real iPhone) · ⬜ prod | ✅ dev · ⬜ prod |
| API-002 | Record each device's API version for sunset decisions | server only | ✅ dev · ⬜ prod | — | — |
| API-004 | Build and support info in Options; support-code lookup in the console | app only (no API change) | ✅ dev (lookup) · ⬜ prod | ✅ dev · ⬜ prod | ✅ dev · ⬜ prod |
| API-005 | Anonymous usage counts: `POST events/<id>/usage/`, console Usage pages (5-minute buckets) | additive | ✅ dev · ⬜ prod | ✅ dev · ⬜ prod | ✅ dev · ⬜ prod |
| API-006 | Live updates: content push on every change, 5 s foreground polling | behaviour only | ✅ dev · ⬜ prod | ✅ dev · ⬜ prod | ✅ dev · ⬜ prod |
| API-007 | Live updates via silent push to every device; slow safety poll; offline caching | additive (behaviour only, no shape change) | ✅ dev · ⬜ prod | ✅ dev (f7c1d9c) | ✅ dev (c03cc2d) |
| API-008 | CI keys: DEMO-only API access for live integration tests (`CI_API_KEYS`); `ci-` devices not counted | additive (server only, new key type) | ✅ dev (not deployed yet) · ⬜ prod | — (CI uses it: live tests) | — (CI uses it: live tests) |
| API-003 | Update check: `/api/app-status/`, update screen, 410 for retired versions | additive | ✅ dev · ⬜ prod | ✅ dev · ⬜ prod | ✅ dev · ⬜ prod |

## Log

Newest first. Each entry says what changed and what each app must do.

### API-008: CI keys for live integration tests
**Why:** the three repos run live integration tests against dev *and* production. Production will turn the static key off
(SETUP.md 4d), and the tests must never see or change real events.
**Server (additive, no shape change):** `CI_API_KEYS` (comma-separated, ≥ 32 characters each, compared by SHA-256 in
constant time) are accepted alongside the static key and attestation JWTs, also with `STATIC_API_KEY_ENABLED=0`, but only
for the DEMO event (`DEMO_EVENT_CODE`): lookup/nearby show only DEMO (other events: `404` like an unknown code), DEMO's
`bundle/`, `join/`, `leave/`, `usage/` (other ids: `404`), and `devices/`. With a CI key, device ids must start `ci-`
(`403`), and `usage/` answers exactly as usual (`accepted` included) while discarding the counts. Everything else is `401`.
Devices with a `ci-` id (any key) are excluded from console audience numbers, install/join/leave counts, `api_usage`
and pushes; `/join/` opens with `attnd-ci` in the User-Agent aren't counted.
**iOS / Android:** nothing in the apps. Their CI live tests use device ids `ci-ios-…` / `ci-android-…`, read the key
from the `ATTND_PROD_CI_KEY` secret for production (the dev static key for dev) and always leave DEMO at the end.

### API-007: live updates via silent push
**Why:** the 5 s foreground poll (API-006) is ~200 req/s per 1,000 open apps, too much for one Basic dyno. Push becomes the primary live-update signal; polling is only a slow safety net.
**Server (additive, no contract shape change):**
- `content_updated` silent pushes go to **every device in the event with a `push_token`**, whatever `subscribed` says. Visible organizer notifications (`type=notification`) still go only to `subscribed` devices.
- Payload unchanged: `{type: "content_updated", event_id, content_version}` (FCM data values are strings, as before).
- Debounce: 3 s after the last change, at most one per 15 s per event (`CONTENT_PUSH_DELAY` / `CONTENT_PUSH_MIN_INTERVAL`, env-overridable).
- Collapsible and short-lived: APNs `apns-push-type: background`, priority 5, `apns-collapse-id: content-<event_id>`, `apns-expiration` now + 1 h. FCM `collapse_key: content_<event_id>`, Android priority `normal`, TTL 3600 s. Alerts are unchanged (APNs priority 10, FCM `high`).
- `POST devices/` stores `push_token` regardless of `subscribed` (it already did; now documented and tested).

**iOS:**
- Always register for push at launch: `registerForRemoteNotifications()` without asking for alert permission (silent pushes need none; the `remote-notification` background mode is already on). Ask for alert authorization only when the user turns notifications on in Options.
- Always send the APNs token with `POST devices/`, with `subscribed` = the notification preference (visible alerts only).
- On `content_updated` (foreground or background): refetch `bundle/` only if the pushed `content_version` is newer than the current bundle's, after a random 0–10 s delay.
- Refresh on foreground and pull-to-refresh as today. Replace the 5 s poll: every **60 s** while active only when there is no push token (push unavailable, App Clip), otherwise a **5 min** safety poll while active. Drop the separate "every 5 min on event days" timer if it duplicates this.
- Offline caching: persist the raw bundle to disk (atomic write) and start from it offline; persist downloaded images to disk and prefetch the bundle's images (location images, logo, flyer) after each new bundle.

**Android:**
- Always fetch the FCM token at launch when Firebase is configured (data messages need no `POST_NOTIFICATIONS`); request `POST_NOTIFICATIONS` only when the user turns notifications on in Options.
- Always send the token with `POST devices/`, with `subscribed` = the notification preference. Data `type=notification` still shows an alert only when notifications are enabled.
- On `content_updated`: refetch only if `content_version` is newer than the current bundle's, after a random 0–10 s delay.
- Refresh on resume and pull-to-refresh as today. Replace the 5 s live poll: every **60 s** while resumed only without a push token (no Firebase / no token), otherwise a **5 min** safety poll. Keep the "offline notice after 3 failed polls" rule.
- Offline caching: the bundle is already cached on disk; add a disk cache for images (behind the in-memory `LruCache` in `RemoteImage`) and prefetch the bundle's images after each new bundle.

Old builds keep polling every 5 s and keep working; nothing is removed from the contract.

### API-006: live updates
**Why:** organizers scan their event's QR code, keep the app open and edit on the web: the phone should change within seconds.
**Server:** every attendee-visible change bumps `content_version` and schedules a silent `content_updated` push (debounced: 5 s after the last change, at most one per minute per event; `CONTENT_PUSH_*` settings). No contract change.
**iOS / Android:** while the app is in the foreground and in an event, `GET bundle/` every 5 seconds with `If-None-Match` (a `304` when unchanged), silently. Shared code, so the iOS App Clip (no push) is live too. Usage counts move from hourly to 5-minute buckets (`at` = start of the UTC 5-minute window).

### API-005: anonymous usage counts
**Why:** organizers see how the app was used during their event (joins over time, activity by hour, which features, locations and schedule items people looked at). The admin sees the same for every event, plus app-wide totals.
**Privacy:** counts only. Apps send no device ID; the server stores hourly totals per event, metric, detail and platform (`UsageCount`), and nothing that links a count to a device or a person. The privacy policy says so.
**API (additive):** `POST events/<id>/usage/` (see `API_V2.md` for metrics and client behaviour). The server records `join` (only when a device newly joins), `leave` and `install` itself.
**Console:** event sidebar → **Usage**; admins also get **App usage** (all events, versions in use, support lookup).
**iOS / Android:** add a small usage reporter that aggregates by (name, detail, hour), flushes every ~60 s, on background and before leaving, and never blocks the UI or shows errors. Instrument: `app_open` (launch/foreground while in an event), `tab_view` (each tab selection, including Options), `location_view`, `schedule_item_view`, `notification_open` (push tap), `search` (when a search is submitted or the query settles; never the text), `refresh` (pull to refresh).

### API-004: build and support info (app only)
**Why:** when someone reports a problem, the admin can find their device and see exactly which build and server it uses.
**Apps:** Options gains an **About / Support** section:
- non-production builds (server isn't `attnd.app`) show a prominent "Internal test build: dev server" warning at the top of Options;
- app version and build number, server host, API version, platform/OS version and device model, current event (name, code, id, content version), last refresh time;
- a **support code**: the first 8 characters of the device ID, upper case, without dashes;
- **Copy support info**, which copies all of the above plus the full device ID as plain text.
**Console:** admin → App usage → **Support lookup** finds the device by support code (platform, app and API version, event, joined, last seen, push status).

### API-003: update check and update screen
**Why:** if a change ever has to break old builds, or an old build has a serious bug, the server can tell those builds to update instead of letting them fail.
**API (additive, outside the versions):** `GET /api/app-status/?platform&version&api` (see `API_V2.md`). It needs no auth and its shape is frozen. It answers `update: none | available | required` with `min_version`, `latest_version`, `update_url` and `message`. Any `/api/vN/` call to a retired version answers `410` with `"error": "update_required"`.
**Server:** Heroku config `APP_MIN_VERSION_IOS`/`_ANDROID` (older builds must update), `APP_LATEST_VERSION_IOS`/`_ANDROID` (older builds see the banner), `APP_UPDATE_MESSAGE` (optional text), `SUPPORTED_API_VERSIONS` (default `1,2`), `IOS_APP_STORE_URL`. Unset means no prompt.
**iOS / Android:** check on launch and on every return to the foreground. `required` (or a `410`/`426` from any call) replaces the whole UI with an "Update Attnd" screen and a button to `update_url`. `available` shows a dismissible banner, remembered per `latest_version`. Unknown `update` values mean no prompt.

### API-002: device API version (server only)
`Device.api_version` records the newest API version each device has called: 1 for the legacy endpoints, 2 for v2. `manage.py api_usage` reports active devices by API version, platform and app version. No contract change, no app work.

### API-001: API v2
The v2 API described in `API_V2.md`: one `bundle/` request with event, locations, schedule, notifications and the server-resolved `display` config; ETag/304; join/leave/devices with push tokens; `notification` and `content_updated` pushes. v1 stays unchanged for older builds.
**iOS / Android:** rewritten onto v2 (done; see each repo's `CLAUDE.md`).
