# Attnd "glance, hearts, plan, live activity" spec (shared by iOS + Android; server parts in attnd-web)

Agreed with the user on Nov 15, 2026. Same rules and same unit-test cases on both apps (like the search spec:
search_spec.md). Everything is on the
device unless marked SERVER. No third-party libraries (CLAUDE.md of each repo).

## 1. Relative time + distance formatting (bug fix: "thousands of hours")
Pure function `relativeStart(start, end?, now, eventTimeZone)` → text; tests with a fixed `now`, America/Chicago.
- ended (end < now): "Ended"; started (start ≤ now < end): "Started 20 min ago" (< 1 h), "Now" otherwise / "Now · ends 8:30 PM" where there's room
- future: < 1 min "Starting now"; < 60 min "in 12 min"; < 6 h "in 3 h 20 min" (drop " 0 min"); same calendar day "today 7:30 PM";
  next day "tomorrow 7:30 PM"; < 7 days "in 3 days" (calendar days in the event tz); < 5 weeks "in 2 weeks"; < 12 months "in 4 months";
  else "in 1 year" / "in 2 years". Never hours ≥ 6.
- Applies to: launch-screen nearby events (server sends `starts_in_hours`: convert to a date, don't print hours), schedule rows/detail
  "starts in", search detail lines, AppFunctions/Siri text where relevant.
- Distance in the phone's region units: US/UK/Liberia/Myanmar → feet under 0.1 mi ("500 ft"), else miles with 1 decimal under 10 ("0.6 mi"),
  whole miles above; elsewhere metres under 1 km ("350 m"), km with 1 decimal under 10. Use the platform measurement formatter where it
  matches these rules. iOS LaunchScreen currently hard-codes "%.1f mi away".

## 2. Distance + compass arrow
- Show "0.6 mi ↗" (distance + arrow) wherever a distance is shown and where it's useful: launch nearby events, location rows (new: distance
  from the user when location permission is granted and the location has coordinates), location detail, map card, search results for
  locations, schedule detail (to its location).
- Arrow = bearing(user → target) − device heading (true north; iOS CLHeading.trueHeading, fall back to magneticHeading; Android
  FusedOrientationProviderClient from Play services location if available, else rotation-vector sensor). Hidden without location permission,
  without a heading, or when within ~20 m ("Here"). Sensors run only while a screen that shows arrows is visible; throttle to ~5 Hz, animate.
- Accessibility: "Northeast, 0.6 miles". Pure bearing/compass-point maths in the domain layer with tests.

## 3. Walking route on the map
- Tapping a pin (selected location card): if the user is within 5 miles / 8 km and has location permission, draw a walking route and show
  "12 min walk · 0.6 mi" in the card. Never start navigation (the existing "Directions" button stays for that).
- iOS: MKDirections, transportType .walking, MapPolyline. Android: Google Routes API v2 computeRoutes (travelMode WALK) over
  HttpURLConnection with the existing MAPS_API_KEY and headers X-Android-Package / X-Android-Cert (SHA-1 of the signing cert, computed at
  runtime) and X-Goog-FieldMask: routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline; decode the polyline (own code).
- Fallback (offline, error, no route, or route > 2.5× the straight-line distance, which happens inside festival grounds): a dashed straight
  line and "about 8 min walk" estimated at 1.3 m/s × straight-line × 1.2. Cache per (location, user position rounded to ~50 m); re-route when
  the user moves > 50 m. Same walk-time helper is reused by schedule rows and reminders.

## 4. Glanceables (no extra screens)
- Map pins: a small "live" dot when something is happening now at that location; subtitle "Next 7:30" when something starts within 3 h.
- "Now playing" strip at the top of the map: horizontally scrolling chips "Main Stage · Prairie Lights" for items in progress; tap → select
  that location. Hidden when nothing is on.
- Location rows (stages etc.): one extra line "Now: X · Next 8:30 Y" when the location has schedule items.
- Schedule rows: live countdown text that updates every 30 s (uses §1), a thin progress bar for items in progress, and walk time to the
  item's location when the user is at the event (within 5 mi).
- Peek: long-press a location/schedule row → preview card (iOS .contextMenu with preview; Android a ModalBottomSheet) with the key info and
  actions: Heart, Show on map, Open.
- Updates tab: unread badge (count of notifications newer than the last time the tab was seen, stored per event), and the newest unread
  update as a dismissible banner at the top of the other tabs for 24 h.

## 5. Hearts
- Heart toggle on schedule items and locations: rows, details, map card, search results, peek. Stored on the device per event
  (iOS: in the saved-event store or UserDefaults keyed by event id; Android: DevicePrefs/datastore), included in the platform backup.
- "Hearted" filter chip on Schedule, Locations, Map (only hearted pins) and in search (a "Hearted" suggestion chip).
- Anonymous usage (SERVER, API-009): metrics `location_heart`, `location_unheart` (detail = location id), `schedule_item_heart`,
  `schedule_item_unheart` (detail = item id), sent through the existing usage reporter (same buckets, no device id).
- Siri/AppFunctions: heart/unheart an item; "what have I hearted".

## 6. Planner ("build me a plan") — only when the on-device model is available
- No model (old phone, Apple Intelligence off, no Gemini Nano): no Ask, no planner, no planner onboarding; classic search + hearts.
- Model turns the request (+ hearted items) into preferences (structured output): liked/disliked tags & categories, "similar to" item ids
  (hearted or named), days, time window (earliest/latest), pace (relaxed/balanced/packed), include meals (bool), kid-friendly (bool).
  Every id it returns is validated against the bundle.
- Similarity: on-device text embeddings of "name + tags + category + description + location": iOS NLEmbedding.sentenceEmbedding
  (NaturalLanguage, on device); Android: the on-device embedding available alongside Gemini Nano if any (check ML Kit GenAI / AICore
  docs), else the MediaPipe Text Embedder (Google first-party) only if it is a Google Maven artifact, else a TF-IDF cosine over the same text.
- Scheduling is deterministic code, identical on both platforms, unit-tested with the DEMO-like fixture:
  score = 3·(hearted) + 2·max similarity to liked items + 1·tag/category match − 2·disliked match; candidates within the time window;
  weighted interval scheduling (maximise total score) with a walk-time gap between consecutive items (§3 estimate) + 5 min buffer;
  pace limits the share of the window that's booked (relaxed 50 %, balanced 70 %, packed 90 %); meals add a 45 min break near 12:00–13:30
  and 18:00–19:30 when free; hearted items are must-include unless they overlap another hearted item (keep the higher score, list the other
  as "or instead"). Each planned item carries a reason ("because you hearted live country").
- "My plan" view (a section in Schedule, filter "My plan"): timeline with walk legs ("6 min walk"), swap button per slot (next best
  alternatives), "Re-plan from now". Plan is stored on the device per event; when a content update changes/removes a planned item, show
  "Your plan changed" with one-tap re-plan.

## 7. Onboarding (first time an event is joined; skippable; re-open from Options → "Tour" and empty states)
- 2–3 cards: what you can do (map, schedule, live updates, search button); location (walk times, compass, nearby) → permission prompt;
  notifications (set-time changes, reminders for hearts) → permission prompt; if the model is available: "Plan your day" with example
  requests generated from this event's data (top categories/tags: "Plan my Saturday around live music and BBQ", "I've got 3 hours, show me
  the highlights", "Kid-friendly afternoon", "What's on near me right now?") as chips that open Ask with that text.
- Skipped in screenshot mode. Store review notes mention it.

## 8. Live Activity (iOS) / Live Update (Android) — device-driven, server only for URGENT announcements
- Content state carries current item, next item, the one after (ids, names, location names, start/end dates), walk time to the next, and
  an optional `urgent` block. All fields optional so a server push carrying only `urgent` decodes.
- Views: countdown to next (`Text(timerInterval:)` / chronometer), progress bar for the current item, "Up next …"; before the event:
  "Gates open in …"; end of day summary. Dynamic Island compact/minimal/expanded. Stale date set on every update.
- Deep links by region: item → its detail; walk/location → map with route; urgent/update → Updates scrolled to it; "plan changed" → My plan.
  Interactive buttons (App Intents on iOS; notification actions on Android): Heart current, Skip (next plan item), Show on map.
- Driven by: hearted items or the plan if any, else the event's own now/next for the user's day.
- Start: iOS can only start while the app is in the foreground: start automatically when the app is open on an event day within 2 h of the
  first relevant item (or gates open); otherwise a local notification at that time ("Prairie Lights in 1 h · tap to follow on your Lock
  Screen") opens the app at the item and starts it; a venue geofence (CLMonitor / region monitoring, radius from the event's spread) sends the
  same local notification on arrival. Re-start each day (8 h limit). Android: start/update from the background (AlarmManager inexact
  windows, WorkManager, Play services geofence); Android 16 promoted ongoing notification (ProgressStyle + status chip); older: ongoing
  notification. Toggle in Options per event ("Follow on Lock Screen"); default on when the user has hearts/plan.
- Updates: background app refresh, location changes, the existing silent content_updated push.
- URGENT (SERVER, API-011): organizers mark a notification "Urgent" (e.g. cancellation). The server sends it to every device in the event
  with a token (regardless of `subscribed`), high priority; iOS additionally sends an APNs `liveactivity` update (apns-push-type
  liveactivity, topic <bundle id>.push-type.liveactivity, event "update", content-state {"urgent": {...}}, alert so it lights up) to each
  live-activity push token the app registered with `POST devices/` (`live_activity_token`, cleared with null / on leave). Android: FCM data
  `type=notification` + `priority=urgent` → urgent notification channel + the Live Update switches to urgent mode. The urgent block shows
  front and centre until dismissed or 12 h; tap → Updates at that notification.

## 9. Order
Wave 1 (apps): §1, §2, §3, §4, §5. Wave 2 (apps): §6, §7, §8 (+ widgets if time allows: "Happening now / next in my plan").
SERVER first (attnd-web): API-009 heart metrics, API-010 AI tags (locations get `tags`; import AI fills tags for locations and schedule
items; backfill command; demo data tagged), API-011 urgent announcements + live activity tokens.
