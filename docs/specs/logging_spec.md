# Attnd logging & diagnostics spec (iOS, Android, attnd-web) — agreed Nov 15, 2026

Decisions: storage = the existing Postgres with strict caps (separate table, nightly purge, 250 MB cap, oldest sessions
first); retention dev 7 days, production debug sessions 14 days; production debug mode is user-started only (never remote);
"CRI requests" = Siri/assistant requests (category `assistant`). No third-party libraries or services.
Tracker: **API-013** diagnostics log upload + admin panel; **API-014** anonymous performance metrics (API-012 is reserved).

## 1. Record format (identical everywhere; `v` = 1)
JSON object per line:
`{"v":1,"ts":"2026-11-15T19:04:12.381Z","mono_ms":523101,"seq":4182,"level":"debug","cat":"net",
  "event":"net.request.finished","msg":"GET bundle 304 in 182 ms","f":{...},"app":"2.0.131","build":"131",
  "os":"iOS 27.2","model":"iPhone18,1","state":"fg"|"bg","net":"wifi"|"cell"|"none","event_id":5,"diag":"D-7K3QX"}`
- levels: trace, debug, info, notice, warning, error, fault (numeric 0–6).
- categories: net, push, background, live, location, map, planner, assistant (Siri/Gemini/Ask), search, storage, ui, perf,
  auth, lifecycle, logging.
- event names: dotted, stable, listed in attnd-web `LOG_EVENTS.md` (the shared catalogue; every app event name must be in
  it). Examples: lifecycle.launch (cold/warm, ms), net.request.started/finished/failed (path template, status, ms, bytes,
  req_id, cache 304), push.received/handled, background.refresh.started/finished, live.start/update/end/token,
  location.permission, planner.run (ms, items), assistant.request (kind, ms, ok), storage.bundle.saved, perf.signpost,
  ui.screen (name), error.caught (domain, code, type).
- `f` = event fields. **Redaction (unit-tested)**: never search/Ask/plan text (log length only; dev builds may log text),
  never tokens/keys/push tokens/emails, URLs as path templates with ids replaced by `{id}` and no query string,
  location rounded to 0.01° or only distances, no device id/support code.
- `diag` = the debug code (see §3). `req_id` = the X-Request-Id sent with that API call.

## 2. On device
- iOS: `os.Logger` (subsystem com.arcu.Attnd, category = cat; values public only when safe) + our ring buffer.
  Android: `android.util.Log` tag `Attnd/<cat>` + ring buffer. One small logging facade per app (`AppLog.debug(.net,
  "net.request.finished", "…", fields)`), level checked first, writes off the main thread.
- Ring buffer: JSON lines on disk, ~2 MB, newest kept; feeds the viewer and uploads.
- Log viewer (dev builds always; production only while debug mode is on or from About & support → "Recent logs"):
  filters level/category/text/errors-only, share/export .jsonl.
- Signposts (iOS `OSSignposter`) / Android `Trace` sections around launch, bundle fetch, planner, map first render.
- Every API call sends `X-Request-Id` (random); debug sessions also send `X-Diag-Session: D-…`.
- Default levels: dev builds `debug` + continuous upload; production `warning`, local only.

## 3. Debug mode (production, user-started, 10 minutes)
- About & support → "Turn on debug logging for 10 minutes": consent line, countdown, the debug code `D-XXXXX`
  (5 Crockford base32 chars, random per session) with Copy. Level debug + upload for 10 min, plus the previous 5 minutes
  from the ring buffer (pre-roll); then back to normal (survives restarts; expiry stored). Never linked to device id.
- Dev builds: one persistent debug code per install, always on.

## 4. Dev builds (readable code)
- Android: build type `dev` (release-signed, not debuggable — Play rejects debuggable bundles on every track — minify OFF,
  BuildConfig.DEV = true) used for develop → Play internal testing; `release` (R8 minify, DEV false) for main → production.
  CI uploads R8 `mapping.txt` (and native symbols if any) for every release via Play API `edits.deobfuscationfiles` in the
  same edit, and to attnd-web (`POST /api/diagnostics/symbols/` with a CI key, stored per version) for our retrace.
- iOS: a `Dev` build configuration copied from Debug (-Onone, DEBUG + DEV flags, assertions) archived for develop →
  TestFlight with distribution signing; `Release` for main. dSYMs are uploaded to App Store Connect (already) and kept as
  CI artifacts (90 days) per build.

## 5. Server (attnd-web)
- `POST /api/v2/diagnostics/logs/` (app auth; CI keys discard): gzip JSON `{session: "D-…", platform, app, build, os,
  model, reason: "dev"|"debug_mode", entries: [...]}`, ≤ 500 entries / 256 KB per request; per-session cap 20,000 entries per
  10 min; 429 + `Retry-After` when over caps or the table is above its size cap. Validates `v`, levels, categories;
  stores `DiagnosticSession` + `DiagnosticEntry` (indexes: session+ts, level, cat, event; trigram/full-text on msg).
- Server side: a middleware logs every API request as a structured line (path template, status, ms, X-Request-Id,
  X-Diag-Session) to stdout (Heroku logs) and, when the request carries X-Diag-Session, also as a DiagnosticEntry
  (source "server") so it joins the app's timeline.
- Retention: nightly purge in `run_scheduled_tasks` (dev 7 d, debug_mode 14 d, total cap 250 MB → oldest first).
- Symbols: store Android mapping files per version (gzip in DB or media) and a pure-Python R8 retrace (classes, methods,
  line ranges) used by the admin panel; iOS: a "Symbolicate" action that dispatches a macOS workflow
  (`symbolicate.yml` in Attnd-iOS: downloads the build's dSYM artifact, runs `atos`, posts the result back).
- Admin "Diagnostics" (staff, existing 2FA): sessions list (search by debug code, time, platform, app version, event,
  has errors, reason), session timeline (lanes per category, server lines interleaved by req_id), filters (level, category,
  event, text, status ≥ 400, duration > N, fg/bg, ±30 s around a line), network waterfall, summaries (errors grouped,
  slowest requests, launch time, pushes received vs handled, background wakes), crash/hang reports with retrace /
  symbolicate, export .jsonl. Plus an endpoint timing table (p50/p95 per endpoint from the server request log).

## 6. Anonymous production performance (API-014, low load)
- No per-person logs. Aggregated on device and sent with the existing usage batch (`POST events/<id>/usage/`), as new metric
  names with bucketed values (detail = bucket): `perf_launch_cold` / `perf_launch_warm` (buckets <0.5s, <1s, <2s, <4s,
  ≥4s), `perf_bundle_fetch` (<250ms, <500ms, <1s, <2s, <5s, ≥5s), `perf_bundle_304`, `perf_api_error` (detail = 4xx/5xx/
  timeout/offline), `perf_push_refresh_delay` (buckets), `perf_live_start_fail`, `perf_planner_ms` (buckets),
  `perf_map_first_render` (buckets). Events outside an event go to the next usage flush of an event or are dropped.
- Crashes/hangs: iOS MetricKit (`MXMetricManager` daily metrics + diagnostic payloads: crashes, hangs, launch, memory) and
  Android `ApplicationExitInfo` (crash/ANR/low-memory reasons read at next launch) → uploaded anonymously to
  `POST /api/v2/diagnostics/reports/` (no device id; app version, os, model), shown in the admin panel with
  retrace/symbolicate. Play Console vitals and Xcode Organizer remain the store-side views.
- Privacy: App Store privacy labels "Diagnostics: crash, performance, other diagnostic data — not linked to identity, not
  tracking"; Play data safety likewise; a privacy-policy paragraph on debug mode (the web `/privacy/` page).
