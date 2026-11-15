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
- Production config: `GOOGLE_MAPS_BROWSER_KEY`, `IOS_APP_STORE_URL`; `APNS_USE_SANDBOX` off;
  `APPLE_ATTEST_ENVIRONMENT=production`.
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
- Revisit the "push straight to develop" rule (branch protection, PRs) once there are real users.

## Future

### Android: move FCM to the Firebase Installation ID registration API (would be API-009; API-008 became the CI keys)
firebase-messaging 25.1.0 (June 2026) deprecated `FirebaseMessaging.getToken()` / `onNewToken` in favour of
`register()` / `onRegistered`, which hands out a Firebase Installation ID instead of an FCM token. The app
currently suppresses the deprecation (`PushNotifications.kt`, `AttndMessagingService.kt`). Moving needs a tracker
entry: what `push_token` carries, how `events/push.py` sends to it (FCM HTTP v1 target), and a transition where
the server accepts both. Do it before the old API is removed.

