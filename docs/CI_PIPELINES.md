# CI/CD pipelines (all three repos)

## Current mode: LEAN (early build-out, since Nov 15, 2026)
The repos are private, so GitHub Actions minutes are limited (macOS minutes bill at ~10× Linux). While there are no
real users, CI runs the bare minimum needed to iterate:

| Repo | What runs automatically | Off (until "full" mode) |
|---|---|---|
| attnd-web | push to develop: **Checks & tests** (Linux) → **Deploy → Heroku dev**. Push to main: tests → deploy production | Integration · dev, Integration · prod (live tests); the weekly dependency upgrade (manual only) |
| attnd-ios | push to develop: **Archive & upload** only (Dev → TestFlight, ~8 Mac min). Push to main: Test + Archive + Submit for App Review | The Test job on develop pushes (unit tests: run by hand with "Run workflow", or set CI_FULL); Integration · dev / prod |
| attnd-android | **nothing automatic**: the whole pipeline runs only when started by hand (Actions → Android → Run workflow, pick the branch) | Every push/PR trigger; the integration matrix (needs CI_FULL) |
| manual workflows (all repos) | unchanged: store text, store screenshots, symbolicate (iOS), upgrade deps (web) | — |

Working rules in lean mode (Claude follows these):
- **Android**: never start an Android build unless the owner asks for one. Commits still go to develop.
- **iOS**: batch commits and push when a change is ready to try on the phone (each push = one ~8-minute Mac archive).
  Before pushing, run the pure-logic tests in the Swift-on-Linux harness (the planner/assistant/tab/proposal tests);
  run the full XCTest suite by hand ("Run workflow" on develop) before a release, or when something risky changed.
- **Web**: Linux minutes are cheap; normal pushes are fine. Docs-only commits (`*.md`, `docs/**`) never run.
- Newer pushes cancel older runs on develop (all repos).
- Artifacts: iOS dSYMs kept 30 days (was 90), test results 7 days.

## Full mode (restore before real customers)
1. In each repo, set the repository variable **`CI_FULL` = `true`** (Settings → Secrets and variables → Actions →
   Variables), or: `gh variable set CI_FULL --body true --repo apbeers/Attnd-Web` (and `Attnd-iOS`, `Attnd-Android`).
   That re-enables: web Integration · dev + prod; iOS Test on every push + both integration suites; Android integration.
2. Android: restore the push/pull_request triggers in `.github/workflows/android.yml` (the lean commit replaced them;
   the full trigger block was: `push: branches [main, develop]` with `paths-ignore: ['**/*.md', 'scripts/ci/screenshots.sh',
   'scripts/ci/compose_screenshots.py', 'store/en-US/captions.txt', '.github/workflows/store-*.yml']`, and
   `pull_request: paths-ignore: ['**/*.md']`).
3. Web: re-add the weekly schedule to `upgrade-deps.yml` (`cron: '17 14 * * 1'`).
4. iOS: dSYM retention back to 90 days if symbolication needs older builds.

What the full pipelines do (as built through Nov 15, 2026):
- **Web**: tests on Postgres 18 (`--parallel auto`), deploy dev (develop) / production (main) via
  `scripts/ci/heroku_deploy.sh` with a health check, live integration suites (`live_tests/`) against dev after its
  deploy and against production with the DEMO-only CI key (informational), weekly `upgrade_deps.py`.
- **iOS** (`ios.yml`): Test (Mac, `xcode-27`, Xcode 27.2): build-for-testing once while the simulator boots, then
  Unit tests, Integration · dev, Integration · prod on that build; Archive & upload (Dev on develop, Release on main,
  `get-task-allow` check, dSYM artifact); Submit for App Review on main (`asc.py metadata` + `release`). Compilation
  cache (CAS) kept per job. ~8–9 min push → TestFlight.
- **Android** (`android.yml`): Unit tests & lint (+ `lint_summary.py`), Integration · dev / prod matrix, Signed bundle
  (`bundleDev` / `bundleRelease`, mapping upload to attnd-web on main), Publish to Play (internal on develop,
  production on main with listing + mapping). Gradle caches per job. ~4–5 min.

## Planned for later (with real customers)
- Tickets' end-to-end tests (`docs/specs/tickets_spec.md`, Testing): **written, waiting for CI_FULL** —
  `live_tests/live_tickets.py` (runs with the other live suites: read-only on production, the Stripe test-mode flow on
  dev) and the web job "E2E · dev (tickets)" (`e2e/tickets_e2e.py`, Playwright, needs CI_FULL and the
  `E2E_ORGANIZER_*` secrets). App suites for My tickets / offline scanning come with the apps.
- One end-to-end test per user-facing flow on every develop push (join, bundle, push, planner/assistant, Studio
  publish, forms, diagnostics, passkey sign-in), so the three repos are always checked together.
- Pull-request based flow with required status checks on `main` (branch rulesets) once there are users.
- iOS UI tests (XCUITest) — the reason the tested code stays in the app target (no simulator-free package).
