# Attnd API v2

v2 is what the current iOS and Android apps use. The v1 endpoints in `API.md` stay available, unchanged in shape, for older app builds.

- **Base URL:** `https://attnd.app/api/v2/`
- **Authentication:** the same as v1. Send `Authorization: Bearer <attestation JWT>`. In dev and CI builds you can send the static key instead. When you get a `401`, invalidate the JWT, re-attest, and retry once.
- **CI keys (API-008):** live integration tests may send a CI key (server setting `CI_API_KEYS`) instead. It works even when the
  static key is turned off (production), but only for the DEMO event (`DEMO_EVENT_CODE`):
  - `events/lookup/` answers `404` for any other event (exactly like an unknown code); `events/nearby/` lists only DEMO;
  - `events/<id>/bundle|join|leave|usage/` answer `404` unless `<id>` is DEMO;
  - `devices/`, `join/` and `leave/` take only device ids starting `ci-` (`403` otherwise), e.g. `ci-ios-<run>`;
  - `usage/` answers exactly as for a real app (same `accepted`) but records nothing;
  - every other API path answers `401`. `/api/app-status/` needs no key at all.

  Devices whose id starts with `ci-` (with any key) are left out of organizer audience counts, install/join/leave
  counts and pushes, so test runs don't show up in DEMO's stats. Apps never send a CI key.
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
  "notifications": [<Notification>]
}
```
Hidden locations and items are already filtered out. `locations` are already in the organizer's order, and `schedule` is sorted by `start_time`. `notifications` are newest first.

### `POST events/<id>/join/`
```json
{ "device_id": "uuid", "platform": "ios" | "android", "push_token": "…", "app_version": "2.0.0" }
```
→ `{ "status": "ok", "content_version": 15 }`. Only `device_id` is required.

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

- **Hearts (API-009):** send one `…_heart` each time the attendee turns a heart on and one `…_unheart` each time they
  turn it off, through the same reporter, buckets and rules as every other count (no device id, ever). Don't re-send
  hearts that are restored from storage at launch. The console shows **net** hearts per item (hearts − unhearts over
  the event, never below 0). A heart without an id, or with an id from another event, is dropped.

- `count` is optional (default 1). `at` is optional ISO 8601, used to place the count in its UTC 5-minute window (within the last 7 days). Up to 200 entries per request.
- Unknown names, invalid details and ids from other events are dropped silently. A malformed body still returns `200`.
- **Client behaviour:** aggregate in memory by (name, detail, UTC 5-minute window, sending its start as `at`) and send at most every ~60 seconds, when the app goes to the background, and before leaving an event. Drop the batch after one failed retry. Analytics must never block or slow the UI, and never show errors.
- The server counts joins, leaves and new installs itself; don't send those.

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
  "join_url": "https://attnd.app/join/FWFM2026/"
}
```
- `is_demo == true`: show the prominent **Demo Event** banner.
- `status == "archived"`: show an "This event has ended" banner. The content is still viewable.

### Location
```json
{ "id": 5, "name": "…", "snippet": "…", "description": "…", "category": "Food" | null,
  "website": "https://…" | null, "image_url": "https://…" | null, "address": "…",
  "latitude": 32.74 | null, "longitude": -97.36 | null, "sort_order": 0,
  "tags": ["food", "bbq", "kid-friendly"] }
```
- `tags` (API-010): see Tags below. Missing in responses from servers before API-010: default `[]`.

### ScheduleItem
```json
{ "id": 1, "name": "…", "description": "…", "start_time": "…", "end_time": "…" | null,
  "location_id": 5 | null, "location_name": "…" | null, "tags": ["Music"] }
```
An item may have no location. Show it under "General" or "No location".

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
