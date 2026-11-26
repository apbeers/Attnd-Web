# Moving Attnd to an LLC (plan + running checklist)

**Status:** not started (Nov 15, 2026). Today everything runs on Andrew's personal accounts. The plan is to finish
building first, then form a Texas LLC and move every account, service, domain, app listing and contract to it
**before** Attnd takes outside organizers or any payments (Stripe, tickets). Until then the site stays invite-only
(`SIGNUPS_OPEN` off).

**Keep this file current:** whenever a feature adds an account, key, certificate, service, domain, store listing or
legal text, add it to the inventory (§4) in the same commit. Claude does this as part of the feature work.

Not legal or tax advice: have a Texas CPA/attorney review §1–2 (an hour of their time is worth it, especially for
sales tax on the $299/$499 tiers and ticketing).

## 1. Form the LLC (Texas)
1. Pick the name ("Attnd LLC" or similar) and check it on Texas SOSDirect (name availability) and USPTO (trademark
   search for "Attnd"); optionally file a Texas name reservation.
2. Registered agent with a Texas street address (yourself at a Texas address, or a registered-agent service so your
   home address isn't public).
3. File the **Certificate of Formation – LLC (Form 205)** with the Texas Secretary of State on SOSDirect ($300).
   Member-managed, single member. **Done: accepted by Texas (owner, Nov 23, 2026).** Next: 5, 6, then Stripe.
4. **EIN** from the IRS (free, online, minutes) once the Certificate of Formation is accepted. **Done (owner, Nov 23, 2026)**;
   the number and the CP 575 letter stay with the owner, never in the repo.
5. **Company agreement** (operating agreement, not filed): single-member, keeps the LLC separate from you.
6. **Business bank account** + business card in the LLC's name (EIN + certificate). Never mix personal money in.
7. **Texas Comptroller**: franchise tax account (annual report due May 15; below the no-tax-due revenue threshold you
   file the No Tax Due + Public Information Report). **Sales and use tax permit**: Texas taxes SaaS / data processing
   (80% of the price is taxable): ask the CPA whether the $299/$499 tiers and the ticket processing fee are taxable and
   whether Stripe Tax should collect it.
8. **D-U-N-S number** (free, Dun & Bradstreet; Apple has a lookup/request page): needed for Apple's organization
   account and Google Play's organization verification. Can take ~1–2 weeks.
9. **Insurance**: general liability + tech E&O / cyber (customer data, payments, AI-generated sites).
10. Optional: USPTO trademark application for "Attnd" (in the LLC's name); a business mailing address/virtual mailbox
    for public listings.

## 2. Identity and email first (everything else hangs off these)
1. **Google Workspace** on `attnd.app` (Business Starter is enough), billed to the LLC card. An older Workspace
   already exists on `attnd.app`: admin access recovered Nov 16, 2026 (admin user `andrew@attnd.app`), subscription
   re-activated by the owner Nov 23, 2026 (`andrew@attnd.app` is a Workspace mailbox; DNS: SETUP.md §11b). Move its
   billing to the LLC, then add the shared aliases below. Mailboxes/aliases:
   `admin@`, `support@`, `billing@`, `dev@` (shared aliases rather than personal ones, so services aren't tied to a
   person). DNS at Namecheap: Workspace MX + SPF/DKIM/DMARC; Mailgun keeps `mg.attnd.app` (no conflict).
2. This also creates a **Google Cloud organization** (attnd.app) for §3.
3. A password manager vault owned by the LLC (1Password Business or similar) for every credential in §4, with 2FA
   recovery codes; hardware/passkey 2FA on admin accounts.
4. Every account below is re-pointed to an `@attnd.app` address owned by the LLC (not a personal Gmail/iCloud).

## 3. Move each service (order matters: least risky first, stores last)
| Service | How to move | Notes / breakage to watch |
|---|---|---|
| Namecheap (`attnd.app`) | Change registrant/admin contacts to the LLC (or transfer the domain to an LLC-owned registrar account) | Keep DNS identical; the dev/sites domains too |
| GitHub (`apbeers/Attnd-*`) | Create a GitHub organization owned by the LLC; transfer the three repos | Repo secrets/variables/environments move with the repo, but re-check them; update the Claude GitHub app, `GITHUB_SYMBOLICATE_REPO`, workflow links, CLAUDE.md links |
| Heroku (`attnd-web`, `attnd-dev`) | Create a Heroku Team owned by the LLC, transfer both apps, billing to the LLC card | Add-ons (Postgres, Scheduler, Mailgun, Cloudinary) move with the apps; regenerate the CI `HEROKU_API_KEY` and the Claude environment's Heroku token as an LLC user |
| Google Cloud project `attnd-df28e` (Firebase/FCM, Maps + Routes keys, Play Integrity service account, Google Wallet API + `attnd-wallet` service account) | Move the project into the Workspace organization; new billing account in the LLC's name | Keys and service accounts keep working; update owners/IAM to LLC accounts |
| Google Pay & Wallet Console (issuer) | Update the business profile to the LLC (name, address, EIN/MCC 7922); add LLC users, remove personal | Issuer ID stays |
| Apple Developer Program (team GQQX4L6TE2, individual) | Ask Apple Developer Support to convert the membership from Individual to Organization (needs the LLC, D-U-N-S, a website on attnd.app and an `@attnd.app` email). Conversion keeps the Team ID and apps (confirm with Apple); otherwise App Transfer to a new organization team | The App Store seller name becomes the LLC. If the Team ID changed, re-create: Pass Type ID + certificate, App Group, APNs key, App Attest config, ASC API key, dev certificate, associated-domains (AASA team prefix) |
| App Store Connect (Attnd, id1470627406, App Clip, widgets) | Follows the membership; update agreements/tax/banking to the LLC (EIN, LLC bank) | Paid Apps agreement only if ever needed (tickets are sold on the web) |
| Google Play Console (`com.arcu.attnd`) | Create an organization developer account for the LLC (D-U-N-S verification), then request an app transfer from the personal account (or change the account type if Play offers it) | Upload key/app signing key move with the app; update the Play publishing service account (`GOOGLE_PLAY_SERVICE_ACCOUNT_JSON`) |
| Mailgun (Heroku add-on) | Moves with the Heroku app; sending domain `mg.attnd.app` unchanged | Update the account contact email; the webhooks to `/email/mailgun/` and their signing key (`MAILGUN_WEBHOOK_SIGNING_KEY`) stay, re-set them if the account changes |
| Cloudinary (Heroku add-ons, dev + prod) | Move with the apps; update account owner emails | PDF/ZIP delivery setting stays |
| OpenAI (server key) | Create an LLC organization, new API key, billing on the LLC card; turn off data sharing/training for customer data | Rotate `OPENAI_API_KEY` on both apps |
| Anthropic (`ANTHROPIC_API_KEY`, two keys: attnd-dev and attnd-web, set Nov 21, 2026; Ask Attnd on Claude Haiku 5.5, Studio on Claude Sonnet 5.5) | Same as OpenAI: an LLC organization in the Anthropic Console, new key, billing and a monthly spend limit on the LLC card, no training on customer data | Rotate `ANTHROPIC_API_KEY` on both apps |
| Stripe (tickets, tiers; not set up yet) | **Create it in the LLC's name from day one** (EIN, LLC bank account), Connect platform profile | Never on a personal account |
| Studio sites domain `attnd.site` (bought Nov 21, 2026; DNS at its registrar: `@` → attnd-web's Heroku DNS target, `dev` → attnd-dev's; Heroku domains + ACM on both apps) | Move the domain to the LLC's registrar account; keep the DNS records identical | `SITES_HOST` unchanged |
| Claude Code cloud environment / GitHub app | Re-authorize under the LLC GitHub org; environment secrets re-created | |
| Store listings, support/marketing/privacy URLs, review contact | Point to `@attnd.app` addresses and the LLC | `store/en-US/*` in both app repos |

## 4. Inventory (running list: add to it with every feature)
Accounts and services:
- Google Workspace on `attnd.app` (admin `andrew@attnd.app`, access recovered Nov 16, 2026; no active subscription).
- Namecheap: `attnd.app` (DNS: Heroku `attnd.app`, `dev.attnd.app`, Mailgun `mg.attnd.app`).
- GitHub: `apbeers/Attnd-Web`, `apbeers/Attnd-iOS`, `apbeers/Attnd-Android` (+ environments, secrets, variables, rulesets).
- Heroku: `attnd-web` (prod), `attnd-dev` (dev); Postgres essential-0 ×2, Scheduler ×2, Mailgun ×2, Cloudinary
  starter ×2 (`cloudinary-metric-01769`, `cloudinary-fluffy-65757`); ACM certificates.
- Google Cloud `attnd-df28e`: Firebase (FCM), Maps SDK key (Android, `af7cec96-…`), browser Maps key, Places key,
  Routes API, Geolocation API (browser key, home page "Happening near you", Nov 21, 2026; to enable), ~~Weather API~~ (enabled and then disabled on Nov 19, 2026: not used), Play Integrity service account, Google Wallet API + service account `attnd-wallet`.
- Google Pay & Wallet Console: business profile "Attnd" (MCC 7922), Issuer ID.
- Apple Developer team GQQX4L6TE2 (Individual): App IDs `com.arcu.Attnd`, `.clip`, `.widgets` (Live Activity
  extension); App Group `group.com.arcu.Attnd` (on the app and the App Clip since Nov 21, 2026: the clip → app handover; a new team needs a new group id and both apps updated, or the handover breaks); Pass Type ID `pass.com.arcu.attnd.tickets` + certificate (files in
  `/Users/andrewbeers/Documents/Attnd/apple-wallet-pass`); APNs auth key; App Store Connect API key (CI, Admin);
  Apple Development certificate for CI (expires Nov 14, 2027); Apple Distribution certificate + three App Store
  profiles for CI signing (Nov 19, 2026, expire Nov 2027; files in `/Users/andrewbeers/Documents/Attnd/ios-signing`,
  GitHub secrets `IOS_DIST_*` / `IOS_PROFILE_*`); WeatherKit on `com.arcu.Attnd` (no longer used since Nov 19, 2026;
  untick it with the next profile); App Attest. Profiles regenerated Nov 21, 2026 with the App Group on the app and
  the clip; **Broadcast** (Push Notifications → Broadcast Capability) turned on for `com.arcu.Attnd` but unused (App
  Clip App IDs don't offer it, so the channel plan was dropped); the clip's App ID offers no Time Sensitive
  Notifications. Media ID `media.com.arcu.attnd` (MusicKit only; ShazamKit and Apple Music Feed off) and the MusicKit
  key "Attnd MusicKit" (Key ID `2X6W6X6PLA`, `.p8` in `/Users/andrewbeers/Documents/Attnd/ios-signing/`; Heroku
  `APPLE_MUSIC_KEY_ID` / `APPLE_MUSIC_PRIVATE_KEY`, dev set Nov 21, 2026, production later; API-030 artist links): a
  new team needs a new Media ID and key.
- App Store Connect: Attnd (id1470627406), App Clip, TestFlight.
- Apple Business (Business Connect): organization "Attnd" (Org ID 552089622879), attnd.app verified, the App Store
  app linked, brand logo added; Branded Mail pending DMARC; no public Location. Move it to the LLC's Apple account.
- Search engines: Google Search Console (attnd.app domain property, DNS TXT), Bing Webmaster Tools (attnd.app via a
  CNAME record, sitemap + home page submitted; Microsoft Clarity deliberately not used), ChatGPT business listing
  (category Software). Re-verify under LLC accounts (the DNS records stay).
- Google Play Console: `com.arcu.attnd` (upload key, Play App Signing), publishing service account.
- OpenAI (server key; since Nov 21, 2026 (stage C) only Studio's image generation. Before: AI import, website watching, Studio, the staff
  Prospects dashboard's web search tool: billed per search call on the same key/project, and since Nov 20, 2026
  **Ask Attnd**, the console's AI agent for every organizer (`agent/`, `AGENT_MODEL` gpt-5.5, capped per person per
  day by `AGENT_DAILY_SPEND_CAP_USD`): it sends console page text (event content, team member emails, organizer
  notes) to OpenAI as the signed-in organizer, so the LLC's OpenAI org must have data sharing/training off and the
  privacy policy / organizer terms should mention the AI assistant as a processor).
- Anthropic (Nov 21, 2026): the Anthropic Console account and two API keys (`ANTHROPIC_API_KEY` on attnd-dev and
  attnd-web). **Ask Attnd** runs on Claude Haiku 5.5 (`AGENT_PROVIDER`, the official `anthropic` SDK) and **Studio** on
  Claude Sonnet 5.5 (`STUDIO_ANTHROPIC_MODEL`); since stage C also AI import, website watching, tags, asset vision, form fills, plan suggestions and prospects (Sonnet web search); like OpenAI before it, Ask Attnd it receives console page text (event content, team emails,
  notes) as the signed-in organizer, so the privacy policy / organizer terms should name Anthropic as a processor.
- **Search Console and Apple Business** (Nov 21, 2026): the Google Search Console domain property for `attnd.app`
  (owner: the owner's Google account; add the LLC's account as owner, then remove the personal one); the Apple
  Business organization "Attnd" (Org ID 552089622879, in review; its legal name and address must change to the LLC's).
- **To create** (business listings, SETUP.md "Business listings & discoverability"; create them under the LLC if it
  exists by then): Google Business Profile (service-area, optional), Bing Webmaster Tools,
  LinkedIn company page, Crunchbase, X and Instagram handles "attnd", G2, Capterra / GetApp / Software Advice, Product
  Hunt, AlternativeTo, SaaSHub, Eventbrite App Marketplace, EventMB / event-tech directories, Wikidata (later). Their
  URLs go into `SOCIAL_PROFILES` (the home page's schema.org `sameAs`).
- Prospects data (staff sales pipeline, `prospects/`): organizer contact details scraped from public event pages,
  with do-not-contact flags. Moves with the database; outreach from the LLC must identify the LLC and its physical
  mailing address (CAN-SPAM).
- **Parked with ticketing (git branch `parked/tickets`, Nov 19, 2026): the Stripe, Apple Wallet and Google Wallet items
  below.** Nothing on `develop` uses them; keep them listed so they move to the LLC if ticketing comes back.
- Stripe (tickets, parked; built Nov 16, 2026, not set up): the platform account + Connect (Express, destination charges,
  manual payouts), test + live API keys (`STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`), two webhook endpoints per mode
  (`STRIPE_WEBHOOK_SECRET`, `STRIPE_CONNECT_WEBHOOK_SECRET`), organizers' connected accounts (they move with the
  platform account), statement descriptor "ATTND TICKETS". Create it in the LLC's name (SETUP step 16a on the branch).
- Apple Wallet: the Pass Type ID certificate + key and the WWDR G4 intermediate as Heroku config vars
  (`APPLE_WALLET_*`, SETUP 16b on the branch; not set); passes issued under team GQQX4L6TE2 (moving the team to the LLC means a new Pass
  Type ID certificate; already-issued passes keep working but stop updating).
- Google Wallet: issuer id + `attnd-wallet` service-account JSON key on Heroku (`GOOGLE_WALLET_*`, SETUP 16c on the branch; not set); Event
  Ticket classes `attnd-event-<id>` / `attnd-dev-event-<id>`.
- GitHub secret `ANTHROPIC_HARNESS_KEY` (Attnd-Web, Nov 21, 2026): the Anthropic key of the nightly Ask Attnd quality
  harness; re-create it under the LLC's Anthropic account.
- Place photos found automatically (API-022, Nov 21, 2026): stored in Cloudinary with their source and credit;
  Wikimedia Commons photos carry their authors' licences (CC BY / BY-SA need the credit shown, which the apps do);
  Wikimedia's API needs no account. Mention third-party photo credits in the Terms when they are written.
- Attnd's own data: organizer accounts (emails, passkeys), anonymous device ids, usage counts, diagnostics; buyers'
  names/emails/orders, ticket holders' names/emails, door check-ins (tickets).
- Open sign-ups (Nov 21, 2026): anyone can create an organizer account and AI-built demo events from the home page;
  the home page's anonymous counts (`home.HomeMetric`) and the composer's waiting submissions (`home.PendingImport`,
  deleted after a day). Terms of Service should cover demo events, the 20-phone free limit, claims of events made from
  public pages (staff-approved hand-over) and AI-generated content.
- Pricing (D2, Nov 21, 2026): Standard $299 and Studio $499 per event, kept in `PlatformSettings` (Platform admin →
  Pricing). Upgrades are requests a superuser approves; each approval is a `PaymentRecord` (event, plan, amount, who).
  Payments are collected outside Attnd for now (no checkout); invoices, sales tax and the payment account should be in
  the LLC's name, and the Terms should name the plans and what each includes.
Legal/policy surface (to re-issue in the LLC's name):
- Privacy policy (`/privacy/`; the rewrite waiting for approval: `docs/privacy_policy_draft.md` + `home/templates/home/privacy_draft.html`, batch 7), Terms of Service (to write), Terms of Sale + Organizer Agreement (tickets), App Store
  privacy labels, Play data safety, support email, App Review contact.

- Stripe for plan billing (batch 7, Nov 22, 2026; built, off): `STRIPE_SECRET_KEY` + `STRIPE_WEBHOOK_SECRET` on the
  Heroku apps, the webhook endpoint `https://attnd.app/console/billing/stripe/webhook/` (event
  `checkout.session.completed`), Customer Portal settings. Create the account in the LLC's name; invoices say "Attnd"
  (`platform_admin/billing.py SELLER`): add the LLC's legal name and address there once it exists.

- MCP server (Nov 25, 2026, docs/mcp_plan.md): staff connect Claude to `https://dev.attnd.app/mcp` (and later
  `https://attnd.app/mcp`) as custom connectors on their own Claude accounts. Nothing to move but the people: when
  the LLC has a Claude organization, reconnect from it. No keys or client secrets (Claude uses a Client ID Metadata
  Document); grants live in each environment's database and can be revoked on Platform → Connected apps.

## 5. Order of operations (when it's time)
1. Form the LLC, EIN, bank account, D-U-N-S, insurance (§1).
2. Workspace + password vault; re-point every account's email (§2).
3. Low-risk moves: domain contacts, GitHub org, Heroku team, Cloudinary/Mailgun/OpenAI, Google Cloud org/billing.
4. Store accounts: Apple conversion, Google Play organization account + app transfer (plan around review times; no
   releases during the transfer).
5. Legal: ToS, privacy policy, Terms of Sale, Organizer Agreement naming the LLC; store listings updated.
6. Then: Stripe in the LLC's name, `SIGNUPS_OPEN=1`, tickets.
