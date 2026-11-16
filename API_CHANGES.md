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
| API-001 | API v2: bundle, display config, devices | additive (new version alongside v1) | ✅ dev · ✅ prod (Nov 15, 2026) | ✅ dev (TestFlight, App Attest verified on a real iPhone) · ⬜ prod | ✅ dev · ⬜ prod |
| API-002 | Record each device's API version for sunset decisions | server only | ✅ dev · ✅ prod (Nov 15, 2026) | — | — |
| API-004 | Build and support info in Options; support-code lookup in the console | app only (no API change) | ✅ dev (lookup) · ✅ prod (Nov 15, 2026) | ✅ dev · ⬜ prod | ✅ dev · ⬜ prod |
| API-005 | Anonymous usage counts: `POST events/<id>/usage/`, console Usage pages (5-minute buckets) | additive | ✅ dev · ✅ prod (Nov 15, 2026) | ✅ dev · ⬜ prod | ✅ dev · ⬜ prod |
| API-006 | Live updates: content push on every change, 5 s foreground polling | behaviour only | ✅ dev · ✅ prod (Nov 15, 2026) | ✅ dev · ⬜ prod | ✅ dev · ⬜ prod |
| API-007 | Live updates via silent push to every device; slow safety poll; offline caching | additive (behaviour only, no shape change) | ✅ dev · ✅ prod (Nov 15, 2026) | ✅ dev (f7c1d9c) | ✅ dev (c03cc2d) |
| API-008 | CI keys: DEMO-only API access for live integration tests (`CI_API_KEYS`); `ci-` devices not counted | additive (server only, new key type) | ✅ dev · ✅ prod (Nov 15, 2026) | — (CI uses it: live tests) | — (CI uses it: live tests) |
| API-009 | Anonymous heart counts: usage metrics `location_heart` / `location_unheart` / `schedule_item_heart` / `schedule_item_unheart`; net hearts in the console | additive (new usage names) | ✅ dev (443e1dc) · ⬜ prod | ✅ dev (e1802d9) | ✅ dev (41291f1) |
| API-010 | Tags: `Location.tags` in the bundle, all tags lower case; AI import/refresh tags everything; `backfill_tags`; DEMO tags | additive (new key; tag case normalised) | ✅ dev (443e1dc) · ⬜ prod | ✅ dev (e1802d9) | ✅ dev (41291f1) |
| API-011 | Urgent announcements: `Notification.priority`, `priority` in notification pushes, urgent = every device + time-sensitive/high priority; iOS Live Activity push via `POST devices/` `live_activity_token` | additive (new keys, new optional request field, new push variant) | ✅ dev (443e1dc) · ⬜ prod | ✅ dev (b3cb181, d688ed0) | ✅ dev (d491c93) |
| API-013 | Diagnostics: debug log upload (`POST diagnostics/logs/`), crash/hang reports, `X-Request-Id` / `X-Diag-Session`, Android R8 symbols + retrace, iOS symbolication workflow, staff Diagnostics panel | additive (new endpoints, new request headers) | 🚧 built, not pushed · ⬜ prod | ⬜ | ⬜ |
| API-014 | Anonymous performance metrics: `perf_*` usage names with bucket details | additive (new usage names) | 🚧 built, not pushed · ⬜ prod | ⬜ | ⬜ |
| API-015 | Shared plan links `/p/<EVENT_CODE>/<payload>` (web page + store routing like `/join/`), claimed in apple-app-site-association (app + App Clip) and opened by the apps | additive (new web route, new app link paths; no API endpoint) | ✅ dev · ⬜ prod | ✅ | ✅ |
| API-016 | Attnd Studio: `event.website_url` (the event's Studio-hosted website, or null) in every v2 event object | additive (new key) | ✅ dev · ⬜ prod | ✅ | ✅ |
| API-017 | Tickets: `event.tickets_url` + `bundle.tickets` (all-in prices, Get tickets opens the web); My tickets (`tickets/code|verify|mine|signout/`, buyer token); door mode (`door/…`: Ed25519 keys + ticket list + revocations for offline scanning, idempotent check-in upload, counts) | additive (new keys, new endpoints) | ✅ dev · ⬜ prod | ✅ | ⬜ (later) |
| API-003 | Update check: `/api/app-status/`, update screen, 410 for retired versions | additive | ✅ dev · ✅ prod (Nov 15, 2026) | ✅ dev · ⬜ prod | ✅ dev · ⬜ prod |

API-012 is reserved (Android FCM → Firebase Installation ID registration, `ROADMAP.md`).

## Log

Newest first. Each entry says what changed and what each app must do.

### API-017: tickets (Get tickets, My tickets, door mode with offline scanning)
**Why:** Attnd Tickets (`docs/specs/tickets_spec.md`, owner-approved Nov 15–16, 2026): Eventbrite-style ticketing for
events on the $499 Studio + tickets plan. Buying happens **only on the website** (Stripe Checkout); the apps link
out, show tickets the person holds, and scan at the door, offline. Additive: two new keys and new endpoints.
**Server (attnd-web):**
- `event.tickets_url` in every v2 event object and `bundle.tickets` (`on_sale`, `state`, `sold_out`, `url`,
  `min_price_cents` / `max_price_cents` **all-in** (fee included), `currency`, `price_text`, `prices_include_fees`), or
  `null`. On-sale changes and sold-out flips bump `content_version`.
- My tickets: `POST tickets/code/` → `POST tickets/verify/` (6-digit email code → `buyer_token`, 180 days) →
  `GET tickets/mine/` (header `X-Attnd-Buyer`, ETag) with each ticket's QR string, status, Wallet links, event;
  `POST tickets/signout/`.
- Door mode: `POST door/code/` → `POST door/verify/` (`door_token`, 30 days, + the events this email may scan: door
  staff from the console, or the event's team), header `X-Attnd-Door`: `GET door/events/`,
  `GET door/events/<id>/manifest/` (Ed25519 public keys, ticket types, tickets with name + email hash only),
  `GET door/events/<id>/revocations/?since=`, `POST door/events/<id>/checkins/` (batches ≤ 500, idempotent by
  `client_id`, device scan times kept, earliest admission wins, later ones flagged), `GET door/events/<id>/counts/`,
  `POST door/signout/`.
- QR = `ATK1.<payload>.<sig>` Ed25519 (exact format in `API_V2.md` → Tickets), verifiable offline; transfers,
  refunds and voids bump the ticket version and add a revocation.
- No enumeration (code requests answer the same for any address), rate limits on codes, wrong codes and door calls.
- CI keys: DEMO + the ticket demo event only, test addresses only (`@ci.attnd.test`, never mailed); dev-only
  `tickets/test-inbox/`, `tickets/test/order/`, `tickets/test/pay/` for the live suites.
- Documented in `API_V2.md` → Tickets; contract snapshot updated; tests in `tickets/tests_*.py`.
- **DEMO ticket facts** (`manage.py load_ticket_demo`, `tickets.tests_web.DemoFactsTests`): event `TIXDEMO` (Studio
  plan, a week ahead, America/Chicago); 7 ticket types in order General Admission $25 ×200, VIP $75 ×20 (pool "Venue"
  250), Kids under 12 free ×50, Support the artists pay-what-you-want from $5, Parking add-on $10 ×40 (re-entry),
  Backstage hidden (unlock `BACKSTAGE`) $150 ×10, Early Bird $15 ×10 (sold out, waitlist); promo `SAVE10` (10% off GA and
  VIP); door staff `door@ci.attnd.test`; 14 valid tickets (buyer1 ×2 General Admission, one checked in at "Gate A";
  buyer2 VIP + Parking; buyer3 ×10 Early Bird) and 1 refunded (buyer4), buyers `buyerN@ci.attnd.test`.

**iOS:**
- Decode `event.tickets_url` (`URL?`) and `bundle.tickets` leniently (every field optional, unknown `state` = don't
  show a buy button). Show **Get tickets** (with `price_text`, e.g. "From $26.06", already including fees) when
  `on_sale`, else the state ("Sold out", "Not on sale yet", "Event cancelled"), in the event's Options header and the
  search event detail (and wherever the design puts it). It opens `url` in Safari (`openURL` / `SFSafariViewController`),
  never an in-app purchase; nothing else is sold in the app (App Review 3.1.3(e): goods used outside the app). The App
  Clip may show the same link.
- **My tickets** (Options and/or a Tickets entry; works without joining the event): email → `tickets/code/` → code
  field (`.oneTimeCode`) → `tickets/verify/`; keep `buyer_token` in the Keychain. `GET tickets/mine/` on open and pull to
  refresh (with `If-None-Match`); persist the response (Application Support, excluded from backup) and show it offline:
  event, type, holder, code, the QR (Core Image `CIQRCodeGenerator` from `qr`, correction level M, brightness up while
  shown), status, checked-in time. "Add to Apple Wallet" downloads `apple_wallet_url` and presents
  `PKAddPassesViewController` (no entitlement needed for adding); "Manage" opens `ticket_url` in Safari (transfer,
  refund, resend). `401` → sign in again. Sign out = `tickets/signout/` + delete the stored copy.
- **Door mode** (Options → Door mode): email code (`door/code/`, `door/verify/`), pick the event, **Download tickets**
  (`manifest/`, store it + the keys), then scan with VisionKit `DataScannerViewController` (existing QR code) and verify
  offline per `API_V2.md` → "Scanning offline" (CryptoKit `Curve25519.Signing.PublicKey(rawRepresentation:)
  .isValidSignature`): full-screen green / red / amber "Already checked in 7:42 PM at Gate B", haptics + sound; gate
  name setting; manual search (name, code, order ref; email via the same SHA-256 hash); re-entry (scan out) for types
  with `reentry`. Queue every scan (`client_id` UUID, scan time, `device_result`) on disk; upload in batches of ≤ 500
  when online (and every ~15 s while online), poll `revocations/?since=` every ~30 s, show `counts/`. Everything works
  in airplane mode; a sync summary shows flagged results.
- Unit tests: token parse/verify against a fixed key + token (generate one with `tickets.signing` and paste it into
  the tests), revocation rules, the offline duplicate rules, queue/merge; the live suite: My tickets via the dev test
  inbox (`tickets/test-inbox/`, CI key, `@ci.attnd.test`), download the TIXDEMO manifest, verify a QR offline, upload a
  check-in, Wallet pass download (content type `application/vnd.apple.pkpass`) when configured.
- LOG_EVENTS: the `tickets.*` rows in `LOG_EVENTS.md` (new category `tickets`), never emails, names, codes, tokens
  or QR strings.

**Android:**
- Decode `tickets_url` / `tickets` (`String? = null`, all fields defaulted). **Get tickets** opens `url` with
  `Intent.ACTION_VIEW` / Custom Tabs (no WebView, no payment code or Google Pay; Play's payments policy allows
  linking out for physical event tickets).
- **My tickets** as on iOS (token in encrypted prefs / DataStore, offline copy in `filesDir`, QR via the in-house
  `domain/QrCode.kt` encoder at level M); "Save to Google Wallet" opens `google_wallet_url`.
- **Door mode** as on iOS: CameraX + ML Kit barcode scanning (existing), Ed25519 with `Signature.getInstance("Ed25519")`
  (API 33+, minSdk 35), offline verification, queue in Room/prefs, WorkManager-free foreground sync while the screen
  is open, counts, manual search, re-entry.
- Same unit and live tests as iOS (shared token fixture). Later (owner: "Android app later"): not started.

### API-016: event website link (Attnd Studio)
**Why:** Attnd Studio (invite-only beta, `docs/specs/studio_spec.md`) lets an organizer design the event's website
with AI, and Attnd hosts it. The apps should link to it. Additive: one new nullable key; nothing else changes.
**Server (attnd-web):**
- Every v2 event object (`bundle/`, `lookup/`, `nearby/`) has `website_url`: `https://<SITES_HOST>/<slug>/` while
  the event is on the Studio plan and its site is published and not taken down, else `null`. Publishing,
  unpublishing, a takedown, changing the address or the plan bump `content_version`, so apps refetch the bundle.
- The site lives on a separate registrable domain (`SITES_HOST`, a herokuapp.com hostname until a domain is bought),
  never on attnd.app. Documented in `API_V2.md` → Event; contract snapshot updated; tests in `studio/tests.py`
  (`DataTests.test_bundle_website_url_api_016`).

**iOS:**
- Decode `website_url` leniently (`String?`, missing = nil; ignore values that aren't `https` URLs).
- When set, show a **Website** row in Options (event header) and in `EventInfoView` (search's event result), opening
  it with `openURL` (Safari, not in-app). No `X-Device-Id`, tokens or cookies go to it. Not in the App Clip's tab bar.
- App Intents: `EventEntity` may expose it as a URL property (optional).

**Android:**
- Decode `website_url` (`String? = null`, ignore non-`https`).
- When set, show a **Website** item in Options (event section) and on the search screen's event detail, opened with
  `Intent.ACTION_VIEW` (the browser / Custom Tab, not a WebView). Nothing from the app is sent to it.

### API-015: shared plan links
**Why:** the event assistant (assistant spec §7, Nov 15, 2026) shares a plan or an item as a link that opens the
shared plan in the app and still works for someone without it. Additive: a new web route and new Universal Link / App
Link paths; no API endpoint, no new key, nothing stored on the server.
**Server (attnd-web):**
- `GET /p/<EVENT_CODE>/<payload>` (also with a trailing `/`): `payload` = base64url without padding of compact JSON
  `{"v":1,"ids":[<schedule item ids>],"d":"YYYY-MM-DD"?}`, at most 1,500 characters. Known string:
  `{"v":1,"ids":[101,114],"d":"2026-11-14"}` → `eyJ2IjoxLCJpZHMiOlsxMDEsMTE0XSwiZCI6IjIwMjYtMTEtMTQifQ`.
- Same routing as `/join/<code>/` (`join/views.py` `SharedPlanRedirect`): Android → Google Play with
  `referrer=event_code%3D<code>`; iPhone → the join page with the App Clip banner and the App Store link, plus the
  shared items; desktop → the join page listing the shared items by name, time (event's time zone) and place, both
  store links and a QR code of the share link (`/p/<code>/<payload>/qr.svg`). Unknown, hidden or other events' ids are
  skipped; an invalid payload shows the plain join page; an unknown or draft event is the join `404`. Opens are counted
  as `link_open` (by device kind), like join links. Pages are `noindex`.
- `/.well-known/apple-app-site-association`: `/p/*` added for the app and the App Clip (the same `appIDs` entry as
  `/join/*`), with `/p/*/*/qr.svg` excluded. `assetlinks.json` is domain-wide (`handle_all_urls`), so it already covers
  `/p/*`; which paths open the Android app is decided by the app's intent filter.
- Documented in `API_V2.md` → "Outside the version: shared plan links"; tests in `join/tests.py`
  (`SharedPlanLinkTests`, AASA paths).

**iOS:**
- Make share links as above (encode: compact JSON, base64url, strip `=`; ids only, no personal data; stay ≤ 1,500
  characters, e.g. by sharing one day) and unit-test the codec against the known string (shared with Android).
- `/p/*` arrives through the existing Universal Links handler (the `applinks:attnd.app` entitlement already covers the
  domain; the AASA now lists the path): join the event if needed (code from the path), then show the read-only
  "Shared plan" with "Use this plan" and "Merge with mine" (spec §7). Ids not in the bundle are skipped; a payload the
  app can't read (`v` ≠ 1) just joins the event.
- App Clip: a `/p/` link opens the clip like `/join/` (it joins the event; no share import, spec §8). The App Clip
  card for `/p/` URLs only appears if App Store Connect's App Clip experiences cover that prefix (the default
  experience covers the meta-tag banner on the page); add an advanced experience for `https://attnd.app/p/` if
  scanning a share QR should show the card.
- Log `share.created` / `share.opened` (LOG_EVENTS.md).

**Android:**
- Same codec and known string; `share_plan` / `share_item` through the system share sheet.
- **App Links need the app's intent filter:** add `https://attnd.app/p/` (`android:pathPrefix="/p/"`) to the
  `autoVerify="true"` intent filter that handles `/join/` in `AndroidManifest.xml` (assetlinks.json needs no change),
  and route it in `MainActivity` / `domain/JoinLinks.kt`: join the event by its code, then show the shared plan.
  Without the app, the link goes to Play with the event code as install referrer, so a first install joins the
  event (the shared plan itself isn't carried through the referrer).
- Log `share.created` / `share.opened` (LOG_EVENTS.md).

### API-014: anonymous performance metrics in usage
**Why:** see how fast launches, bundle fetches, push refreshes, the planner and the map are in production, and how
often calls fail, without any per-person logs. Agreed in the logging spec §6 (Nov 15, 2026).
**Server (additive, new usage names):** `POST events/<id>/usage/` accepts `perf_launch_cold`, `perf_launch_warm`,
`perf_bundle_fetch`, `perf_bundle_304`, `perf_api_error`, `perf_push_refresh_delay`, `perf_live_start_fail`,
`perf_planner_ms`, `perf_map_first_render`, each with `detail` = a bucket label from the table in `API_V2.md` → usage
→ Performance (an unknown or missing bucket drops the entry; `perf_live_start_fail` may have none). They are not
"interactions" (organizer Usage pages ignore them); staff see them in Diagnostics → Sessions → App performance.
Older servers drop the unknown names harmlessly.

**iOS / Android (same rules on both):**
- Measure on the device, map the value to its bucket (`<X` = less than X; buckets don't overlap: 500 ms is `<1s`) and
  record it through the existing usage reporter (`track(.perfBundleFetch, detail: "<500ms")`), same 5-minute
  buckets, flushes and retry rules, never a device id. A value measured outside an event goes into the next flush made
  while in an event (keep at most ~50 pending), or is dropped.
- `perf_launch_cold`: process start (iOS: `ProcessInfo`/`kinfo_proc` start time or the earliest `main`; Android:
  `Process.getStartUptimeMillis()`) → first frame of the event UI. `perf_launch_warm`: foreground → interactive. Only
  while in an event (launches to the launch screen aren't counted).
- `perf_bundle_fetch` / `perf_bundle_304`: `GET bundle/` start → decoded (200) / answered (304), every refresh source.
- `perf_api_error`: every failed API call: `4xx` (except 304), `5xx`, `timeout`, `offline` (no connection / DNS).
- `perf_push_refresh_delay`: `content_updated` received → new bundle applied (includes the 0–10 s random delay);
  not counted when the push was skipped as not newer.
- `perf_live_start_fail`: `disabled` (user turned Live Activities/promoted notifications off), `denied` (no
  notification permission), `limit` (ActivityKit too many activities), `error` (anything else).
- `perf_planner_ms`: one `Planner` run (not the model's preference extraction).
- `perf_map_first_render`: Map tab shown → first pins drawn (iOS: first `MapCameraUpdate`/annotations rendered;
  Android: `GoogleMap` `onMapLoaded` with pins).
- Also log each measurement as `perf.metric` (LOG_EVENTS.md) so debug sessions show it.
- Unit-test the bucket function on both (edges: 249/250/499/500 ms, 0.5 s, 4 s, 60 s).

### API-013: diagnostics: debug logs, crash reports, symbols, admin panel
**Why:** when something goes wrong on a phone at an event, staff need the app's own timeline (network, push,
background, Live Activity, location, planner, assistant…) next to the server's, without collecting anything personal.
Agreed in the logging spec (Nov 15, 2026): storage in our Postgres with strict caps, production debug mode only when the
user turns it on (never remotely), dev builds always. The shared record format and every event name are in
[`LOG_EVENTS.md`](LOG_EVENTS.md).
**Server (additive: new endpoints, new request headers, server-only tables):**
- `POST /api/v2/diagnostics/logs/` (app auth, gzip JSON, ≤ 500 entries / 256 KB; per-record validation; 20,000 entries
  per session per 10 min; `429` + `Retry-After` over caps or when the storage is full; CI keys discarded).
- `POST /api/v2/diagnostics/reports/` (anonymous crash / hang / ANR / exit reports, ≤ 20 per request).
- `POST /api/diagnostics/symbols/` (CI key or dev static key): Android R8 `mapping.txt` per build (gzip in the DB,
  newest 20 kept); a pure-Python R8 retrace (`diagnostics/retrace.py`).
- `GET|POST /api/diagnostics/symbolicate/<id>/`: the callback of attnd-ios's `symbolicate.yml`, which the panel
  dispatches through the GitHub API (`GITHUB_SYMBOLICATE_TOKEN`, SETUP.md step 14) with a one-time token.
- Every `/api/` request: one JSON log line on stdout (method, path **template**, status, ms, `req_id`, `diag`),
  `X-Request-Id` echoed (or made) in the response, timings per endpoint (p50/p95 in the panel); with
  `X-Diag-Session` the line is also stored in that session (`server.request`, category `server`).
- Retention (`run_scheduled_tasks`, also `manage.py purge_diagnostics`): dev 7 days, debug mode 14 days, reports 30
  days; total 250 MB (`pg_total_relation_size`), oldest sessions first, then VACUUM FULL.
- Console → **Diagnostics** (staff, strong 2FA): sessions (search by code, time, platform, version, event, errors,
  reason), session timeline (category lanes, server lines next to the app's by `req_id`, filters: level, category,
  event, text, status ≥ 400, slower than N ms, fg/bg, ±30 s), network waterfall, summaries (errors grouped, slowest
  requests, launch times, pushes received vs handled, background wakes), .jsonl export; reports grouped by problem
  with retrace (Android) / Symbolicate (iOS); endpoint timings; Android mappings with a paste-and-retrace tool.
- Privacy page: a paragraph on crash reports and debug logging.

**iOS:**
- **Logging facade** `AppLog` (one file, shared with the App Clip and the widget extension where useful):
  `AppLog.debug(.net, "net.request.finished", "GET bundle 304 in 182 ms", ["method": …])` and `trace/info/notice/
  warning/error/fault`. Check the level first (cheap when off); build the record and write it off the main thread
  (a serial queue/actor). Each record goes to `os.Logger(subsystem: "com.arcu.Attnd", category: <cat>)` (dynamic
  values `.public` only when the redaction rules say they're safe, else `.private`) **and** to the ring buffer.
- Record format v1 exactly as `LOG_EVENTS.md` (`ts` UTC with ms and `Z`, `mono_ms` from `ContinuousClock`/
  `mach_continuous_time` since launch, `seq` per install, `app`, `build`, `os` "iOS 27.2", `model` from `utsname`,
  `state` fg/bg, `net` from `NWPathMonitor`, `event_id`, `diag`). Only event names from `LOG_EVENTS.md`.
- **Redaction**, unit-tested (`LogRedactionTests`): never search/Ask/plan text (log `query_len`; `DEV` builds may log
  `query`), never tokens/keys/push or Live Activity tokens/JWTs/emails/device id/support code; URLs only as path
  templates (`/api/v2/events/{id}/bundle/`, no query, no host); coordinates rounded to 0.01° or distances only.
- **Ring buffer:** JSON lines in `Application Support/attnd-logs/` (excluded from backup), ~2 MB, newest kept (two
  rotating files are fine). Feeds the viewer, the export and uploads.
- **Signposts:** `OSSignposter` intervals around launch, bundle fetch + decode, planner, map first render (also logged
  as `perf.signpost` with `ms`).
- **Every API call** sends `X-Request-Id` (new random 16 hex per request; retries get a new one) and logs it as
  `req_id` in `net.request.started/finished/failed`; while a debug session is on, also `X-Diag-Session: D-XXXXX`.
- **Levels:** `Dev` builds `debug` + continuous upload (`reason: "dev"`, one persistent code per install, made once,
  stored in UserDefaults); production `warning`, local only.
- **Debug mode** (production, About & support): "Turn on debug logging for 10 minutes" with a consent line ("Sends a
  technical log of what the app does, labelled only with this code, for 10 minutes. No search text, location or
  anything that identifies you."), a countdown, the code `D-XXXXX` (5 random Crockford base32 characters
  `0-9A-HJKMNP-TV-Z`, new each time) with Copy, and Stop. While on: level `debug`, upload every ~10 s and on
  background, plus the previous 5 minutes from the ring buffer (pre-roll) first. Store the code and expiry
  (UserDefaults) so it survives restarts; at expiry go back to normal and log `logging.debug_mode on=false`. Never
  link the code to the device id (not in support info, not sent with `devices/`). Never started remotely.
- **Uploads:** `POST /api/v2/diagnostics/logs/` gzip (`Content-Encoding: gzip`), ≤ 500 entries and ≤ 256 KB per
  request (split), off the main thread, never an error in the UI. On `429` wait `Retry-After` seconds; on other
  failures keep the records and retry with the next batch. Not in screenshot mode. The App Clip: dev builds only.
- **Log viewer** (Dev builds always; production while debug mode is on, and About & support → "Recent logs"): filter
  by level, category, text, errors only; share/export `.jsonl` (ShareLink).
- **Reports:** subscribe to MetricKit (`MXMetricManager.shared.add`); for each `MXDiagnosticPayload`, send its crash,
  hang, CPU-exception, disk-write and app-launch diagnostics to `POST /api/v2/diagnostics/reports/` (`kind` crash/
  hang/cpu/disk_write/launch, `payload` = `jsonRepresentation()` decoded to an object, `summary` e.g.
  "EXC_BAD_ACCESS (SIGSEGV)", per-report `app`/`build`/`os` from `metaData`, `diag` if a debug session was on). No
  device id. Keep unsent ones (≤ 20) and retry at the next launch. Don't send daily `MXMetricPayload`s.
- **Dev configuration:** add a `Dev` build configuration copied from Debug (`-Onone`, `DEBUG` + `DEV` compilation
  conditions, assertions on), archived by `ios.yml` for `develop` → TestFlight with distribution signing; `Release`
  for `main`. dSYMs: keep uploading them to App Store Connect, and in the archive job also upload
  `…xcarchive/dSYMs/` as the artifact **`dsyms-<build number>`** (`actions/upload-artifact`, `retention-days: 90`,
  one per build, every branch) so `symbolicate.yml` finds them.
- **Workflow:** add `.github/workflows/symbolicate.yml` (content in this entry's notes, provided by attnd-web; it
  must be on `main` to be dispatchable). No secret is needed in attnd-ios (it uses `github.token`).
- **Privacy labels** (App Store Connect → App Privacy): "Diagnostics → Crash data, Performance data, Other diagnostic
  data: not linked to the user's identity, not used for tracking".
- Commit with `API-013:`; tick the iOS column.

**Android:**
- **Logging facade** `AppLog` (`AppLog.debug(Cat.NET, "net.request.finished", "…", mapOf(…))`): level checked first,
  records written on a single background dispatcher (`Dispatchers.IO.limitedParallelism(1)`). Each record goes to
  `android.util.Log` with tag `Attnd/<cat>` **and** the ring buffer. Same record format, names and redaction rules
  as iOS, unit-tested on the JVM (`LogRedactionTest`, `LogRecordTest`).
- **Ring buffer:** JSON lines in `filesDir/logs/` (excluded from backup in `data_extraction_rules.xml`), ~2 MB, newest
  kept.
- **Trace sections:** `android.os.Trace` (`trace("bundle_fetch") { … }`) around launch, bundle fetch + decode,
  planner, map first render (also `perf.signpost` with `ms`).
- **Every API call** (`AttndApi`/`HttpClient`) sends `X-Request-Id` and logs it as `req_id`; while a debug session
  is on also `X-Diag-Session`.
- **Levels:** `BuildConfig.DEV` builds `debug` + continuous upload (`reason: "dev"`, one persistent code per install
  in `DevicePrefs`); production `warning`, local only.
- **Debug mode:** the same Options → About & support flow as iOS (consent, countdown, code with Copy, Stop, 10 min,
  5-min pre-roll, survives restarts via `DevicePrefs`, never linked to the device id, never remote).
- **Uploads:** the same endpoint and rules as iOS (gzip, ≤ 500 / 256 KB, `Retry-After`, background, never blocking).
- **Log viewer:** dev builds always; production while debug mode is on and "Recent logs" in About & support; filters
  level/category/text/errors only; export `.jsonl` through the share sheet (a `FileProvider` only if needed; or
  `Intent.ACTION_SEND` with the text for small exports).
- **Reports:** at launch read `ActivityManager.getHistoricalProcessExitReasons(packageName, 0, 10)` newer than the
  last one handled (timestamp in `DevicePrefs`): `REASON_CRASH`/`REASON_CRASH_NATIVE` → `crash`, `REASON_ANR` → `anr`
  (with `getTraceInputStream()` text as `stack`, ≤ 128 KB), `REASON_LOW_MEMORY` → `low_memory`, others (excluding
  user-requested / exit-self) → `exit`; `reason` = the reason's name, `occurred_at` = `timestamp`. Add an uncaught
  exception handler (chained to the previous one) that writes the **obfuscated** Java stack trace + exception
  type/first message line to `filesDir/logs/crash.txt`; send it with the matching exit info (or alone) at the next
  launch as `crash` with `stack`. `build` = `versionCode` (that's what the mapping is keyed by). No device id.
- **Build types:** add `dev` (`initWith(release)`: release-signed, **not debuggable** (Play rejects debuggable
  bundles), `isMinifyEnabled = false`, `buildConfigField DEV = true`) for `develop` → Play internal testing;
  `release` keeps R8 minify with `DEV = false` for `main` → production. Debug builds also count as DEV for logging.
- **Mapping upload (CI, `android.yml`, every minified release build):** in the same Play edit as the bundle upload,
  `edits.deobfuscationfiles.upload` (`deobfuscationFileType: proguard`) for the version code; and upload the same file
  to attnd-web: `POST /api/diagnostics/symbols/?platform=android&version=<versionName>&build=<versionCode>` with
  `Authorization: Bearer $ATTND_PROD_CI_KEY` (production server) / `$ATTND_API_KEY` (dev server), body gzip (see
  `API_V2.md`). A failed attnd-web upload is a warning, never a failed release. (Native symbols: none today.)
- **Play data safety:** "Crash logs" and "Diagnostics" collected, not shared, not linked, debug logs optional.
- Commit with `API-013:`; tick the Android column.

**Notes — `symbolicate.yml` for attnd-ios** (inputs `report_id`, `build`, `version`, `server`, `token`; runs on
`macos-latest`; reads the frames from `GET /api/diagnostics/symbolicate/<id>/`, downloads artifact `dsyms-<build>`,
maps `binaryUUID` → DWARF with `dwarfdump --uuid`, `atos -arch <arch> -o <DWARF> -l 0x100000000 <0x100000000 +
offset>…`, posts `{frames: [{uuid, offset, symbol}]}` or `{error}`; only ever to `https://attnd.app` or
`https://dev.attnd.app`). The file content was handed to attnd-ios with this entry.


### API-011: urgent announcements and Live Activity push
**Why:** organizers need one way to reach everyone at once for a cancellation, a weather hold or a safety notice, and
iPhone attendees who follow the event on their Lock Screen (Live Activity, shared spec §8) should see it there.
**Server (additive):**
- `Notification.priority`: `normal` (default) or `urgent`. The console's "Send an update" form has an **Urgent**
  checkbox with a warning (everyone in the event, right away, Lock Screen Live Activities); urgent ones are highlighted.
- Bundle: every notification gains `"priority": "normal" | "urgent"`. Notification pushes gain `priority` (APNs top-level
  key next to `aps`; FCM `data`), for normal ones too.
- An urgent notification goes to **every** device in the event with a push token, whatever `subscribed` says:
  - APNs alert, `aps.interruption-level: "time-sensitive"` (never `critical`), priority 10, `apns-collapse-id:
    notification-<id>`, expires after 12 h.
  - FCM **data-only**, Android priority `high`, TTL 12 h; `data` = `type: notification`, `event_id`, `notification_id`,
    `priority: urgent`, `title`, `body` (no `notification` block, so the app chooses the channel).
  - plus, for iOS, an ActivityKit push to every `live_activity_token` registered in the event: `apns-push-type:
    liveactivity`, topic `<bundle id>.push-type.liveactivity`, priority 10, payload
    `aps: {timestamp, event: "update", content-state: {"urgent": {id, title, body, sent_at}}, alert: {title, body, sound},
    stale-date: sent + 12 h, relevance-score: 100}` (`sent_at` is an ISO 8601 string). APNs `410` / `BadDeviceToken` /
    `Unregistered` clears that device's `live_activity_token`.
- Limit: at most 3 urgent notifications per event per hour (`URGENT_MAX_PER_HOUR`). The console refuses a 4th "Send
  now"; a scheduled one over the limit goes out as normal (and is stored as normal).
- `POST devices/` (and `join/`) accept an optional `live_activity_token` (hex; `null` or `""` clears it; non-hex is
  ignored; leaving it out keeps it). It is cleared when the device leaves or joins another event.
- DEMO: the sent "Set time change" notification is `urgent`; the other three are `normal`.

**iOS:**
- Decode `Notification.priority` (default `normal`, unknown → normal). Highlight urgent updates in the Updates tab and
  use them for the urgent block of the Live Activity (§8).
- Push: an alert with `priority = "urgent"` (top-level key) is shown time-sensitive by iOS: add the **Time Sensitive
  Notifications** capability (entitlement `com.apple.developer.usernotifications.time-sensitive`). Don't add Critical
  Alerts. Tapping opens Updates at `notification_id`, as today; refresh the bundle.
- Live Activity: start activities with `pushType: .token`; send each token from `pushTokenUpdates` (hex) with
  `POST devices/` as `live_activity_token`; send `null` when the activity ends or the user turns "Follow on Lock Screen"
  off. Not needed after leave/switch (the server clears it), but harmless.
- `ContentState`: every property optional, so a push that carries **only** `urgent` decodes (the push replaces the whole
  state). `urgent: {id: Int, title: String, body: String, sent_at: String}` — `sent_at` is ISO 8601 text, **not** `Date`
  (ActivityKit's default decoder would read a `Date` as seconds since 2001). When the app next updates the activity
  itself, keep `urgent` until it's dismissed or 12 h old, and refill the other fields. The alert lights up the screen /
  expands the Dynamic Island; the push also sets `stale-date` = sent + 12 h, which the app's next update replaces.
- The App Clip: decode `priority` only (no Live Activity, no token).

**Android:**
- Decode `Notification.priority` (default `normal`). Highlight urgent updates in the Updates tab.
- FCM data `type=notification` with `priority=urgent`: data-only (title/body in `data`), high priority. Show it on a new
  **urgent** notification channel (high importance) whenever `POST_NOTIFICATIONS` is granted, **even if the in-app
  notification preference is off** (the organizer marked it urgent; the server sends it to every device with a token).
  Tap → Updates at `notification_id`; refresh the bundle; switch the Live Update (§8) to urgent mode for up to 12 h or
  until dismissed. Normal notifications are unchanged (they now carry `priority=normal` in `data`).
- Never send `live_activity_token`.

### API-010: tags for locations and schedule items
**Why:** "Hearted" filters, the planner and search (shared spec §5–6) work on genre/category/audience tags
("live music", "country", "bbq", "kid-friendly", "free"), so every location and schedule item needs a few consistent ones.
**Server (additive, plus a case change):**
- `Location.tags` (new, in the bundle's locations). Lockable like other fields: a person's edit is never overwritten by
  the AI. The console editors show and edit tags (comma separated) and list them.
- Tags are normalised everywhere (`events.models.normalize_tags`): lower case, trimmed, single-spaced, de-duplicated, at
  most 8 of at most 32 characters. Schedule item tags already in the database were normalised by migration `0034`
  (which bumped those events' `content_version`), so **`"Music"` is now `"music"`**.
- AI import/refresh (`scrape/service.py`, strict structured output): a per-event `tag_vocabulary` and 2–6 tags per
  location and item from it; refreshes are given the current vocabulary and tags to reuse; `scrape/tags.py` folds
  spelling variants ("Kids friendly", "BBQs") onto the event's existing spelling. Tag changes count as minor.
- `manage.py backfill_tags (<code|id> … | --all) [--dry-run]` tags existing events with the AI: only things with fewer
  than 2 tags, keeping their tags first, never touching locked tags; one revision and one version bump per event; safe
  to re-run.
- DEMO (hand-written, counts unchanged): every location and item has 2–6 tags; music items `["live music", <genre>]` (36),
  chef demos `["food", "demo", …]` (9), "Face painting & games" `["family", "kid-friendly", "free"]` (3), the general items
  `["general", "free"]` (3); Main Stage `["live music", "headliners", "free"]`.

**iOS / Android:**
- Decode `Location.tags` (default `[]`) like `ScheduleItem.tags`. Treat tags as lower-case values: filter and search on
  them as sent; capitalise only for display (e.g. "Live music").
- Search already weighs tags 2 for schedule items: apply the same weight to location tags (update the shared search
  spec cases if they assert on tags).
- **Live tests (DEMO facts changed, update all three suites together):** `tags == ["General"]` → `["general", "free"]`;
  `"Music" in tags` → `"live music" in tags` (still 36, all on the three stages); `Food`/`Demo` → `food`/`demo` (still 9,
  all at Chef Demo Kitchen); Face painting `["Family"]` → contains `family` and `kid-friendly`. New facts: every location
  and item has 2–6 lower-case tags; Main Stage's are `["live music", "headliners", "free"]`. The web suite
  (`live_tests/`) is already updated; it and the app suites pass against a server only after DEMO is reloaded there.

### API-009: anonymous heart counts
**Why:** attendees heart locations and schedule items (shared spec §5); organizers want to see what people liked,
without knowing who.
**Server (additive):** `POST events/<id>/usage/` accepts `location_heart` / `location_unheart` (detail = location id) and
`schedule_item_heart` / `schedule_item_unheart` (detail = schedule item id), validated like `location_view` (a heart
without an id, or an id from another event, is dropped). CI keys keep discarding usage. Console: **Usage** shows hearts
given/removed and the most hearted locations and items (net = hearts − unhearts over the event, never below 0); the
Locations and Schedule lists show each one's net count; the event overview has a **Most hearted** list.
**iOS / Android:** hearts are stored on the device per event (spec §5). Each time the attendee turns a heart on, record
`location_heart` / `schedule_item_heart` with the id; each time they turn it off, the `…_unheart` metric. Use the existing
usage reporter (same 5-minute buckets, flushes and retry rules, never a device id). Don't record anything when hearts are
restored at launch, re-joined or cleared because the event was left.

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
