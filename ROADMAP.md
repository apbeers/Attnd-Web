# Roadmap

Work agreed but not built yet. Move an item into `API_CHANGES.md` (for API changes) or just build it when
it's picked up, then delete it from here.

## Needed before release

### File storage for uploads (images, PDFs)
Organizers upload flyers (image/PDF), location photos and logos in the console. Without permanent storage
they land on Heroku's temporary disk and are **lost on every restart or deploy**.
- Today: `django-cloudinary-storage` is wired in `attnd/settings.py`: it switches on when `CLOUDINARY_URL`
  is set, and neither Heroku app has it yet (see SETUP.md step 2/5).
- To decide: stay with Cloudinary (already integrated; free plan; image transforms) or move to S3-compatible
  storage (e.g. Cloudflare R2 / AWS S3; needs a storage backend). Check PDF handling, file size limits, cost
  at scale and delivery URLs/caching for the apps.
- Also: upload limits and validation (type, size), cleanup of replaced/deleted files, and separate
  dev vs prod buckets/accounts.

### Sending email
Needed for team invites (`EventInvite`), password reset, sign-up confirmation and task/ingest notices.
- Today: SMTP settings point at Mailgun (`MAILGUN_SMTP_LOGIN` / `MAILGUN_SMTP_PASSWORD`, `attnd/settings.py`),
  but no credentials are configured, so nothing is actually sent.
- To decide: provider (Mailgun, Postmark, Amazon SES, Resend, …), sending domain on attnd.app with
  SPF/DKIM/DMARC records, a from address, plain-text + HTML templates in the house style, and a dev setup
  that never emails real people (console backend or a sandbox domain).

### Go-live checklist (everything else flagged so far)
**Release + production config** (production changes need the user's go-ahead)
- First web release: merge `develop` → `main`. Production still runs old code: no new join page / AASA
  (App Clip experience and Universal Links depend on it), and main's old packages carry ~200 Dependabot alerts.
- Production config: `GOOGLE_MAPS_BROWSER_KEY`, `IOS_APP_STORE_URL`; `APNS_USE_SANDBOX` off;
  `APPLE_ATTEST_ENVIRONMENT=production`.
- Production DEMO event (`load_demo_event`) and the App Review account (`create_review_account`): both
  stores' review notes tell reviewers to use code DEMO on attnd.app.
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
- Database backups/rollback: check what the Heroku Postgres plan includes; upgrade the plan or schedule
  `pg:backups` before real data exists.
- Error alerting: today errors only reach Heroku logs. At minimum, email admins on server errors
  (Django `ADMINS` + email, once email works) and a log drain or Heroku alerting.
- Load: the 5 s poll is ~200 req/s per 1,000 open apps on one Basic dyno (gthread, 4 threads). Either build
  the silent-push live updates below first, or size dynos for the first big event.
- Revisit the "push straight to develop" rule (branch protection, PRs) once there are real users.

## Future

### Live updates via silent push instead of 5 s polling (would be API-007)
Open apps poll `bundle/` every 5 s: ~200 req/s per 1,000 attendees with the app open, too much for one
Basic dyno (gthread, 4 threads). Plan, explored Nov 14, 2026:
- **Push is the primary signal.** Every device registers for push at launch: iOS silent pushes need no
  user permission; Android FCM data messages never do. The notification opt-in only controls visible
  alerts. The server sends `content_updated` (with `content_version`) to *all* devices in the event, not
  only `subscribed` ones (`events/push.py` `deliver`/`_recipients`).
- **Faster, collapsible pushes.** Debounce ~2–3 s instead of `CONTENT_PUSH_MIN_INTERVAL` 60 s, with
  `apns-collapse-id` / FCM `collapse_key` so only the latest is delivered. Background delivery stays
  best-effort (iOS throttles it); foreground delivery is immediate.
- **Apps.** On `content_updated`: fetch only if the pushed `content_version` is newer, after a random
  0–3 s delay so 1,000 phones don't refresh at once.
- **Safety net.** Refresh on resume, plus a 60 s conditional poll while open (catches lost pushes, Doze,
  Low Power Mode, force-quit). App Clip (no push): poll every 15–30 s.
- **Not now:** SSE/WebSockets or long-polling (each open app would hold a connection; needs an ASGI
  server and dependencies).
- Additive change: old app builds keep polling. Track as API-007 in `API_CHANGES.md` when started.
