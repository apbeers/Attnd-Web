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

- **Web: Lighthouse pass on staff pages.** Stopped; everything is pushed (`e9c8ab9`, `03f83fc`, `2630af5`).
  - **Results:** staff lists paged 50 at a time, Customers 51 → 10 queries, a Diagnostics session draws 200 rows
    instead of 1,500, gallery 258 → 183 queries.
  - **Scores so far (phone):** Diagnostics 74 → 100, Prospects 73 → 98.
  - **Next:** finish the after-scores and run `scratchpad/lh/staff/cmp.py` for the table. Note: the scratchpad is
    session-only, so re-measure in a new session.
  - **Not measured:** the platform-admin and superuser pages (the test account is staff only).
  - **Cleanup:** remove the seeded rows with `python manage.py seed_staff_volume --clean`.
- **Android phase 4: done** (`01ef69f`).
  - **Built:** Home first (a temporary list of today's essentials), the event's tabs, Explore and the My plan tab
    removed, the floating search or My plan button, the Options gear, swipe-to-dismiss pills and banners, the in-app
    update banner, and the Permissions section.
  - **Not yet:** lint wasn't re-run after the final rebase (tests and build pass).
  - **Moved to other phases:** the urgent card's swipe and Undo (5b), and hiding the planner without the model (6b).
  - **Missing:** place results in the search sheet have no distance or arrow yet.
- **Android phase 6a:** the assistant domain, the router rules from the eval, the JSON tool protocol, and the
  Android eval runner. Told to skip the Plan ↔ My schedule sync code because of the one-list change.
- **Android phase 8: done** (`a04e8c0`; 391 tests pass).
  - **Built:** the Live Update follows plan → My schedule → day; Now / Next always filled; the `live_activity`
    follows / buttons settings; posts on join; per-entry reminders.
  - **Differs from iOS:** `stale_text` is stored but not shown (an Android notification can't redraw itself when
    it goes stale).
  - **To do:** run `lintDebug assembleDebug` on develop (not re-run after the rebase). Mark API-027's Android column
    in `API_CHANGES.md` as partial: `live_activity` is done, the screens aren't.
- **iOS one-list change: done** (owner-approved; spec `docs/specs/one_list_spec.md`; Attnd-iOS `2b6114b`).
  - [x] Baseline: `ATTND_ASSISTANT_EVAL=router scripts/linux_harness.sh` (93 tests, 242/247). The `MyScheduleTests`
    "fatalError" was `ReminderPlanner` missing: the harness skipped `LiveFollowLogic.swift` (it needs
    `LiveActivityModel.swift`, ActivityKit). It now copies that file without the import plus an `ActivityAttributes`
    stand-in; `MyScheduleTests` and `LiveFollowTests` are in the default list (132 tests).
  - [x] `MySchedule.swift`: `source`, `reason`, `SchedulePreferences`, `ScheduleProposal`, `ScheduleHistory`,
    `Planner.propose`, `dayPlan` (the derived day view, via `Planner.dayView`), `absorb(legacy:)`.
  - [x] `PlanStore.swift` rewritten: `Plan` is in memory only; `apply(plan:)`, `reconcile`, `onApply`,
    replan / keep / clear plan and the `pref.plan` / `pref.planHistory` storage are gone (one-time, idempotent
    migration, then deleted).
  - [x] One flow and one Undo history (≤ 20, hearts included); new plans preview before Apply; Live Activity follows
    My schedule; "Find Another" is a swap proposal; the button and chat are **Ask Attnd**.
  - [x] Router eval 242/247, critical all pass.
  - **On a device:** migration from a real older build (plan → entries, keys gone), the Undo toast and card, the Ask
    Attnd proposal card for "Plan my Saturday", the Duo planning board and the Live Activity. The UI files
    (AppState, AssistantController, PlanStore, views) weren't compiled here; check the CI archive.

## Next after those

1. **Android one-list update: done** (Attnd-Android `1bdd57c`, spec `docs/specs/one_list_spec.md`).
   - [x] `source` / `reason` on entries, `SchedulePreferences` and the check-off set per event.
   - [x] No stored Plan: `PlanStore` keeps none; `applyPlan`, reconcile, `PlanHistory`, re-plan / keep / clear and
     "Your plan changed" removed. `Planner.propose` → `ScheduleProposal` (only `planner` entries removed unless asked).
   - [x] One flow and one Undo history over My schedule (≤ 20, hearts included); new plans preview as a proposal.
   - [x] Derived day view (`MySchedule.dayPlan`) for the timeline, routes, live hints, WhyNot, sharing and the Live
     Update (no plan source).
   - [x] One-time idempotent migration of `plan_<id>` / `plan_history_<id>`, keys deleted.
   - [x] "My plan" → Ask Attnd in the UI. 432 unit tests, eval router 245/247, critical 50/50; lint and build green.
   - **On a device:** migration from a real older build, Ask Attnd's proposal for "Plan my Saturday", Undo, the Live
     Update. The heart's Undo snackbar is phase 3b (`ScheduleController.undo` is ready).
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
