# Attnd platform help (for Ask Attnd)

How-to answers about the console. When you answer one of these, always include the link to the exact page
where it is done, written as a Markdown link: [Account](/console/account/). Replace {event_id} with the id of
the event the person means (the CONTEXT block has the current event). Links to account pages are fine to give;
you can't open or change those pages yourself.

## Account and sign-in
- Change your email, password, passkeys, authenticator app, emailed codes or recovery codes: [Account](/console/account/).
- Forgot your password (signed out): [Reset your password](/accounts/password_reset/).
- Passkeys are the fastest way to sign in (Face ID, Touch ID, a security key). Add one on [Account](/console/account/).
- Sessions last until midnight in your own time zone: people sign in once a day.
- Sign-ups are invite-only: a teammate invites you from an event's Team page, then you sign up with that email.

## Your events
- Every event you own or help run: [Your events](/console/).
- Create an event (the AI reads a website, PDF, flyer image or pasted text, or start empty): [New event](/console/new/).
- An event's overview (status, numbers, join QR code, what needs review): [Overview](/console/events/{event_id}/).
- Edit the name, dates, time zone, address, flyer and logo: [Details](/console/events/{event_id}/edit/).
- Locations (stages, booths, food, anywhere attendees go): [Locations](/console/events/{event_id}/locations/).
  Add one: [New location](/console/events/{event_id}/locations/new/).
- The schedule: [Schedule](/console/events/{event_id}/schedule/). Add an item: [New schedule item](/console/events/{event_id}/schedule/new/).

## Publishing
- A draft is invisible to the apps. Publish, unpublish or archive from the [Overview](/console/events/{event_id}/)
  (publishing always asks you to confirm). Archived events stay viewable and the apps say "Event ended".
- "List in nearby events" makes the event appear in the apps' nearby search; unlisted events are reachable by
  code, QR code or link only.

## Notifications (push)
- Send now or schedule for later: [Notifications](/console/events/{event_id}/notifications/). Sending always asks first.
- Urgent notifications reach every device in the event, even people who turned notifications off, as a time-sensitive
  alert and on Lock Screen Live Activities. Use them only for cancellations, safety and big changes (at most 3
  an hour per event; more go out as normal notifications).
- Scheduled notifications go out on 10-minute marks.

## QR code and join links
- The join QR code, the code and the link are on the [Overview](/console/events/{event_id}/) ("Live on your
  phone"). A printable poster is there too. On a phone, "Open in Attnd" opens the event in the app.
- Join links look like https://attnd.app/join/CODE/: they open the app, or the store when it isn't installed.

## Team
- Invite people by email, change roles, remove people or transfer ownership: [Team](/console/events/{event_id}/team/).
  Admins manage the team and settings, contributors edit content. Invites work before the person has an account.

## Imports and website watching
- Point Attnd at the event's website and it keeps checking it: [Sources](/console/events/{event_id}/sources/).
  Only pages that changed are read again; "Check for updates now" re-reads everything.
- What the AI found waits for review unless you chose otherwise: [Suggested changes](/console/events/{event_id}/changes/).
  Fields you edited yourself are locked: the AI only suggests changes to them.

## Layout
- Which tabs the apps show, the default tab, styles, accent colour and logo: [App layout](/console/events/{event_id}/display/).

## History and undo
- Every change can be undone for 24 hours with the Undo button next to it (or say "undo that").
- Who changed what, field by field, and earlier versions to restore: [History](/console/events/{event_id}/history/).

## Usage and likes
- Anonymous counts: opens, views, searches, likes and the most liked places and items:
  [Usage](/console/events/{event_id}/usage/). Attnd never collects who attendees are.

## Attnd Studio (websites)
- Studio designs and hosts an event website with AI. It is in a private beta for Attnd's own team: ask for changes
  right here in the chat (design_website), and see the preview, versions and form entries on the
  [Website](/console/events/{event_id}/studio/) page or the full-screen chat's Website tab (only shown to people who have it).

## Billing
- Events without a paid plan show a "Demo event" badge in the apps. There is no checkout in the console yet:
  to upgrade an event, contact Attnd. Studio is a separate plan, also arranged with Attnd.
