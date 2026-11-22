# Roadmap

Work agreed but not built yet. Move an item into `API_CHANGES.md` (for API changes) or just build it when
it's picked up, then delete it from here.

## Needed before release

### File storage for uploads (done: Cloudinary, Nov 15, 2026)
Cloudinary free add-on on both apps (SETUP.md step 5); only the PDF delivery setting is left for the owner. Later:
upload limits/validation (type, size), cleanup of replaced/deleted files, and a paid plan if storage or bandwidth grows.

### Sending email (mostly done: see SETUP.md step 11)
Needed for team invites (`EventInvite`), password reset, sign-up confirmation and task/ingest notices.
- Today: SMTP settings point at Mailgun (`MAILGUN_SMTP_LOGIN` / `MAILGUN_SMTP_PASSWORD`, `attnd/settings.py`),
  but no credentials are configured, so nothing is actually sent.
- Decided (Nov 14, 2026): Mailgun via the Heroku add-on, sending domain `mg.attnd.app`, DNS at Namecheap.
  Account/DNS steps: SETUP.md step 11.
- Code to do with it: `EMAIL_USE_TLS = True` (port 587 needs it; missing today); `DEFAULT_FROM_EMAIL` /
  `SERVER_EMAIL` = `Attnd <noreply@mg.attnd.app>` instead of hard-coded senders; links built from a
  per-environment site URL (emails hard-code https://attnd.app today, so dev mail links to production);
  dev allow-list (only listed addresses get real mail, the rest goes to the log); console backend when no
  credentials; password reset (Django's built-in views, console-styled templates); `ADMINS` error emails;
  plain-text + HTML templates in the house style.

### Go-live checklist (everything else flagged so far)
**Release + production config** (production changes need the user's go-ahead)
- ✅ First web release: `develop` → `main` on Nov 15, 2026 (release v110): new join page, AASA, passkeys, email, security headers, own design system. Later web releases: fast-forward `main` to `develop`.
- Production config: ✅ `GOOGLE_MAPS_BROWSER_KEY`, `IOS_APP_STORE_URL` (Nov 15, 2026); ✅ `APNS_USE_SANDBOX` unset (production APNs);
  ✅ `APPLE_ATTEST_ENVIRONMENT` unset = production (TestFlight and App Store builds use production too).
- ✅ Production DEMO event loaded (Nov 15, 2026; the prod integration tests pass against it). Still to do: the App Review account (`create_review_account`); both stores' review notes tell reviewers to use code DEMO on attnd.app.
- Production cleanup (SETUP step 7): list users and demo events, delete with a go-ahead. Keep DEMO and the App Review
  sample events FWFM2026 / ATIS2026 / DACN2026 (kept for App Review, owner Nov 19, 2026; `seed_test_data` keeps them current).
- Heroku Postgres: check the version and upgrade to 18 (SETUP step 2).
- First app releases to `main` (iOS App Review, Play production), then Play production lock-down (SETUP 4d).

**Store setup (user, in the consoles)**
- Play Console: exclude tablets (SETUP 4e); App access (no login, code DEMO); data safety; content rating;
  category; contact email; privacy policy URL; the service account needs "Manage store presence".
- App Store Connect: App Clip experiences (default + advanced for `https://attnd.app/join/`; work once
  production serves the new AASA); App Review contact (`REVIEW_CONTACT_*` repo variables); App Privacy answers.
- Store screenshots: finish getting both screenshot pipelines working, upload, and remove their temporary
  push triggers.

**Product gaps**
- Payments: events stay "Demo event" (`paid=False`) and there is no way for an organizer to pay/upgrade
  yet. Stripe is the expected exception to the dependency policy.
- Legal pages: check `/privacy/` (and add terms) actually describe what the apps and console collect
  (device id, anonymous usage counts, push tokens, organizer accounts, AI processing of uploads/URLs).

**Operations (suggested, not yet discussed)**
- ✅ Database backups: daily scheduled on both apps (Nov 14, 2026; SETUP.md). Consider a higher Postgres plan for point-in-time rollback before real data grows.
- Error alerting: today errors only reach Heroku logs. At minimum, email admins on server errors
  (Django `ADMINS` + email, once email works) and a log drain or Heroku alerting.
- Load: since API-007 open apps poll every 5 min (60 s without a push token), but each content push makes
  every open app with an older `content_version` fetch the full bundle within ~10 s (random 0–10 s delay: ~100 req/s per 1,000
  open apps, briefly). Older builds still poll every 5 s until they update. Load-test or size dynos before
  the first big event.
- Push fan-out: `send_notification` sends one APNs/FCM request per device, in order, inside the transaction that
  marks the notification sent. Urgent announcements (API-011) go to every device with a token plus every Live
  Activity, so a big event (thousands of devices) takes minutes on the web dyno. Before the first big event: send
  concurrently (HTTP/2 multiplexing, a thread pool) or on the worker dyno, outside the row lock.
- Revisit the "push straight to develop" rule (branch protection, PRs) once there are real users.

## Round 3 web plan (owner-approved Nov 21, 2026; in progress)
Built on `develop` in this order; ticked as it ships.
- [ ] **A. Console + chat polish**: address search on every location form (Google Places, else Find on the map via
  Google Places / Nominatim on the server) ✅, the location map with the event's other locations ✅, Fill from this
  website ✅, Notifications two columns + Scheduled / Sent sections + editing scheduled ones ✅, canvas-first answers
  (one line in the chat) ✅, Team and Metrics tabs ✅, platform help with links ✅, narrower margins ✅, no dumps / one
  answer ✅, History hides Locked fields ✅, Add with AI on every form + fill_form ✅, AI fills Details for the
  assistant ✅, Updates → Notifications in the console ✅, one reveal helper for every cross-place change ✅, the event
  map on the Overview ✅, live numbers every 60 s ✅, the rest of the organizer's work on the canvas (review, layout,
  watching, event details, flyer, sharing kit, lifecycle, restore, CSV) ✅, Prospects on the canvas (staff) ✅, Chat as
  default ✅, reload without jumps ✅, A-fix architecture corrections ✅ except splitting agent.js into modules
  (chat, cards, canvas, overlay, fill: ~1,500 lines in one closure today; do it with stage H's lazy-loaded Ask Attnd).
- [x] **B. Sign-in flow** ✅: signed in → console; logo → /home/; no prompts during a session.
- [x] **C. All AI on Anthropic** ✅ (official SDK; OpenAI only for Studio images).
- [x] **D. Place images in our own store** (event pages, venue site, Wikimedia; API-022 `image_credit`).
- [x] **E. Packages**: latest everything, psycopg 3 + pooling; Python stays `3.15` in `.python-version` (newest 3.15.x
  on Heroku and CI).
- [x] **F. Django built-ins**: database cache ✅, LoginRequiredMiddleware ✅, Postgres search ✅, humanize + db_default ✅
  (no GeneratedField: the ends we query need interval maths, which Postgres won't store), django.tasks with our own
  DB-backed worker ✅ (built, off by default: `TASKS_BACKEND=database` on dev first, then a `tasks` dyno or the scheduler).
- [x] **G. Security** ✅: ALLOWED_HOSTS, Scrypt, Permissions-Policy, SECRET_KEY_FALLBACKS, Ask Attnd rate limit + magic bytes.
- [x] **H. Performance** ✅ (agent.js split into modules still open, see A): console scripts in `<head>` with `defer`, lazy Ask Attnd, cached root files, sized images, polling back-off.
- [x] **I. Chat-first additions** ✅ (real-model check: the nightly harness once `ANTHROPIC_HARNESS_KEY` is set) (Tiers 1–3: preview-before-apply, briefing / readiness, paste anything, server-side
  conversation, quality harness, live ops, notification drafting, voice, numbers, command bar, preferences, roll over,
  search, streaming, mobile chat-first, organizer notifications, first-run walkthrough, talk to a person).

## Future

### Event assistant (in progress; spec agreed Nov 15, 2026)
Spec: `docs/specs/assistant_spec.md` (the "Attnd event assistant spec", read with `glance_plan_spec.md` and `search_spec.md`).
The planner grows into one assistant screen in both apps (search, ask, like, plan, plan edits with undo, follow,
reminders, share), answering with native "blocks"; on-device only, rule-based without the model; Android's `domain/`
is the reference. Web parts:
- Done (not pushed): shared plan links `/p/<code>/<payload>` + AASA paths (**API-015**), console "likes" wording
  (metric names unchanged), the new log event names in `LOG_EVENTS.md`.
- Apps: everything else (§1–9), including the `/p/` App Link intent filter on Android and an App Clip advanced
  experience for `https://attnd.app/p/` if scanning a share QR should show the clip card (owner, App Store Connect).

### Assistant eval follow-ups (eval built Nov 22, 2026: `docs/assistant_eval/`)
- **Run the assistant eval on Android once the rewrite lands**: a JVM router-mode test over `questions.json` +
  `demo_bundle.json` and an instrumented model-mode test on a Gemini Nano phone, same report shape
  (`docs/assistant_eval/README.md` → Android); first port iOS's new router rules (safety, amenities, FAQ / topics,
  menus, updates / changes, names inside questions) with the same answers.
- Run iOS **model mode** on the owner's Mac or iPhone with Apple Intelligence and write its findings.
- Router gaps left: "is the beer cold?", cheapest food from menu prices, "this morning" as a time word, two-edit typos
  (a Damerau transposition in the shared search spec).
- 911 is hard-coded in the safety answers (US events only today); use the event's country when events go abroad.

### Glance / hearts / planner / Live Activity follow-ups (built Nov 15, 2026; API-009/010/011)
Built on both apps: relative times, distance + compass, walking routes, map/list glanceables, hearts, the
on-device planner (identical rules on both, Android's `Planner.kt` is the reference), onboarding, Live
Activity / Live Update (device-driven; server only for urgent announcements), reminders. Still open:
- Home-screen widgets ("Happening now / Next in my plan"): Android has its Glance widget. iOS built a "Now & Next"
  WidgetKit widget and removed it on Nov 19, 2026 (its App Group can't be used by the App Clip, which is the app);
  `AttndWidgets` is the Live Activity only. Not planned again for iOS.
- Android venue arrival: needs `ACCESS_BACKGROUND_LOCATION` + the Play Console background-location
  declaration (code is in place, off). Owner decision.
- Align small differences: iOS reminders use a fixed 15 min lead (Android adds walk time); iOS Live Activity
  leaves the plan once today's planned items have ended (Android keeps it for the day).
- Production: release develop → main, reload DEMO and run `backfill_tags --all` there (owner go-ahead).

### Diagnostics follow-ups (server built Nov 15, 2026: API-013 / API-014)
Server side is done (uploads, caps, purge, request log, panel, R8 retrace, symbolicate dispatch). Still open:
- attnd-ios: `AppLog` facade + ring buffer + viewer, debug mode, `Dev` configuration, uploads, MetricKit reports,
  dSYM artifacts per build and the `symbolicate.yml` workflow (content in the API-013 log entry's notes); API-014 counts.
- attnd-android: the same with a `dev` build type, ApplicationExitInfo + crash handler reports, the CI mapping upload
  (Play `deobfuscationfiles` + `POST /api/diagnostics/symbols/`); API-014 counts.
- Store privacy answers: App Store "Diagnostics: crash, performance, other diagnostic data — not linked, not
  tracking"; Play data safety "Crash logs, Diagnostics — not shared, optional for debug logs" (owner, in the consoles).
- Later, if needed: a request-log drain to a cheaper store than Postgres; server lines for every request (not only
  debug sessions) would need it. Retrace of R8 outline/synthesized frames (rare; shown unchanged today).
- Live tests: once the apps upload, add a CI-key upload check to the three live suites (answered, discarded).

### Android: move FCM to the Firebase Installation ID registration API (API-012, reserved; API-008 became the CI keys, API-009–011 the heart counts, tags and urgent announcements, API-013/014 diagnostics)
firebase-messaging 25.1.0 (June 2026) deprecated `FirebaseMessaging.getToken()` / `onNewToken` in favour of
`register()` / `onRegistered`, which hands out a Firebase Installation ID instead of an FCM token. The app
currently suppresses the deprecation (`PushNotifications.kt`, `AttndMessagingService.kt`). Moving needs a tracker
entry: what `push_token` carries, how `events/push.py` sends to it (FCM HTTP v1 target), and a transition where
the server accepts both. Do it before the old API is removed.

### Live connections for attendees (parked, Nov 21, 2026)
API-026 gives only the organizers' preview phones a long-poll (`changes/`). Since batch 7 the web dyno runs ASGI and
long-polls wait on the event loop (`attnd/longpoll_asgi.py`, ≤ 500 per process, measured 300 held with no effect on
other requests), so the server side of attendee long-polls exists; what's left is an API change (tracker entry, apps
opt in) and a decision on how many attendees one dyno should hold. Until then attendees stay on push + the slow
safety poll. Also parked:
partial bundles (send only what changed) and content inside the push.

## Growth: prospects → free app → Studio (dashboard built Nov 19, 2026)

The funnel: find events that suit the app (staff **Prospects** dashboard, `/console/prospects/`, DFW first), make a
testing-only copy to show the organizer the app with their own event, offer the app free, then sell Attnd Studio (we
build their website; they always keep the app). Outreach is by a person, CAN-SPAM compliant, do-not-contact honoured.
Follow-ups:
- "Refresh details" on one prospect (re-read its pages for new contacts and dates; today only new prospects are read).
- A way to hand a testing-only event to the organizer once they say yes (transfer ownership, clear `testing_only`,
  publish to nearby, turn on website watching), and a printable one-pager with the QR for the pitch.
- Outreach templates (sender identity, physical address, opt-out line) and a log of who was contacted when; a
  suppression list across prospects so do-not-contact follows the person, not one prospect row.
- More regions: several `ProspectSettings` profiles (Austin, Houston, …) instead of one row.
- Robots.txt check before reading a prospect's pages (the shared fetch has none today).
- Check the real cost of a full 40-event run on dev and tune `PROSPECT_DAILY_SPEND_CAP_USD` / per-run caps.

## Attnd Studio (admins only for now; after that)

### Stripe checkout for the two tiers
Stripe Checkout (the expected exception to the dependency policy) for the two event tiers: **Standard** (import and
manage an existing event) and **Studio** (AI designs the site, flyer and app look; Attnd hosts the site). `Event.plan`
already exists; today an organizer's "Upgrade to publish" / "Remove the limit" is an `UpgradeRequest` a superuser
approves in the Platform admin (D2, which records a `PaymentRecord`); checkout would set the plan (and `paid`) from a
webhook and create the `PaymentRecord` itself, at the prices in `PlatformSettings`. Prices
(owner, Nov 15, 2026): **$299 per event** = bring an event in, **$499 per event** = Studio + ticketing. The home page
already says "$299 per event, flat"; add the $499 tier there when Studio leaves beta.

### Studio custom domains
Let organizers use their own domain for their Studio site (spec §13): `Site.custom_domain` + DNS verification, add it
with the Heroku Platform API (`POST /apps/attnd-web/domains`, ACM certificate), show the DNS target, serve the site at
`/` on that host (same relative links, `<base href="/">`). One origin per site also removes the path-based hosting's
shared-origin limitation. Check Heroku's domain limits per app first.

### Studio later
- A real CDN or cache in front of hosted pages and assets before big events (today: Heroku + Cloudinary redirects).
- Server-side flyer rendering (an HTML flyer layout → PNG/PDF) if image-model flyers aren't enough.

## Tickets: parked, Eventbrite for now (Nov 19, 2026)
Built (web + server, Nov 16, 2026: API-017) but **parked**: since Nov 19, 2026 the code and spec
(`docs/specs/tickets_spec.md`) live only on the git branch `parked/tickets`; `develop` has no ticketing (the bundle's
ticket keys are always null). Organizers who want to sell tickets use Eventbrite for now. When it comes back (merge from
the branch, `TICKETS_ENABLED=1`):
- **Owner setup** (SETUP step 16): Stripe + Connect in the LLC's name, test keys/webhooks on dev, Wallet certificates
  and Google Wallet key (the steps are in SETUP step 16 on the branch). Then re-record the Stripe fixtures (`tickets/fixtures/stripe/`) from real test-mode payloads.
- **Apps**: iOS My tickets + door mode next (API-017 iOS section), Android later.
- Before real sales: Terms of Sale, Organizer Agreement, privacy policy update, store privacy labels; confirm Stripe's
  maximum payout hold for US Express accounts (events months ahead); Stripe Tax / sales tax on tickets if needed;
  the $499 plan checkout itself (today the plan is switched by staff).
- Later (spec phase 3): timed entry, rotating QR codes, resale, affiliate links, tap to pay; Attnd absorbing fees for
  a percentage (one plain price) as an option; a buyer account page for people with Attnd accounts.

## Platform ideas (agreed direction, not built; Nov 16, 2026)
- **Event ops / Work mode, roles, accounts, staff tools, modular features**: `docs/specs/event_ops_plan.md` (iOS first,
  one app; Sign in with Apple/Google/email code/passkey; invites by email, phone share, personal/group QR; roles from
  door staff to stage managers and vendors; staff location sharing; staff notifications with view tracking and optional
  acknowledgement; every feature an optional module, simple by default).
- **Email campaigns + contacts**: one contact list per event (buyers, RSVPs, waitlist, Studio form sign-ups, past
  attendees, CSV) with consent; scheduled campaigns to segments; the default sequence (announce → early bird → lineup →
  last chance → know before you go → day of → thank you/survey); AI drafts with review; unsubscribe/CAN-SPAM; a separate
  marketing sending subdomain; open/click stats. SMS later (urgent only, needs a provider).
- **Organizer billing** ($299 / $499 checkout via Stripe) before launch.
- **Applications**: vendor/artist/food truck/volunteer/sponsor applications, review board, fees via Stripe, documents
  with expiry reminders, waivers/agreements, booth assignment on the map.
- **Sponsors**: tiers, deliverables, automatic logo placement (site/app/flyer/emails), sponsor reports.
- **Map editor**: zones (stages, restrooms, first aid, accessible routes, closures, parking, shuttle stops) feeding app,
  site and flyer.
- **Run of show + task checklists** (internal schedule, AI-drafted tasks by event type, owners, deadlines).
- **Promotion**: tracking/promoter links, social image sizes + captions from the flyer, `.ics` feed, schema.org Event
  data on Studio sites.
- **During**: live ops dashboard, translations of all content, polls / Q&A / photo wall.
- **After**: survey (NPS, per act/vendor), recap email, organizer report/exports, clone for next year.
- **Platform**: organizations (multiple events, series, venues, shared branding/roles), integrations (webhooks,
  Sheets/CSV, Mailchimp import, calendar feeds), legal/compliance (ToS, privacy, data requests, WCAG for sites,
  moderation), help center + AI support for organizers.

## Ask Attnd (console agent) follow-ups
Built Nov 20, 2026 (`agent/`, CLAUDE.md → Ask Attnd). Later:
- Check the panel in real browsers (desktop, iPhone/Android bottom sheet, screen readers: VoiceOver, TalkBack, NVDA)
  and tune the copy; only the server side and the JSON endpoints are tested automatically.
- Cost: the full tool list (~70 tools for staff, ~11k tokens) goes with every call; OpenAI's prompt cache makes repeats
  cheap. If runs get expensive, send only the tools of the pages the run has touched (plus a `more_tools` tool).
- Undo all covers what History snapshots (details, locations, schedule); extend snapshots to the app layout, so
  Undo all can roll that back too. Updates already sent can't be unsent.
- Watch mode navigates the person's own window; a side-by-side "follow along" view on wide screens would be nicer.
- Streaming the model's text into the panel (now: steps appear as each tool finishes, polled every 1.5 s).
- A staff page of runs and spend across users (now: Django admin → Ask Attnd runs / usage).
- Console components (built Nov 20–21, 2026: Your events, the overview's pieces, locations, schedule, updates, event
  details, new event, history): add the app layout as a component when the chat needs it; the bespoke screens (Studio
  editor, diagnostics, AI review, prospects, usage, team) stay pages. In the floating panel a "new location / item"
  form that saved could show the new row (the full-screen canvas goes back to the list, which shows it); a run's model
  context could refresh the JSON of components the person changed by hand after they were shown (now: the stored
  JSON + get_component). Opening a detail on the canvas records a pick, which adds a tile to the chat each time; fold
  repeated picks of the same item into one.
- History (LogEntry, Nov 21, 2026) doesn't record `QuerySet.update()` writes (drag-to-reorder) or background jobs
  outside a request (website watching auto-applying changes); record those with the job as the actor if wanted.

## API-021 in the apps (not started)
The server sends `plan_suggestions` in every bundle (API_CHANGES.md → API-021). iOS and Android: show them as the
"Plan your day" examples when present (plan → make the plan, question → ask it), else keep the local examples.

## API-023 in the apps (web done Nov 21, 2026; iOS built Nov 21, 2026; Android not started)
The bundle's `event.theme` carries each event's colours (light and dark × primary / secondary / tertiary, already
contrast-adjusted; `null` without a theme or when the organizer turned "Use custom theme in the apps" off;
API_CHANGES.md → API-023). iOS and Android: decode it leniently and, when set, use it instead of `accent_color` for
the event's tint (primary), soft surfaces (secondary) and highlights (tertiary), following the system's light / dark
mode; `null` → today's behaviour (`accent_color`, else the brand purple).

## Android: adopt My schedule and the iOS assistant architecture (iOS built Nov 21, 2026)
iOS replaced likes and the separate AI plan with **My schedule** (items, places at a time, saved places; per-entry
reminders; "+ Add" / "In my schedule"; Schedule tab section, Home "Up next in my schedule", organizer-change hints;
migration from hearts and plans; API-009 heart metrics unchanged), removed Ask About This and the Demo badge, and moved
the assistant to: conversation memory ("it", "the second one"), the router's My schedule grammar (add / remove / move /
remind, clashes), tool-calling model turns with a token budget, one component catalogue shared by chat answers and
API-024 layouts (catalogue version 2: `my_schedule` on Home; `plan_today` / `liked` map to it), and My plan as one
continuous chat. Spec: `ios-my-schedule.md` (owner-approved) and the iOS CLAUDE.md sections My schedule / Assistant
architecture / One component catalogue. Android: port `MySchedule`, `ConversationContext`, `Component` and the router
changes into `domain/` with the iOS test cases (the shared fixtures now diverge; CLAUDE.md lists each), then the UI.
Server: when ready, add `my_schedule` to `events/layout.py`'s catalogue as version 2 (the apps already map the old names).

## API-024 server-driven screens: Android, and later sections (iOS + web built Nov 21, 2026)
- **Android adopts it** (phase 5 of the plan): port `ScreenLayout` (catalogue, validation, overrides, conditions, facts;
  the iOS `ScreenLayoutTests` cases) into `domain/`, draw the detail screens, event info, Options' event rows and the
  map overlay from it, add the Home tab, and send `X-Attnd-Sections: 1` on `bundle/`. Until then Android gets no layout
  and no Home tab (the server strips both without the header).
- Later section types (a new catalogue version, an app release each): sponsors, a hero image, nearby places, update
  detail screens (neither app has one yet), weather.
- Check on a device (TestFlight): the DEMO event with a festival vs a single-room layout, and a section turned off in
  the console reaching the phone after the `content_updated` push.

## Onboarding follow-ups (built Nov 21, 2026: open sign-ups, demo events, free limit, home page)
- **Testimonials / "used at" logos** on the home page once real events have run with Attnd (with the organizers' OK).
- **"Happening near you" without Google**: today the browser asks Google's Geolocation API for an approximate position
  from the visitor's IP (no prompt, `home.js`). Once prospects span several regions, consider an IP database on our own
  server instead (e.g. MaxMind GeoLite2, free with an account and attribution; refreshed weekly), so nothing goes to
  Google and it works without the browser key.
- API-025 in the apps: show the server's `event_full_free` message on a full free event (API_CHANGES.md → API-025).
- Checkout: "Remove the limit" emails the admins and an admin marks the event paid; a real checkout (Stripe) later.
- Showcase events on production: set `SHOWCASE_EVENTS=1` on attnd-web when the owner wants the gallery there, and
  `manage.py load_showcase_events --images` once for AI covers (OpenAI, a few cents each).
- More real screenshots on the home page (the planner, the Live Activity, Studio) from the next store screenshot run.

## Parked / skipped (owner, Nov 21–22, 2026)

- **Spotify artist links: skipped.** Spotify's Web API needs the developer account's owner to have Premium; API-030
  links Apple Music only. Revisit if that changes (the bundle field would be additive: `spotify_url` on performers).
- **ShazamKit "What's this song?"**: parked idea (identify the song playing at a stage and link the performer); the
  Media ID has ShazamKit deliberately off until then.
- **Android work paused** (batch 7, owner): when it resumes — foldable screenshots for Play (unfolded captures in the
  phone set), minSdk 35 → 36 (approved), and the parity port (My schedule, the iOS assistant architecture, API-021
  / 023 / 024 / 027 / 030 in the app).

