# Handoff: where the work stopped (Nov 22, 2026)

The owner paused because they were running out of tokens. Every running agent was asked to stop at a clean
commit. Green work went to `develop`; anything not yet green went to a `wip/…` branch on origin. Each agent's
resume note is under "Running when paused" below.

## Done and live

- **Web production** (attnd.app, release v121–123):
  - the batch 7 release, including the site crawling fixes;
  - sign-ups invite-only (`SIGNUPS_OPEN=0`);
  - `INDEXNOW_KEY` and the Apple Music keys;
  - Mailgun webhooks + signing keys on dev and production.
- **Web develop (dev.attnd.app), not yet released to production:**
  - Ask Attnd as one chat (phones use the full-screen page, the session-jump fix);
  - the responsive cleanup (three breakpoints, one chat setting);
  - Lighthouse passes 2 and 3 (console, public pages, Studio sites, staff pages with paging);
  - HTML emails + Emails log;
  - billing (Stripe off);
  - the Architecture, App options and Brand pages;
  - the assistant eval suite (`docs/assistant_eval/`, DEMO facts).

  **Next:** release `develop` → `main` when the owner asks.
- **iOS develop:**
  - TestFlight builds up to the assistant eval commit (`5d78b79`);
  - the Keychain device id;
  - the Duo fixes;
  - the router fixes from the eval (242/247, critical 50/50).

  A manual full test run of `ios.yml` was started on develop; check its result.
- **Android develop** (the rewrite, plan in Attnd-Android `docs/ios_parity_plan.md`):
  - phase 0: toolchain and dependencies, minSdk 36, `scripts/dev/setup_sdk.sh`;
  - phase 1: the data layer, Block Store device id, the offline leave queue;
  - phase 2: the removals and the ViewModel split;
  - phase 3a: the My schedule domain;
  - phase 5a: the screen-layout and component domain;
  - phase 7: themes;
  - phase 9: preview phones.

## Running when paused (resume from each one's note)

- **Web: Lighthouse pass on staff pages.** Main fixes pushed (`03f83fc`). The last piece was the Components gallery
  (`platform_admin/views_gallery.py`), then the after-scores.
- **Android phase 4:** tabs (Home first, ≤ 5), Options placement, the search sheet, swipe-to-dismiss banners, the
  update banner, the Permissions section.
- **Android phase 6a:** the assistant domain, the router rules from the eval, the JSON tool protocol, and the
  Android eval runner. Told to skip the Plan ↔ My schedule sync code because of the one-list change.
- **Android phase 8:** Live Update and per-entry reminders, matching iOS.
- **iOS one-list change** (owner-approved): My schedule is the only stored list; the planner proposes edits to it;
  one preview / Apply / Undo flow; walks and timelines are derived; the old plan is migrated once; "My plan" gets a
  new name. Spec: `docs/specs/one_list_spec.md`.

## Next after those

1. **Android one-list update:** follow `docs/specs/one_list_spec.md` and switch Android's ported My schedule (phase
   3a: `ScheduleStore`, `ScheduleController`, `PlanStore.onApply` / reconcile) to the single list.
2. **Android remaining phases:**
   - 3b: My schedule UI;
   - 5b: the renderer, details, Home, context, Apple Music, photo credit; turn on `X-Attnd-Sections: 2`;
   - 5c: the catalogue-2 tab screens and onboarding;
   - 6b: the chat UI plus the Gemini Nano tool loop. The My plan / Assistant button shows on capable phones; without
     the model downloaded, tapping it offers the download;
   - 10a / 10b: foldables;
   - 11: performance;
   - 12: tracker ticks;
   - then the assistant eval on Android.

   Rules for agents:
   - run several phases in parallel, each in its own git worktree (`/root/wt/…`);
   - build with `--no-daemon --max-workers=1`, and never run `./gradlew --stop`;
   - at most 3 Android builds at once, because the container runs out of memory.
3. **Owner tasks:**
   - **iOS:** test the latest TestFlight on iPhone and the Duo simulator, with the IPHONE_DUO_CHECKLIST.
   - **Assistant eval in model mode:** run it on the Mac (command in `docs/assistant_eval/README.md`).
   - **Web console:** try the phone chat on dev.
   - **Decisions:** whether contributors can publish or send notifications; the privacy policy draft's 5 decisions.
   - **Later:** Stripe in the LLC's name; enable the Maps Static API on the browser key.
4. **Dev test accounts** (dev only):
   - `lighthouse-test@attnd.app` (organizer, admin on DEMO);
   - `lighthouse-staff@attnd.app` (staff, TOTP).

   Their credentials were only in the session's scratchpad. Delete both or reset their passwords when no longer
   needed. Seeded staff-page data on dev can be removed with the `seed_staff_volume` cleanup.
