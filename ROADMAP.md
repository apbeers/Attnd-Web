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
