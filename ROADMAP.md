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
- [ ] **F. Django built-ins**: database cache ✅, LoginRequiredMiddleware ✅, Postgres search ✅, humanize + db_default ✅
  (no GeneratedField: the ends we query need interval maths, which Postgres won't store), django.tasks with our own
  DB-backed worker (last).
- [x] **G. Security** ✅: ALLOWED_HOSTS, Scrypt, Permissions-Policy, SECRET_KEY_FALLBACKS, Ask Attnd rate limit + magic bytes.
- [x] **H. Performance** ✅ (agent.js split into modules still open, see A): no head scripts to defer (all at the end of body), lazy Ask Attnd, cached root files, sized images, polling back-off.
- [ ] **I. Chat-first additions** (Tiers 1–3: preview-before-apply, briefing / readiness, paste anything, server-side
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
already exists and staff switch it by hand; checkout would set it (and `paid`) from a webhook. Prices
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

