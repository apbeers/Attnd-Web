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

**v1** was the legacy API for app builds released before v2. Its events API was retired on Nov 19, 2026 (API-020: `410` `update_required`); `/api/v1/attest/` stays because both current apps use it.

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
| API-017 | **Parked (Nov 19, 2026): moved to git branch `parked/tickets`; keys always `null`, the 15 `tickets/` / `door/` endpoints removed from v2.** Tickets: `event.tickets_url` + `bundle.tickets` (all-in prices, Get tickets opens the web); My tickets (`tickets/code|verify|mine|signout/`, buyer token); door mode (`door/…`: Ed25519 keys + ticket list + revocations for offline scanning, idempotent check-in upload, counts) | additive (new keys, new endpoints) | ⏸ parked (on branch `parked/tickets`) | ✅ | ⬜ (later) |
| API-018 | Rich context for the apps' on-device assistant: `event.context`, `locations[].context`, `schedule[].context` in `bundle/` (about, FAQ, amenities, policies, getting there, weather plan, menus, hours, age limits, performers with bios and genres, links); AI import/refresh extracts it, console "Details for the assistant" edits it (locks), DEMO has it | additive (new keys, `bundle/` only) | ✅ dev (5beae38) · ⬜ prod | ✅ (173575f; tools, search, detail screens) | ⬜ (later) |
| API-019 | App Clip push topic: optional `app_clip` boolean in `POST events/<id>/join/` and `POST devices/` bodies; iOS sends `true` from the App Clip; the server sends clip devices' pushes (alerts, urgent, silent `content_updated`, Live Activity updates) on the clip's APNs topic | additive (new optional request field) | ✅ dev (4ff0467) · ⬜ prod | ✅ (c07ec37, local, not pushed yet) | n/a (no App Clip) |
| API-020 | Retire the v1 events API: `SUPPORTED_API_VERSIONS` default `2`, `/api/v1/events/…` → `410` `update_required`; `/api/v1/attest/` unchanged | breaking for v1 only (no current app uses v1) | ✅ dev · ⬜ prod (needs a release + owner go-ahead) | — (uses v2) | — (uses v2) |
| API-021 | "Plan your day" suggestions written for each event: `plan_suggestions` in `bundle/` (≤ 4 `{text, kind: plan\|question}`; Claude Haiku 5.5 in the background, deterministic fallback from the event's real days, categories and tags) | additive (new key, `bundle/` only) | ✅ dev · ⬜ prod | ⬜ | ⬜ |
| API-022 | Place images found automatically: `locations[].image_credit` (credit / licence of a photo Attnd found and stores itself, or null) | additive (new key) | 🚧 built · ⬜ prod | ⬜ | ⬜ |
| API-023 | Per-event colour themes: `event.theme` in `bundle/` (`{light, dark}` × `primary` / `secondary` / `tertiary` hex, contrast-adjusted; `null` without a theme or when the organizer turned it off for the apps). Made by the AI import, edited in the console; used on the website now. **Apps: later, not in this change** (iOS built Nov 21, 2026) | additive (new key, `bundle/` only) | 🚧 built · ⬜ prod | ✅ dev (Nov 21, 2026: tint, card wash, progress; the Live Activity's full theme, light / dark × primary / secondary / tertiary, 24c2b1c) · ⬜ prod | ⬜ (later) |
| API-024 | Server-driven screens: `layout` in `bundle/` (per screen an ordered list of sections from a closed catalogue, overrides per category / tag, on-device conditions) for apps that send `X-Attnd-Sections: <catalogue version>` (ETag `-s<version>`); `home` in `display.tabs` for those apps; console App layout → Screens editor, Ask Attnd `update_screen_layout` | additive (new key, new optional request header, new `display.tabs` value only for apps that send the header) | ✅ dev (6630ce5, 3d4e0b9) · ⬜ prod | ✅ dev (8bd7bd3, 0f25083; TestFlight 2.0.161) | ⬜ (later) |
| API-027 | Screens catalogue 2 (`X-Attnd-Sections: 2`): `my_schedule`, banners as sections (`status`, `update_banner`), new screens `schedule_tab`, `places_tab`, `updates_tab`, `onboarding`, section fields `ids` / `text` / `image`; `ask_about` out of the defaults; bundle `live_activity` (follows, buttons, urgent, stale text) | additive (new header value, new keys, new object) | 🚧 built · ⬜ prod | ✅ dev (Nov 21, 2026: the four catalogue 2 screens, section ids / text / image, `update_banner`, `live_activity` follows / buttons / stale text; 800c978, TestFlight 2.0.171) · ⬜ prod | ⬜ (parked) |
| API-026 | Faster live updates: the first change pushed at once (later ones grouped), `jitter_ms` in `content_updated` scaled to the event's size; organizer preview phones (`preview_token` on `join/`, `GET events/<id>/changes/` long-poll, `preview` in the join answer); `bundle/` records the version a device fetched | additive (new optional request field, new response and push keys, new endpoint) | 🚧 built · ⬜ prod | ✅ dev (Nov 21, 2026: `preview_token` from links and QR codes, `changes/` long-poll in the foreground, `jitter_ms`; 800c978, 641a8bf, TestFlight 2.0.171) · ⬜ prod | ⬜ (parked) |
| API-031 | Live Google Places photos for places without a free one: `locations[].image_url` may be our `/img/places/<id>/` redirect (fetched per view, never stored), `image_credit` `"Photo: Google Maps"`, new `locations[].image_attribution_url` (the authors' names, JSON) | additive (new optional key; a new kind of image URL) | 🚧 built · ⬜ prod | ⬜ | ⬜ (paused) |
| API-030 | Artist links: `schedule[].context.performers[].apple_music_url` (optional, `https://music.apple.com/…`; confident matches from the Apple Music catalog, or set by the organizer); console "Find music links" | additive (new optional key) | 🚧 built · ⬜ prod (needs `APPLE_MUSIC_*` on attnd-web) | ✅ dev (TestFlight 2.0.180: "Listen on Apple Music", https://music.apple.com links only) · ⬜ prod | ⬜ (paused) |
| API-025 | Free limit: an unpaid event takes 20 different devices on `join/`; a new device beyond it gets `409 {error: "event_full_free", message, limit}` (rejoin always works; paid, DEMO, App Review sample and showcase events exempt) | additive (new error answer on an existing endpoint, for unpaid events only) | 🚧 built · ⬜ prod | ⬜ | ⬜ |
| API-003 | Update check: `/api/app-status/`, update screen, 410 for retired versions | additive | ✅ dev · ✅ prod (Nov 15, 2026) | ✅ dev · ⬜ prod | ✅ dev · ⬜ prod |

API-012 is reserved (Android FCM → Firebase Installation ID registration, `ROADMAP.md`).

## Log

Newest first. Each entry says what changed and what each app must do.

### API-031: Live Google Places photos (fallback)
**Why:** many places have no photo on the event's pages, their website or Wikimedia; Google Places usually has one.
Google's terms forbid storing Places photos (owner, Nov 21, 2026: OK to show them live, attributed, client cache only).
**Server (attnd-web):** when `events/place_images.py` finds no free photo, it sets `Location.google_photo` (and keeps
only the place id, which the terms allow). `GET /img/places/<id>/` (public; published events, or the team for drafts;
`place_photo_ip` 300 / 10 min per IP; `GOOGLE_PHOTOS_DAILY_MAX` loads a Pacific day, 2,000 by default) asks Google
every time (Place Details `photos`, then the photo media URL with `skipHttpRedirect`) and answers a `302` to the photo
(`Cache-Control: public, max-age=3600`), or `?format=json` with the authors. Counted on Diagnostics → Endpoints
(`places_photos`). Off with `GOOGLE_PLACE_PHOTOS=0`. The console and Studio sites use the same URL.
**Bundle (additive):** `locations[].image_url` = that URL, `image_credit` = `"Photo: Google Maps"`, new
`locations[].image_attribution_url` (else `null`). See `API_V2.md` → Location.
**iOS:** nothing to change for the image itself (it's a normal https URL that redirects; `CachedImage` keeps it like
any other, which is within the terms as a client cache). Decode `image_attribution_url` leniently; on the place detail
(and wherever the photo is large) fetch it and show its `credit` under the photo (authors linked when there's room),
`image_credit` until then. Don't prefetch these photos for offline use beyond the normal image cache.
**Android:** later (paused): the same.

### API-030: Apple Music artist links for performers
**Why:** attendees want to hear a band before they walk over (owner, Nov 21, 2026; Apple Music only: Spotify's Web
API needs a Premium account owner, so no Spotify link).
**Server (attnd-web):** `events/music.py`: for each performer in a schedule item's context the server searches the
Apple Music catalog (`GET https://api.music.apple.com/v1/catalog/us/search?types=artists`, developer token = ES256
JWT from the MusicKit key `APPLE_MUSIC_KEY_ID` / `APPLE_MUSIC_PRIVATE_KEY`, team `APPLE_TEAM_ID`) once per name, in
the background after a save (new or renamed performers) or on **Find music links** (console readiness check, Ask
Attnd `find_music_links`). Only confident matches are kept (normalised names equal; genres overlap when both sides
have some; several same-named artists only when the genres single one out). Stored in `ScheduledItem.music` by
normalised name; organizer links (the performers line's last cell `| https://music.apple.com/…`, or `| no link`) are
locked and never replaced. Cached (found 30 days, nothing 7 days), counted per UTC day (Diagnostics → Endpoints), a
429 pauses lookups for an hour; off without the keys.
**Bundle (additive):** `schedule[].context.performers[].apple_music_url`, only when there is a link (`API_V2.md` →
Context). A new or changed link bumps `content_version`.
**iOS:** decode `apple_music_url` leniently (https only; ignore anything else); on performer rows, the item detail and
chat components show **Listen on Apple Music** (Apple's wording; the Apple Music badge per Apple's marketing
guidelines if used) that opens the URL (`openURL`: the Music app, else Safari); no MusicKit SDK, no account. Nothing
when the key is missing. App and App Clip alike.
**Android:** later (Android work paused): the same button with `Intent.ACTION_VIEW`.

### API-027: Screens catalogue 2 and the Live Activity's content choices
**Why:** the owner approved more server-driven screens (tab lists, Updates, banners, onboarding, Live Activity
choices) on Nov 21, 2026; the iOS app's My schedule merges `plan_today` + `liked`.
**Server (attnd-web):** `events/layout.py` `CATALOGUE_VERSION = 2`: new types with `since: 2` (see `API_V2.md` → Layout,
catalogue 2), `for_version()` serves each app its catalogue (1: `my_schedule` → `plan_today` + `liked`, no new screens;
2: `plan_today` / `liked` → one `my_schedule`), tab defaults from `display.schedule_style` / `locations_style`,
`ids` / `text` / `image` section fields, `ask_about` dropped from the defaults (still valid). Console Screens editor:
the new screens with previews, picks for `featured` / `pinned`, the welcome card's text and image.
`events/live_activity.py` + `Event.live_activity`: bundle `live_activity` (always; defaults), console App layout →
**Live Activity** (autosave; Ask Attnd `update_live_activity`). Tests: `events/tests_layout.py`.
**iOS:** send `X-Attnd-Sections: 2`; draw the new screens / types / fields per `API_V2.md` (unknown → skip; a screen
missing → built-in); keep urgent (12 h), offline and screenshot mode fixed; use `live_activity` for the default
follow mode, the buttons and the stale text (urgent block always on).
**Android:** parked.

### API-026: Faster live updates and the organizer's preview phone
**Why:** organizers edit with the app open on their phone and want to see changes at once; attendees should get them
within seconds without anyone holding connections open on a sync server (owner, Nov 21, 2026).
**Server (attnd-web):** `events/push.py`: the first `content_updated` of a burst goes out at once, later changes within
15 s become one follow-up; attendee pushes carry `jitter_ms` (real devices with a token: < 100 → 1,000, < 1,000 →
5,000, else 10,000), preview phones `jitter_ms: 0`. `events/preview.py`: signed tokens (person + event, 7 days, salt
`attnd.preview.v1`), `join/` takes `preview_token` (a valid one marks `Device.preview_until` / `preview_by`, frees the
device's free-limit spot, at most 3 live preview phones per event: a fourth ends the oldest), `GET events/<id>/changes/
?since=` (preview phones only; ≤ 25 s; the database connection goes back to the pool while it sleeps; at most 2 waiting
per process, shared with Ask Attnd's long-poll in `events/longpoll.py`, 3 per event; over a cap `retry_after: 15`).
`bundle/` writes `Device.last_version` once per device per change. Preview phones are excluded from attendee counts
(`Device.objects.attendees()`), never from pushes. Console: the "Live on your phone" card gets **Preview on my phone**
(QR, Open as preview on phones), the preview phones with Remove (`remove_preview_phone`), and a live "Updated on 31 of
37 phones" line (attendee phones seen in the last day, polled every 10 s). Tests: `events/tests_preview.py`.
**iOS:** read `preview` from the join link (Universal Link, App Clip invocation URL, scanned QR, `attnd://join/<code>
?preview=`; `DeepLink.httpsURL(fromAppScheme:)` must keep the query) and send it once as `preview_token` with `join/`;
when the answer has `preview: true`, hold `changes/?since=<content_version>` while in the foreground (refetch at once
on `changed`, honour `retry_after`, stop on `preview: false`); use `jitter_ms` from `content_updated` (0–`jitter_ms`
ms, else 0–10 s). Old builds ignore all of it.
**Android:** parked (owner, Nov 21, 2026). Builds ignore the new keys; `jitter_ms` would be the only easy win.

### API-025: Free limit on unpaid events
**Why:** anyone can now sign up and create demo events (owner, Nov 21, 2026); going live stays invite-only (an admin
marks the event paid, no checkout yet). A demo serves a dry run, not a real crowd.
**Server (attnd-web):** `events/free_limit.py`: `EventJoin` records each device that ever joined an event (the existing
devices were backfilled by the migration); `join/` refuses the 21st different real device of an unpaid event with `409
{"error": "event_full_free", "message", "limit"}` and doesn't register it. A device that joined before always rejoins.
Exempt: `paid`, `showcase`, the DEMO event, the App Review sample events (`seed_test_data`); `ci-` and `showcase-`
device ids never count. Console: "14 of 20 free spots used" + **Remove the limit** (emails the admins) on the event's
status card; staff **Mark paid** (`mark_event_paid`); listing in nearby search needs a paid event. `FREE_DEVICE_LIMIT`
(default 20). Tests: `events/tests_free_limit.py`.
**iOS / Android:** on a `409` from `join/` whose `error` is `event_full_free`, show the server's `message` (written for
attendees: the event is full on its free trial; ask the organizer) instead of the generic join error, and go back to
the launch screen; don't retry automatically. Older builds show their generic join error, which is acceptable.

### API-024: Server-driven screens (layouts)
**Why:** the owner wants each event to show what is relevant to it: every section compiled into the apps, the event's
JSON choosing which appear, in what order and with which options, like the console's components (Nov 21, 2026). Data
choosing among built-in components, never downloaded code (App Store guideline 2.5.2). Additive.
**Server (attnd-web):** `events/layout.py`: the catalogue (screens × section types, each with the catalogue version
that added it, the facts it needs and its parts), the apps' default layouts, `automatic()` (sections nothing in the
event could fill are left out; food places with menus put the menu and hours first; tags whose items have performers
put the performers first; Home per preset), `clean_config()` for `Event.layout_config` (organizers' screens, validated
like the apps do) and `resolve_layout()` (organizer screens over automatic ones, filtered to the app's catalogue
version). `bundle/` reads `X-Attnd-Sections`; without it `layout` is `null` and `display.tabs` never lists `home`
(older apps are unchanged, their ETag too); with it the ETag ends in `-s<version>` and the cached bundle is per
version. `display.tabs` accepts `home` (organizers turn it on; never automatic), at most 5 tabs. Usage accepts
`tab_view` `home`. Console: App layout → **Screens** (per screen: drag order, on / off, header parts, titles, limits,
the automatic overrides, a phone-shaped preview drawn by one Django component per section, autosave, Undo, Reset to
automatic); Ask Attnd `update_screen_layout`. Changing a layout bumps `content_version` (the usual `content_updated`
push). Contract: `API_V2.md` → Layout. Tests: `events/tests_layout.py`, `console/tests_screens.py`.
**iOS:** send `X-Attnd-Sections: 1` on `bundle/`; decode `layout` leniently; resolve each screen with the rules in
`API_V2.md` → Layout (unknown types, parts and conditions dropped, first matching override, data-driven visibility,
fallback to the built-in layouts, which are today's screens); draw the item and place details, the event info, Options'
event rows and the map overlay from them; a Home tab when `display.tabs` lists `home`; screenshot mode always uses the
built-in layouts and never shows Home.
**Android (later; not in this change):** the same rules and catalogue (`domain/` + the screens), sharing the iOS test
cases (`ScreenLayoutTests`); until then Android sends no header and gets no layout and no Home tab.

### API-023: Per-event colour themes
**Why:** every event looked Attnd-purple; organizers want their own colours (owner, Nov 21, 2026). Additive: one new
key in `bundle/`. `accent_color` is unchanged (the apps keep using it until they adopt this).
**Server (attnd-web):** `Event.theme` (`{light: {primary, secondary, tertiary}, dark: {…}, source, …}`) with two
switches, `theme_web` and `theme_apps` (both on by default). `events/theme.py` validates it (upper-case `#RRGGBB`, a
missing mode derived from the other) and guarantees contrast before anything draws it (WCAG 2.2: text ≥ 4.5:1,
highlights ≥ 3:1; a colour is never refused, it is nudged in lightness). The AI import picks one (Claude Haiku from the
website's colours, logo / flyer and the event's style; a deterministic theme from the website's main colour, else
Attnd's, without the AI); organizers edit the six hex codes in the console's **Colour theme** card (an edit locks it
against AI work), **Generate with AI** with an instruction, or reset it. The console's event pages, the Ask Attnd
canvas and the join / shared-plan pages use it when "Use custom theme on the website" is on. The bundle's
`event.theme` = the contrast-adjusted colours, or `null` without a theme or when "Use custom theme in the apps" is
off; changing it (or that switch) bumps `content_version`, so apps get the usual `content_updated` push. Not in
`lookup/` / `nearby/`. Tests: `events/tests_theme.py`.
**iOS / Android (later; not in this change):** decode `event.theme` leniently (missing, `null`, a missing mode or role,
a value that isn't `#RRGGBB` → treat the theme as absent, or derive the missing part from what is there; ignore
unknown keys). When present, prefer it over `accent_color`: `primary` = the tint (buttons, selected tabs, links; text
on it is readable as sent), `secondary` = soft tints (selected rows, chips, backgrounds at low opacity), `tertiary` =
highlights (progress, badges, gradients' end); pick `light` or `dark` by the system appearance. `null` → keep today's
behaviour (`accent_color`, else the brand purple).

### API-022: Place images in our own store, with credit
**Why:** imports used Google Places photo links, which expire and which Google's terms don't let us keep; many places
had no image at all.
**Server (attnd-web):** `events/place_images.py`. A new location without an image (AI import, by hand, Ask Attnd) gets
one looked for in the background: images on the event's own pages that name the place, the place's website
(og:image; the website found with Google Places' `websiteUri`, never its photos), then Wikimedia Commons near the pin
or by name (author + licence kept). Each candidate is ≥ 600 px wide, not a logo / map / icon, not another place's
picture, and confirmed by Claude Haiku's vision; it is re-encoded (JPEG, ≤ 2000 px, no EXIF / GPS) and stored as the
location's own image (Cloudinary), so `image_url` points at our storage. Organizer images and locked images are never
touched; console **Remove photo** never looks again, **Replace photo** / **Find a photo** look now; a daily cost cap.
`manage.py find_place_images` backfills and replaces old Google photo links. The bundle adds `image_credit` (string or
`null`, only for found photos).
**iOS / Android (later):** decode `image_credit` leniently (missing → `null`). When set, show it small (caption
style, secondary colour) under or over the bottom of the location's large image (location detail; optional on cards).
No other change.

### API-021: "Plan your day" suggestions from the server
**Why:** the apps made their "Plan your day" examples on the phone from tags and weekdays, which often didn't fit: a
Thursday-only art walk got "Plan my Saturday around art and evening" (owner, Nov 21, 2026). The server knows the
event's real days and highlights, so it writes them. Additive: one new key in `bundle/`.
**Server (attnd-web):** `events/plan_suggestions.py`. `bundle.plan_suggestions` = up to 4 `{text, kind}` (`kind`
`plan` or `question`), in order: one or two plan requests on the event's real days, parts of the day and highlights
("Plan my Thursday evening around the galleries"), a short-visit one ("I've got 2 hours — show me the highlights"), a
family one only when schedule items carry a family tag (kid-friendly, kids, family, all ages, family-friendly), and
one question last ("What's on near me right now?" or one for this event). Claude Haiku 5.5 writes them (official
`anthropic` SDK, structured output, cached system prompt, effort `low`), checked in code (only the event's weekdays,
the family rule, ≤ 80 characters, kinds, ≤ 1 question); stored on `Event.plan_suggestions` with the content version
they were made for, regenerated in the background ~90 s after content changes (from the debounced content push; at most
one AI run per event per 10 minutes; `run_scheduled_tasks` catches up) and never in the bundle request. New ones bump
`content_version` once (so apps refetch). The bundle re-checks stored ones against the current days and otherwise
uses the deterministic fallback (the event's real dates, categories and tags), so the key is always present.
Without `ANTHROPIC_API_KEY`, or when the AI fails or refuses, the fallback is used. Tests: `events/tests_plan_suggestions.py`.
**iOS / Android (later; not started):** decode `plan_suggestions` leniently (missing, `null`, empty, entries without
`text` or with an unknown `kind` → skip). **When the bundle has at least one usable entry, show those as the "Plan
your day" examples** (onboarding card, the empty plan page / My plan, the plan card) in the given order: a `plan`
entry makes the plan from its text (`PlanRequest` path, as the local examples do), a `question` entry asks it (the
model / router turn, as "What's on near me right now?" does today). **Otherwise keep the local `PlanRequest.examples`
exactly as now** (offline with an old saved bundle, older servers). No other change; no new usage metrics.

### API-020: API v1 events API retired
**Why:** no shipped app calls `/api/v1/events/` any more (owner, Nov 19, 2026: "nobody uses it"); both apps use v2 and
attest at `/api/v1/attest/`.
**Server (attnd-web):** the v1 routes (`events/urls.py`), views and serializers are gone, with `API.md` and the v1 tests;
`SUPPORTED_API_VERSIONS` defaults to `2`, so every `/api/v1/events/…` path answers `410` `{"error": "update_required",
"update_url": …}` through `retired_api_view` (tests in `events/tests_v2.py`), and `/api/app-status/?api=1` says
`update: required`. `/api/v1/attest/nonce|ios|android/` are unchanged. The unused iOS App Attest assertion endpoint
(`/api/v1/attest/ios/assert/`) was removed at the same time.
**Production:** on dev now. Production gets it with the next release (`develop` → `main`), with the owner's go-ahead;
if production's config sets `SUPPORTED_API_VERSIONS`, change it to `2` then.
**iOS / Android:** nothing (they use v2).

### API-019: App Clip devices get pushes on the clip's APNs topic
**Why:** the iOS App Clip (`com.arcu.Attnd.clip`) is now the same as the app: it registers for APNs and starts Live
Activities. APNs needs the token's own bundle id as `apns-topic`, so the server must know which devices are the clip.
The team-wide APNs auth key works for both topics. Additive: one new optional request field.
**Server (attnd-web):**
- `Device.app_clip` (migration 0038, default `false`), set from `app_clip` in `join/` and `devices/` bodies only when it
  is a boolean (missing or anything else keeps the stored value).
- `events/push.py`: iOS tokens are split into the app's and the clip's (`ios_token_groups`) and sent per topic
  (`send_apns_groups`, `apns_topic`): app → `APNS_TOPIC`, clip → `APNS_CLIP_TOPIC` (default `<APNS_TOPIC>.clip`), Live
  Activity tokens + `.push-type.liveactivity`. Every APNs path: organizer alerts, urgent alerts, silent
  `content_updated`, Live Activity urgent updates. Invalid tokens are forgotten per group as before.

**iOS:** send `"app_clip": true` in `join/` and `devices/` bodies from the App Clip and `false` from the app (runtime
check: the bundle id ends in `.clip`).

**Android:** nothing (no App Clip); never send the key.

### API-018: rich event context for the on-device assistant
**Why:** the apps' assistants (Apple's Foundation Models, Gemini Nano) answer questions fully offline, so the bundle
must carry what the event's pages say beyond the description (`docs/specs/planner_view_spec.md` §5). Additive: three
new nullable keys in `bundle/`; nothing else changes.
**Server (attnd-web):**
- `context` JSON on `Event`, `Location`, `ScheduledItem` (migration 0037), normalised by `events/context.py` on save
  and on output (trim, cut at a word boundary, drop empties and unknown keys, https links only, limits per
  `API_V2.md` → Context). Snapshots/History restore it; any edit bumps `content_version`.
- Bundle: `event.context`, `locations[].context`, `schedule[].context` (`null` when empty), amenity `location_id`s
  only for visible locations (else `null`), all context together ≤ ~150 KB (item, then location `about` texts dropped
  last first, logged). Not in `lookup/` / `nearby/`, not in v1.
- AI import/refresh (`scrape/service.py` strict schema + `CONTEXT_RULES`, `scrape/diff.py`): extracts the context per
  event / location / item; one field `context` per entity in changesets (minor), merged key by key onto the current
  one (empty AI values never blank data; performers / menu entries keep bios / prices the AI left out), locked
  `context` → conflict (unticked); amenities name their location and get its id when applied. Review shows a summary
  ("FAQ +2 · Hours added").
- Console: "Details for the assistant" (collapsible) on Details, location edit and schedule item edit; text fields
  plus validated line formats for FAQ / amenities / menu / performers / links. Editing locks `context`.
- DEMO: event about / FAQ (11) / amenities (11, with location ids) / accessibility / policies / getting there /
  weather plan / links; menus and hours on the food and drink places; performers (bio + genre) on all 36 music
  items; chef demo and kids items with about / price / age limit. Counts unchanged; `events/tests_context.py`.

**iOS:**
- Decode `context` on `Event` (bundle only), `Location` and `ScheduleItem` leniently: every key optional, `null` /
  missing = none, unknown keys and amenity kinds (→ `other`) ignored, entries' optional parts (`note`, `price`, `bio`,
  `genres`) may be missing. Save it with the offline bundle like everything else.
- Feed it to the on-device assistant's retrieval (Foundation Models tools / `AssistantModel.retrieve`): event FAQ,
  amenities (with their location), policies, getting there, weather plan; location menus / hours / age limits;
  performers' bios and genres. Cite the items as today.
- Show it on detail pages where useful (e.g. a location's menu and hours, an item's performers and price, an "Event
  info" FAQ in Options / `EventInfoView`). Links open in the browser.

**Android (later):** the same: decode leniently (`@Serializable`, every field defaulted), store with the bundle, use
in `AssistantModel.retrieve` / `AssistantPrompt.context` and the event facts for AppFunctions, show on details.

### API-017: tickets (Get tickets, My tickets, door mode with offline scanning)
**Moved to a branch (Nov 19, 2026, owner: "move ticketing to the git branch"):** the `tickets/` app, its settings,
URLs, console tools, Studio `<attnd-tickets>` element, AI drafts, tests, live/e2e suites, CI steps and
`docs/specs/tickets_spec.md` were removed from `develop`; everything is on the git branch `parked/tickets` (cut from
`develop` before the removal). `event.tickets_url` and `bundle.tickets` stay in v2, always `null`
(`events/tests_contract.py` checks it). The 15 `tickets/…` / `door/…` endpoints were removed from `contract/v2.json` and
`API_V2.md` (owner decision: no app calls them). Restoring = merge or cherry-pick from `parked/tickets` (its
`TICKETS_ENABLED` switch, migrations of the `tickets` app, contract entries). Dev's database keeps its orphan
`tickets_*` tables (SETUP.md §16).
**Earlier (Nov 19, 2026, owner):** ticketing is kept in the code but included in nothing: the server runs with
`TICKETS_ENABLED` off (default), so `event.tickets_url` and `bundle.tickets` are always `null` (keys kept; the
contract test compares the ticket endpoints only with `TICKETS_ENABLED=1`) and the `tickets/…` / `door/…` endpoints
return a JSON 404. **Apps:** Get tickets already hides itself while both keys are `null`. iOS's My tickets and Scan
tickets (Options and the launch screen, not tied to an event's keys) call the parked endpoints and would get a 404:
hide them too (follow-up in attnd-ios). Don't start Android's part. Organizers use Eventbrite for now.
Re-enabling = `TICKETS_ENABLED=1` on the server (CLAUDE.md → Attnd Tickets).
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
**Why:** Attnd Studio (invite-only beta then, admins only since Nov 19, 2026; `docs/specs/studio_spec.md`) lets an organizer design the event's website
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
