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
- Production cleanup (SETUP step 7): list users and demo events, delete with a go-ahead.
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
- Home-screen widgets ("Happening now / Next in my plan"): Android Glance widget and iOS WidgetKit widget
  (the `AttndWidgets` extension already exists).
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
Built (web + server, Nov 16, 2026: API-017; spec `docs/specs/tickets_spec.md`) but **parked**: `TICKETS_ENABLED` is
off, so the `tickets` app is in nothing (not installed, no URLs, links, tasks or AI drafts; the bundle's ticket keys are
null). Organizers who want to sell tickets use Eventbrite for now. When it comes back (`TICKETS_ENABLED=1`):
- **Owner setup** (SETUP step 16): Stripe + Connect in the LLC's name, test keys/webhooks on dev, Wallet certificates
  and Google Wallet key. Then re-record the Stripe fixtures (`tickets/fixtures/stripe/`) from real test-mode payloads.
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
