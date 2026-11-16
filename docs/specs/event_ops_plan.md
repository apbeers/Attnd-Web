# Event ops plan: Work mode, roles, accounts, staff tools, modular features (planned, not built)

Agreed with the owner Nov 16, 2026. **iOS first** (Android later), one app, one download with every feature. Pricing
is out of scope (decided later). Builds on tickets (`tickets_spec.md`: door staff, offline scanning) and the
invite-only organizer site.

## 1. One app, two modes
- **Attendee mode** (today's app) and **Work mode** for anyone holding a role in an event. A person with several roles
  (in one or several events) sees them all in a role switcher ("Lost 'N Sound · Door staff, Gate B",
  "ArtsGoggle · Volunteer") and picks one; the Work tab/screen changes with it. Attendee mode is always one tap away.
- Every role works **offline first**: what the role needs is downloaded ahead, actions queue and sync with defined
  conflict rules, the UI shows "Saved on this phone · will sync".
- The web console keeps the organizer's full tools; the app versions are built for working on your feet.

## 2. Accounts and onboarding
**Attnd account** (one per person, across events and roles; attendees still need none to use the app):
- Sign in / create account with **Sign in with Apple**, **Google**, an **email code**, or a **passkey** (no passwords in
  the app). Same verified email → same account; Apple's private relay emails link through the invite code instead.
  The web console gets Apple/Google sign-in too, so organizers and staff have one account everywhere.
- Offering Google sign-in on iOS requires an equivalent privacy-preserving option (App Review guideline 4.8):
  Sign in with Apple covers it. Accounts created in the app need in-app account deletion (Apple requires it).
- Sessions are per device, revocable by the person and the organizer; role access expires after the event
  (+ a grace period for reports).

**Inviting people** (organizer or a coordinator, from the console or the app):
- **Email invite**: link + 6-character code, the role(s) and scope(s) in the email.
- **Phone invite**: we don't run SMS (no provider; cost and compliance). The app opens the organizer's own Messages
  with the invite link + code prefilled (share sheet); the web console shows a copyable text. A paid SMS provider is a
  later option.
- **Personal QR code** (single use, tied to one invite) to print or show on screen.
- **Group QR code** for a role ("Volunteers: scan to join"), multi-use, with organizer approval or auto-approve up to a
  cap, expiry, and revoke; good for posters and briefings.
- **Bulk invites**: CSV paste/upload (name, email or phone, role, scope).

**Accepting** (universal link `/i/<code>` or scanning the QR; App Clip for people without the app could show the
role and send them to the App Store, since roles need the full app):
1. Sign in or create the account (Apple / Google / email code / passkey).
2. See the event, role(s), scope, dates and what the role can see/do; accept.
3. Role-specific permissions, each explained: **notifications** (always, for staff announcements); **camera** (door
   staff); **location "Always"** only for roles with staff location sharing (see §4); calendar for shifts (optional).
4. Short role briefing (organizer-written + AI draft), then Work mode opens.

## 3. Roles (scopes: gate, stage, zone, vendor row, department)
| Role | Work mode |
|---|---|
| Organizer / event admin | Live ops dashboard (check-ins vs capacity per gate, sales, incidents, staff on shift, map with staff), push to attendees or any role/scope, change set times on the fly (app + site + pushes to people who liked it), approvals, incident triage, schedule/shift edits |
| Coordinator / department lead | The organizer's tools limited to an area ("Volunteers", "Main Stage", "Vendors") |
| Door staff | Offline ticket scanning, lookup, re-entry rules, will-call/comps lookup, gate counts; also scans staff badges at controlled zones |
| Volunteer | Shifts + sign-up/swap, check in/out of a shift (QR or arrival), tasks, briefings, hours, "need relief", perks (meal token QR) |
| Security / safety | Incident reports (category, severity, photo, location pin), lost & found intake, crowd/weather concerns, escalate to medical/organizer |
| First aid / medical | Incidents routed with location, respond/resolve; minimal personal data |
| Stage manager | Stage run of show; **Set started / Set ended** (makes Now playing, schedules, Live Activities real; "running 15 min behind" adjusts the schedule and notifies), call next act, message performers |
| Performer / speaker | Set time, stage, call time, load-in spot, hospitality/green room, guest list requests, changes pushed, "running late" to the stage manager |
| Vendor / exhibitor | Load-in time and map spot, organizer messages, **status** (open / closed / sold out of X / short line) shown to attendees on the map and in search, menu/special updates |
| Parking / shuttle | Lot full/open, shuttle running/delayed + position, shown to attendees immediately |
| Info booth / guest services | Ticket/order lookup for attendees, lost & found search, FAQ via the assistant, report issues |
| Sponsor rep | Activation info and schedule, live anonymous visibility counts |
| Media / press | Credential, press kit, photo areas/times, embargo notes |

**Digital staff badge**: a Wallet pass per role holder (role, scope, photo optional) that door staff can scan at
controlled zones (backstage, VIP), with zone rules per role; offline like tickets.

## 4. Live staff location sharing
- Only for roles the organizer enables it for; the person opts in when accepting (needs location **Always** so it
  works with the phone locked), sees a visible indicator, and can pause (the dashboard shows "paused").
- Runs only during the person's shifts or the event's hours, inside the event area (geofence), and **stops
  automatically after the event**. Battery-aware (significant changes / ~30–60 s while moving, not continuous GPS).
- Visible only to organizers, coordinators of that area and security; positions are live only, kept ≤ 24 h, deleted
  after the event; never shown to attendees. Privacy policy + App Store background-location justification needed.

## 5. Staff notifications
- Target by role, scope, shift ("volunteers on shift now"), zone ("staff near Main Stage") or person.
- **View tracking on every staff notification**: delivered → viewed (opened in the app) per person, shown to the sender.
- **Optional "must acknowledge"** per message ("Got it"): the sender sees who hasn't; optional re-notify after N minutes.
- A separate staff notification channel; time-sensitive for urgent ones.
- **Structured requests** from staff → tickets on the ops dashboard (incident, need relief, supply low, gate
  overwhelmed, medical). **No open group chat for now.**
- Later idea: push-to-talk for staff (Apple's PushToTalk framework) as a radio replacement (needs an audio relay).

## 6. Modular by design (simple first)
The platform must never force every feature on anyone:
- **Feature modules per event**, all off except the basics: Basics (schedule, map, updates) → optional Planner &
  assistant, Website (Studio), Tickets, Campaigns, Applications, Volunteers & shifts, Work mode / ops, Sponsors,
  Surveys, Translations… Turned on from an "Add features" catalog (with what each does and AI suggestions:
  "Festivals like yours usually turn on Volunteers and Tickets").
- **Templates** by event type ("Simple meetup: schedule + map", "Street festival", "Conference", "Concert") pick the
  starting modules.
- The **console** shows only enabled modules (sidenav, overview cards); the **apps** show only what's enabled (an
  additive `display.features`/modules list in the bundle; unknown or missing = today's behaviour), so a simple event
  stays a simple schedule + map app.
- Progressive disclosure inside modules too: simple defaults, "More options" for the rest.

## 7. Other ideas to keep in mind
- Gate wait times from scan rates ("Gate A ~10 min"); capacity alerts per zone; weather alerts (official forecasts)
  suggesting an urgent announcement; lost & found matching between intake and attendee reports; accessibility requests
  (seating, mobility help) routed to guest services; shift swap marketplace; volunteer hours reports and certificates;
  audit log of every staff action; post-event staff debrief survey.

## 8. Order (proposed, iOS first)
1. Accounts (Apple/Google/email code/passkey, app + console), invites (email, phone share, personal/group QR), role
   switcher, modules framework.
2. Door staff (from tickets) + organizer live dashboard + staff notifications with view tracking/acks.
3. Volunteers & shifts, security/incidents + first aid, staff location sharing.
4. Stage manager (live set times), vendors (status), parking/shuttle.
5. Performers, info booth, sponsors, press, staff badges + zone access.
