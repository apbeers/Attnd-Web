# Attnd setup checklist

Do these steps once, in order. Each step says which values go where. For any single key, see the **Key reference** at the end. It explains where to create the key and exactly where it goes.

## Progress

| Step | Status |
|---|---|
| 0. GitHub | ✅ Done. Default branch is `main` in all three repos (verified). The environments and secrets get verified by the first deploy run; a missing one fails with a message naming it. |
| 1. Rotate API key | ✅ Done. Run the check under step 1 to confirm. |
| 2. Heroku | ⏳ ✅ Both apps are configured: secrets, OpenAI and Places keys, APNs key and Google key, `CONTACT_EMAIL`, Scheduler jobs and GitHub deploy settings. ✅ Stack heroku-26 and Python 3.15. **Still to do:** check Postgres and upgrade to 18 (commands under "Keeping Heroku current"). ✅ Cloudinary on both apps (step 5). |
| 3. Apple | ⏳ ✅ App ID capabilities and App Attest fixed. ✅ 3a: APNs key `7Q3H2446BU` (Sandbox & Production) is set on both Heroku apps. ✅ 3c: TestFlight group. ✅ 3b: `ASC_ISSUER_ID` re-set; CI signs, uploads and reaches TestFlight (iOS run 100). ✅ **3e: CI signing certificate** stored as `IOS_DEV_CERT_P12_BASE64` / `IOS_DEV_CERT_PASSWORD` (re-set Nov 15, 2026 after the first copy was saved empty; CI signs as "Apple Development: Andrew Beers"; renew before Nov 14, 2027). |
| 4. Google / Android | ⬜ 4e: exclude tablets in Play Console. ✅ 4a Firebase and server key, 4b Play publisher, 4c fingerprints (both Heroku apps serve them in `assetlinks.json`), Maps and Places keys restricted, keystore backed up. ✅ The first Play internal-testing upload succeeded (Android run 14). 4d (production lock-down) waits for the first release. |
| 5. Local dev keys | ⬜ |
| 6. Check it all works | ⏳ ✅ **Web dev** deploys from `develop`. ✅ **Android** publishes to Play internal testing. ✅ **iOS** builds reach TestFlight, and on Nov 14 a real iPhone passed App Attest against dev and joined an event (lookup, join, bundle, 304s). Waiting on: the first release to `main`. |
| 11. Email | ⏳ ✅ Mailgun Starter (free) add-ons on attnd-web (`mailgun-spherical-62131`) and attnd-dev (`mailgun-silhouetted-37822`; separate account, Mailgun can't be shared between apps; dev stays on its sandbox, which only mails authorized recipients). ✅ `mg.attnd.app` added (tracking off), Namecheap SPF/DKIM/DMARC added, **verified** by Mailgun. ✅ SMTP credentials (`postmaster@…`) set on both apps as `EMAIL_HOST_USER`/`EMAIL_HOST_PASSWORD` + `DEFAULT_FROM_EMAIL`; dev `SITE_URL` set. ✅ Code: TLS, from address, per-site links, allow-list, password reset. ✅ `ADMIN_EMAILS` (both apps) and dev `EMAIL_ALLOWED_RECIPIENTS` = the owner's address; owner authorized on dev's sandbox; Nov 14 dev test emails (plain + `--admins`) **delivered** to iCloud. ✅ MX records for `mg` added; production-domain test from `noreply@mg.attnd.app` **delivered** to iCloud (Nov 14). ⬜ The production app itself sends once develop is released to main. |
| 12. Admin sign-in security | ⬜ After the next deploy: optionally set a private `ADMIN_URL_PATH`, then add a passkey (or an authenticator app) at the first admin sign-in, on dev and again on production (passkeys are per domain), and save the recovery codes. |
| 13. CI keys (live tests) | ✅ Nov 15, 2026: `CI_API_KEYS` set on production (release v109; takes effect once API-008 is released to `main`), `ATTND_PROD_CI_KEY` in all three repos, `ATTND_DEV_API_KEY` in Attnd-Web. |
| 14. Diagnostics (API-013) | ✅ `GITHUB_SYMBOLICATE_TOKEN` on both Heroku apps (Nov 15, 2026) (iOS symbolication). Everything else needs no setup: the tables, caps and purge come with the deploy. |
| 15. Attnd Studio (beta) | ⬜ Works with no setup on each Heroku app's herokuapp.com hostname. To do: buy a separate sites domain, add it to Heroku and set `SITES_HOST` (step 15). AI on Claude Sonnet 5.5 (step 18). |
| 16. Tickets (Stripe, Wallet) | ⏸ **Parked** (Nov 19, 2026): moved to git branch `parked/tickets` (API-017), organizers use Eventbrite for now. The setup steps live on that branch. Optional: drop dev's orphan `tickets_*` tables (step 16). |
| 17. Prospects (staff) | ✅ No setup: it uses `ANTHROPIC_API_KEY` (Claude Sonnet's web search, ~$0.01 per search + tokens; stage C). The schedule is off until turned on in `/console/prospects/settings/`. Optional: `PROSPECT_MODEL`, `PROSPECT_DAILY_SPEND_CAP_USD` (default 5), `PROSPECT_RUN_MAX_MINUTES` (step 17). |
| 18. Ask Attnd (console agent) | ✅ Runs on Anthropic's Claude Haiku 5.5 (`ANTHROPIC_API_KEY` set on dev and production, Nov 21, 2026; Studio on Claude Sonnet 5.5, step 18). Without the key it uses the existing `OPENAI_API_KEY` (gpt-5-mini → gpt-5). Optional: `AGENT_PROVIDER`, `AGENT_ANTHROPIC_MODEL`, `AGENT_DAILY_SPEND_CAP_USD` (default 2 per person per UTC day), `AGENT_MAX_STEPS` (40). |
| 19. Business listings & discoverability | ⬜ Search Console verified for attnd.app (owner, Nov 21, 2026); the rest of the list under Open owner tasks. Code side done: dev never indexed, canonicals, sitemap with listed events, `public/llms.txt`, schema.org `sameAs` from `SOCIAL_PROFILES`. |
| 20. Apple Music (API-030 artist links) | ⏳ ✅ Media ID `media.com.arcu.attnd` (MusicKit only; ShazamKit and Apple Music Feed deliberately off) and the MusicKit key "Attnd MusicKit" (Key ID `2X6W6X6PLA`) made Nov 21, 2026; `APPLE_MUSIC_PRIVATE_KEY` + `APPLE_MUSIC_KEY_ID` set on attnd-dev (verified). ⬜ Production later (step 20). Spotify skipped (ROADMAP). |
| 7. Production cleanup | ⏸ Deferred. Production still runs the old code, where deleting a user also deletes their events. After the first web release to `main`: list users and demo events, then delete them with a go-ahead (real events become unassigned). **Keep** DEMO and the App Review sample events FWFM2026, ATIS2026, DACN2026 (owner, Nov 19, 2026), and their owner account `fixtureuser`. |

**Open owner tasks (remind the owner at the start of each working session until done):**
- [ ] IndexNow on production (step 22): `heroku config:set -a attnd-web INDEXNOW_KEY=$(openssl rand -hex 16)` (batch 7).
- [ ] Privacy policy rewrite: read `docs/privacy_policy_draft.md` (five decisions at the top), then say publish (batch 7).
- [x] 3e: store the CI signing certificate (`.p12` + password as GitHub secrets). Done Nov 14, 2026 (certificate expires Nov 14, 2027).
- [x] Database backups: daily at 03:00 America/Chicago on attnd-web and attnd-dev, first manual backup taken (Nov 14, 2026). Essential plans keep 7 daily + 1 weekly backup. Restore: `heroku pg:backups:restore <backup-id> DATABASE_URL -a <app>`.
- [x] Protect `main` in all three repos: ruleset "Protect main" blocks force-push and deletion (Nov 14, 2026). Add "require status checks" once releases go through pull requests.
- [x] Step 13: CI keys for the live integration tests (`CI_API_KEYS` on attnd-web, `ATTND_PROD_CI_KEY` in all three repos, `ATTND_DEV_API_KEY` in Attnd-Web).
- [ ] API-010/011 data (after the server reaches each app): reload DEMO so it has the new tags and the urgent
      "Set time change" (`heroku run -a attnd-dev python manage.py load_demo_event --owner apbeers@icloud.com`, then the
      same on `attnd-web` with a go-ahead; the three live suites check the new facts), and tag the existing events with
      the AI: `heroku run -a attnd-dev python manage.py backfill_tags --all --dry-run`, then without `--dry-run` (step 9).
- [x] iOS manual CI signing (Nov 19, 2026; first manually signed build 145 uploaded): an Apple Distribution
      certificate and three App Store profiles stored as GitHub secrets (step 3f; renew before Nov 19, 2027). App Group `group.com.arcu.Attnd`
      back on `com.arcu.Attnd` and `com.arcu.Attnd.clip` (Nov 21, 2026: the App Clip hands its event, My schedule,
      chat and settings to the full app; app, clip and widgets profiles regenerated and their secrets updated; the
      app's App ID also has Push Notifications → Broadcast ticked, unused). WeatherKit is no longer used (removed from iOS Nov 19, 2026: not offered for App Clip App
      IDs); the capability can be unticked on `com.arcu.Attnd` whenever its profile is regenerated.
- [x] Google Weather API **disabled** (Nov 19, 2026, owner): it was enabled in `attnd-df28e` and on the Android Maps key
      `af7cec96-…` the same day for planner weather, which neither app builds now. Nothing is billed.
- [x] iOS app icon (Nov 19, 2026): iOS went back from the Icon Composer icon (`AppIcon.icon`) to a PNG app icon set,
      because the Icon Composer build made `Assets.car` about 5 MB (App Clip size). The website's
      `brand/app-icon-1024.png` and the site icons (`scripts/make_icons.py`) stay as they are.
- [x] App Review sample events: FWFM2026, ATIS2026 and DACN2026 on production are **kept on purpose**: App Review uses
      them (owner, Nov 19, 2026). The release phase's `seed_test_data` keeps them current; don't delete them or the
      command.
- [x] Studio sites on **attnd.site** (owner bought it, Nov 21, 2026; production `SITES_HOST=attnd.site` set and verified Nov 21, 2026). Done: the Heroku domains `dev.attnd.site` on
      attnd-dev (DNS target `genetic-salmon-1jo14r3peboo8qusoemv7xrz.herokudns.com`) and `attnd.site` on attnd-web
      (DNS target `encircled-eggplant-pbyuddr6oxnp2ggjecvwc5be.herokudns.com`), automatic certificates (ACM) on both.
      DNS records set and both certificates issued (dev.attnd.site and attnd.site); **dev is switched**
      (`SITES_HOST=dev.attnd.site` on attnd-dev, Nov 21, 2026) and **production is switched** (`SITES_HOST=attnd.site` on
      attnd-web, verified Nov 21, 2026); after a switch run `heroku run -a <app> python manage.py
      refresh_site_addresses` so the apps pick up the new `website_url`. The old herokuapp.com sites host then only
      301s site paths to the new host. Older note (step 15):
      `heroku domains:add <domain> -a attnd-web` (+ DNS + ACM) and `heroku config:set -a attnd-web SITES_HOST=<domain>`
      with a go-ahead; until then sites are served on the Heroku hostnames. (`ANTHROPIC_API_KEY`: done, step 18.)
- [x] Tickets prep (Wallet passes, not payments; nothing built yet; done Nov 15, 2026): Apple Pass Type ID `pass.com.arcu.attnd.tickets`
      + Pass Type ID certificate (✅ Pass Type ID registered Nov 15, 2026; key, CSR, `.cer` and PEM in
      `/Users/andrewbeers/Documents/Attnd/apple-wallet-pass` on the owner's Mac, plus the password manager), and a Google Pay & Wallet Console business
      profile with its Issuer ID, the Google Wallet API enabled in `attnd-df28e` and a service account
      `attnd-wallet@attnd-df28e.iam.gserviceaccount.com` added as a Developer user. Plan: `docs/specs/tickets_spec.md` on branch `parked/tickets`.
- [ ] ⏸ Tickets: parked on git branch `parked/tickets` (Nov 19, 2026; Eventbrite for now), not a task now. Optional: drop
      dev's orphan `tickets_*` tables (step 16).
- [x] Ask Attnd harness (stage I): `ANTHROPIC_HARNESS_KEY` Actions secret set on apbeers/Attnd-Web (owner, Nov 21,
  2026). Only remaining: the nightly run starts once `agent-harness.yml` is on `main` (the next production release);
  then run it once with `gh workflow run agent-harness.yml -R apbeers/Attnd-Web`.
- [x] Anthropic monthly spend limit **$20** set in the Anthropic Console (owner, Nov 21, 2026). It covers everything:
  the nightly Ask Attnd harness and all dev and production AI (Ask Attnd, Studio, imports, website checks, themes,
  place photos, plan suggestions, prospects). Keep the per-feature daily caps under it (`AGENT_DAILY_SPEND_CAP_USD`,
  `STUDIO_DAILY_SPEND_CAP_USD`, `PROSPECT_DAILY_SPEND_CAP_USD`, `PLACE_IMAGES_DAILY_CAP_USD`, the new accounts' import
  cap): one busy day of Studio on its own default ($10 per event) could use half the month.
- [x] Background tasks on dev (F5, owner Nov 21, 2026): `TASKS_BACKEND=database` set on attnd-dev and a `tasks` dyno
  scaled to 1 Basic. Production only after it has proved itself on dev: `heroku config:set TASKS_BACKEND=database -a
  attnd-web` and `heroku ps:scale tasks=1:basic -a attnd-web` (with a go-ahead).
- [x] DEMO reloaded on dev with its theme (owner, Nov 21, 2026). Production: the same `load_demo_event` with a go-ahead
  after the release.
- [ ] Onboarding (Nov 21, 2026, on dev): sign-ups are **open by default** now (`SIGNUPS_OPEN` defaults to on; anyone
  makes demo events, capped at 20 phones each, API-025). Before releasing to production decide: keep it open, or
  `heroku config:set SIGNUPS_OPEN=0 -a attnd-web` (invited emails only). Showcase events are dev-only (`SHOWCASE_EVENTS`
  unset = dev.attnd.app only); for production later: `heroku config:set SHOWCASE_EVENTS=1 -a attnd-web`, and once for
  AI covers on either app: `heroku run -a attnd-dev python manage.py load_showcase_events --images` (OpenAI, a few
  cents each). Watch `ADMIN_EMAILS`: "Remove the limit" requests and event claims are emailed there.
- [x] Home page "Happening near <city>" (done Nov 21, 2026, 17:44 UTC: Geolocation API enabled and
      `geolocation.googleapis.com` added to the "Attnd web (browser)" key's API restrictions; referrers unchanged): in Google Cloud (project `attnd-df28e`), enable the
      **Geolocation API** and add it to the **browser key**'s API restrictions (the key behind
      `GOOGLE_MAPS_BROWSER_KEY`, referrer-restricted to attnd.app / dev.attnd.app). The browser calls it at most once a day
      for an approximate position from the visitor's IP (no prompt); billed per request after the monthly free usage,
      about $5 per 1,000. Without it the page shows the prospects region's cards instead (nothing breaks).
- [ ] Later (before outside organizers or payments): move everything to a Texas LLC — `docs/LLC_MIGRATION.md`.
- [x] Repos made public (Nov 19, 2026); history scan found old secrets: the OpenAI key in `_set_heroku_env.command`
      (revoked Apr 29, 2026) and the 2021 service-account keys `attnd-prod` (`bf9dce7d…`) and `attnd-742` (`73dbb440…`),
      deleted Nov 19, 2026. Heroku uses neither. To check: the two old Maps keys in history (`AIzaSyC_f5…` web 2021,
      `AIzaSyD5d9fk…` Android) are deleted or restricted; turn on GitHub secret scanning + push protection on all three
      repos (`gh api -X PATCH repos/apbeers/<repo> -F security_and_analysis[secret_scanning][status]=enabled
      -F security_and_analysis[secret_scanning_push_protection][status]=enabled`).
- [x] Keys rotated (owner, Nov 21, 2026; dev config values had been exposed in a session log): the Anthropic keys on
      attnd-dev and attnd-web (now separate keys per app, the old ones deleted), the dev static `API_KEY` on attnd-dev,
      and the GitHub secrets `ATTND_API_KEY` (Attnd-iOS, Attnd-Android) and `ATTND_DEV_API_KEY` (Attnd-Web).
- [ ] Low priority: rotate the APNs key (`7Q3H2446BU`, step 3a), deliberately deferred on Nov 21, 2026. Make a new key,
      set `APNS_KEY_ID` / `APNS_AUTH_KEY` on both apps (the `read -s` way below), test a push, then revoke the old key.
- **Business listings & discoverability** (owner, Nov 21, 2026; not urgent). Add each profile's URL to `SOCIAL_PROFILES`
  on attnd-web (comma-separated; the home page's schema.org `sameAs`):
  - [x] Google Search Console: attnd.app verified as a domain property (DNS TXT), Nov 21, 2026. Still to do:
        - [ ] submit https://attnd.app/sitemap.xml;
        - [ ] next, after the SEO release reaches production: **Validate fix** on the "Duplicate without
              user-selected canonical" and "Not found (404)" reports;
        - [ ] add an attnd.site property once a Studio site is published there.
  - [ ] Google Business Profile: only as a service-area business (no storefront); optional.
  - [x] Bing Webmaster Tools: attnd.app verified (CNAME record), sitemap and the home page submitted (owner, Nov 21,
        2026); Bing's index feeds ChatGPT search, Copilot and DuckDuckGo. Still to do:
        - [ ] in a few weeks: look at **AI Performance**;
        - [ ] after the next production release: run **Site Scan**;
        - [ ] add attnd.site later (once a Studio site is published).
        - Microsoft Clarity: deliberately skipped (session recording; the platform keeps to anonymous counts).
  - [x] ChatGPT: business listing done (category Software, owner, Nov 21, 2026). It also finds Attnd through Bing and
        OpenAI's crawler (OAI-SearchBot, allowed by robots.txt). Later, optional: a ChatGPT app (Apps SDK).
  - [~] Apple Business (Business Connect): organization "Attnd" submitted for review Nov 21, 2026 (Org ID 552089622879).
        After approval:
        - [x] verify attnd.app under Domains (done, owner, Nov 21, 2026);
        - [x] link the App Store app (done, owner, Nov 21, 2026);
        - [x] brand logo added (owner, Nov 21, 2026); description and website once approved;
        - [ ] Branded Mail: pending, needs a DMARC policy of quarantine or reject on mg.attnd.app (Gmail BIMI is
              skipped, owner, batch 7);
        - no public Location listing (no storefront; the organization's address is private and never goes in the repo).
  - [ ] Perplexity, Claude and other AI search: allowed by robots.txt and `llms.txt`; nothing to submit.
  - [ ] LinkedIn company page, Crunchbase profile, X and Instagram handles (claim "attnd").
  - [ ] Software directories AI answers cite: G2, Capterra / GetApp / Software Advice, a Product Hunt launch,
        AlternativeTo, SaaSHub.
  - [ ] Event industry: Eventbrite App Marketplace listing (an integration later), EventMB and event-tech directories.
  - [ ] Wikidata entry, once there is press coverage.
- [ ] Sign in to the dev console and add a passkey or authenticator app (staff accounts must, step 12); again on production after the release (a dev passkey doesn't work on attnd.app).

Notes:
- If the Heroku CLI says an update is available, run `heroku update` first. It's optional.
- Paste only the lines inside the command boxes. zsh doesn't treat `#` as a comment by default, so the boxes contain no comments.
- **GH-web**, **GH-ios** and **GH-android** mean that repo's GitHub page, under **Settings → Secrets and variables → Actions**.
- **Heroku prod** is the app `attnd-web`. **Heroku dev** is the new app `attnd-dev`.
- Once everything is set up, pushing to `develop` deploys all three apps to dev and beta, and merging `develop` → `main` releases to production.

---

## Where keys live

| Store | What it's for | Can you read keys back? |
|---|---|---|
| **Password manager** (Apple Passwords or 1Password) | The **master copy of every key**. Save each key here the moment you create it. | Yes |
| **GitHub environment secrets** | Keys the CI/CD pipelines use. | No, write-only |
| **Heroku config vars** | Keys the running server uses. | Yes (`heroku config:get`) |
| **Claude cloud environment** ("Attnd Cloud Environment") | Network secrets that give Claude API access. Currently: a Heroku API token for `api.heroku.com`. Claude never sees the value. | No, write-only |

**Setting a secret without exposing it** (Nov 21, 2026): `heroku config:set` prints the values it set, so type the
value into a hidden prompt and throw the output away, and never paste a key copied from instructions or the clipboard
history:
```
read -s VALUE && heroku config:set -a attnd-dev ANTHROPIC_API_KEY="$VALUE" > /dev/null; unset VALUE
```
For GitHub, pipe it the same way: `read -s VALUE && printf %s "$VALUE" | gh secret set NAME -R apbeers/<repo>; unset VALUE`.

Never keep keys only on your Mac. Once a key is in the password manager and wherever it's used, delete the downloaded file (`.p8`, `.jks`, `.json`).

The Claude environment allows `api.heroku.com`, `attnd.app` and `dev.attnd.app`. To give Claude access to another API later, add its token there as a network secret. Claude asks for these through a secure form, never in chat.

---

## 0. GitHub (5 min)

1. ✅ Set the default branch to `main` in **Attnd-iOS** and **Attnd-Android** (Settings → General → Default branch). Attnd-Web was already on `main`.
2. Optional cleanup:
   - **Attnd-Android:** `master` is a single 2021 "initial commit" and can be deleted. The real code now lives on `main` and `develop`.
   - **Attnd-Web:** `master` is an old side line that is not part of `main`. Glance at it before deleting. Web's old 2021 `develop` history is still reachable, because it was merged into the new `develop` without its content.
3. ✅ In each of the three repos, create two environments under Settings → Environments: **`dev`** and **`production`**. On `production` you can also add yourself as a required reviewer if you want a one-tap approval before each production deploy.

## 1. Rotate the leaked API key (2 min) — required

Also replace the old `ATTND_API_KEY` secret in Attnd-iOS and Attnd-Android. It's probably the leaked key. Use the dev app's new key (see section 5).

```bash
heroku config:set API_KEY=$(openssl rand -hex 32) -a attnd-web
```
The old key is public in iOS git history. The server already rejects it, but this gives you a fresh one. Production app builds don't use this key at all, because they authenticate with App Attest or Play Integrity.

**Check the new key works:**
```bash
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $(heroku config:get API_KEY -a attnd-web)" "https://attnd.app/api/v2/events/lookup/?q=FWFM2026"
```
- `200` or `404` means the key works (FWFM2026 is one of the App Review sample events).
- `401` means the key didn't take.

Requests with the old key now get `401`. Once this release is on `main`, the server also blocks the old key permanently by its hash.

## 2. Heroku (15 min)

### Production (`attnd-web`)
1. ✅ **Required before the first release to `main`:** set real `SECRET_KEY` and `ATTESTATION_JWT_SECRET` values on production (done and verified). Production previously had neither. It has been running on a hardcoded fallback, and the new code refuses to start without a real key. Run this once:
   ```bash
   heroku config:set -a attnd-web SECRET_KEY=$(openssl rand -hex 32) ATTESTATION_JWT_SECRET=$(openssl rand -hex 32)
   ```
   This signs everyone out of the web console once. The apps re-attest automatically.
2. ✅ Scheduler job `python manage.py run_scheduled_tasks` (every 10 minutes) is added. It fails harmlessly until the first release to `main`, because the command doesn't exist in the old code.
3. ✅ The OpenAI and Places keys are already set on production.
4. **Play Integrity isn't configured on production** (there's no `GOOGLE_SERVICE_ACCOUNT_JSON`), so Android builds from the Play Store can't attest. Old Android builds fell back to the static key, which is now rotated, so the current Play Store version can't reach the API until this is set (step 4) and a new build ships. iOS builds use App Attest, which is configured.

5. ✅ **File uploads: Cloudinary** (Nov 15, 2026). The free `cloudinary:starter` add-on is on both apps (`cloudinary-fluffy-65757` on attnd-dev, `cloudinary-metric-01769` on attnd-web; separate Cloudinary accounts, like Mailgun). It sets `CLOUDINARY_URL` and the server stores uploads there automatically. Checked on dev: images upload and are served (200).
   - ✅ **PDF delivery allowed on both accounts** (Nov 15, 2026; dev re-checked: a test PDF is served with 200). Free Cloudinary accounts refuse to serve PDFs (401) until it's allowed. Open each dashboard, then Settings → Security → tick **"Allow delivery of PDF and ZIP files"** → Save:
     ```
     heroku addons:open cloudinary -a attnd-dev
     heroku addons:open cloudinary -a attnd-web
     ```

### Dev (`attnd-dev`) — created by Claude
- ✅ App `attnd-dev` at **`https://dev.attnd.app/`** (custom domain, Nov 14, 2026: Namecheap CNAME `dev` → the app's herokudns target, Heroku ACM certificate; `SITE_URL=https://dev.attnd.app`). Use `dev.attnd.app` wherever this guide says `ATTND_SERVER`. The Heroku hostname `attnd-dev-7ab893f37020.herokuapp.com` still works, but nothing should depend on it: the custom domain survives replacing the Heroku app, so dev passkeys keep working. ✅ The `dev` environment variable `ATTND_SERVER` = `dev.attnd.app` in Attnd-iOS and Attnd-Android (Nov 15, 2026).
- ✅ Postgres (essential-0), Scheduler, and generated `SECRET_KEY`, `API_KEY` and `ATTESTATION_JWT_SECRET`.
- ✅ The OpenAI and Places keys have been copied from production. For reference, the command was:
  ```bash
  heroku config:set -a attnd-dev OPENAI_API_KEY="$(heroku config:get OPENAI_API_KEY -a attnd-web)" GOOGLE_PLACES_API_KEY="$(heroku config:get GOOGLE_PLACES_API_KEY -a attnd-web)"
  ```
- ✅ The Scheduler job is added on `attnd-dev`.
- The dev `API_KEY` is what goes in the `ATTND_API_KEY` GitHub secrets and in your local dev config. These commands send it straight to GitHub without showing it:
  ```bash
  heroku config:get API_KEY -a attnd-dev | gh secret set ATTND_API_KEY -R apbeers/Attnd-iOS
  heroku config:get API_KEY -a attnd-dev | gh secret set ATTND_API_KEY -R apbeers/Attnd-Android
  ```
  ✅ Done: both repos have the dev key as `ATTND_API_KEY`. To see the key itself (for example for local dev config), run `heroku config:get API_KEY -a attnd-dev`.

### Keeping Heroku current
- ✅ **Stack:** both apps are on `heroku-26` (Ubuntu 26.04, supported until April 2031). Production was already set. Claude moved dev on Nov 14, and the switch takes effect on each app's next deploy. `app.json` pins `heroku-26`.
- ✅ **Python:** `.python-version` says `3.15`, and Heroku reads that file. Patch releases install automatically on the next deploy.
- ⏳ **Postgres 18:** the current Heroku default. CI tests against 18. Essential-tier databases upgrade in place in a few minutes, and the app is unavailable while that runs. Check versions first:
  ```bash
  heroku pg:info -a attnd-dev | grep -i version
  heroku pg:info -a attnd-web | grep -i version
  ```
  If either is below 18, upgrade dev first, check that the site still works, then upgrade production:
  ```bash
  heroku pg:upgrade:run DATABASE_URL -a attnd-dev --confirm attnd-dev
  heroku pg:upgrade:wait DATABASE_URL -a attnd-dev
  curl -s https://dev.attnd.app/health/
  ```
  ```bash
  heroku pg:backups:capture -a attnd-web
  heroku pg:upgrade:run DATABASE_URL -a attnd-web --confirm attnd-web
  heroku pg:upgrade:wait DATABASE_URL -a attnd-web
  ```
- **Each year:** check that the stack, Python and Postgres versions are still current. Heroku emails before anything is deprecated. Python packages are bumped weekly by the "Upgrade dependencies" workflow.

### Let GitHub deploy to Heroku
```bash
heroku authorizations:create -d "GitHub Actions"
```
Copy the token it prints into **GH-web secret `HEROKU_API_KEY`** and into your password manager. GitHub needs its own token: the one in the Claude environment can't be copied out.
| Where | Type | Name | Value |
|---|---|---|---|
| Attnd-Web → Settings → Secrets and variables → Actions | **Repository secret** (not an environment secret) | `HEROKU_API_KEY` | that token |
| Attnd-Web → Settings → Environments → `dev` | **Environment variable** (not a secret; it's just an app name) | `HEROKU_APP` | `attnd-dev` |
| Attnd-Web → Settings → Environments → `production` | **Environment variable** | `HEROKU_APP` | `attnd-web` |

✅ Done (with `gh`):
```bash
heroku authorizations:create -d "GitHub Actions" -S | gh secret set HEROKU_API_KEY -R apbeers/Attnd-Web
gh variable set HEROKU_APP -e dev -b attnd-dev -R apbeers/Attnd-Web
gh variable set HEROKU_APP -e production -b attnd-web -R apbeers/Attnd-Web
```

**Rule of thumb:** anything that grants access is a **secret**. Names, hosts and settings are **variables**. Secrets used by every deploy go at repository level; values that differ between dev and production go on the environment.

## 3. Apple (30 min)

### 3a. Push notifications (APNs)
1. ✅ Go to [developer.apple.com](https://developer.apple.com) → Certificates, IDs & Profiles → **Identifiers** → `com.arcu.Attnd`. Check that **Push Notifications**, **App Attest** and **Associated Domains** are enabled. All three are.
   - Push shows **"Certificates (0)"**. That's expected. Attnd sends pushes with a token-based APNs **key** (step 2), not certificates, so don't click **Configure**.
   - Leave **Broadcast Capability** unticked. It's for live-broadcast push channels, which Attnd doesn't use.
2. Go to **Keys → +**, name the key "Attnd APNs", tick **Apple Push Notifications service (APNs)**, and click **Configure**. Set:
   - **Environment:** **Sandbox & Production**. This can't be changed after saving. TestFlight and App Store builds use Production; Sandbox is only for builds run from Xcode.
   - **Key Restriction:** **Team Scoped (All Topics)**.

   Save, then **Continue → Register → Download** `AuthKey_XXXXXXXXXX.p8`. Apple only lets you download it once, so put it in your password manager right away. The **Key ID** is shown on the key's page.
3. ✅ Set it on **both** Heroku apps (done, key ID `7Q3H2446BU`):
   ```bash
   for app in attnd-web attnd-dev; do
     heroku config:set -a $app APNS_KEY_ID=XXXXXXXXXX APNS_AUTH_KEY="$(cat AuthKey_XXXXXXXXXX.p8)"
   done
   ```
   - The team ID and bundle ID default to `APPLE_TEAM_ID` and `APPLE_APP_BUNDLE_ID`.
   - TestFlight and App Store builds use the production push servers. Only builds run directly from Xcode need `APNS_USE_SANDBOX=1`.
   - The same key sends Live Activity updates for urgent announcements (API-011): topic `<bundle id>.push-type.liveactivity`,
     which a **Team Scoped (All Topics)** key already covers. Nothing to configure on the server.
4. For urgent announcements (API-011) the iOS app needs the **Time Sensitive Notifications** capability (Xcode → Signing &
   Capabilities; automatic signing adds it to the App ID). Without it urgent alerts still arrive, as normal alerts.
   Don't request **Critical Alerts**: that entitlement needs Apple's approval and Attnd doesn't use it.

### 3b. App Store Connect API key (signing and uploads from CI)
1. Go to [App Store Connect](https://appstoreconnect.apple.com) → Users and Access → **Integrations** → App Store Connect API → **Team Keys → +**.
2. Name it "GitHub CI" with access **Admin**. Admin is required so Xcode can create the signing certificate in the cloud. Download the `.p8` and note the **Key ID** and **Issuer ID**.
3. Add to GitHub:

| Where | Name | Value |
|---|---|---|
| GH-ios secret | `ASC_KEY_ID` | Key ID |
| GH-ios secret | `ASC_ISSUER_ID` | Issuer ID |
| GH-ios secret | `ASC_KEY_P8` | full contents of the `.p8` file |

### 3b-2. ✅ App Clip App ID
The App Clip (`com.arcu.Attnd.clip`) ships inside the app and now uses App Attest too. In [Identifiers](https://developer.apple.com/account/resources/identifiers/list), open **com.arcu.Attnd.clip** and tick **App Attest**. Leave the rest as is, then **Save**.

### 3c. TestFlight
1. ✅ The internal group already exists: **App Store Connect Users**, with you as a tester and TestFlight installed on your iPhone. New CI builds get added to this group.
2. Set the pipeline variables:
   ```bash
   gh variable set TESTFLIGHT_GROUP -e dev -b "App Store Connect Users" -R apbeers/Attnd-iOS
   gh variable set ATTND_SERVER -e dev -b dev.attnd.app -R apbeers/Attnd-iOS
   gh variable set ATTND_SERVER -e dev -b dev.attnd.app -R apbeers/Attnd-Android
   ```
3. Add to GitHub:

| Where | Name | Value |
|---|---|---|
| GH-ios env `dev` variable | `ATTND_SERVER` | `dev.attnd.app` |
| GH-ios env `dev` variable | `TESTFLIGHT_GROUP` | `App Store Connect Users` |
| GH-ios secret + variables (optional) | `ATTND_API_KEY` secret = attnd-dev's `API_KEY`; variables `LIVE_API_TESTS=true`, `LIVE_API_SERVER=dev.attnd.app` | turns on the live API tests in CI |
| GH-ios env `production` variable (optional) | `WHATS_NEW` | release notes for the next App Store version |

### 3d. Before the first App Store release from CI

Attnd is **already approved and live** on the App Store and Google Play, so CI ships **updates**; there's no first-time app setup. Version numbers continue upward automatically: iOS goes from `1.0.44` to `2.0.<run>`, and Android's version code goes from 223/224 to `<run>000`. Each App Store update still goes through normal App Review.

The new release also changes behaviour reviewers and users will notice:
- **iOS** now requires **iOS 26 or later**.
- **Android** now requires **Android 15 or later**, so older phones stop getting updates.
- Push notifications are new and **opt-in** (asked for when the user turns them on in Options).

- ✅ The privacy policy at `/privacy/` is rewritten to match what the apps and site actually collect. It goes live with the next production release. Set the contact email it shows:
  ```bash
  heroku config:set -a attnd-web CONTACT_EMAIL=apbeers@icloud.com
  ```
- **Update the App Store privacy answers.** In App Store Connect → Attnd → **App Privacy → Edit**:
  - **Identifiers → Device ID:** collected for *App Functionality*, **not** linked to the user's identity, **not** used for tracking. This covers the random app ID and the push token.
  - **Location:** answer **not collected**. It's sent only for a nearby-events search and isn't stored, which is how Apple defines data that isn't collected.
  - **Everything else:** not collected. That includes contact info, usage data, diagnostics and identifiers used for tracking.
- **Update the Google Play Data safety form.** Play Console → Attnd → **App content → Data safety**:
  - **Device or other IDs:** collected, not shared, used for *App functionality*, processed ephemerally = No, required.
  - **Approximate location:** the same, but optional, and processed ephemerally = Yes.
  - **Data is encrypted in transit:** Yes.
  - **Users can request data deletion:** Yes, through the contact on the privacy page.
- Optional: refresh the App Store screenshots and description to show the new layouts and push updates. Set the release notes with `gh variable set WHATS_NEW -e production -b "…" -R apbeers/Attnd-iOS` (Android: `RELEASE_NOTES` on Attnd-Android).

### 3e. Signing certificate for CI (one-time, renew yearly)
Each CI run on a fresh Mac used to create a new "Created via API" Apple Development certificate, and Apple's limit filled up (iOS run 101 failed with "maximum number of certificates"). CI now reuses one stored certificate.

1. Free the slots: [developer.apple.com → Certificates](https://developer.apple.com/account/resources/certificates/list), revoke every **Apple Development** certificate named **"Created via API"**. This doesn't affect shipped apps or TestFlight builds.
2. Create one certificate: Xcode → Settings → Accounts → your team → **Manage Certificates…** → **+** → **Apple Development**.
3. Export it: Keychain Access → My Certificates → the new "Apple Development: …" certificate (with its private key) → right-click → **Export…** → save as `attnd-dev.p12` with a strong password. Save the `.p12` and password in the password manager.
   - **If `.p12` is greyed out** you exported from *All Items*/*Certificates*, where the certificate and its key are separate rows. Use the **My Certificates** tab (login keychain), or in All Items ⌘-click the certificate *and* the key row under it, then **Export 2 items…**. If the certificate isn't in My Certificates at all, its private key isn't on this Mac: create a new one in Xcode.
4. Add both to GitHub (the second command asks for the password):
   ```
   test -s attnd-dev.p12 && base64 -i attnd-dev.p12 | gh secret set IOS_DEV_CERT_P12_BASE64 -R apbeers/Attnd-iOS
   gh secret set IOS_DEV_CERT_PASSWORD -R apbeers/Attnd-iOS
   ```
5. Delete `attnd-dev.p12` from the Mac, then re-run the iOS workflow (`gh workflow run ios.yml -R apbeers/Attnd-iOS --ref develop`).

The certificate expires after a year: repeat steps 2–5 then.

### 3f. Manual App Store signing for CI (done Nov 19, 2026; renew yearly)
Automatic signing with the App Store Connect API key couldn't sign the App Group ("Authentication failed"; the group
has since been dropped with the widget), so CI signs
with a stored **Apple Distribution** certificate and three **App Store** provisioning profiles
(`Attnd/scripts/ci/signing_profiles.py` + `archive_upload.sh`). Files on the owner's Mac:
`/Users/andrewbeers/Documents/Attnd/ios-signing` (`dist.key`, `dist.csr`, `distribution.cer`, `dist.p12`, the three
`.mobileprovision` files); keep the `.p12` password in the password manager.

| Secret (Attnd-iOS) | What |
|---|---|
| `IOS_DIST_CERT_P12_BASE64` / `IOS_DIST_CERT_PASSWORD` | Apple Distribution certificate + key (`openssl pkcs12 -export -legacy`) |
| `IOS_PROFILE_APP_B64` | App Store profile "Attnd AppStore" for `com.arcu.Attnd` |
| `IOS_PROFILE_WIDGETS_B64` | "Attnd Widgets AppStore" for `com.arcu.Attnd.widgets` |
| `IOS_PROFILE_CLIP_B64` | "Attnd Clip AppStore" for `com.arcu.Attnd.clip` |
| `IOS_PROFILE_CLIP_WIDGETS_B64` | "Attnd Clip Widgets AppStore" for `com.arcu.Attnd.clip.widgets` |

App Group `group.com.arcu.Attnd` (Nov 21, 2026): enabled on `com.arcu.Attnd` and `com.arcu.Attnd.clip` only (the
widget extensions don't use it); both entitlements files list it, so those two profiles must include it.

Set them from the folder:
```
cd ~/Documents/Attnd/ios-signing
base64 -i dist.p12 | gh secret set IOS_DIST_CERT_P12_BASE64 --repo apbeers/Attnd-iOS
gh secret set IOS_DIST_CERT_PASSWORD --repo apbeers/Attnd-iOS
base64 -i Attnd_AppStore.mobileprovision | gh secret set IOS_PROFILE_APP_B64 --repo apbeers/Attnd-iOS
base64 -i Attnd_Widgets_AppStore.mobileprovision | gh secret set IOS_PROFILE_WIDGETS_B64 --repo apbeers/Attnd-iOS
base64 -i Attnd_Clip_AppStore.mobileprovision | gh secret set IOS_PROFILE_CLIP_B64 --repo apbeers/Attnd-iOS
```

**Adding a capability later** (e.g. Sign in with Apple): enable it on the App ID in Identifiers, then each affected
profile shows "Invalid": Profiles → open it → **Edit** → **Save** → **Download**, and re-run the matching
`base64 … | gh secret set IOS_PROFILE_…` line. CI fails with the capability's name if a profile lacks one.

**Renewal (yearly, the certificate expires Nov 2027; CI warns 30 days ahead):** new CSR + certificate
(`openssl req -new -newkey rsa:2048 -nodes -keyout dist.key -out dist.csr -subj "/CN=Attnd Distribution"`, upload at
Certificates → **+** → Apple Distribution, then `openssl x509 -inform der -in distribution.cer -out dist.pem` and
`openssl pkcs12 -export -legacy -inkey dist.key -in dist.pem -out dist.p12`), edit the three profiles to use the new
certificate and download them, then set all five secrets again.

## 4. Google / Android (40 min)

### 4a. Firebase (push notifications)
1. ✅ The Firebase project **Attnd** already exists (Blaze plan) with the **Attnd - Android** app (`com.arcu.attnd`). Reuse it. Its old keys get rotated in step 4 below.
2. ✅ Both Android apps are in Firebase. Add the debug app: **Project Overview → + Add app → Android**, package `com.arcu.attnd.debug`, nickname "Attnd - Android (debug)". Skip the SDK steps, because the build already supports Firebase. Then download the updated `google-services.json` from **Project settings → General**; it now lists both apps. Store it:
   ```bash
   gh secret set GOOGLE_SERVICES_JSON -R apbeers/Attnd-Android < /Users/andrewbeers/Downloads/google-services.json
   ```
3. ✅ Put the file at `Attnd-Android/app/google-services.json` for local builds. It's gitignored.
4. **Give the server one Google key that covers both Android push and the Play Integrity device check.**
   1. ✅ **Check that Play Integrity uses this project.** It's linked to the **Attnd** Cloud project (number 271167704653), and all 7 integrity responses are turned on. The server ignores the extra ones. Go to Play Console → Attnd → **Protected with Play** (the shield icon). On the **Play Integrity API** row, click **Manage**. The linked Google Cloud project shown there should be the Firebase **Attnd** project. If it's a different project, or none, link the Attnd project.
      - **Automatic protection** (1 of 1 active) is fine to leave on. It blocks unofficial installs, which fits the server's rules.
      - The other optional Play Integrity services, such as the app access risk and Play Protect verdicts, aren't used by the server. You don't need to turn them on.
   2. ✅ **Turn on the two APIs.** Both are enabled. Open each link, choose the **Attnd** project at the top, and click **Enable**. If it says *Manage*, the API is already on.
      - https://console.cloud.google.com/apis/library/playintegrity.googleapis.com
      - https://console.cloud.google.com/apis/library/fcm.googleapis.com
   3. **Create a new key.** Firebase → **Project settings → Service accounts → Generate new private key**. This downloads `attnd-…-firebase-adminsdk-….json` to Downloads. The Firebase admin account can send pushes, and because it belongs to the linked project it can also decode Play Integrity tokens.
   4. ✅ **Find the Project ID.** It's `attnd-df28e`. It's on Firebase → **Project settings → General → Project ID**.
   5. ✅ **Put the key on both Heroku apps.** Done and verified: `FCM_PROJECT_ID` and `GOOGLE_SERVICE_ACCOUNT_JSON` are set, and both releases succeeded. Swap in your Project ID:
      ```bash
      heroku config:set -a attnd-web FCM_PROJECT_ID=attnd-df28e GOOGLE_SERVICE_ACCOUNT_JSON="$(cat /Users/andrewbeers/Downloads/attnd-df28e-firebase-adminsdk-fbsvc-2af30fbbb4.json)"
      heroku config:set -a attnd-dev FCM_PROJECT_ID=attnd-df28e GOOGLE_SERVICE_ACCOUNT_JSON="$(cat /Users/andrewbeers/Downloads/attnd-df28e-firebase-adminsdk-fbsvc-2af30fbbb4.json)"
      ```
   6. **Rotate out the old keys.** Go to https://console.cloud.google.com/iam-admin/serviceaccounts (Attnd project) → the **firebase-adminsdk** account → **Keys**. Delete every key except the one created today. Do the same on any other service account that has old keys you don't use.
   7. Back up the JSON in your password manager, then delete it:
      ```bash
      rm /Users/andrewbeers/Downloads/attnd-df28e-firebase-adminsdk-fbsvc-2af30fbbb4.json
      ```

### 4b. Google Play publishing from CI
1. In [Google Cloud console](https://console.cloud.google.com), using the same project, enable the **Google Play Android Developer API**.
2. ✅ Service account **github-play-publisher** created in the Attnd project, with no roles (Play access is granted in Play Console), and its JSON key downloaded. Store it in GitHub, then remove the file:
   ```bash
   gh secret set GOOGLE_PLAY_SERVICE_ACCOUNT_JSON -R apbeers/Attnd-Android < /Users/andrewbeers/Downloads/attnd-df28e-51b6a4e6e951.json
   rm /Users/andrewbeers/Downloads/attnd-df28e-51b6a4e6e951.json
   ```
3. ✅ In **Play Console → Users and permissions → Invite new users**, enter that service account's email. Under App permissions → Attnd, grant **Release apps to testing tracks**, **Release to production**, and **Manage testing tracks**.
4. ✅ In Play Console → Attnd → **Testing → Internal testing → Testers**, add yourself, then open the **opt-in link** on your Android phone once.

### 4c. Signing fingerprints and the Maps key

Android apps are identified by their package name **plus the certificate they're signed with**. Two different certificates matter:
- **Upload key:** your `.jks`. CI signs bundles with it before uploading.
- **App signing key:** Google holds this one and re-signs every build Play delivers to phones. It's what's on users' devices.

Three things need these fingerprints.

**1. Look up the fingerprints.** Play Console → Attnd → **Protected with Play** (**Test and release → App integrity** opens the same page). Expand the **Play Store protection** row with its ▾ arrow, then open **Play App Signing** (*Manage* / *Settings*). That page lists the app signing key and upload key certificates with their fingerprints. Note four values:
- **App signing key certificate:** SHA-256 and SHA-1
- **Upload key certificate:** SHA-1 (and SHA-256, for reference)

**2. ✅ App links and the signing-certificate pin (server).** Put the **App signing key SHA-256** on both Heroku apps:
```bash
heroku config:set -a attnd-web ANDROID_SHA256_FINGERPRINT=AA:BB:CC:...
heroku config:set -a attnd-dev ANDROID_SHA256_FINGERPRINT=AA:BB:CC:...
```
This one value does two jobs:
- `https://attnd.app/.well-known/assetlinks.json` lists it, so `attnd.app/join/<code>` links open the app directly instead of the browser.
- The device check rejects any Android app not signed with this certificate, even if its package name matches.

Check it afterwards: `curl -s https://attnd.app/.well-known/assetlinks.json` should show your fingerprint.

**3. ✅ Restrict the Maps API key.** It's restricted to Android apps (`com.arcu.attnd` ×2 and `com.arcu.attnd.debug`) and the Maps SDKs. Right now anyone who extracts the key from the app could use it and bill your account. Restricting it means only your signed app can use it.
1. Find the key: [Cloud console → APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials). Its value matches the `MAPS_API_KEY` in your `local.properties`. Check the project switcher at the top, because it may be in an older project.
2. Make sure **Maps SDK for Android** is enabled in that project (APIs & Services → Library).
3. Open the key:
   - **Application restrictions → Android apps → Add**, one row per signing certificate:

     | Package | Fingerprint | Used by | Where to get the SHA-1 |
     |---|---|---|---|
     | `com.arcu.attnd` | **App signing key** SHA-1 | Everything installed from Google Play, including internal testing | Play Console → **Protected with Play** → ▾ **Play Store protection** → **Play App Signing** → *App signing key certificate* → **SHA-1 certificate fingerprint** |
     | `com.arcu.attnd` | **Upload key** SHA-1 | Release builds signed with your `.jks` and installed directly (not through Play) | The same Play page → *Upload key certificate* → **SHA-1**. Or from the keystore (it asks for the store password): `keytool -list -v -keystore /path/to/upload.jks -alias YOUR_KEY_ALIAS \| grep SHA1` |
     | `com.arcu.attnd.debug` | **Debug** SHA-1 | Builds run from Android Studio on your Mac | `keytool -list -v -keystore ~/.android/debug.keystore -alias androiddebugkey -storepass android -keypass android \| grep SHA1`. Android Studio creates this keystore on the first debug build. |

     If `keytool` says *"Unable to locate a Java Runtime"*, your Mac has no system Java. Use the copy bundled with Android Studio instead:
     ```bash
     "/Applications/Android Studio.app/Contents/jbr/Contents/Home/bin/keytool" -list -v -keystore ~/.android/debug.keystore -alias androiddebugkey -storepass android -keypass android | grep SHA1
     ```

     A shortcut for the last two: in the Attnd-Android folder, run `./gradlew signingReport`. It prints the SHA-1 for the debug build, and for the release build when `signing.properties` is present.

     The debug SHA-1 is specific to each Mac. If you build on another computer, add that computer's debug SHA-1 as another `com.arcu.attnd.debug` row.
   - **API restrictions → Restrict key**: Maps SDK for Android (`maps-android-backend`, `mapsandroid`), **Routes API** (`routes`: the map's walking routes) and the two services the key was already being used for, `firebaseinstallations` and `geocoding-backend` (Google refuses to drop a service with recent usage).
   - **Save**. It can take a few minutes to apply.
   - ✅ Done Nov 15, 2026 (key "Android Map API Key" `af7cec96-…`, project `attnd-df28e`; Routes API enabled). The same change from the command line:
     ```bash
     gcloud services api-keys update af7cec96-8ebf-4b57-be9b-176e0efe7896 --project=attnd-df28e --api-target=service=maps-android-backend.googleapis.com --api-target=service=mapsandroid.googleapis.com --api-target=service=routes.googleapis.com --api-target=service=firebaseinstallations.googleapis.com --api-target=service=geocoding-backend.googleapis.com
     ```
     (`--api-target` replaces the whole list; the Android app restrictions stay as they are.)
4. Test: open the map in the next internal-testing build. A grey map with no streets means a fingerprint or package is wrong. Tap a pin within 8 km: a solid route line means the Routes API works; a dashed straight line ("about 8 min walk") means the request was refused (check the key's API list).

**4. ✅ Keystore backup (required).** The upload keystore secrets are already in GitHub and work: CI built a signed bundle. GitHub won't give them back, though, so keep your own copy. This command finds your `signing.properties` with Spotlight and copies one bundle to the clipboard: the passwords and alias, plus the keystore file as text. Paste it into a new secure note in your password manager:
```bash
P=$(mdfind -name signing.properties | grep -i attnd-android | head -1); S=$(grep '^STORE_FILE=' "$P" | cut -d= -f2-); case "$S" in /*) J="$S";; *) J="$(dirname "$P")/$S";; esac; { echo "Attnd Android upload keystore - backed up $(date +%F)"; echo; cat "$P"; echo; echo "Keystore file: $(basename "$J")"; echo "To restore: copy everything below this line, then run: pbpaste | base64 -D > upload.jks"; base64 -i "$J"; } | pbcopy && echo "Copied $(basename "$J") + signing.properties - paste into a secure note"
```
If it prints nothing useful, Spotlight didn't find the file. Run it from your Attnd-Android folder with `P=signing.properties` in place of the `mdfind …` part.

| Where | Name | Value |
|---|---|---|
| GH-android secret | `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` | publisher service-account JSON (4b) |
| GH-android secret | `ANDROID_KEYSTORE_BASE64` | `base64 -i upload.jks` |
| GH-android secret | `ANDROID_KEYSTORE_PASSWORD` / `ANDROID_KEY_ALIAS` / `ANDROID_KEY_PASSWORD` | from `signing.properties` |
| GH-android secret | `MAPS_API_KEY` | Maps key |
| GH-android secret | `GOOGLE_SERVICES_JSON` | contents of `google-services.json` |
| GH-android secret + variables (optional) | `ATTND_API_KEY` secret = attnd-dev's `API_KEY`; variables `LIVE_API_TESTS=true`, `LIVE_API_SERVER=dev.attnd.app` | turns on the live API tests in CI |
| GH-android env `dev` variable | `ATTND_SERVER` | `dev.attnd.app` |
| GH-android env `production` variable (optional) | `PLAY_ROLLOUT` | e.g. `0.2` for a 20% staged rollout (default: 100%) |
| GH-android env `production` variable (optional) | `RELEASE_NOTES` | text shown in Play |

Note: the app now requires **Android 15 or later**. Play will stop offering updates to phones on older versions.

### 4e. Phones only, no tablets (Play Console, 5 min)
The Android app supports phones and foldables, not tablets, so turn tablets off in Play Console. The app's own settings can't hide it from tablets without also hiding it from unfolded foldables.
1. Play Console → Attnd → **Reach and devices → Device catalog** (older consoles: **Release → Device catalog**).
2. Click **Manage exclusion rules** (or filter the catalog by **Form factor: Tablet**) and exclude **Tablet**. Leave **Phone** included. Also exclude Chromebook, TV, Wear, Car and XR if they're listed.
3. **Release → Advanced settings:** leave **Form factors → Android XR** as it is. Headsets show the phone app as a flat window, and the XR settings have no simple off switch. Leave **Inline Installs** off; it only matters for apps that promote other apps.
4. Search the catalog for a foldable, for example `Pixel Fold` or `Galaxy Z Fold`, and check it still says **Supported**.

Existing installs on excluded tablets keep working but stop getting updates. If you want screenshots to match, remove tablet screenshots from **Store presence → Main store listing**.

## 4d. Lock the production API to genuine apps (after the first release)

The server now only issues API access to:
- **iPhone:** the official Attnd app (App Store or TestFlight), verified by Apple App Attest on a genuine iPhone.
- **Android:** the official Attnd app, unmodified as Google Play distributed it, installed from Google Play, signed with our key, on a device that passes Play Integrity (not rooted, not an emulator, not tampered with).

The static API key is the remaining way around these checks, so turn it off on production **once the new app versions are live in both stores**:
```bash
heroku config:set -a attnd-web STATIC_API_KEY_ENABLED=0
```
Keep it on for `attnd-dev`: simulators, Xcode/Android Studio builds and CI need it there.

✅ The Android signing-certificate pin is already done. It's the same `ANDROID_SHA256_FINGERPRINT` you set in 4c step 2, and the server uses that one value for app links and for the pin.

Limits to know about:
- App Attest proves the app is genuine and unmodified, and that it runs on real Apple hardware. It isn't a full jailbreak detector.
- Play Integrity's device check is stronger.
- Someone with a genuine device could still copy a short-lived access token (valid up to 24 hours) and replay it. This stops scraping by bots, modified apps and emulators. It does not stop a determined person using a real phone.

## 5. Local development keys

| App | File (gitignored) | Contents |
|---|---|---|
| iOS | `Attnd-iOS/Config/Secrets.xcconfig` | `ATTND_API_KEY = <attnd-dev API_KEY>` and `ATTND_SERVER = dev.attnd.app` |
| Android | `Attnd-Android/local.properties` | `ATTND_API_KEY=…`, `MAPS_API_KEY=…`, `ATTND_SERVER=dev.attnd.app` |
| Web | your shell | `DEBUG=1 API_KEY=dev-key uv run python manage.py runserver` |

**Web needs Docker** for its database: the local server and tests use PostgreSQL 18 (the same as Heroku and CI), started by a script. One-time install on your Mac, then start it:
```bash
brew install --cask docker
open -a Docker
cd Attnd-Web && scripts/dev_db.sh
```

## 6. Check it all works

1. Push anything to `develop` in each repo, or re-run the latest workflow from the Actions tab.
2. Watch **Actions**. The order is web → Heroku dev, iOS → TestFlight, Android → Play internal.
3. On your phone:
   - **iPhone:** TestFlight → Attnd → Install. The build is usually ready about 10–20 minutes after the run.
   - **Android:** open Play from the internal testing opt-in link, or Play Store → Attnd → update. Allow a few minutes after the run.
4. To release, merge `develop` → `main`. Web deploys to production, iOS is submitted to App Review, and Android goes to Play production.

### How the pipelines work (reference)

| Branch | Web | iOS | Android |
|---|---|---|---|
| push to `develop`/`main`, or a PR | tests (Postgres 18) | build + unit tests | unit tests, lint, debug build |
| `develop` | Heroku dev | TestFlight "Internal", using the dev server | Play internal testing, using the dev server |
| `main` | Heroku production | App Review → auto-release | Play production |

- Version numbers come from the run number, so you never bump them by hand.
  - iOS: `VERSION` file + run number, e.g. `2.0.57`.
  - Android: `2.0.<run>`, with version code `<run>000`.
- The pipelines use only GitHub's own actions and the official Apple and Google tools and APIs:
  - `scripts/ci/heroku_deploy.sh` (web)
  - `scripts/ci/asc.py` and `archive_upload.sh` (iOS)
  - `scripts/ci/play_publish.py` (Android)
- Dependencies:
  - Python: **Upgrade dependencies** runs every Monday (or run it by hand) and opens a PR into `develop`.
  - GitHub Actions and Gradle: Dependabot handles these.

---

## 7. App Store link and update prompt
**App Store link** (required). `/join/<code>/` links and the iPhone update screen use it. Until it's set, iPhones opening a join link get the landing page instead of the App Store. ✅ Set on attnd-dev and attnd-web (Nov 15, 2026).
```bash
heroku config:set IOS_APP_STORE_URL=https://apps.apple.com/app/id1470627406 -a attnd-web
```

**Ask users to update** (API-003, optional, any time). Apps check on launch and on every return to the foreground:
```bash
heroku config:set APP_LATEST_VERSION_IOS=2.0.90 APP_LATEST_VERSION_ANDROID=2.0.40 -a attnd-web
```
That shows a dismissible "new version available" banner to older builds. To force older builds onto a blocking "Update Attnd" screen instead:
```bash
heroku config:set APP_MIN_VERSION_IOS=2.0.90 APP_MIN_VERSION_ANDROID=2.0.40 -a attnd-web
```
Optional custom text: `heroku config:set APP_UPDATE_MESSAGE="…" -a attnd-web`. Undo any of these with `heroku config:unset NAME -a attnd-web`. Version numbers are the ones TestFlight and Play show (iOS `2.0.<run>`, Android `2.0.<run>`, which differ per platform).

**Retire an old API version** (only when `heroku run -a attnd-web python manage.py api_usage` shows nobody uses it):
```bash
heroku config:set SUPPORTED_API_VERSIONS=2 -a attnd-web
```
Builds that still use the retired version are then shown the update screen.

## 8. Google Maps on the website (address autocomplete, pin map)
✅ Browser key "Attnd web (browser)" in `attnd-df28e` (Maps JavaScript API + Places API (New)) is set as `GOOGLE_MAPS_BROWSER_KEY` on attnd-dev and, since the first web release (Nov 15, 2026), on attnd-web. ✅ Referrers updated Nov 15, 2026: attnd.app, www.attnd.app, dev.attnd.app and the dev app's Heroku host.

The console uses Google Maps for address suggestions and a draggable pin when `GOOGLE_MAPS_BROWSER_KEY` is set; without it, it keeps the plain address field and the OpenStreetMap pin map. This must be a **new browser key**, restricted to our sites: browser keys are visible to anyone, so never reuse the server or Android keys. Create it (Maps JavaScript API + Places API (New), referrer-restricted) and set it on dev:
```bash
gcloud services enable maps-backend.googleapis.com places.googleapis.com --project attnd-df28e
gcloud services api-keys create --project attnd-df28e --display-name "Attnd web (browser)" --allowed-referrers "https://attnd.app/*,https://www.attnd.app/*,https://dev.attnd.app/*" --api-target service=maps-backend.googleapis.com --api-target service=places.googleapis.com
gcloud services api-keys list --project attnd-df28e --filter "displayName='Attnd web (browser)'" --format "value(name)"
heroku config:set -a attnd-dev GOOGLE_MAPS_BROWSER_KEY="$(gcloud services api-keys get-key-string NAME_FROM_PREVIOUS_LINE --project attnd-df28e --format 'value(keyString)')"
```
Optional: a Map ID (Google Cloud console → Map Management) in `GOOGLE_MAPS_MAP_ID`; the default `DEMO_MAP_ID` is fine for dev.

## 9. Demo event
A fully populated event that is happening now, with usage history, AI updates and a team. Load or refresh it (same code, same id, so phones stay joined):
```bash
heroku run -a attnd-dev python manage.py load_demo_event --owner apbeers@icloud.com
```
Its tags (API-010) are hand-written; other events get theirs from the AI import. To tag events made before API-010
(safe to re-run; skips anything with 2+ tags and anything whose tags were edited by hand):
```bash
heroku run -a attnd-dev python manage.py backfill_tags --all --dry-run
heroku run -a attnd-dev python manage.py backfill_tags --all
```

## 10. Join links and the App Clip card
One link and QR code per event, `https://attnd.app/join/<CODE>/`, for every device (`join/views.py`):
- **iPhone with the app:** iOS opens the app (Universal Links: `/.well-known/apple-app-site-association` claims only `/join/*`).
- **iPhone without the app:** the Camera shows the App Clip card. In Safari the page shows the App Clip banner.
- **Android:** App Links open the app; otherwise Google Play with the code as install referrer.
- **Computers:** an event page with a QR code.
Opens are counted per event (iPhone / Android / computer) on the Usage pages.

One-time setup so iPhones show the App Clip card when they scan a code (App Store Connect → Attnd → the version page → **App Clip**):
1. **Default App Clip Experience:** header image 3000×2000 PNG (a purple Attnd image), subtitle "Map, schedule and live updates", action **Open**.
2. **Advanced App Clip Experience** (App Clip section → Advanced experiences → +): URL `https://attnd.app/join/`, action **Open**, and turn on "this URL prefix covers all URLs below it" so every event code uses it. Business/location fields are optional.
3. Apple verifies the domain from the association file on attnd.app, so this works after the web release to production.

## 11. Email (Mailgun, DNS at Namecheap)
⏳ In progress (see the progress table). Emails the server sends: team invites and "AI updates waiting for review" (both skipped silently until this is done); planned: password reset and error alerts to admins. The code fixes this needs (TLS on port 587, a per-environment site URL for links, `DEFAULT_FROM_EMAIL`, a dev allow-list so dev never emails real people) are listed in `ROADMAP.md` and get done together with this step.

Sending domain: **`mg.attnd.app`** (a subdomain keeps the main domain's reputation separate). From address: `Attnd <noreply@mg.attnd.app>`.

**1. ✅ Mailgun accounts through Heroku** (done Nov 14, 2026 with the Platform API; Starter is free). Mailgun add-ons can't be attached to a second app, so each app has its own account. Production uses `mg.attnd.app`; dev keeps its sandbox domain (only authorized recipients receive mail, so dev can't email real people):
```
heroku addons:create mailgun:starter -a attnd-web
heroku addons:create mailgun:starter -a attnd-dev
``` The add-on sets `MAILGUN_SMTP_LOGIN`, `MAILGUN_SMTP_PASSWORD`, `MAILGUN_DOMAIN` (and API keys) on the apps. Until a custom domain is verified, they point at a Mailgun **sandbox** domain that can only email addresses you authorize.

**2. Add the domain in Mailgun:** `heroku addons:open mailgun -a attnd-web` → **Sending → Domains → Add new domain** → `mg.attnd.app`, US region. Mailgun then lists the DNS records to add. Turn **click/open tracking off** (Domain settings → Tracking): it rewrites links in transactional email and needs an extra CNAME.

**3. Add the records at Namecheap:** Namecheap → Domain List → attnd.app → **Manage** → **Advanced DNS** → **Add New Record**. In Namecheap's **Host** field, type only the part *before* `attnd.app` (Namecheap appends the domain; entering `mg.attnd.app` would create `mg.attnd.app.attnd.app`). Copy each **Value** exactly from Mailgun; TTL Automatic.

| Type | Host (Namecheap) | Value |
|---|---|---|
| TXT | `mg` | SPF from Mailgun, e.g. `v=spf1 include:mailgun.org ~all` |
| TXT | the DKIM name Mailgun shows, minus `.attnd.app` (e.g. `pic._domainkey.mg`) | the long `k=rsa; p=…` value from Mailgun |
| TXT | `_dmarc` | `v=DMARC1; p=none; rua=mailto:YOUR_EMAIL` (monitor first; tighten to `p=quarantine` once mail flows cleanly) |

| MX | `mg` | `mxa.mailgun.org`, priority 10 |
| MX | `mg` | `mxb.mailgun.org`, priority 10 |

- **The MX records are required**, not optional: Mailgun sends from a bounce address at `mg.attnd.app`, and iCloud (and others) reject mail whose sender domain has no MX ("450 4.1.8 Sender address rejected: Domain not found", seen Nov 14, 2026). At Namecheap, MX rows can only be added when **Mail Settings** (in Advanced DNS) is **Custom MX**. If it is set to *Email Forwarding* for attnd.app today, switching turns that forwarding off: then also add MX rows for host `@` pointing at Namecheap's forwarding servers (as listed on Namecheap's email-forwarding help page) or whatever receives attnd.app mail, so mail to attnd.app addresses keeps working.
- If attnd.app already has an SPF TXT on the root (`@`), leave it; this one is on `mg` only.

**4. Verify:** back in Mailgun → the domain → **Verify DNS settings** (DNS can take minutes to a few hours). When it shows verified, use the SMTP credentials for `mg.attnd.app` (Domain settings → **SMTP credentials**, login `postmaster@mg.attnd.app`; reset the password to see it) on both apps, so they stop using the sandbox:
```
heroku config:set -a attnd-web MAILGUN_SMTP_LOGIN=postmaster@mg.attnd.app MAILGUN_DOMAIN=mg.attnd.app
heroku config:set -a attnd-dev MAILGUN_SMTP_LOGIN=postmaster@mg.attnd.app MAILGUN_DOMAIN=mg.attnd.app
heroku config:set -a attnd-web MAILGUN_SMTP_PASSWORD="$(pbpaste)"
heroku config:set -a attnd-dev MAILGUN_SMTP_PASSWORD="$(pbpaste)"
```
(Copy the SMTP password from Mailgun first, then run the last two lines; save it in the password manager too.)

**5. Test** (after the code fixes are deployed): `heroku run python manage.py sendtestemail YOUR_EMAIL -a attnd-dev`. Check it arrives in the inbox, not spam, and that Gmail's "Show original" says SPF, DKIM and DMARC **PASS**.

### 11b. Email security (done Nov 14, 2026)
Live DNS (Namecheap, DNSSEC on), checked through dns.google:

| Record | Value | Why |
|---|---|---|
| TXT `_dmarc` | `v=DMARC1; p=reject; rua=mailto:<owner>` | Receivers **reject** mail claiming to be `@attnd.app` / `@mg.attnd.app` that fails SPF/DKIM. Daily reports go to the owner. |
| TXT `@` | `v=spf1 -all` | The main domain never sends mail (alongside `google-site-verification`). |
| TXT `mg` | `v=spf1 include:mailgun.org ~all` | Only Mailgun sends for `mg.attnd.app`. |
| TXT `k2026._domainkey.mg` | 2048-bit RSA key | Active Mailgun DKIM key (selector `k2026`). The old 1024-bit `mailo` key is deactivated in Mailgun and its DNS record deleted. |
| MX `mg` | `mxa`/`mxb.mailgun.org`, priority 10 | Bounces; iCloud rejects senders without MX. |
| CAA `@` | `0 issue "letsencrypt.org"` | Only Let's Encrypt may issue HTTPS certificates (Heroku ACM uses it for attnd.app and www). |

> **Adding any service that sends email as `@attnd.app` or a subdomain** (Google Workspace, Stripe receipts from your domain, a newsletter tool, another app): set up its SPF and DKIM records *before* it sends. With `p=reject`, its mail is otherwise refused from the first message. Hosting must also stay on Let's Encrypt (CAA) unless the CAA record is updated first.

## 12. Admin sign-in security (5 min, after the next deploy)

The console and admin now have sign-in rate limits, two-factor sign-in for staff/superusers, a Content Security Policy and a moved admin (CLAUDE.md → Security). Nothing is needed for them to work; these steps finish the setup.

1. **Pick a private admin path** (optional; the default is `/manage-attnd/`, and `/admin/` is a 404):
   ```
   heroku config:set -a attnd-dev ADMIN_URL_PATH=<something-private>/
   heroku config:set -a attnd-web ADMIN_URL_PATH=<something-private>/
   ```
2. **Secure each admin account with a passkey (or an authenticator app).** Sign in with your password at `/accounts/login/` on dev (and on production after the release). You are sent to "Secure your admin account": choose **Add a passkey** (Face ID / Touch ID / your password manager), or scan the QR code with an authenticator app (iOS Passwords, 1Password, Google Authenticator …) and enter the code. Then **save the 10 recovery codes** in your password manager. Every staff/superuser account does this at its next sign-in. Afterwards "Sign in with a passkey" on the sign-in page (or the email field's autofill) signs you in with no password or code.
   - **Passkeys are per domain.** A passkey made on the dev app (`dev.attnd.app`) only works there; production (`attnd.app`) needs its own, made after you sign in there. Your password manager will list two "Attnd" passkeys, one per domain. The domain comes from `SITE_URL` (dev sets `https://dev.attnd.app`); `WEBAUTHN_RP_ID` overrides it. Don't change either on production later: existing passkeys would stop working (sign in with the password + authenticator/recovery code, then add a new passkey).
   - Add a second passkey or the authenticator app as a backup from **Account** (`/console/account/`), so losing one device doesn't mean using recovery codes.
3. **Locked out?** (lost passkeys/phone and recovery codes; removes every second factor):
   ```
   heroku run -a attnd-web python manage.py reset_2fa you@example.com
   ```
   Then sign in again and set it up anew.
4. **CSP on production** (optional): it enforces by default. To watch first without blocking anything, `heroku config:set -a attnd-web CSP_MODE=report`, check the logs for `CSP violation` lines (`heroku logs -a attnd-web | grep CSP`), then `heroku config:unset -a attnd-web CSP_MODE`.

## 13. CI keys for the live integration tests (5 min)

Each repo's pipeline runs live integration tests: **Integration · dev** against the dev server (its static `API_KEY`) and
**Integration · prod** against attnd.app. Production gets a **CI key** instead of the static key (API-008): it still works
after 4d turns the static key off, but only for the DEMO event (lookup, nearby, its bundle/join/leave/usage, `ci-` devices).
Usage it posts is discarded and `ci-` devices never appear in audience numbers. Until the API-008 code is on `main` and the
key is set, **Integration · prod** fails, as a warning only: it never blocks a deploy or release.

1. Generate one key and send it straight to Heroku production and to the three repos (it's never shown or saved).
   Setting it on `attnd-web` is a production config change:
   ```bash
   CI_KEY="$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")"
   heroku config:set -a attnd-web CI_API_KEYS="$CI_KEY"
   for r in Attnd-Web Attnd-iOS Attnd-Android; do printf %s "$CI_KEY" | gh secret set ATTND_PROD_CI_KEY -R apbeers/$r; done
   unset CI_KEY
   ```
2. The web repo's dev integration job uses the dev app's static key:
   ```bash
   heroku config:get API_KEY -a attnd-dev | gh secret set ATTND_DEV_API_KEY -R apbeers/Attnd-Web
   ```
3. Check: the next push to `develop` shows **Integration · dev** green after the deploy. **Integration · prod** turns green
   once production runs the API-008 code.

To rotate: run step 1 again (the old key stops working the moment `CI_API_KEYS` changes). Several keys can be set at once,
comma-separated, to rotate without a gap. Keys shorter than 32 characters are ignored.

## 14. Diagnostics: iOS symbolication token (5 min)

The staff **Diagnostics** panel (`/console/diagnostics/`, API-013) symbolicates iOS crash reports by dispatching the
`symbolicate.yml` workflow in **Attnd-iOS** (it downloads the build's dSYM artifact, runs `atos` on a Mac runner and
posts the symbols back). The server needs a GitHub token that may only start that workflow.

1. GitHub → **Settings → Developer settings → Fine-grained personal access tokens → Generate new token**:
   name "Attnd symbolicate", expiry 1 year, **Repository access: Only select repositories → Attnd-iOS**.
   Under **Permissions** (Repositories tab) press **+ Add permissions**, search **Actions**, tick it, close the menu,
   then set its access to **Read and write**. Nothing else (Metadata: read appears by itself). **Generate token** and
   copy it into the password manager.
2. Set it on both apps (the second line is a production config change):
   ```bash
   heroku config:set -a attnd-dev GITHUB_SYMBOLICATE_TOKEN=github_pat_PASTE_HERE
   heroku config:set -a attnd-web GITHUB_SYMBOLICATE_TOKEN=github_pat_PASTE_HERE
   ```
3. Check: open an iOS crash report in the panel and press **Symbolicate**; the Attnd-iOS **Actions** tab shows a
   "Symbolicate" run, and the report shows symbols a few minutes later. The workflow file must be on Attnd-iOS's
   `main` branch (GitHub only dispatches workflows from the default branch's file list).

Optional settings (defaults are fine): `GITHUB_SYMBOLICATE_REPO` (`apbeers/Attnd-iOS`), `GITHUB_SYMBOLICATE_REF`
(`main`), `DIAGNOSTICS_MAX_MB` (250), `DIAGNOSTICS_DEV_DAYS` (7), `DIAGNOSTICS_DEBUG_DAYS` (14). Android needs no
token: its CI uploads R8 mappings with the CI key / dev static key (`POST /api/diagnostics/symbols/`).

To rotate or if leaked: revoke it on the same GitHub page, make a new one and run step 2 again.

## 15. Attnd Studio: the sites domain (15 min, optional until real customers)

Attnd Studio (admins only, `docs/specs/studio_spec.md`) hosts event websites at `https://<SITES_HOST>/<slug>/`.
They must live on a **different registrable domain** from attnd.app (never `sites.attnd.app`): the sites run the
organizers' own JavaScript, and a separate site keeps it away from the console's cookies.

**Today, nothing to do:** without `SITES_HOST`, production serves sites at `attnd-web-7a02b020850d.herokuapp.com` and
dev at `attnd-dev-7ab893f37020.herokuapp.com` (herokuapp.com is on the Public Suffix List, so each is its own site).
On those hostnames the app serves only the sites (console, admin and API are 404 there).

**When you buy a domain** (e.g. at Namecheap; something short like `attnd.site` or `attndsites.com`):
1. Add it to the production app and point DNS at Heroku (the command prints the DNS target; use a CNAME/ALIAS at
   Namecheap), then turn on the certificate:
   ```bash
   heroku domains:add attndsites.com -a attnd-web
   heroku certs:auto:enable -a attnd-web
   ```
2. Once `heroku domains -a attnd-web` shows the certificate, switch Studio to it (a production config change):
   ```bash
   heroku config:set -a attnd-web SITES_HOST=attndsites.com
   ```
   Published sites move to the new address (the apps get the new `website_url` with the next refresh). Optionally do the
   same for dev with a second domain or a subdomain of it (`dev.attndsites.com`, `-a attnd-dev`).

**Settings:** `ANTHROPIC_API_KEY` (set on dev and production, Nov 21, 2026: Studio uses Claude, `claude-sonnet-5-5` (`STUDIO_ANTHROPIC_MODEL`); otherwise OpenAI `gpt-5.5`
with `gpt-5` as fallback), `STUDIO_DAILY_SPEND_CAP_USD` (default 5). Studio is for admins only (staff and
superusers; Nov 19, 2026): `STUDIO_BETA_EMAILS` (default empty) is only an explicit override, and the old **Studio
beta** invite page is gone.
```bash
heroku config:set -a attnd-dev ANTHROPIC_API_KEY=sk-ant-PASTE_HERE
```

## 16. Tickets: Stripe, Apple Wallet, Google Wallet (⏸ parked on git branch `parked/tickets`)

Ticketing was moved off `develop` on Nov 19, 2026 (owner: organizers use Eventbrite for now). The code, its spec and
the full setup steps (Stripe account + Connect in the LLC's name, webhooks, Apple Wallet PEMs, Google Wallet issuer and
service account, policies before selling) are on the git branch `parked/tickets` (this file's step 16 there). Nothing
here is needed until it comes back. The Pass Type ID `pass.com.arcu.attnd.tickets` and its certificate (Nov 15, 2026)
and the Google Wallet issuer profile still exist; renew or delete them as you like (docs/LLC_MIGRATION.md).

**Optional clean-up on dev:** attnd-dev's database still has the orphan `tickets_*` tables (and `django_migrations`
rows for `tickets`) from when the app was installed. They do no harm. To drop them (dev only, never production):
```bash
heroku pg:psql -a attnd-dev -c "DO \$\$ DECLARE t text; BEGIN FOR t IN SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'tickets\_%' LOOP EXECUTE 'DROP TABLE IF EXISTS ' || quote_ident(t) || ' CASCADE'; END LOOP; END \$\$; DELETE FROM django_migrations WHERE app='tickets';"
```
The TIXDEMO event on dev, if it is still there, can be deleted in the console like any event.

## Key reference

For every key: where to create it, where it goes, and what to do if it's lost. Save each one to your password manager first, then put it where it's used, then delete any downloaded file.

### How to store a key in each place

### GitHub from the command line (`gh`)

Run `gh auth login` once. After that, every GitHub setting in this guide can be set from Terminal. `gh secret set` reads the value from a pipe, so keys go straight from Heroku (or a file) into GitHub without being shown or saved.

**One-time GitHub setup.** Paste the whole block:
```bash
for r in Attnd-Web Attnd-iOS Attnd-Android; do gh api -X PUT repos/apbeers/$r/environments/dev >/dev/null; gh api -X PUT repos/apbeers/$r/environments/production >/dev/null; done
heroku authorizations:create -d "GitHub Actions" -S | gh secret set HEROKU_API_KEY -R apbeers/Attnd-Web
gh variable set HEROKU_APP -e dev -b attnd-dev -R apbeers/Attnd-Web
gh variable set HEROKU_APP -e production -b attnd-web -R apbeers/Attnd-Web
heroku config:get API_KEY -a attnd-dev | gh secret set ATTND_API_KEY -R apbeers/Attnd-iOS
heroku config:get API_KEY -a attnd-dev | gh secret set ATTND_API_KEY -R apbeers/Attnd-Android
gh variable set ATTND_SERVER -e dev -b dev.attnd.app -R apbeers/Attnd-iOS
gh variable set ATTND_SERVER -e dev -b dev.attnd.app -R apbeers/Attnd-Android
gh variable set TESTFLIGHT_GROUP -e dev -b "App Store Connect Users" -R apbeers/Attnd-iOS
```

**Optional:** run the live API tests against dev in CI:
```bash
for r in Attnd-iOS Attnd-Android; do gh variable set LIVE_API_TESTS -b true -R apbeers/$r; gh variable set LIVE_API_SERVER -b dev.attnd.app -R apbeers/$r; done
```

**Secrets that come from downloaded files.** Run each one from the folder the file is in, then delete the file:
```bash
gh secret set ASC_KEY_P8 -R apbeers/Attnd-iOS < AuthKey_XXXXXXXXXX.p8
gh secret set ASC_KEY_ID -b XXXXXXXXXX -R apbeers/Attnd-iOS
gh secret set ASC_ISSUER_ID -b 00000000-0000-0000-0000-000000000000 -R apbeers/Attnd-iOS
gh secret set GOOGLE_PLAY_SERVICE_ACCOUNT_JSON -R apbeers/Attnd-Android < github-play-publisher.json
gh secret set GOOGLE_SERVICES_JSON -R apbeers/Attnd-Android < google-services.json
```

**Check what's set** (names only; values are never shown):
```bash
for r in Attnd-Web Attnd-iOS Attnd-Android; do echo "== $r"; gh secret list -R apbeers/$r; gh variable list -R apbeers/$r; for e in dev production; do gh secret list -e $e -R apbeers/$r; gh variable list -e $e -R apbeers/$r; done; done
```

**In the browser instead:**


**GitHub repository secret** (used by every workflow in that repo):
Repo → **Settings → Secrets and variables → Actions → Secrets tab → New repository secret**. Paste the name and value, then **Add secret**. To paste a file's contents without opening it, use `pbcopy < file` in Terminal, then ⌘V in the browser.

**GitHub environment secret or variable** (used only by deploys to that environment):
Repo → **Settings → Environments → `dev` or `production`** → **Add environment secret** for anything sensitive, or **Add environment variable** for plain settings like app names and hosts.

**Heroku config var** (used by the running server):
- From the Mac, without saving anything locally: `heroku config:set -a <app> NAME="value"`.
- From a file: `NAME="$(cat file)"`, then delete the file.
- Dashboard: app → **Settings → Reveal Config Vars → Add**.

Setting a config var restarts the app (a few seconds).

**Claude cloud environment** (gives Claude API access without showing it the value):
When Claude needs one, it opens a secure form for you; never paste keys into chat. To add one yourself: claude.ai → Claude Code → environment menu → **Attnd Cloud Environment → Edit → Network secrets**.

**Password manager:** add an entry named after the key, and attach the file if there is one (`.p8`, `.jks`, `.json`).

---

### Server keys (Heroku: `attnd-web` = production, `attnd-dev` = dev)

**`SECRET_KEY`, `ATTESTATION_JWT_SECRET`**: Django signing and app-token signing.
- **Create:** generate it inline with `$(openssl rand -hex 32)`. You never need to see it.
- **Put:** both Heroku apps, with a different value on each. See step 2.
- **If lost or leaked:** set a new value. Web sessions sign out and the apps re-attest automatically.
- **Password manager:** not needed.

**`API_KEY`**: static key for dev builds, CI and simulators.
- **Create:** `openssl rand -hex 32`. Already set on both apps.
- **Put:** Heroku (both apps). Also put the **dev** value in the `ATTND_API_KEY` repo secret in Attnd-iOS and Attnd-Android, and in your local dev config (step 5). Get it with `heroku config:get API_KEY -a attnd-dev | pbcopy`.
- **If leaked:** set a new value and update the GitHub secrets. To block a leaked value permanently, add its SHA-256 to `REVOKED_API_KEY_SHA256` in `events/apis.py`.

**`OPENAI_API_KEY`**: only Studio's image generation since stage C (Nov 21, 2026); AI import, refresh, tags, vision, prospects and Ask Attnd run on Claude (`ANTHROPIC_API_KEY`).
- **Create:** [platform.openai.com](https://platform.openai.com) → **API keys → Create new secret key**. Name it "Attnd server", pick the project, and set permissions to "Restricted" with Model capabilities = Write. Optionally set a monthly budget under Settings → Limits.
- **Put:** both Heroku apps. Use one key for both, or create a separate "Attnd dev" key to keep spending apart.
- **If leaked:** revoke it on the same page and create a new one.

**`GOOGLE_PLACES_API_KEY`**: map pins and photos for AI-imported locations. ✅ Restricted to Geocoding API and Places API (New); application restriction **None** (correct for a server).
- **Create:** [Google Cloud console](https://console.cloud.google.com) → APIs & Services → **Library**: enable **Places API (New)** and **Geocoding API**. Then **Credentials → Create credentials → API key → Edit**:
  - **API restrictions:** restrict the key to those two APIs.
  - **Application restrictions:** None. Heroku has no fixed IP address.
- **Put:** both Heroku apps.
- **Daily quotas** ✅ (owner, Nov 21, 2026, project `attnd-df28e` → APIs & Services → Places API (New) → Quotas &
  System Limits, "per day"): Autocomplete **300**, GetPlace **150**, SearchText **400**, SearchNearby **30**, Photo media
  **30**, reviews / media / summary **0**. Recent usage had been ~244 SearchText a day (place-image venue lookups during
  the cover backfill), before the caching below. The same from the command line (the quota names are listed by the
  first command):
  `gcloud services quota list --service=places.googleapis.com --consumer=projects/attnd-df28e` and
  `gcloud alpha services quota update --service=places.googleapis.com --consumer=projects/attnd-df28e --metric=<metric> --unit=1/d/{project} --value=<n>`.
- **When a quota is reached** Google answers 429 / `RESOURCE_EXHAUSTED`; `attnd/google.py` logs it once and that API
  returns "nothing" (no pin, no venue website) until midnight Pacific, when Google resets the daily quotas. Nothing
  else breaks: imports keep the AI's own coordinates, the console's Find falls back to typing coordinates. The staff
  Diagnostics → Endpoints page shows calls, cache hits, errors and refusals per day ("over quota" badge).

**`APNS_KEY_ID`, `APNS_AUTH_KEY`**: iPhone push notifications.
- **Create:** [developer.apple.com](https://developer.apple.com/account/resources/authkeys/list) → **Keys → +**, name it "Attnd APNs", tick **Apple Push Notifications service (APNs)**, then **Continue → Register → Download**. The Key ID is shown on that page.
- **Put:** both Heroku apps:
  ```bash
  heroku config:set -a attnd-web APNS_KEY_ID=ABC123DEFG APNS_AUTH_KEY="$(cat AuthKey_ABC123DEFG.p8)"
  heroku config:set -a attnd-dev APNS_KEY_ID=ABC123DEFG APNS_AUTH_KEY="$(cat AuthKey_ABC123DEFG.p8)"
  ```
- **Password manager:** required. Attach the `.p8`, because Apple only lets you download it once.
- **If lost:** revoke it and create a new key; an account can have two APNs keys at a time.

**`GOOGLE_SERVICE_ACCOUNT_JSON`, `FCM_PROJECT_ID`**: Android device check (Play Integrity) **and** Android push (FCM). One service account covers both.
1. **Pick the Google Cloud project:** the one linked to Play: **Play Console → **Protected with Play** / App integrity → Play Integrity API → Link a Cloud project**. Use the same project for Firebase in the next step.
2. **Firebase:** [console.firebase.google.com](https://console.firebase.google.com) → **Add project → choose that existing Cloud project**. Then **Project settings → General → Your apps → Add app → Android**, twice: `com.arcu.attnd` and `com.arcu.attnd.debug`. `FCM_PROJECT_ID` is the **Project ID** shown on that page.
3. **Enable the APIs:** Cloud console → APIs & Services → Library → **Google Play Integrity API** and **Firebase Cloud Messaging API**.
4. **Service account:** Cloud console → **IAM & Admin → Service accounts → Create**. Name it "attnd-server" and give it the role **Firebase Cloud Messaging API Admin**. Then open it → **Keys → Add key → JSON**, which downloads the file.
5. **Put:**
   ```bash
   heroku config:set -a attnd-web FCM_PROJECT_ID=<project-id> GOOGLE_SERVICE_ACCOUNT_JSON="$(cat attnd-server.json)"
   heroku config:set -a attnd-dev FCM_PROJECT_ID=<project-id> GOOGLE_SERVICE_ACCOUNT_JSON="$(cat attnd-server.json)"
   ```
   The server uses this one account for push too, so you don't need a separate `FCM_SERVICE_ACCOUNT_JSON`.
- **If leaked:** delete the key under the service account's **Keys** tab and add a new one.

**`GITHUB_SYMBOLICATE_TOKEN`**: lets the Diagnostics panel start attnd-ios's `symbolicate.yml` (API-013).
- **Create:** fine-grained token, Attnd-iOS only, Actions: Read and write. See step 14.
- **Put:** both Heroku apps.
- **If leaked:** revoke it on GitHub; it can only run workflows in Attnd-iOS.

**`ANDROID_SHA256_FINGERPRINT`**: makes `attnd.app/join/…` links open the Android app.
- **Where:** Play Console → Attnd → **Test and release → App integrity → App signing** → *App signing key certificate* → **SHA-256 certificate fingerprint**.
- **Put:** both Heroku apps. This value is public, not secret.

---

### GitHub keys

**`HEROKU_API_KEY`** (Attnd-Web repo secret): lets the pipeline deploy.
- **Create:** `heroku authorizations:create -d "GitHub Actions"`, then copy the **Token**.
- **Put:** Attnd-Web repository secret.
- **If leaked:** `heroku authorizations` lists tokens; remove one with `heroku authorizations:revoke <id>`.

**`HEROKU_APP`** (Attnd-Web environment variable): `attnd-dev` on `dev`, `attnd-web` on `production`.

**`ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_KEY_P8`** (Attnd-iOS repo secrets): signing and App Store uploads.
- **Create:** [App Store Connect](https://appstoreconnect.apple.com/access/integrations/api) → **Users and Access → Integrations → App Store Connect API → Team Keys → +**. Name it "GitHub CI" with access **Admin**, then **Generate → Download** (one time only).
  - The **Issuer ID** is shown above the list of keys.
  - The **Key ID** is in the key's row.
- **Put:** the three Attnd-iOS repository secrets. For `ASC_KEY_P8`, paste the whole file including the BEGIN/END lines (`pbcopy < AuthKey_XXXX.p8`).
- **Password manager:** required (attach the `.p8`).
- **If leaked:** revoke it on the same page and create a new one.

**`ATTND_SERVER`** (Attnd-iOS and Attnd-Android, `dev` environment variable): `dev.attnd.app`. On `production`, leave it unset; it defaults to `attnd.app`.

**`TESTFLIGHT_GROUP`** (Attnd-iOS, `dev` environment variable): `App Store Connect Users`. It has to match your TestFlight group's name exactly.

**`GOOGLE_PLAY_SERVICE_ACCOUNT_JSON`** (Attnd-Android repo secret): uploads builds to Play.
- **Status:** this secret already exists and signs in, but its account isn't invited in Play Console.
- **Find the account's email:** GitHub can't show you the secret. Look under Cloud console → IAM & Admin → **Service accounts**. If you can't tell which account it is, create a new one as below and replace the secret.
- **Create:** Cloud console → APIs & Services → Library → enable **Google Play Android Developer API**. Then IAM & Admin → **Service accounts → Create**, name it "github-play-publisher" (no roles needed), then **Keys → Add key → JSON**.
- **Grant access:** **Play Console → Users and permissions → Invite new users** → paste the service account's email → **App permissions → Add app → Attnd** → tick **Release apps to testing tracks**, **Release to production, exclude devices, and use Play App Signing**, and **Manage testing tracks and edit tester lists** → **Invite user**.
- **Put:** the Attnd-Android repository secret.

**`ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`** (Attnd-Android repo secrets): the upload key.
- **Status:** ✅ these already exist and work; CI built a signed bundle.
- **Password manager:** **required.** Attach the `.jks` and save all three values. Losing this key means asking Google support to reset it, which takes days.
- **If you ever need to re-add it:** `base64 -i upload.jks | pbcopy`, then paste into `ANDROID_KEYSTORE_BASE64`.

**`MAPS_API_KEY`** (Attnd-Android repo secret): Google Maps in the Android app.
- **Status:** ✅ it already exists.
- **Restrict it:** Cloud console → Credentials → that key:
  - **API restrictions:** Maps SDK for Android.
  - **Application restrictions:** Android apps. Add `com.arcu.attnd` with the **App signing** SHA-1 and the **Upload** SHA-1, both from Play Console → App integrity. Add `com.arcu.attnd.debug` with your debug SHA-1.

**`GOOGLE_SERVICES_JSON`** (Attnd-Android repo secret): Firebase configuration for the Android app (push).
- **Where:** Firebase → Project settings → General → Your apps → Android app → **google-services.json** download. Both Android apps are in the same file.
- **Put:** `pbcopy < google-services.json`, then paste into the Attnd-Android repository secret. This file isn't truly secret, but keeping it out of git keeps the repo clean.

**`CI_API_KEYS`** (Heroku `attnd-web`) and **`ATTND_PROD_CI_KEY`** (repo secret in all three repos): the DEMO-only CI key
for the production integration tests (API-008, step 13). **If leaked:** run step 13 again; the key can only read the public
DEMO event and register `ci-` test devices.

**`ATTND_DEV_API_KEY`** (Attnd-Web repo secret): the **dev** app's `API_KEY`, for the web repo's **Integration · dev** job
(step 13). Same value as `ATTND_API_KEY` in the app repos.

**`ATTND_API_KEY`** (Attnd-iOS and Attnd-Android repo secrets): the **dev** app's `API_KEY`, used only for the optional live API tests. **Replace the old value**, which is likely the leaked production key. To turn the live tests on, also add the repo variables `LIVE_API_TESTS=true` and `LIVE_API_SERVER=dev.attnd.app`.

---

### Claude cloud environment keys

| Network secret | Host | Status |
|---|---|---|
| Heroku API token ("Heroku API token (Claude)") | `api.heroku.com` | ✅ saved; Claude can manage both Heroku apps |

Revoke it any time with `heroku authorizations` / `heroku authorizations:revoke <id>`, or from the environment's settings.

## 17. Prospects (staff sales dashboard): nothing to set up

`/console/prospects/` (staff and superusers only) finds events in a region that suit the app with Claude's web
search tool, reads their pages for organizer contacts and can make testing-only copies of them. It uses the existing
`OPENAI_API_KEY` (web search must be allowed for the key's project; it is by default). Optional settings:
```bash
heroku config:set -a attnd-dev PROSPECT_DAILY_SPEND_CAP_USD=5 PROSPECT_RUN_MAX_MINUTES=20 PROSPECT_MODEL=gpt-5.5
```
Region, themes, caps and the schedule (off by default; Heroku Scheduler's `run_scheduled_tasks` starts it when due)
are edited on the dashboard's Settings tab. Manual run from a shell: `heroku run -a attnd-dev python manage.py
find_prospects --max 10`. Outreach is done by a person and must follow CAN-SPAM (who you are, a physical mailing
address, an opt-out that is honoured); Attnd never emails prospects.

## 18. Ask Attnd and Studio on Anthropic ✅

Done Nov 21, 2026 (owner): `ANTHROPIC_API_KEY` is set on **attnd-dev and attnd-web**, and both AI features run on
Anthropic:
- **Ask Attnd** (`agent/`): the official `anthropic` SDK with `claude-haiku-5-5` ($0.10 / $0.50 per million input /
  output tokens, cache reads a tenth); `AGENT_PROVIDER` defaults to `anthropic` when the key is set (`openai` to switch
  back). A request Claude Haiku declines continues on Claude Sonnet (`AGENT_REFUSAL_MODEL`).
- **Studio** (`studio/`): `STUDIO_ANTHROPIC_MODEL=claude-sonnet-5-5` ($2 / $10) on both apps; dev also has
  `STUDIO_AI_PROVIDER=anthropic`, production's is `auto` (Anthropic because the key is set). Images and vision stay on
  OpenAI.
```bash
heroku config:set -a attnd-dev ANTHROPIC_API_KEY=sk-ant-… STUDIO_AI_PROVIDER=anthropic STUDIO_ANTHROPIC_MODEL=claude-sonnet-5-5
heroku config:set -a attnd-web ANTHROPIC_API_KEY=sk-ant-… STUDIO_ANTHROPIC_MODEL=claude-sonnet-5-5
```
✅ Anthropic Console monthly spend limit: $20 (owner, Nov 21, 2026), a second cap behind the per-feature daily caps.
Still to check there: no training on customer data (docs/LLC_MIGRATION.md).


## Rotating SECRET_KEY (stage G, Nov 21, 2026)

Sessions, password-reset links and signed values keep working during a rotation: the old key goes into
`SECRET_KEY_FALLBACKS` while the new one becomes `SECRET_KEY`, and it is removed after two weeks.

```bash
heroku config:get SECRET_KEY -a attnd-web > /tmp/old_key
heroku config:set SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')" SECRET_KEY_FALLBACKS="$(cat /tmp/old_key)" -a attnd-web
rm /tmp/old_key
```

Two weeks later:

```bash
heroku config:unset SECRET_KEY_FALLBACKS -a attnd-web
```

## 20. Apple Music artist links (API-030)

The server looks performers up in the Apple Music catalog to add "Listen on Apple Music" links (API-030). It signs a
developer token (ES256) with a MusicKit key; the team is `APPLE_TEAM_ID`.

Done (owner, Nov 21, 2026):
- Media ID `media.com.arcu.attnd` with **MusicKit** only (ShazamKit and Apple Music Feed deliberately left off).
- MusicKit key **"Attnd MusicKit"**, Key ID `2X6W6X6PLA`. The `.p8` can be downloaded only once: it is kept on the
  owner's Mac at `/Users/andrewbeers/Documents/Attnd/ios-signing/AuthKey_2X6W6X6PLA.p8` (and in the password
  manager). **Never commit it.**
- `APPLE_MUSIC_KEY_ID` and `APPLE_MUSIC_PRIVATE_KEY` (the whole `.p8`, header lines included) on **attnd-dev**
  (verified).

To do (production, when releasing API-030):
```
heroku config:set APPLE_MUSIC_KEY_ID=2X6W6X6PLA -a attnd-web
heroku config:set APPLE_MUSIC_PRIVATE_KEY="$(cat /Users/andrewbeers/Documents/Attnd/ios-signing/AuthKey_2X6W6X6PLA.p8)" -a attnd-web
```
Without the two variables the lookups are off and nothing else changes. Spotify is skipped (its Web API needs a
Premium account owner; ROADMAP).


## 21. Web server: ASGI (batch 7, nothing to set up)

Since Nov 22, 2026 the `web` process runs gunicorn's own ASGI worker (`Procfile`: `gunicorn attnd.asgi --worker-class
asgi`), no new package. Django still runs as before on a pool of `WEB_THREADS` threads (default 4, optional config
var); long-polls (preview phones, Ask Attnd) wait on the event loop instead of holding a thread. To go back, change
the Procfile's web line to `gunicorn attnd.wsgi --worker-class gthread --workers 1 --threads 4 --timeout 300` and
push (nothing else depends on it). Check after a deploy:
```bash
curl -s -o /dev/null -w "%{http_code}\n" https://dev.attnd.app/health/
```

## 22. IndexNow (batch 7, 1 min, production only)

Tells Bing and the other IndexNow engines when a listed event's join page or a Studio site changes. Off until the key
is set (dev never sends). A random key, then nothing else (the key file is served automatically):
```bash
heroku config:set -a attnd-web INDEXNOW_KEY=$(openssl rand -hex 16)
```
Check after the next deploy: Diagnostics → Endpoints → IndexNow shows "0 waiting" and the counts per day.
