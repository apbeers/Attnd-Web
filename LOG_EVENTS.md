# Attnd log events (shared catalogue, record format v1)

The one list of log event names for attnd-ios, attnd-android and attnd-web (API-013). **Every event name an app
logs must be in this file**, with the same category and the same fields on both platforms, so the Diagnostics panel
(`/console/diagnostics/`) can lay iOS and Android sessions out the same way. Add a row here (in attnd-web) before an
app logs a new name; never rename or reuse one (old builds keep sending it). `diagnostics/tests.py` checks the table
format.

## Record format (`v` = 1)

One JSON object per line (on the device in the ring buffer, in `entries` when uploaded):

```json
{"v":1,"ts":"2026-11-15T19:04:12.381Z","mono_ms":523101,"seq":4182,"level":"debug","cat":"net",
 "event":"net.request.finished","msg":"GET bundle 304 in 182 ms",
 "f":{"method":"GET","path":"/api/v2/events/{id}/bundle/","status":304,"ms":182,"bytes":0,"req_id":"9f2c41d07a3b5e68","cache":"304"},
 "app":"2.0.131","build":"131","os":"iOS 27.2","model":"iPhone18,1","state":"fg","net":"wifi","event_id":5,"diag":"D-7K3QX"}
```

| Key | Meaning |
|-----|---------|
| `v` | Always `1`. Records with another version are rejected by the server. |
| `ts` | UTC wall clock, ISO 8601 with milliseconds and `Z`. |
| `mono_ms` | Monotonic ms since process start (orders lines when the wall clock jumps). |
| `seq` | Per-install counter, +1 per record (gaps = dropped records). |
| `level` | `trace` 0, `debug` 1, `info` 2, `notice` 3, `warning` 4, `error` 5, `fault` 6 (string, or the number). |
| `cat` | One of the categories below. |
| `event` | A dotted name from the tables below (`[a-z][a-z0-9_]*` parts, 2–5 parts, ≤ 64 chars). |
| `msg` | Short human text (≤ 2,000 chars; the server cuts longer ones). Never personal data. |
| `f` | Event fields (≤ 4 KB as JSON, else the server stores `{"_truncated": true}`). |
| `app`, `build`, `os`, `model` | App version, build number, OS name + version, hardware model (`iPhone18,1`, `Pixel 9`). |
| `state` | `fg` or `bg`. |
| `net` | `wifi`, `cell`, `none` or `other`. |
| `event_id` | The joined Attnd event's id, if any. |
| `diag` | The debug code `D-XXXXX` of the session the record belongs to (omitted outside debug sessions). |

### Redaction (both apps unit-test it; the server scrubs again)

- **Never** search, Ask or plan-request text: log its length (`query_len`). Dev builds may log the text itself.
- **Never** tokens, keys, push tokens, Live Activity tokens, JWTs, emails, the device id or the support code.
- URLs only as **path templates**: ids → `{id}`, no query string, no host (`/api/v2/events/{id}/bundle/`).
- Location rounded to 0.01° (`lat`, `lng`), or only distances (`m`).
- Exception messages: first line, ≤ 280 chars, and only from our own code or the system (never user text).

The server drops keys whose name contains `token`, `password`, `secret`, `authorization`, `api_key`, `email`,
`device_id`, `support_code`, `cookie`, `jwt` (and, except from dev builds, `query`, `text`, `prompt`, `q`; `…_len` /
`…_length` / `…_count` keys are kept), cuts query strings from `url`/`path`/`href`/`uri`/`link` and rounds
`lat`/`lng`/`latitude`/`longitude` to 2 decimals.

## Categories

| Category | What |
|----------|------|
| `net` | API calls, image downloads, walking routes, reachability |
| `push` | APNs / FCM tokens and messages |
| `background` | Background refresh, BGTask / WorkManager / alarms, silent-push wakes |
| `live` | Live Activity (iOS) / Live Update (Android), reminders, venue arrival |
| `location` | Permission, position and heading sources (no coordinates beyond 0.01°) |
| `map` | Map rendering, clustering, pins, routes |
| `planner` | My plan: preference extraction, the planner run, swaps, plan edits and undo |
| `assistant` | Siri / App Intents / Spotlight, Gemini AppFunctions, Ask (on-device model), the event assistant's turns, blocks and actions — "CRI requests" |
| `search` | Local search |
| `storage` | Bundle cache, image cache, preferences |
| `ui` | Screens, sheets, tabs, banners, errors shown, share links |
| `perf` | Signposts / trace sections and measured durations |
| `auth` | App Attest / Play Integrity, JWT, 401 retries |
| `lifecycle` | Launch, foreground/background, memory warnings, update prompt |
| `logging` | The logging system itself: debug mode, uploads, the ring buffer |
| `tickets` | My tickets and door mode (API-017): fetches, QR shown, manifest downloads, scans, check-in sync |
| `server` | **Server only**: the server's own line for an API request in a debug session (apps never send it) |

Levels: `debug` for routine detail, `info` for milestones, `notice` for state changes worth seeing in production
logs, `warning` for something that went wrong and was handled, `error` for a failure the user may notice, `fault`
for a bug (should never happen). Production builds keep `warning` and above outside debug mode.

## Events

Fields in **bold** are required. `ms` = a duration in milliseconds (integer); `bytes` = response body size.

### lifecycle

| Event | Category | Level | Fields | When |
|-------|----------|-------|--------|------|
| `lifecycle.launch` | `lifecycle` | info | **`kind`** (`cold`/`warm`), **`ms`** (to first event UI frame), `restored` (bool: bundle from disk) | the app is usable after a launch / return |
| `lifecycle.foreground` | `lifecycle` | info | `since_bg_s` | the app came to the foreground |
| `lifecycle.background` | `lifecycle` | info | `fg_s` | the app went to the background |
| `lifecycle.terminate` | `lifecycle` | info | — | the system says the app will terminate (when told) |
| `lifecycle.memory_warning` | `lifecycle` | warning | `level` (Android trim level) | memory warning / `onTrimMemory` |
| `lifecycle.exit_reason` | `lifecycle` | notice | **`reason`** (Android `ApplicationExitInfo` reason name / iOS MetricKit kind), `status`, `importance` | the previous run's exit, read at launch |
| `lifecycle.update_prompt` | `lifecycle` | notice | **`update`** (`none`/`available`/`required`), `min_version`, `latest_version` | `/api/app-status/` answered |
| `lifecycle.deep_link` | `lifecycle` | info | **`kind`** (`join`/`open`/`live`/`referrer`/`shortcut`), `target` (template, no code) | the app was opened by a link |
| `lifecycle.event.joined` | `lifecycle` | notice | **`event_id`**, `via` (`qr`/`link`/`search`/`nearby`/`referrer`/`rejoin`) | an event was joined |
| `lifecycle.event.left` | `lifecycle` | notice | **`event_id`**, `reason` (`user`/`switch`/`404`) | the event was left or forgotten |

### net

| Event | Category | Level | Fields | When |
|-------|----------|-------|--------|------|
| `net.request.started` | `net` | trace | **`method`**, **`path`**, **`req_id`** | an API call is sent (`X-Request-Id` = `req_id`) |
| `net.request.finished` | `net` | debug | **`method`**, **`path`**, **`status`**, **`ms`**, **`req_id`**, `bytes`, `cache` (`304`/`etag`/`none`), `attempt` | an API call got an HTTP answer (any status) |
| `net.request.failed` | `net` | warning | **`method`**, **`path`**, **`req_id`**, **`ms`**, **`error`** (`timeout`/`offline`/`tls`/`dns`/`cancelled`/`decode`/`other`), `domain`, `code` | an API call got no HTTP answer, or the body didn't decode |
| `net.request.retry` | `net` | info | **`path`**, **`req_id`**, **`reason`** (`401`/`backoff`) | the call is retried (after re-attesting on 401) |
| `net.bundle.applied` | `net` | info | **`content_version`**, `previous_version`, `source` (`join`/`foreground`/`poll`/`push`/`pull`), `locations`, `items`, `notifications` | a new bundle replaced the shown one |
| `net.bundle.not_modified` | `net` | debug | **`content_version`**, `source` | a bundle refresh answered 304 |
| `net.poll.scheduled` | `net` | trace | **`interval_s`** (60 or 300), `has_push_token` | the safety poll interval was (re)set |
| `net.reachability` | `net` | info | **`net`** (`wifi`/`cell`/`none`/`other`), `constrained`, `expensive` | the network path changed |
| `net.offline_notice` | `net` | notice | **`shown`** (bool), `failures` | the offline notice appeared / cleared |
| `net.image.fetched` | `net` | debug | **`ms`**, `bytes`, `status`, `kind` (`location`/`logo`/`flyer`) | an image download finished (no URL) |
| `net.image.failed` | `net` | warning | **`error`**, `status`, `kind` | an image download failed |
| `net.route.fetched` | `net` | debug | **`ms`**, **`ok`**, `fallback` (bool: dashed estimate), `m` | a walking route (MKDirections / Routes API) |
| `net.app_status` | `net` | debug | **`status`**, **`ms`** | `/api/app-status/` |
| `net.usage.flushed` | `net` | debug | **`entries`**, **`status`**, `retry` (bool) | a usage batch was sent |
| `net.device.registered` | `net` | info | **`status`**, `has_push_token`, `subscribed`, `has_live_activity_token` | `POST devices/` |

### push

| Event | Category | Level | Fields | When |
|-------|----------|-------|--------|------|
| `push.token` | `push` | info | **`kind`** (`apns`/`fcm`), `changed` (bool), `length` | the system gave the app a push token (never the token) |
| `push.token.failed` | `push` | warning | **`error`**, `domain`, `code` | push registration failed |
| `push.permission` | `push` | notice | **`status`** (`granted`/`denied`/`provisional`/`not_determined`), `asked` (bool) | notification permission changed or was asked |
| `push.received` | `push` | info | **`type`** (`notification`/`content_updated`/`liveactivity`/`unknown`), `priority`, `content_version`, `notification_id`, `state` | a push arrived (foreground or background) |
| `push.handled` | `push` | info | **`type`**, **`action`** (`refreshed`/`skipped_not_newer`/`shown`/`ignored_other_event`/`urgent`), `ms`, `delay_ms` (the 0–10 s random delay) | the app finished handling it |
| `push.dropped` | `push` | warning | **`type`**, **`reason`** | a push the app couldn't use (bad payload, no event) |
| `push.opened` | `push` | info | **`type`**, `notification_id` | the user tapped a notification |

### background

| Event | Category | Level | Fields | When |
|-------|----------|-------|--------|------|
| `background.wake` | `background` | info | **`reason`** (`silent_push`/`app_refresh`/`alarm`/`geofence`/`boot`/`region`/`intent`) | the system woke the app (`app_refresh`, `region`, `geofence`: Android only / removed on iOS Nov 19, 2026) |
| `background.refresh.started` | `background` | info | **`task`** (`bundle`/`live`/`reminders`/`images`), `reason` | background work started |
| `background.refresh.finished` | `background` | info | **`task`**, **`ms`**, `result` (`new_data`/`no_data`) | it finished |
| `background.refresh.failed` | `background` | warning | **`task`**, **`ms`**, **`error`** | it failed |
| `background.refresh.expired` | `background` | warning | **`task`**, `ms` | the system ended it early (expiration handler / stopped worker) |
| `background.task.scheduled` | `background` | debug | **`task`**, **`at`** (ISO time), `window_s` | the next wake (BGAppRefreshTask / `AlarmManager.setWindow`) |

### live (Live Activity / Live Update, reminders, arrival)

| Event | Category | Level | Fields | When |
|-------|----------|-------|--------|------|
| `live.start` | `live` | notice | **`mode`** (`before`/`active`/`end_of_day`/`urgent`), **`source`** (`plan`/`hearts`/`event`), `scheduled` (bool), `items` | a Live Activity / Live Update started (or was scheduled) |
| `live.start.failed` | `live` | warning | **`reason`** (`disabled`/`denied`/`limit`/`error`), `domain`, `code` | it could not start (also counted as `perf_live_start_fail`) |
| `live.update` | `live` | debug | **`mode`**, `changed` (list of keys), `stale_at` | its content was updated |
| `live.end` | `live` | notice | **`reason`** (`day_over`/`user`/`unfollowed`/`left_event`/`system`) | it ended |
| `live.token` | `live` | info | **`action`** (`received`/`sent`/`cleared`), `status` | the ActivityKit push token (iOS; never the token) |
| `live.push.received` | `live` | info | **`kind`** (`urgent`) | an ActivityKit push updated it (iOS, when the app sees it) |
| `live.action` | `live` | info | **`action`** (`heart`/`unheart`/`skip`/`dismiss_urgent`/`show_on_map`/`open`) | a button on it was used |
| `live.wake.scheduled` | `live` | debug | **`at`**, **`reason`** | the next recompute was scheduled |
| `live.reminder.scheduled` | `live` | debug | **`count`** | reminders were (re)scheduled |
| `live.reminder.shown` | `live` | info | **`item_id`**, `walk_min` | a reminder fired |
| `live.arrival` | `live` | notice | **`action`** (`entered`/`prompted`/`started`), `radius_m` | the venue geofence (Android only, still off there; iOS `CLMonitor` removed Nov 19, 2026) |
| `live.follow.changed` | `live` | notice | **`on`** (bool), `default` (bool) | "Follow on Lock Screen" changed |
| `live.running_late` | `live` | notice | **`item_id`**, **`minutes_left`**, `walk_min`, `action` (`shown`/`left_now`/`swap_offered`/`swapped`/`dismissed`) | the running-late check fired at departure time (assistant spec §6; no coordinates) |
| `live.checkoff` | `live` | info | **`item_id`**, **`source`** (`auto`/`tap`), **`done`** (bool), `minutes_near` | a planned item was checked off (or un-checked) |

### location

| Event | Category | Level | Fields | When |
|-------|----------|-------|--------|------|
| `location.permission` | `location` | notice | **`status`** (`granted`/`denied`/`not_determined`/`restricted`), **`precision`** (`precise`/`approximate`), `background` (bool), `asked` | permission changed or was asked |
| `location.updates` | `location` | debug | **`on`** (bool), `heading` (bool), `consumers` | position/heading tracking started or stopped |
| `location.fix` | `location` | trace | **`accuracy_m`**, `age_s`, `lat`, `lng` (0.01°) | a position fix (sampled, at most 1 per 30 s) |
| `location.heading.source` | `location` | debug | **`source`** (`clheading`/`fused_orientation`/`rotation_vector`), `accuracy` | which compass source is used |
| `location.error` | `location` | warning | **`error`**, `domain`, `code` | location failed |

### map

| Event | Category | Level | Fields | When |
|-------|----------|-------|--------|------|
| `map.first_render` | `map` | info | **`ms`**, `pins`, `clustered` (bool) | the Map tab drew its first pins (also `perf_map_first_render`) |
| `map.pin.selected` | `map` | debug | **`location_id`** | a pin was tapped |
| `map.route.shown` | `map` | debug | **`location_id`**, **`kind`** (`route`/`estimate`), `min` | a walking route / estimate was drawn |
| `map.fit` | `map` | trace | **`pins`**, `reason` (`open`/`reselect`/`filter`) | the camera fit the pins |
| `map.tiles.error` | `map` | warning | **`error`** | the map failed to load (e.g. a rejected Maps key) |

### planner

| Event | Category | Level | Fields | When |
|-------|----------|-------|--------|------|
| `planner.request` | `planner` | info | **`query_len`**, `source` (`card`/`ask`/`onboarding`/`example`) | a plan was asked for (never the text) |
| `planner.extract` | `planner` | info | **`ms`**, **`ok`** (bool), `model` (`foundation_models`/`gemini_nano`/`rules`), `liked_tags`, `disliked_tags`, `days` (counts) | preferences were extracted |
| `planner.run` | `planner` | info | **`ms`**, **`items`**, `candidates`, `days`, `meals`, `pace` | the planner made a plan (also `perf_planner_ms`) |
| `planner.swap` | `planner` | info | **`item_id`**, `alternatives` | Swap was used |
| `planner.changed` | `planner` | notice | **`removed`**, **`moved`** | a content update changed the plan ("Your plan changed") |
| `planner.availability` | `planner` | info | **`status`** (`available`/`downloadable`/`downloading`/`unavailable`), `reason` | the on-device model's availability |
| `plan.edit` | `planner` | info | **`ops`** (list of op names: `replace`/`remove`/`add`/`move`/`keep`/`unkeep`/`constraint`), **`source`** (`assistant`/`plan`/`live`/`siri`/`appfunction`), `applied` (bool), `removed`, `added`, `moved`, `ms` | a plan edit was previewed or applied (assistant spec §4; never the typed text) |
| `plan.undo` | `planner` | info | **`depth`** (undo stack size after), `ok` (bool) | the last plan edit was undone |

### assistant (Siri, App Intents, Spotlight, AppFunctions, Ask, the event assistant)

| Event | Category | Level | Fields | When |
|-------|----------|-------|--------|------|
| `assistant.request` | `assistant` | info | **`kind`** (`intent`/`appfunction`/`ask`/`spotlight`/`shortcut`), **`name`** (intent / function name), **`ms`**, **`ok`** (bool), `results`, `query_len` | a Siri intent, AppFunction or Ask finished (`intent`/`spotlight`/`shortcut`: removed on iOS Nov 19, 2026) |
| `assistant.model.status` | `assistant` | info | **`status`** (`available`/`downloadable`/`downloading`/`unavailable`), `reason` | Apple Intelligence / Gemini Nano availability |
| `assistant.model.download` | `assistant` | info | **`state`** (`started`/`progress`/`finished`/`failed`), `pct` | Gemini Nano download |
| `assistant.answer` | `assistant` | debug | **`ms`**, **`citations`**, `tool_calls`, `fallback` (bool) | the model answered (no text) |
| `assistant.error` | `assistant` | warning | **`kind`**, **`error`**, `domain`, `code` | the model or an intent failed |
| `assistant.index` | `assistant` | debug | **`ms`**, **`entities`**, `action` (`reindexed`/`removed`) | Spotlight entities / shortcuts updated (Android dynamic shortcuts only; Spotlight removed on iOS Nov 19, 2026) |
| `assistant.turn` | `assistant` | info | **`path`** (`model`/`rules`), **`intent`** (router intent, e.g. `search`/`plan_edit`/`like`/`why_not`/`question`), **`ms`**, **`blocks`**, `query_len`, `fallback` (bool: model output invalid → rules), `tool_calls` | one assistant turn finished (event assistant, spec §3; never the text in production) |
| `assistant.block` | `assistant` | debug | **`type`** (block type, e.g. `item_card`/`carousel`/`timeline`/`map`/`plan_diff`), **`ids`** (count), `dropped` (unknown ids dropped) | a response block was rendered |
| `assistant.action` | `assistant` | info | **`name`** (action registry name, e.g. `like`/`plan_edit`/`follow_lock_screen`/`share_plan`), **`result`** (`applied`/`confirmed`/`declined`/`undone`/`failed`), `confirm` (`none`/`chip`/`sheet`) | an action was run from the assistant |

### search

| Event | Category | Level | Fields | When |
|-------|----------|-------|--------|------|
| `search.query` | `search` | debug | **`query_len`**, **`results`**, **`ms`**, `time_words` (bool), `source` (`sheet`/`tab`/`suggestion`) | a search settled (never the text, except dev builds: `query`) |
| `search.open` | `search` | debug | **`kind`** (`schedule`/`location`/`notification`/`event`), **`id`** | a result was opened |

### storage

| Event | Category | Level | Fields | When |
|-------|----------|-------|--------|------|
| `storage.bundle.saved` | `storage` | debug | **`bytes`**, **`ms`**, **`content_version`** | the bundle was written to disk |
| `storage.bundle.restored` | `storage` | info | **`bytes`**, **`ms`**, **`content_version`**, `age_s` | the saved bundle was loaded at launch |
| `storage.bundle.failed` | `storage` | error | **`op`** (`save`/`restore`), **`error`** | reading or writing it failed |
| `storage.images.prefetch` | `storage` | debug | **`total`**, **`fetched`**, `failed`, `ms` | an image prefetch round finished |
| `storage.images.pruned` | `storage` | debug | **`files`**, **`bytes`** | unreferenced images were deleted |
| `storage.prefs.migrated` | `storage` | notice | **`from`**, **`to`** | stored preferences were migrated |

### ui (incl. share links)

| Event | Category | Level | Fields | When |
|-------|----------|-------|--------|------|
| `ui.screen` | `ui` | debug | **`name`** (`launch`/`map`/`locations`/`location`/`schedule`/`schedule_item`/`flyer`/`updates`/`options`/`search`/`plan`/`onboarding`/`update_required`), `id` | a screen / tab / sheet appeared |
| `ui.action` | `ui` | debug | **`name`** (`heart`/`unheart`/`refresh`/`share`/`directions`/`copy_support`/`tour`/…), `id` | a user action worth seeing in a timeline |
| `ui.banner` | `ui` | debug | **`kind`** (`update`/`urgent`/`offline`/`demo`/`ended`/`update_available`), **`shown`** (bool) | a banner appeared / was dismissed |
| `ui.error.shown` | `ui` | warning | **`kind`** (`join`/`lookup`/`refresh`/`scan`/`other`), `status` | an error message was shown to the user |
| `ui.hang` | `ui` | warning | **`ms`** | the main thread was blocked > 250 ms (debug sessions only) |
| `share.created` | `ui` | info | **`target`** (`plan`/`item`), **`items`**, `payload_len`, `via` (`share_sheet`/`qr`/`copy`) | a share link was made (API-015; never the link) |
| `share.opened` | `ui` | notice | **`target`** (`plan`/`item`), **`items`**, `known` (ids found in the bundle), **`action`** (`viewed`/`use`/`merge`), `joined` (bool: the link joined the event) | a shared plan link was opened in the app |

### perf

| Event | Category | Level | Fields | When |
|-------|----------|-------|--------|------|
| `perf.signpost` | `perf` | debug | **`name`** (`launch`/`bundle_fetch`/`bundle_decode`/`planner`/`map_first_render`/`search_index`), **`ms`** | an `OSSignposter` interval / Android `Trace` section ended |
| `perf.metric` | `perf` | debug | **`name`** (an API-014 metric, e.g. `perf_bundle_fetch`), **`bucket`** | a value went into the anonymous perf counts |
| `perf.frame_drops` | `perf` | debug | **`screen`**, **`dropped`**, `ms` | janky frames on a screen (when measured) |

### auth

| Event | Category | Level | Fields | When |
|-------|----------|-------|--------|------|
| `auth.attest.started` | `auth` | info | **`kind`** (`app_attest`/`play_integrity`/`static_key`) | attestation began |
| `auth.attest.finished` | `auth` | info | **`kind`**, **`ms`**, **`ok`** (bool), `status` | it finished (never the JWT) |
| `auth.attest.failed` | `auth` | warning | **`kind`**, **`error`**, `domain`, `code`, `status`, `retry_after_s` | it failed (10-minute back-off on Android) |
| `auth.token.expired` | `auth` | info | **`reason`** (`401`/`expiry`) | the stored JWT was dropped |

### logging

| Event | Category | Level | Fields | When |
|-------|----------|-------|--------|------|
| `logging.debug_mode` | `logging` | notice | **`on`** (bool), `minutes`, `reason` (`user`/`expired`/`restart`) | debug mode started / ended (production) |
| `logging.upload` | `logging` | debug | **`entries`**, **`bytes`**, **`status`**, `retry_after_s`, `preroll` (bool) | a log batch was uploaded (never logged into the batch it reports on twice) |
| `logging.upload.failed` | `logging` | warning | **`error`**, `status`, `entries` | an upload failed (kept for the next try) |
| `logging.dropped` | `logging` | warning | **`count`**, **`reason`** (`ring_full`/`rate`/`cap`) | records were dropped |
| `logging.ring.trimmed` | `logging` | trace | **`bytes`** | the ring buffer was trimmed to ~2 MB |
| `logging.export` | `logging` | info | **`entries`** | the viewer exported a .jsonl |
| `logging.report.sent` | `logging` | info | **`kind`**, **`count`**, **`status`** | crash/hang reports were uploaded |

### tickets (API-017; never emails, names, codes, tokens or QR strings)

Parked with ticketing (git branch `parked/tickets`, Nov 19, 2026): no app logs these now; kept for when it comes back.


| Event | Category | Level | Fields | When |
|-------|----------|-------|--------|------|
| `tickets.mine.fetched` | `tickets` | info | **`status`**, **`count`**, `ms`, `cache` (`304`/`none`), `offline` (bool) | My tickets loaded (or shown from the stored copy) |
| `tickets.mine.signin` | `tickets` | info | **`step`** (`code_sent`/`verified`/`failed`), `error` | the email-code sign-in for My tickets |
| `tickets.qr.shown` | `tickets` | debug | **`event_id`**, `offline` (bool) | a ticket's QR was shown |
| `tickets.wallet.add` | `tickets` | info | **`platform`** (`apple`/`google`), **`ok`** (bool), `error` | Add to Apple Wallet / Save to Google Wallet |
| `tickets.door.signin` | `tickets` | info | **`step`**, `events` (count), `error` | door mode sign-in |
| `tickets.door.manifest` | `tickets` | info | **`event_id`**, **`tickets`**, **`status`**, `ms`, `bytes`, `cache` | the door ticket list was downloaded (or `304`) |
| `tickets.door.scan` | `tickets` | info | **`result`** (`ok`/`duplicate`/`revoked`/`void`/`invalid`/`wrong_day`/`unreadable`), **`offline`** (bool), `kind` (`in`/`out`), `manual` (bool), `ms` | one scan, as the device judged it |
| `tickets.door.sync` | `tickets` | info | **`sent`**, **`status`**, `flagged`, `queued`, `ms` | a batch of check-ins was uploaded |
| `tickets.door.revocations` | `tickets` | debug | **`count`**, `cursor` | revocations fetched |

### errors (any category)

| Event | Category | Level | Fields | When |
|-------|----------|-------|--------|------|
| `error.caught` | `ui` | error | **`domain`** (Swift error domain / Kotlin exception class), **`code`**, **`type`**, `where` (function or screen) | an unexpected error was caught. Log it in the category of the code that caught it (`net`, `storage`, … are all allowed); the panel groups by (`event`, `domain`, `code`, `type`) |
| `error.assertion` | `ui` | fault | **`where`**, `detail` | an internal invariant broke (dev builds also assert) |

### server (attnd-web only)

| Event | Category | Level | Fields | When |
|-------|----------|-------|--------|------|
| `server.request` | `server` | info | **`method`**, **`path`**, **`status`**, **`ms`**, **`req_id`** | the server handled an API request carrying `X-Diag-Session` (warning ≥ 400, error ≥ 500) |
