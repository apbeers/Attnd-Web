# Attnd Tickets — plan (draft, Nov 15, 2026; not built)

Goal: Eventbrite-style ticketing inside Attnd, so an organizer runs the whole event here (site, app, plan, tickets, door).

## Business rules (owner's decisions)
- Price ladder: **$299** = bring an existing event in (import + manage + app). **$499** = everything: Studio (AI site,
  flyer, hosting) **and ticketing**. No Attnd cut of ticket sales. Attnd covers Stripe's fees on the organizer's own
  $299/$499 payment.
- **Ticket buyers pay the processing fee** (passed on per order), so a big event never costs Attnd money. Later option
  (not now): Attnd absorbs fees and takes a percentage of sales, so buyers see one plain price.
- Fee per order = gross-up of Stripe's rate so the organizer receives the full ticket price:
  total = (subtotal + fixed) / (1 − rate), fee = total − subtotal, rounded up to the cent; rate/fixed in settings
  (US cards 2.9% + 30¢; Stripe Checkout may add Link/wallet variations — use the standard rate). Free orders: no fee.
  Collected as the destination charge's `application_fee_amount`, which pays the Stripe fee billed to the platform.
- **All-in price up front**: the FTC rule on live-event tickets (in force since May 2025) requires the total including
  mandatory fees to be shown first, so every price display (web list, Studio sites, apps' "Get tickets") shows the
  all-in price per ticket, with the fee itemised at checkout. Per-ticket all-in = gross-up of that ticket alone;
  the order total is recomputed for the cart (never more than the sum shown).
- Refunds (owner agreed): ticket price refunded; the processing fee refunded only when the event is
  cancelled (Stripe keeps its fee, Attnd absorbs it then).
- **Stripe for all money**; our code never handles card data or holds funds. Stripe Checkout (hosted) wherever
  possible; Stripe Connect onboarding (hosted) for organizer payouts.
- **Payouts held until after the event** (released N days after the event ends).
- Buyers: **no account required**; email + verification code is enough; an Attnd account is optional.
- No reserved seating. No Stripe Terminal / tap to pay for now.
- **Buying happens only on the website**, on Stripe's hosted Checkout page (whatever it offers: cards, Apple Pay,
  Google Pay, Link). We integrate nothing payment-related ourselves and the apps sell nothing: no Apple Pay merchant
  ID, no Google Pay profile, no payment code or SDK in the apps. The apps show a "Get tickets" button that opens the
  event's ticket page in the browser, and show tickets already bought ("My tickets").
- **Offline door scanning is critical.**
- AI drafts everything ticket-related; changes go through the red/green review + history; money fields need a human.

## Stripe design (code can be written now, keys later)
- Connect: **Express** connected accounts (hosted onboarding + hosted Express dashboard) with **destination charges**
  `on_behalf_of` the organizer and **manual payouts** controlled by the platform, released after the event
  (scheduled task). Confirm Stripe's maximum hold period for US Express accounts before launch.
  - Charge = tickets + buyer's processing fee; `application_fee_amount` = that fee, which covers the Stripe fee billed
    to the platform (destination charges), so the organizer receives the full ticket price.
- Checkout Session per order (website only), `payment_intent_data.transfer_data.destination`,
  metadata = order id. Webhooks (`checkout.session.completed`, `payment_intent.succeeded/payment_failed`,
  `charge.refunded`, `charge.dispute.*`, `account.updated`, `payout.*`) → idempotent handlers (event id stored).
- Settings: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PUBLISHABLE_KEY` (web), test mode on dev. Feature
  off when unset (everything still testable with Stripe calls mocked).

## Data model (web)
TicketType (event, name, description, price, currency, quantity, sales start/end, min/max per order, hidden,
unlock code, sort, kind paid/free/donation, add-on flag, day/slot), Capacity (shared caps), PromoCode, Order
(email, name, status pending/held/paid/refunded/cancelled/expired, totals, stripe ids, expires_at for the 10-minute
hold), OrderItem, Ticket (order, type, holder name/email, code id, status valid/refunded/void/transferred,
checked_in_at/by/device, scans), Buyer (email, verified, optional user), CheckIn (ticket, device, at, gate, offline
sync id), Payout (event, amount, status, release_at), WalletPass (ticket, platform, serial, auth token, updated).
Inventory: `SELECT … FOR UPDATE` on TicketType/Capacity when creating a held order; holds expire by scheduled task.

## Buyer flows
- Web (attnd.app event page, Studio `<attnd-tickets>`, embeddable widget): ticket list → quantities → hold (10 min
  countdown) → buyer details/questions → Stripe Checkout → confirmation page + email (tickets, Wallet buttons).
- Apps: "Get tickets" opens the event's ticket page in the browser (no buying in the app). "My tickets" lists
  tickets bought with an email the person verifies in the app (code by email; optional account), with the QR and
  Add to Apple Wallet / Google Wallet.
- Free/RSVP tickets skip Stripe.
- Self-service: resend tickets, transfer to another email (re-issues the code), refund requests per the event's policy.

## Tickets, Wallet, scanning
- QR payload: compact signed token (Ed25519, event key pair; payload = ticket id, event id, type, version) — verifiable
  offline. Transfers/refunds bump the version and land in a revocation list.
- Apple Wallet: Pass Type ID + certificate (owner task); `.pkpass` signed server-side (`cryptography`, PKCS#7);
  pass web service (register/update/log endpoints) + APNs pass updates on time/venue changes; relevant location.
- Google Wallet: Event Ticket class per event + objects per ticket, "Save to Google Wallet" signed JWT link; issuer
  account approval (owner task, can take days); updates via the API.
- Door mode in the apps (new team role **door staff**, invited by email, no console access): download the event's
  public key + ticket list + revocations for offline use; scan (existing camera/ML Kit code on Android, VisionKit on
  iOS) → green / red / "already checked in 7:42 PM at Gate B"; manual search by name/email/order; re-entry rules per
  type; per-gate counts; check-ins queue offline and sync (duplicates resolved by earliest time, flagged).

## Organizer tools (console, AI-enabled)
- Stripe onboarding status/link, ticket types + promo codes + capacity + questions + refund policy, sales dashboard,
  orders/attendees (search, CSV), refunds (full/partial, mass refund on cancellation), comps, emails/pushes to ticket
  holders, check-in report, payouts (held/released).
- AI: drafts ticket types, tiers, descriptions, FAQ, refund policy, emails; proposes changes as a ChangeSet with
  red/green diff and history/rollback (EventRevision); money fields (prices, quantities, refunds) always require a human
  approval; never alters sold tickets.

## Policies & stores
- Privacy policy + Terms of Service + Terms of Sale (buyers) + Organizer Agreement; App Store privacy labels and Play
  data safety (name, email, purchase history, linked to identity for buyers); in-app account deletion (Apple requires
  it when accounts can be created); data export/deletion requests.
- App Review: the apps only link out to buy tickets for real-world events (allowed: goods and services used outside
  the app, not in-app purchase rules); nothing is sold in the apps.
- Sign in with Apple required on iOS only if other third-party logins are offered (email code + passkeys don't trigger it).

## Also in scope (agreed)
- Transfers and resending tickets by the buyer; refund requests following the event's refund policy; mass refunds when
  an event is cancelled; door staff role without console access; Wallet passes that update themselves; full price
  shown up front (no added fees).
- Owner sign-ups (no payment ones): Apple Wallet Pass Type ID + certificate (passes, not Apple Pay) and a Google Wallet
  issuer account (approval takes days). Stripe account + Connect when ready.

## Testing (agreed, part of the build)
- **Dev = Stripe test mode end to end.** dev.attnd.app and the dev app builds (TestFlight Dev, Play internal) use
  Stripe test keys (`sk_test_…`, a test webhook endpoint for dev), a test Connect platform, and Stripe test cards
  (success, decline, 3-D Secure, dispute). Production keys never on dev; production never accepts test-mode webhooks.
- **Walkthrough kit on dev** (staff-only page + management command `load_ticket_demo`): a DEMO ticketed event with
  every ticket kind (paid, free/RSVP, donation, add-on, hidden + unlock code, promo code, sold-out + waitlist), a test
  organizer with a completed Stripe test Connect account, a door-staff account, and buyer test emails, so every role
  can be walked through: organizer sets up tickets with AI → buyer buys on the site (test card) → email + Wallet
  passes → "My tickets" in the dev app → door staff scans (also in airplane mode) → refund / transfer / cancel the
  event → payout released after the event (a "fast-forward to after the event" button on dev only).
- Emails on dev go only to `EMAIL_ALLOWED_RECIPIENTS`; Wallet passes on dev use the same Pass Type ID with a "DEV"
  label and a separate Google Wallet class prefix.
- **Automated tests across everything**:
  - Unit (web, iOS, Android): fee gross-up and all-in prices, inventory holds and expiry, promo/unlock rules, refund
    rules, QR token signing/verification and revocation, offline check-in merge, Wallet pass contents and signing.
  - Server integration (Django tests, Stripe mocked at the HTTP layer, recorded fixtures of real test-mode payloads):
    checkout → webhook → tickets issued → refund/dispute/cancel → payouts; webhook idempotency and replay; oversell
    under concurrent holds (parallel transactions on Postgres).
  - Live integration (like today's `live_tests/` and the apps' DEMO suites, CI against dev): real Stripe test mode —
    create a hold, complete a Checkout Session with a test card via Stripe's test helpers, receive the webhook, fetch
    tickets through the API, verify the QR offline, check in, refund. Runs on every develop deploy; prod runs only
    read-only checks.
  - App suites: the iOS and Android integration tests gain "My tickets" (email code via a dev test inbox endpoint),
    offline scan verification against the dev DEMO event, and Wallet pass download.
  - Browser end-to-end (Playwright in CI against dev): organizer creates tickets → buyer checkout with a Stripe test
    card → confirmation → console sees the order.
  - Cross-repo: the DEMO ticket facts live in one place (`load_ticket_demo` + a test of it, like `tests_demo_facts.py`);
    web, iOS and Android suites assert the same facts, and the tracker entry lists them.
- **Broader goal** (beyond tickets): every user-facing flow gets an end-to-end test that runs on each push to develop
  (join, bundle, push, planner/assistant, Studio publish, forms, diagnostics, sign-in with passkeys via a software
  authenticator), so the three repos are always checked together.

## Phases
1. Core: Stripe Connect onboarding, ticket types (paid/free), holds, web checkout, emails + QR, Wallet passes, door
   scanning offline + door staff role, orders/refunds/dashboard, AI drafting with review, payouts released after
   the event.
2. Apps: Get tickets link, My tickets, transfers, promo/unlock codes,
   waitlist, add-ons, buyer questions, `<attnd-tickets>` + embed widget.
3. Later: timed entry, rotating QR, resale, affiliate links, tap to pay.
