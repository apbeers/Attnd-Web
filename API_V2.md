# Attnd API v2

v2 is what the current iOS and Android apps use. The v1 events API was retired on Nov 19, 2026 (API-020): `/api/v1/events/…` answers `410` `update_required`; the attestation endpoints stay at `/api/v1/attest/`.

- **Base URL:** `https://attnd.app/api/v2/`
- **Authentication:** send `Authorization: Bearer <attestation JWT>`. In dev and CI builds you can send the static key instead. When you get a `401`, invalidate the JWT, re-attest, and retry once.
- **CI keys (API-008):** live integration tests may send a CI key (server setting `CI_API_KEYS`) instead. It works even when the
  static key is turned off (production), but only for the DEMO event (`DEMO_EVENT_CODE`):
  - `events/lookup/` answers `404` for any other event (exactly like an unknown code); `events/nearby/` lists only DEMO;
  - `events/<id>/bundle|join|leave|usage/` answer `404` unless `<id>` is DEMO;
  - `devices/`, `join/` and `leave/` take only device ids starting `ci-` (`403` otherwise), e.g. `ci-ios-<run>`;
  - `usage/` answers exactly as for a real app (same `accepted`) but records nothing;
  - every other API path answers `401`. `/api/app-status/` needs no key at all.

  Devices whose id starts with `ci-` (with any key) are left out of organizer audience counts, install/join/leave
  counts and pushes, so test runs don't show up in DEMO's stats. Apps never send a CI key.
- **Request ids (API-013):** send `X-Request-Id: <random>` with every call (and `X-Diag-Session: D-XXXXX` during a
  debug session); the response echoes `X-Request-Id`. See Diagnostics.
- **Timestamps:** ISO 8601 with a UTC offset. Display times in the **event's** `timezone`, not the device's.
- **Coordinates:** may be `null`, meaning unknown. Never drop a pin for a `null` coordinate, and never treat `0,0` as a real location.
- **Forward compatibility:** clients **must ignore unknown keys** and **must fall back to defaults for missing keys**, in every object, and especially in `display`.
- **Changes:** every change to this contract is logged in [`API_CHANGES.md`](API_CHANGES.md), with what each app must do. v2 only ever grows (new keys and endpoints). Anything that would break installed apps goes into a new version instead. `contract/v2.json` is the machine-checked snapshot.

---

## Endpoints

### `GET events/nearby/?lat=<float>&lng=<float>`
Returns published, listed events within roughly 35 miles that start between 3 days ago and 7 days from now. Results are sorted by relevance.
```json
{ "count": 1, "results": [ { "event": <Event>, "distance_miles": 1.2, "starts_in_hours": 5.0 } ] }
```

### `GET events/lookup/?q=<event code or exact name>`
Finds an event by exact code, case-insensitive, then by exact name. Unlisted events are included, so this is what QR codes and deep links use. Drafts are never returned.
```json
{ "event": <Event> }
```
`404` means no live event matches.

### `GET events/<id>/bundle/`
Returns everything the app shows, in one response.
- **Caching:** send `If-None-Match: <ETag from last response>`. You get `304 Not Modified` if nothing changed.
- **Device tracking:** optionally send `X-Device-Id: <device id>`, which updates the device's last-seen time for organizer stats.
- **When to call it:**
  - on join
  - when the app comes to the foreground
  - on pull-to-refresh
  - when a `content_updated` push arrives (API-007), **only if its `content_version` is newer than the bundle you have**, after
    a random 0–10 s delay so every phone in the event doesn't refresh at the same moment. The server sends it about 3 s after the
    last change, at most once every 15 s per event, and collapses it per event (an offline device gets only the latest).
  - **while the app is open (foreground) and in an event, as a safety poll** (catches lost or throttled pushes):
    - every **60 seconds** when the device has **no push token** (push unavailable or not granted, the iOS App Clip);
    - every **5 minutes** when it has one.

    With `If-None-Match` an unchanged bundle is a bodiless `304`. Don't show a spinner or an error for these refreshes.
    (Builds before API-007 poll every 5 s; the server still supports that.)
```json
{
  "schema_version": 2,
  "content_version": 15,
  "updated_at": "2026-11-14T13:29:44+00:00",
  "event": <Event>,
  "display": <Display>,
  "categories": ["Food", "Stage"],
  "locations": [<Location>],
  "schedule": [<ScheduleItem>],
  "notifications": [<Notification>],
  "tickets": null,
  "plan_suggestions": [{"text": "Plan my Thursday evening around the galleries", "kind": "plan"}, …]
}
```
`plan_suggestions` (API-021): up to 4 "Plan your day" examples written for this event, in order, each
`{"text": string, "kind": "plan" | "question"}`: plan requests on the event's real days and highlights, a short-visit
one ("I've got 2 hours — show me the highlights"), a family one only when items have a family tag, and at most one
question, last ("What's on near me right now?" or one for this event). Tapping one sends its `text` to the app's
planner (`plan`) or asks it (`question`). Written in the background by the server (Claude Haiku 5.5, checked against
the event's days; a deterministic fallback from the event's real dates, categories and tags otherwise), so it is
always present in new bundles, but **decode leniently**: a missing key, `null`, an empty list, entries without `text`,
or an unknown `kind` → ignore that entry (or the key) and keep the app's own local examples. Older servers don't send it.
`tickets` (API-017): always `null` (ticketing parked, see Tickets). `event.context`, `locations[].context` and `schedule[].context` (API-018): see
Context. In `bundle/` only; `lookup/` and `nearby/` event objects have no `context` key.
Hidden locations and items are already filtered out. `locations` are already in the organizer's order, and `schedule` is sorted by `start_time`. `notifications` are newest first.

### `POST events/<id>/join/`
```json
{ "device_id": "uuid", "platform": "ios" | "android", "push_token": "…", "app_version": "2.0.0" }
```
→ `{ "status": "ok", "content_version": 15 }`. Only `device_id` is required. iOS also sends `app_clip` (API-019, see
`POST devices/`).

### `POST events/<id>/leave/`
```json
{ "device_id": "uuid" }
```
→ `{ "status": "ok" }`

### `POST devices/`
Registers or updates a device. Call it on launch, and whenever the push token or the notification preference changes.

**Always register for push and send `push_token`, even when notifications are off** (API-007): the token is stored whatever
`subscribed` says and is used for silent `content_updated` pushes (live updates). `subscribed` only controls visible organizer
alerts. iOS silent pushes need no notification permission (`registerForRemoteNotifications()` alone); Android FCM data messages
never need `POST_NOTIFICATIONS`. Send `"push_token": ""` only to clear a token you no longer have.
```json
{ "device_id": "uuid", "platform": "ios", "push_token": "…", "app_version": "2.0.0", "subscribed": true }
```
→ `{ "status": "ok", "subscribed": true, "event_id": 1 }`. `event_id` is `null` if the device hasn't joined an event.

**`live_activity_token` (API-011, iOS, optional):** the ActivityKit push token (`Activity.pushTokenUpdates`, as hex) of
the Live Activity the app is running for the joined event.
```json
{ "device_id": "uuid", "live_activity_token": "80f3a1…" }
```
- Send it whenever ActivityKit hands the app a new token; send `"live_activity_token": null` (or `""`) when the activity
  ends. Leaving the key out leaves the stored token unchanged. A value that isn't hex is ignored (no error).
- The server clears it by itself when the device leaves the event or joins another one (`join/` may carry the new
  token in the same body), and when APNs reports it invalid (`410`, `BadDeviceToken`).
- It is only used for urgent announcements (see Push notifications). Android never sends it.

**`app_clip` (API-019, iOS, optional boolean):** `true` when the request comes from the App Clip (bundle id
`com.arcu.Attnd.clip`), `false` from the app. Accepted in `join/` and `devices/` bodies. Leaving the key out (or sending
anything but a boolean) keeps the stored value; new devices default to `false`. The server uses it to push to the
device on the right APNs topic (see Push notifications). Android never sends it.

---

### `POST events/<id>/usage/` (API-005)
Anonymous usage counts. **Never send a device ID or anything identifying**; the server ignores one if sent.
```json
{"platform": "ios", "events": [
  {"name": "tab_view", "detail": "map", "count": 3, "at": "2026-11-14T16:05:00Z"},
  {"name": "location_view", "detail": "42"}, {"name": "app_open"}]}
```
→ `{"status": "ok", "accepted": 3}`

| `name` | `detail` |
|--------|----------|
| `app_open` | none. The app came to the foreground (or launched) while in this event. |
| `tab_view` | `map`, `locations`, `schedule`, `flyer`, `notifications` or `options` |
| `location_view` | location id. A location's detail was opened, from a list, a map pin or a schedule item. |
| `schedule_item_view` | schedule item id |
| `notification_open` | notification id when known. A push notification was tapped. |
| `search` | none. A search was run. **Never send the search text.** |
| `refresh` | none. Pull to refresh. |
| `location_heart` / `location_unheart` (API-009) | location id (required). The attendee hearted / removed the heart from a location. |
| `schedule_item_heart` / `schedule_item_unheart` (API-009) | schedule item id (required). The same for a schedule item. |
| `perf_…` (API-014) | the bucket (see Performance below). Anonymous timings and failures. |

- **Hearts (API-009):** send one `…_heart` each time the attendee turns a heart on and one `…_unheart` each time they
  turn it off, through the same reporter, buckets and rules as every other count (no device id, ever). Don't re-send
  hearts that are restored from storage at launch. The console shows **net** hearts per item (hearts − unhearts over
  the event, never below 0). A heart without an id, or with an id from another event, is dropped.

- **Performance (API-014):** anonymous, bucketed timings and failure counts, sent through this same endpoint and
  reporter. `detail` is the **bucket label, exactly as written here** (ASCII, lower case); an unknown bucket, or a
  missing one where one is required, drops the entry. Measure on the device, put the value into its bucket, count it
  like any other metric. Only while in an event: a value measured outside an event is added to the next flush made
  while in one, or dropped.

  | `name` | `detail` (bucket) | What is measured |
  |--------|-------------------|------------------|
  | `perf_launch_cold` | `<0.5s`, `<1s`, `<2s`, `<4s`, `>=4s` | process start → first frame of the event UI (cold launch) |
  | `perf_launch_warm` | same | return to the foreground → interactive |
  | `perf_bundle_fetch` | `<250ms`, `<500ms`, `<1s`, `<2s`, `<5s`, `>=5s` | `GET bundle/` answered `200`: request start → decoded |
  | `perf_bundle_304` | same as `perf_bundle_fetch` | `GET bundle/` answered `304` |
  | `perf_api_error` | `4xx`, `5xx`, `timeout`, `offline` | any API call that failed (one count per failed call, retries included) |
  | `perf_push_refresh_delay` | `<5s`, `<10s`, `<15s`, `<30s`, `<60s`, `>=60s` | `content_updated` push received → the new bundle shown (includes the random 0–10 s delay) |
  | `perf_live_start_fail` | none, or `disabled`, `denied`, `limit`, `error` | a Live Activity (iOS) / Live Update (Android) could not start |
  | `perf_planner_ms` | `<100ms`, `<250ms`, `<500ms`, `<1s`, `<2s`, `>=2s` | one planner run (preference extraction excluded) |
  | `perf_map_first_render` | `<250ms`, `<500ms`, `<1s`, `<2s`, `<4s`, `>=4s` | Map tab opened → first pins drawn |

  A bucket `<X` means "less than X" and the buckets don't overlap: 480 ms is `<500ms`, 500 ms is `<1s`, 5 s is `>=5s`.
  These counts are for staff only (Diagnostics panel); organizers never see them and they never count as interactions.

- `count` is optional (default 1). `at` is optional ISO 8601, used to place the count in its UTC 5-minute window (within the last 7 days). Up to 200 entries per request.
- Unknown names, invalid details and ids from other events are dropped silently. A malformed body still returns `200`.
- **Client behaviour:** aggregate in memory by (name, detail, UTC 5-minute window, sending its start as `at`) and send at most every ~60 seconds, when the app goes to the background, and before leaving an event. Drop the batch after one failed retry. Analytics must never block or slow the UI, and never show errors.
- The server counts joins, leaves and new installs itself; don't send those.

## Diagnostics (API-013)

Debug logs and anonymous crash/hang reports. The record format and every event name are in
[`LOG_EVENTS.md`](LOG_EVENTS.md) (the shared catalogue); only names listed there may be logged. Staff read them in
the console's **Diagnostics** panel (`/console/diagnostics/`). Nothing here is ever linked to a device id: a log
session is keyed by its random **debug code**, and reports carry no identifier at all.

### Request headers on every API call

- **`X-Request-Id`** (every call, every build): a new random id per request, 8–64 characters of `A-Z a-z 0-9 . _ : -`
  (e.g. 16 hex characters). The server logs it with the request and returns it in the response's `X-Request-Id`
  header (it makes one when the app sends none or an invalid one). Log it as `req_id` in the app's `net.request.*`
  records, so the app's line and the server's line for the same call join up in the panel. A retry is a new request
  with a new id.
- **`X-Diag-Session: D-XXXXX`** (only while a debug session is on: dev builds always, production only during debug
  mode): the server then also stores its own line for that request (`server.request`, category `server`) in that
  session.

### Debug codes

`D-` + 5 characters of Crockford base32 (`0-9 A-H J K M N P-T V-Z`, i.e. no `I L O U`), random, upper case:
`D-7K3QX`. Production: a new code each time the user turns debug logging on (10 minutes). Dev builds: one code per
install, made once and kept. The code is shown to the user (with Copy) and is the only thing that names the session.

### `POST diagnostics/logs/`
Upload a batch of log records of a debug session. Same auth as the rest of v2. Only dev builds (always, `reason:
"dev"`) and production builds during debug mode (`reason: "debug_mode"`, including the 5-minute pre-roll from the ring
buffer) upload; production never uploads otherwise.

- Body: JSON, **gzip-compressed** with `Content-Encoding: gzip` (plain JSON is accepted too). At most **256 KB as
  sent** and 2 MB decompressed, at most **500 entries**.
```json
{"session": "D-7K3QX", "platform": "ios", "app": "2.0.131", "build": "131", "os": "iOS 27.2",
 "model": "iPhone18,1", "reason": "debug_mode",
 "entries": [{"v": 1, "ts": "2026-11-15T19:04:12.381Z", "mono_ms": 523101, "seq": 4182, "level": "debug",
              "cat": "net", "event": "net.request.finished", "msg": "GET bundle 304 in 182 ms",
              "f": {"method": "GET", "path": "/api/v2/events/{id}/bundle/", "status": 304, "ms": 182,
                    "req_id": "9f2c41d07a3b5e68", "cache": "304"},
              "app": "2.0.131", "build": "131", "os": "iOS 27.2", "model": "iPhone18,1", "state": "fg",
              "net": "wifi", "event_id": 5, "diag": "D-7K3QX"}]}
```
→ `200 {"status": "ok", "accepted": 1, "rejected": 0}`

- `session` must be a debug code, `platform` `ios`/`android`, `reason` `dev`/`debug_mode`, `entries` a list; otherwise
  `400` (`{"error": "…"}`). Too large / too many entries: `413`. Send what's left in smaller batches.
- **Each record** is checked on its own; an invalid one is dropped and counted in `rejected` (the others are kept):
  `v` must be `1`; `level` one of `trace debug info notice warning error fault` (or 0–6); `cat` one of the app
  categories (`net push background live location map planner assistant search storage ui perf auth lifecycle
  logging`; `server` is the server's own); `event` a dotted lower-case name (2–5 parts, ≤ 64 characters); `ts` ISO 8601
  with a zone, not older than the retention (7 / 14 days) and not more than a day ahead; `diag`, if present, the same
  code as `session`. `msg` is cut at 2,000 characters, `f` (an object) is replaced by `{"_truncated": true}` above
  4 KB of JSON.
- **The server scrubs `f` again** (the apps must already redact, see `LOG_EVENTS.md`): keys naming tokens, passwords,
  secrets, authorization, API keys, emails, device ids, support codes, cookies or JWTs are dropped; so are `query`,
  `text`, `prompt` and `q` except from dev builds (`…_len`/`…_length`/`…_count` keys are kept); `url`/`path`/`href`/
  `uri`/`link` lose their query string; `lat`/`lng` are rounded to 0.01°.
- **Caps: `429` with `Retry-After` (seconds)** and `{"error": …, "retry_after": n}`:
  - a session takes at most **20,000 entries per 10 minutes** (the window starts with the first accepted batch);
    `Retry-After` is the rest of the window;
  - while the diagnostics storage is full (250 MB), every upload: `Retry-After: 3600`;
  - at most 600 uploads per 10 minutes from one network.

  On `429` keep the records in the ring buffer and try again after `Retry-After`, not before. Records the ring buffer
  overwrites meanwhile are lost (log `logging.dropped` when they are).
- With a CI key the answer is the same and nothing is stored (API-008).
- **Retention:** entries from dev builds are deleted after **7 days**, from debug mode after **14 days**; when the
  storage passes 250 MB the oldest sessions go first.
- **Client behaviour:** batch every ~10 s (dev builds: every ~30 s or 200 records, whichever first) and when the app
  goes to the background; upload off the main thread; never show an error; failures stay in the ring buffer and are
  retried on the next batch. The upload itself is logged as `logging.upload` (into the next batch).

### `POST diagnostics/reports/`
Anonymous crash, hang and exit reports, from every production and dev build (no opt-in needed: no identifier is
sent). iOS: MetricKit `MXDiagnosticPayload` (crash, hang, CPU, disk-write and launch diagnostics). Android:
`ActivityManager.getHistoricalProcessExitReasons` read at the next launch (crash, ANR, low memory, other exits;
ANR thread dump from `getTraceInputStream`), plus the app's own uncaught-exception handler writing the Java stack
trace to a file that is sent at the next launch. Same auth as the rest of v2. Same body limits as `logs/` (gzip,
≤ 256 KB as sent, ≤ 2 MB decompressed); at most **20 reports** per request, each ≤ 256 KB.
```json
{"platform": "android", "app": "2.0.131", "build": "131000", "os": "Android 16", "model": "Pixel 9",
 "reports": [{"kind": "crash", "occurred_at": "2026-11-15T18:02:11Z",
              "exception": {"type": "java.lang.IllegalStateException", "message": "bundle missing"},
              "stack": "java.lang.IllegalStateException: bundle missing\n\tat a.b.c.b(SourceFile:3)\n…",
              "reason": "CRASH", "diag": "D-7K3QX"}]}
```
```json
{"platform": "ios", "app": "2.0.131", "build": "131", "os": "iOS 27.2", "model": "iPhone18,1",
 "reports": [{"kind": "crash", "occurred_at": "2026-11-15T18:02:11Z", "summary": "EXC_BAD_ACCESS (SIGSEGV)",
              "payload": <MXCrashDiagnostic.jsonRepresentation() as an object>}]}
```
→ `200 {"status": "ok", "accepted": 1, "rejected": 0}`

- `kind`: `crash`, `hang`, `anr`, `low_memory`, `exit`, `launch`, `cpu`, `disk_write`, `memory` or `metrics`
  (anything else is rejected). `occurred_at` (ISO 8601) optional. `app`/`build`/`os`/`model` per report override the
  top-level ones (MetricKit can deliver a report from the previous app version).
- `exception` `{type, message}` (first line of the message, ≤ 280 characters, never user text) and/or `summary`
  (≤ 500); `reason` (≤ 200: Android's exit reason name, iOS termination reason/signal).
- Android: `stack` = the **obfuscated** stack trace or ANR dump as text (≤ 128 KB). The server retraces it with the
  R8 mapping uploaded for that `build` (version code).
- iOS: `payload` = the diagnostic's `jsonRepresentation()` decoded to an object (it carries `callStackTree` with
  `binaryUUID`, `binaryName`, `offsetIntoBinaryTextSegment`, and `diagnosticMetaData`). Staff symbolicate it from the
  panel with the build's dSYMs. Daily `MXMetricPayload`s are **not** sent (use API-014 counts).
- `diag`: the debug code, only if a debug session was on when it happened (links the report to the session).
- Never a device id, push token, user text or location.
- Caps: `429` + `Retry-After` while the storage is full (`3600`) or after 60 reports-requests per hour from one
  network. Reports are deleted after **30 days**. A CI key gets the same answer and nothing is stored.
- **Client behaviour:** send at launch / when MetricKit delivers, off the main thread; on failure keep them (at most
  20, the newest) and retry at the next launch; drop them after 7 days.

### `POST /api/diagnostics/symbols/` (CI only, outside the version)
attnd-android's release job uploads the R8 `mapping.txt` of every minified build (the same file it gives Play with
`edits.deobfuscationfiles`).
- `POST /api/diagnostics/symbols/?platform=android&version=<versionName>&build=<versionCode>[&kind=r8_mapping]`,
  body = the mapping file, plain or gzip (≤ 25 MB as sent, ≤ 200 MB decompressed),
  `Authorization: Bearer <CI key>` (`ATTND_PROD_CI_KEY` for production, the dev static key `ATTND_API_KEY` for dev;
  the static key works only where it is enabled). Any other bearer: `401`.
- → `200 {"status": "ok", "id": 12, "bytes": 4518123, "sha256": "…"}`. The same `build` again replaces the file.
  `400` for anything that isn't an R8 mapping or a missing `version`/`build`. The newest 20 mappings are kept.
```bash
gzip -c app/build/outputs/mapping/release/mapping.txt | curl -sf -X POST \
  -H "Authorization: Bearer $ATTND_CI_KEY" -H "Content-Type: application/gzip" --data-binary @- \
  "https://attnd.app/api/diagnostics/symbols/?platform=android&version=$VERSION_NAME&build=$VERSION_CODE"
```

### `GET|POST /api/diagnostics/symbolicate/<report id>/` (attnd-ios workflow only)
Used only by attnd-ios's `symbolicate.yml`, which the panel dispatches with a one-time token (input `token`, valid
2 hours, `Authorization: Bearer <token>`; anything else `401`).
- `GET` → `{"report_id": 42, "platform": "ios", "app": "2.0.131", "build": "131",
  "frames": [{"uuid": "3F2A…", "binary": "Attnd", "offset": 123456}]}` (unique frames, crashed thread first).
- `POST {"frames": [{"uuid": "3F2A…", "offset": 123456, "symbol": "EventViewModel.refresh() (EventViewModel.swift:42)"}]}`
  or `{"error": "No dSYM for build 131"}` → `{"status": "ok", "symbols": 1}`. The token is used up by the POST.

---


## Tickets (API-017, parked)

Ticketing is **parked** (Nov 19, 2026, owner: organizers sell tickets elsewhere, e.g. Eventbrite). Its code, spec and
the old `tickets/…` and `door/…` endpoints live on the git branch `parked/tickets` only; `develop` has none of them (and `main` from the next release): those
paths are a plain JSON `404`.
`event.tickets_url` and `bundle.tickets` **stay in v2 and are always `null`**, so installed apps decode as before; apps
show no ticket UI while they are `null`.

## Outside the version: update check (API-003)

### `GET /api/app-status/?platform=ios|android&version=<app version>&api=<API version the build speaks>`
Full path, **not** under `/api/v2/`, and **no `Authorization` header**. It works even for a build whose API version was retired or whose attestation is failing. Its shape is frozen: keys are only ever added.
```json
{"update": "none" | "available" | "required", "min_version": "2.0.50" | null, "latest_version": "2.0.90" | null,
 "update_url": "https://apps.apple.com/app/id1470627406", "message": "…" | null}
```
- `required`: show a blocking "Update Attnd" screen with a button to `update_url`. This happens when the build is older than `min_version`, or its `api` version is no longer served.
- `available`: show a dismissible banner, at most once per `latest_version`.
- `none`, or any value the app doesn't know: no prompt.

Call it on launch and whenever the app returns to the foreground.

**Retired API versions:** any `/api/vN/…` call to a version the server no longer serves gets `410 Gone` with `{"error": "update_required", "update_url": …}`. Treat `410` or `426` from any API call as `update: required`.

## Outside the version: shared plan links (API-015)

`https://attnd.app/p/<EVENT_CODE>/<payload>` (a trailing `/` is optional) is a web link, not an API call: no
`Authorization`, nothing stored. The apps make it to share a plan or an item and open it via Universal Links / App
Links (the apple-app-site-association claims `/p/*` for the app and the App Clip, `/p/*/*/qr.svg` excluded; the
Digital Asset Links file is domain-wide, so the Android app's verified intent filter decides).
- `payload` = base64url **without padding** of compact JSON (`{"v":1,"ids":[101,114],"d":"2026-11-14"}` →
  `eyJ2IjoxLCJpZHMiOlsxMDEsMTE0XSwiZCI6IjIwMjYtMTEtMTQifQ`), at most 1,500 characters. `v` is `1`; `ids` are
  schedule item ids (integers) of that event; `d` (optional) is the plan's event day. No personal data.
- The server reads it leniently (padding, `"s12"` ids and a malformed `d` are tolerated; unknown or hidden ids are
  skipped) and serves it like `/join/<code>/`: Android → Google Play with `referrer=event_code%3D<code>`; iPhone → the
  App Clip banner + App Store link; desktop → the event's join page listing the shared items (name, time in the event's
  time zone, place) with a QR code of the link (`/p/<code>/<payload>/qr.svg`). An invalid payload (not base64url
  JSON, `v` ≠ 1, `ids` not a list, too long) shows the plain join page; an unknown or draft event is the join `404`.
  Opens count as `link_open` like join links.
- Apps: treat ids not in the bundle as gone; a payload with `v` > 1 that the app can't read joins the event only.

## Objects

### Event
```json
{
  "id": 1, "name": "…", "event_code": "FWFM2026", "description": "…", "address": "…",
  "latitude": 32.75, "longitude": -97.33,
  "start_time": "…", "end_time": "…" | null, "timezone": "America/Chicago",
  "status": "published" | "archived",
  "paid": false, "is_demo": true,
  "flyer_url": "https://…" | null,
  "accent_color": "#FF5A1F" | null, "logo_url": "https://…" | null,
  "content_version": 15,
  "join_url": "https://attnd.app/join/FWFM2026/",
  "website_url": "https://sites.attnd-example.com/fw-fair/" | null,
  "tickets_url": null
}
```
- `tickets_url` (API-017): always `null` (ticketing parked, see Tickets).
- `is_demo == true`: show the prominent **Demo Event** banner.
- `website_url` (API-016): the event's website hosted by Attnd Studio while it is published, else `null` (missing in
  older responses = `null`). Apps show a **Website** link (opens in the browser). It is on a separate domain from
  attnd.app (`SITES_HOST`), so never send credentials or the device id to it.
- `status == "archived"`: show an "This event has ended" banner. The content is still viewable.
- `context` (API-018, `bundle/` only): see Context.

### Location
```json
{ "id": 5, "name": "…", "snippet": "…", "description": "…", "category": "Food" | null,
  "website": "https://…" | null, "image_url": "https://…" | null, "image_credit": "…" | null, "address": "…",
  "latitude": 32.74 | null, "longitude": -97.36 | null, "sort_order": 0,
  "tags": ["food", "bbq", "kid-friendly"] }
```
- `image_credit` (API-022): set when Attnd found the location's photo automatically (its venue website, the event's
  pages or Wikimedia Commons), e.g. `"Jane Doe · CC BY-SA 4.0 · Wikimedia Commons"` or `"Photo: venue.example"`;
  `null` for the organizer's own images and when there is no image. Show it small on or under the image where the
  image is shown large (location detail). Missing (older servers) = `null`.
- `tags` (API-010): see Tags below. Missing in responses from servers before API-010: default `[]`.
- `context` (API-018): see Context. Missing = `null`.

### ScheduleItem
```json
{ "id": 1, "name": "…", "description": "…", "start_time": "…", "end_time": "…" | null,
  "location_id": 5 | null, "location_name": "…" | null, "tags": ["Music"] }
```
An item may have no location. Show it under "General" or "No location". `context` (API-018): see Context; missing =
`null`.

### Context (API-018)
Details the apps' **on-device assistant** answers questions from, fully offline: what the event's own pages say beyond
the description (FAQ, amenities, policies, menus, performers…). The AI import/refresh extracts them and organizers
edit them in the console ("Details for the assistant"; an edit locks the field against AI updates). Nothing in it is
organizer-private. Each of `event.context`, `location.context` and `schedule_item.context` is `null` or an object:
```
event.context = null | {
  "about": str,                 // long description, ≤ 4000 chars
  "faq": [{"q": str, "a": str}],        // ≤ 40; parking, shuttles, bag policy, re-entry, rules, weather plan, tickets at door, pets, smoking…
  "amenities": [{"kind": str, "name": str, "location_id": int|null, "note": str}],  // ≤ 60; kind ∈ restrooms|water|atm|first_aid|charging|lockers|info|accessible_entrance|food|merch|parking|shuttle|other
  "accessibility": str,         // ≤ 1500
  "policies": str,              // ≤ 1500 (bags, re-entry, age, prohibited items)
  "getting_there": str,         // ≤ 1500 (parking, transit, rideshare, shuttles)
  "weather_plan": str,          // ≤ 600 (rain/heat plan)
  "links": [{"title": str, "url": str}]  // ≤ 10, absolute https only
}
location.context = null | {
  "about": str,                 // ≤ 2000 (vendor/venue story)
  "menu": [{"name": str, "price": str, "note": str}],  // ≤ 40; price as written ("$8")
  "hours": str, "accessibility": str, "age_limit": str,  // short text, ≤ 300 each
  "links": [{"title": str, "url": str}]  // ≤ 5
}
schedule_item.context = null | {
  "about": str,                 // ≤ 2000
  "performers": [{"name": str, "bio": str (≤ 600), "genres": [str] (≤ 5, lower case)}],  // ≤ 10
  "age_limit": str, "price": str, "accessibility": str,  // ≤ 300 each
  "links": [{"title": str, "url": str}]  // ≤ 5
}
```
- **Every key is optional.** Empty strings and empty lists are omitted, so a key is either missing or non-empty; an
  object with nothing left is `null`. Decode leniently: ignore unknown keys and unknown amenity `kind`s (treat as
  `other`), default missing ones.
- Strings are trimmed; text over its limit is cut at a word boundary and ends in `…`. `about`, `accessibility`,
  `policies`, `getting_there`, `weather_plan` and FAQ answers may contain line breaks (`\n`, at most one empty line in
  a row); everything else is one line. Entry parts: FAQ `q` ≤ 300 / `a` ≤ 1000, names ≤ 120, notes ≤ 300, `price` in
  a menu ≤ 40, genres ≤ 32 each, link titles ≤ 120 (a link without a title gets its host).
- Entries are complete: a FAQ entry has `q` and `a`; amenities, menu items and performers have `name`; links have
  `url`. Optional parts inside an entry (`note`, `price`, `bio`, `genres`) are omitted when empty, like the keys above.
  An amenity always has `location_id`: a location in this bundle's `locations`, else `null`.
- `links[].url` is always an absolute `https` URL with a host. Open links in the browser; send nothing to them.
- The keys keep the order shown. The server caps all context in one bundle at about 150 KB of JSON: over it, item
  `about` texts are dropped first (last item first), then location `about` texts, then (only if still over) whole
  item / location contexts, last first.
- A change to any context bumps `content_version` (a `content_updated` push and a new ETag) like other content.

### Tags (API-010)
`Location.tags` and `ScheduleItem.tags` are lists of short labels for filtering and search: genre, category, audience,
cost (`"live music"`, `"country"`, `"bbq"`, `"food"`, `"drinks"`, `"workshop"`, `"kid-friendly"`, `"free"`, `"21+"`).
- Always **lower case**, trimmed, single-spaced, no duplicates, at most 8 tags of at most 32 characters. Since API-010
  schedule item tags are normalised the same way (an item tagged `"Music"` is now `"music"`); compare them as given.
  Capitalise for display if you like (`"Live music"`), but filter and search on the value as sent.
- The AI import gives every location and item 2–6 tags from one small vocabulary per event, so the same concept has the
  same tag across the event. Organizers can edit them; their edits win over later AI updates. An event may still have
  things with no tags (`[]`).

### Notification
```json
{ "id": 1, "title": "…", "body": "…", "send_time": "…", "priority": "normal" | "urgent" }
```
- `priority` (API-011): `urgent` is an organizer announcement such as a cancellation or a safety notice. Highlight it in
  the Updates list. Any other or missing value means `normal`.

### Display
This object holds the organizer's layout choices, already resolved by the server for this event's size. Clients render from it; they don't recompute it.

| Key | Values | Meaning |
|---|---|---|
| `tabs` | ordered subset of `map`, `locations`, `schedule`, `flyer`, `notifications` | The visible tabs, in order. Unknown values are ignored. |
| `default_tab` | one of `tabs` | The tab selected after joining. |
| `locations_style` | `cards` \| `list` \| `grouped` | `cards`: large image cards. `list`: compact rows with a thumbnail. `grouped`: sections by `category`. |
| `schedule_style` | `timeline` \| `by_day` \| `by_location` \| `now_next` | `timeline`: one chronological list. `by_day`: sections per day in the event's time zone. `by_location`: sections per location. `now_next`: a "Happening now" section, then "Up next" (the next 2 hours), then "Later". |
| `map_clustering` | bool | Group nearby pins into clusters. |
| `map_fit_all` | bool | On open, zoom to fit all pins (`true`), or centre on the event at street level (`false`). |
| `show_location_images` | bool | Show location images in lists and cards. |
| `enable_search` | bool | Show a search field on the Locations and Schedule tabs. |
| `group_by_category` | bool | Show category filter chips on Locations and the Map. |
| `secondary_pane` | `locations` \| `schedule` \| `now_next` \| `none` | On an unfolded foldable (iPhone Duo inner display, Android foldable inner display), what sits beside the map. |
| `accent_color` | `#RRGGBB` \| null | The app's tint colour for this event. `null` means the default brand colour. |
| `logo_url` | URL \| null | Shown in the event header. |
| `preset`, `resolved_preset`, `reason`, `stats`, `schema_version` | — | Informational only. Clients may ignore them. |

Client defaults when `display` or a key is missing:
- `tabs`: `[map, locations, schedule, notifications]`, plus `flyer` if `flyer_url` is set
- `default_tab`: `map`
- `locations_style`: `list`
- `schedule_style`: `by_day`
- `map_clustering`: `false`
- `map_fit_all`: `true`
- `show_location_images`: `true`
- `enable_search`: `true`
- `group_by_category`: `false`
- `secondary_pane`: `locations`

---

## Push notifications

The server sends pushes through APNs (iOS) and FCM HTTP v1 (Android) to devices that joined the event and have a `push_token`:
- **Organizer notifications** (visible) only to devices with `subscribed == true`.
- **Urgent organizer notifications** (API-011) to **every** such device, `subscribed` or not, at high priority, plus
  every iOS Live Activity registered with `live_activity_token`.
- **Content updated** (silent) to **every** such device, `subscribed` or not (API-007).
- **APNs topic (API-019):** iOS devices registered with `app_clip: true` get every push on the App Clip's topic
  (`com.arcu.Attnd.clip`; Live Activity updates `com.arcu.Attnd.clip.push-type.liveactivity`), the app's on the app's
  bundle id. The payloads are the same.

### Payloads
- **Organizer notification:**
  - Visible alert with `title` and `body`.
  - Custom data: `type = "notification"`, `event_id`, `notification_id`, `priority` (`"normal"` or `"urgent"`, API-011;
    missing means normal).
  - iOS: the custom keys are top-level in the APNs payload, next to `aps`.
  - Android: they're in the FCM `data` map, as strings.
- **Urgent organizer notification (API-011):** the same `type = "notification"` push with `priority = "urgent"`, sent the
  moment it goes out to every device in the event with a token (`subscribed` ignored), and kept for 12 hours at most.
  An event can send at most 3 urgent notifications an hour; beyond that they go out as normal ones.
  - iOS: alert push with `aps.interruption-level = "time-sensitive"` (never `critical`), `apns-priority: 10`,
    `apns-collapse-id: notification-<id>`. Time-sensitive delivery needs the app's **Time Sensitive Notifications**
    capability (`com.apple.developer.usernotifications.time-sensitive` entitlement); without it iOS shows it as a normal
    alert. Devices that haven't allowed alerts don't show it.
  - Android: **data-only** message (no `notification` block), Android priority `high`, TTL 12 h. `data` has `type`,
    `event_id`, `notification_id`, `priority = "urgent"`, `title`, `body`. Show it on an urgent channel, whatever the
    in-app notification preference, when `POST_NOTIFICATIONS` is granted. (Builds before API-011 show it from
    `data.title` / `data.body` like any notification.)
  - Both: tapping opens the Updates tab at that notification; refresh the bundle (it's in `notifications` with
    `priority: "urgent"`).
- **Live Activity update (API-011, iOS):** for an urgent notification, also an ActivityKit push to each registered
  `live_activity_token` of the event: `apns-push-type: liveactivity`, `apns-topic: <bundle id>.push-type.liveactivity`,
  `apns-priority: 10`, token-based auth.
  ```json
  {"aps": {"timestamp": 1794760800, "event": "update",
           "content-state": {"urgent": {"id": 9, "title": "Main Stage closed", "body": "Storm on the way…",
                                        "sent_at": "2026-11-15T16:40:00+00:00"}},
           "alert": {"title": "Main Stage closed", "body": "Storm on the way…", "sound": "default"},
           "stale-date": 1794804000, "relevance-score": 100}}
  ```
  - `content-state` **replaces** the activity's whole content state and is decoded by ActivityKit with a default
    `JSONDecoder`: every field of the app's `ContentState` must be optional, and `urgent.sent_at` is an ISO 8601
    **string** (declare it as `String`, not `Date`). The app's own fields arrive absent; fill them in again on the next
    local update.
  - `timestamp` and `stale-date` are UNIX seconds; `stale-date` = sent + 12 h.
- **Content updated:**
  - Silent push, sent about 3 s after any attendee-visible change (at most once every 15 s per event).
  - Custom data: `type = "content_updated"`, `event_id`, `content_version` (on Android all strings, as for every data value).
  - iOS: `content-available: 1`, no alert; `apns-push-type: background`, priority 5, `apns-collapse-id: content-<event_id>`,
    expires after about an hour. Background delivery is best-effort (iOS throttles it); in the foreground it arrives at once.
  - Android: data-only message, priority `normal`, `collapse_key: content_<event_id>`, TTL 1 hour.
  - The app refetches the bundle only if `content_version` is newer than its own, after a random 0–10 s delay (see `bundle/`).

When the app is opened from a notification, it should open the Notifications tab for that event.
