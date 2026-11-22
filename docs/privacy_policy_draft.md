# Privacy policy draft (batch 7, Nov 22, 2026) — NOT published

Status: draft for the owner. The live page (`/privacy/`, `home/templates/home/privacy.html`, effective Nov 21, 2026)
is unchanged. The ready page is `home/templates/home/privacy_draft.html` (same text as below); to publish it, change
`home.views.privacy` to render `home/privacy_draft.html`, set the effective date, and delete the old template
(`home/tests_privacy_draft.py` then needs its "not published" check removed).

Written against what the code does today (checked in the repos on Nov 22, 2026). Not legal advice; a lawyer should
read it before the LLC launch (docs/LLC_MIGRATION.md).

## Owner decisions before publishing

1. **Ask Attnd conversations** are kept until the account is deleted (no purge today). Keep that, or add a retention
   (e.g. 12 months)? The draft says "until you delete your account".
2. **Studio website form entries** (what visitors type into an organizer's site form) are kept until the organizer
   deletes them. The draft makes the organizer responsible for them and says we process them for the organizer.
3. **Contact address**: `CONTACT_EMAIL` isn't set on production, so the page falls back to "contact us through
   attnd.app". Set it (e.g. privacy@attnd.app) before publishing.
4. **Payments**: Stripe isn't live. The draft describes it as "when you pay"; drop the paragraph if checkout isn't on
   when this goes live.
5. **Prospects** (staff sales research on publicly listed organizer contacts): the draft discloses it, with an opt-out.
   Confirm you want it in the public policy (US-only today; GDPR/UK would need a legitimate-interest assessment).

## What changed against the live policy

- AI providers: Anthropic (Claude) does every text AI call (imports, website checks, Ask Attnd, Studio), OpenAI only
  Studio's images. The live page still names only OpenAI.
- On-device AI in the apps (Apple Intelligence / Gemini Nano): questions never leave the phone.
- Google: Places / Geocoding (place names and addresses, server-side), live Google photos (API-031: loaded by the
  phone or browser from Google), Maps JS and Geolocation in the browser, Routes on Android, Play Integrity, FCM.
- Apple Music: performer names looked up server-side (no attendee data).
- App Clip, Live Activities (their push token), reminders and Lock Screen content (on the device).
- Preview phones (an organizer's own phone linked to their account for 7 days).
- Organizer security data: passkeys, authenticator, sign-in sessions (browser + a device cookie hash), new-sign-in
  emails; IP addresses for rate limits.
- Studio sites (hosted pages, their visitors, forms, abuse reports), shared plan links, home page composer uploads.
- Emails we send, Stripe (when on), Prospects and event claims, and a fuller retention table.

## The text

### Privacy Policy

Attnd helps people follow live events (maps, schedules, updates and a personal plan) and helps organizers run them.
We collect as little as we can. Attendees never create an account, and we don't use advertising, tracking or
third-party analytics. We don't sell personal information or share it for advertising.

### If you use the Attnd app or App Clip (attendees)

The iPhone app and its App Clip (the instant version that opens from a QR code) work the same way.

- **A random app ID.** On first launch the app creates a random identifier that isn't linked to your name, email,
  phone number or advertising ID. We use it to remember which event you joined, your notification setting, your
  device type and app version, and which version of the event's content the app last downloaded.
- **Push tokens**, only to reach you. Apple or Google gives the app a token; we use it only for the event you joined:
  organizer updates you opted into, urgent announcements (sent to everyone in the event, because they can be about
  safety), and silent "the schedule changed" signals that make the app refresh. On iPhone, if you follow the event on
  your Lock Screen, the Live Activity has its own token, used for urgent announcements and deleted when the Live
  Activity ends or you leave.
- **Your location stays on your phone.** The app uses it to show your position, distances, walking times and "leave
  now" hints, all computed on your phone. Only when you look for nearby events does it send your approximate position
  to our server, for that search; we don't store it. Walking routes on Android are asked from Google with the start
  and end points; on iPhone Apple Maps does it.
- **Your plan, likes and assistant stay on your phone.** What you add to My schedule, your likes, your plan and your
  questions to the in-app assistant are kept on your device. The assistant uses the AI built into your phone (Apple
  Intelligence or Gemini Nano); what you ask is never sent to us or to any AI company. If you share your plan, the link
  contains only the event code and the ids of the items you chose.
- **App and device verification.** The app uses Apple App Attest or Google Play Integrity so our server knows requests
  come from the genuine app. Apple or Google tell us whether the checks pass; we keep the result and a key identifier,
  not personal information.
- **Anonymous usage counts.** The app tells us how often features are used ("the Map was opened", "a place was liked")
  so organizers can see how their app was used. These counts carry no identifier and are stored as 5-minute totals,
  so they can't be linked to you.
- **Crash and performance reports**, without any identifier: the app and system version, the phone model and where in
  our code a problem happened, plus anonymous timings. Used only to fix problems; deleted after 30 days.
- **Debug logging, only if you turn it on** (About & support): for 10 minutes the app sends a technical log labelled
  with a random debug code it shows you, never your app ID, and never what you typed, your exact location or tokens.
  Deleted after 14 days. Our own test builds send these logs all the time and keep them 7 days.
- **Camera**, only while you scan a QR code; scanning happens on the phone.
- **Images.** Place photos come from our servers, or, when no other photo exists, straight from Google Maps (your phone
  loads them from Google, which sees your IP address like any website you visit).

If you are an organizer previewing your own event on your phone, that phone is linked to your account for up to
7 days so it shows your unpublished changes; it isn't counted as an attendee.

### If you visit our websites

- **attnd.app** (the home page, join and shared-plan pages): no tracking cookies. To show "Happening near you", your
  browser asks Google's Geolocation service for an approximate position based on your IP address, at most once a day,
  without asking for your precise location, and sends us that position rounded to about 10 km; we don't store or log
  it. We count taps on the home page per day, without any identifier.
- **If you start an event from the home page** (the composer), the text and file you give us wait on our server for up
  to a day until you sign up, then go to our AI import.
- **Event websites built with Attnd Studio** (on attnd.site): no cookies and no analytics from us. If a site has a
  form (for example a vendor sign-up), what you submit goes to the event's organizer, who decides what to do with it;
  we store it for them until they delete it. "Report this site" sends us your report and the page address.

### If you organize an event (the console)

- **Account:** your email address and a secure hash of your password (never the password). If you add a passkey or
  an authenticator app we keep its public key or secret; recovery codes only as hashes.
- **Security:** each sign-in records the browser and a random cookie that recognises it (so "Signed in on other
  devices" works and we can email you about a sign-in from a new browser). Your IP address is used briefly to limit
  repeated attempts. Session, security (CSRF) and time-zone cookies only; no tracking cookies.
- **Event content:** everything you add: details, places, schedules, images, flyers, notifications, colours, layouts,
  your team and invites, and the websites, files and text you give our AI tools. Changes are kept in the event's
  history so you can undo them.
- **Ask Attnd** (the console's assistant): your requests and its answers, files you attach and short notes you ask it
  to remember, so the conversation continues across pages. They are sent to Anthropic to produce the answers.
- **AI credit:** a record of each AI call's cost, so we can show your monthly credit.
- **Attendee statistics:** you see counts only (joins, busy hours, popular places), never individual attendees.
- **Payments** (when you upgrade an event): Stripe processes the card; we receive the plan, amount, date and invoice,
  never your full card number.
- **Emails we send you:** account and security emails (always), and event emails such as "your import is ready" or
  "changes are waiting for review" (each can be turned off in Account).

### Service providers

Each gets only what its job needs:

- **Heroku (Salesforce):** hosting and the database. **Cloudinary:** stores and resizes uploaded images and files.
- **Anthropic (Claude):** reads the event materials organizers submit and organizers' Ask Attnd requests, to import
  events, check websites, write suggestions and design Studio websites. **OpenAI:** creates images for Studio when an
  organizer asks. Attendee data is never sent to either.
- **Apple:** push notifications, Live Activities, App Attest, Apple Music (we look up performers' names to link their
  artist pages; no attendee data).
- **Google:** Firebase Cloud Messaging and Play Integrity (Android), Maps, Places and Geocoding (place names and
  addresses organizers enter), place photos, Geolocation on the home page, walking routes on Android.
- **Wikimedia Commons:** freely licensed place photos (we download them; nothing about you is sent).
- **Mailgun:** delivers our emails. **Stripe:** payments, when used. **GitHub:** stores our code and crash symbol files
  (no personal data).

### Organizer contacts we research (Prospects)

To offer Attnd to events that could use it, our staff keep a list of upcoming public events and the organizer contact
details those events publish on their own websites (with the page each came from). We never guess addresses, never
email anyone automatically, and anyone can ask to be removed; we then mark the contact "do not contact" and keep only
that. If you ask to claim an event listed on our site, we keep your request and our decision.

### How long we keep data

| Data | Kept |
|---|---|
| App ID record (and its push tokens) | deleted after 12 months without use; push tokens removed as soon as Apple or Google reports them invalid; a Live Activity token when it ends |
| Anonymous usage counts | as long as the event's statistics are shown |
| Crash and performance reports | 30 days |
| Debug logs | 14 days (7 days from our test builds) |
| Home page composer drafts | 1 day |
| Undo history | 24 hours (the event's change history stays with the event) |
| Sign-in sessions | until they end (at most 14 days; staff 1 day) |
| Studio form entries | until the organizer deletes them |
| Ask Attnd conversations, AI cost records | until the account is deleted |
| Organizer accounts and events | until you delete the event or ask us to delete your account |
| Prospects contacts | until removed; "do not contact" kept so we honour it |

Deleting an organizer account doesn't delete its events if other team members use them; we can delete them on request.

### Your choices and rights

Attendees can turn off notifications, leave an event, stop following on the Lock Screen or delete the app at any time;
deleting it removes the app ID from the phone and our record expires as above. Organizers can export and delete their
events and ask us for a copy of, or to delete, their account data. Depending on where you live you may have further
rights (access, correction, deletion, objection); contact us and we'll help.

### Children

Attnd is not directed at children under 13, and we don't knowingly collect personal information from them.

### Changes

If we change this policy, we'll update it here and change the effective date.

### Contact

Questions or requests about your data: CONTACT_EMAIL (owner decision 3).
