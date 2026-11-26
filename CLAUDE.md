# Attnd Web — Backend & API

Django 6.1 backend serving the REST API consumed by the iOS and Android apps, plus a web console for event organizers. Deployed on Heroku.


## Branching (initial development)

- `main` = production (default branch), `develop` = dev/beta. Pushing to `develop` deploys to the dev server / TestFlight / Play internal testing; pushing to `main` deploys to production.
- **Run it yourself** (owner, Nov 22, 2026): set keys and run Heroku commands through the Heroku Platform API (`api.heroku.com`, token injected by the proxy) on dev and production instead of asking the owner; never print secret values (check them by name, shape or fingerprint; copying a value between apps is fine). Only a genuinely destructive command (deleting data, apps, add-ons, scaling to zero) still needs the owner's go-ahead. When something needs the owner's own machine or accounts (a file on their Mac, a web console), give a command to paste rather than click-by-click steps. Note: reading config vars right after a `PATCH` can return a stale cached copy (Nov 25, 2026: the change had applied; the release list showed it). Read them back with a cache-busting query (`?t=<now>`) and `Cache-Control: no-cache`, and check the release list, before retrying.
- **docs/LLC_MIGRATION.md is the running list for moving Attnd to an LLC.** Whenever a feature adds an account, key, certificate, service, domain, store listing or legal text, add it to its inventory in the same commit.
- **ROADMAP.md lists agreed future work** (things needed before release, and later features). Add items there when the user parks an idea; remove them once built.
- **SETUP.md is the living setup checklist.** When the user reports completing a setup step, record it in SETUP.md (progress table and the step itself) instead of repeating instructions in chat. **At the start of each working session, remind the user of the unchecked "Open owner tasks" in SETUP.md.**
- **While there are no real users yet, commit and push straight to `develop`** — no feature branches or PRs needed. Merge `develop` → `main` to release. Revisit this once the apps have users.

## System Overview

Attnd is an event attendance app. This repo is the single backend for both mobile clients:

- **[attnd-ios](https://github.com/apbeers/Attnd-iOS)** — SwiftUI app
- **[attnd-android](https://github.com/apbeers/Attnd-Android)** — Kotlin/Compose app

Current mobile apps use **API v2** (`/api/v2/`, see `API_V2.md`): one `bundle/` request returns the event, locations, schedule, notifications and the server-resolved **display config**. The v1 events API (`/api/v1/events/`) is retired (410, see API v1 below); `/api/v1/attest/` stays. There is no Django REST Framework — all API views are plain Django views returning `JsonResponse`.

## iPhone Duo

iPhone Duo is brand new (released late Oct 2026). For anything Duo-related in any of the three repos, always check online for the latest Apple docs and App Store Connect behaviour instead of relying on model training data (see attnd-ios `CLAUDE.md` → iPhone Duo for the current facts).

## Cross-repo coordination (this repo leads)

attnd-web is the coordinating repo for all three. Any feature that touches the API starts here. Follow `API_CHANGES.md`:
1. Add a tracker row and a log entry: the API change, additive or breaking, and exactly what iOS and Android must do.
2. Server first: implement it, update `API_V2.md`, refresh the snapshot with `UPDATE_CONTRACT=1 uv run python manage.py test events.tests_contract`, and add tests.
3. Then implement the app parts in attnd-ios and attnd-android (in the same session when all three repos are present). Reference the entry ID in commit messages and tick the tracker.

- **Never break installed apps.** Only additive changes go into v2. `events/tests_contract.py` fails CI on a removed or renamed key, endpoint or push type, or a changed type. A breaking change means a new `/api/v3/` alongside v2. Retire an old version only once `manage.py api_usage` (based on `Device.api_version`) shows its devices are gone.
- The mobile repos have no copy of the contract. `API_V2.md` and `API_CHANGES.md` live only here.

## Platform model (the "full vision")

1. **AI import** — an organizer (or a visitor through the home page's composer, after signing up) gives URLs / a PDF or image / pasted text. An `IngestJob` crawls + extracts with Claude Haiku structured output (`attnd/claude.py`) and creates the event (`scrape/jobs.py`, `scrape/service.py`).
   **Reading the website** (`scrape/crawler.py`, batch 7 A): one budget for every source URL (40 pages, 120 s, no page
   twice), every request through Studio's SSRF-guarded fetcher; `sitemap.xml` (+ robots.txt sitemaps, one index level)
   and links like "Event details" / `/artists/<slug>/` give the detail pages; order = start page, listing pages
   (schedule, lineup, map…), detail pages, the rest; up to 3 same-site PDFs whose link says schedule / lineup / map /
   menu / vendors / program (≤ 10 MB) and the largest 1–2 images (≥ 600 px) of schedule / lineup pages go to the model
   as documents / images; thin JS pages keep their `__NEXT_DATA__` / `__NUXT__` / `__INITIAL_STATE__`, WordPress sites
   add The Events Calendar's `/wp-json/tribe/events/v1/events`; lines repeated on 3+ pages (menus, footers) are kept
   once. The website watcher keeps its own fingerprinting (`service._page_text`, unchanged). **Detail-fill pass**
   (`scrape/detail_fill.py`): after the extraction of a crawl (imports and full refreshes), every item / place with an
   empty context gets a focused second look in batches of 15 with only the text that mentions it (Claude Haiku,
   context only, `events.context.normalize`; step "Added details for 38 schedule items"); **Fill in details**
   (`console:event_fill_details`, Ask Attnd `fill_in_details`, a background task card, `IngestJob` kind `details`) runs
   it alone on the event's rows: only empty, unlocked contexts. The readiness / attention checks say "Details on 2 of
   43 schedule items" (and places) with that button. Tests: `scrape/tests_crawler.py`.
2. **Manual management** — the organizer console (`/console/`) edits everything. Any field a person edits is added to that row's `locked_fields`. An event has an owner plus a team (`EventMember`: admins manage the team and settings, contributors edit content); invites by email work before the person has an account (`EventInvite`, claimed on sign-up/sign-in).
3. **Website watching + AI refresh** — `scrape/watch.py` fingerprints the event's pages (conditional GET + hash of visible text/JSON-LD; every 10 min during the event, hourly the week before, 6-hourly earlier) and runs the AI only on pages that changed, with a minimum gap and daily cap (`WATCH_MIN_AI_GAP_MINUTES`, `WATCH_MAX_AI_RUNS_PER_DAY`). `update_mode` review/minor/auto decides what applies itself (removals and locked fields always wait). Manual "Check for updates now" re-crawls everything. The AI re-reads the sources *with the current data* and returns its view; `scrape/diff.py` turns that into a `ChangeSet` of field-level `Change`s. Locked fields become conflicts (unticked), empty AI values never blank data, removals need cited evidence.
4. **Review** — the organizer accepts per change / per field; `apply_changeset` applies atomically, snapshots an `EventRevision` first (rollback from History), and bumps `content_version` once.
5. **Apps follow** — every content change bumps `Event.content_version` (signals in `events/signals.py`); a silent `content_updated` push (to every device with a token) tells apps to refetch `bundle/` with `If-None-Match`; they also poll slowly.
6. **Adaptive layout** — `events/display.py` picks a preset (intimate / standard / festival) from size + spread; organizers override any key in **App layout**; the resolved dict ships in the bundle.

## Claude cloud environment ("Attnd Cloud Environment")

- A Heroku API token is stored as a network secret for `api.heroku.com`. Call the Platform API directly (`Accept: application/vnd.heroku+json; version=3`); the proxy injects the token. There is no Heroku CLI login.
- Allowed hosts: `api.heroku.com`, `attnd.app` (production) and `dev.attnd.app` (dev app `attnd-dev`; its Heroku hostname `attnd-dev-7ab893f37020.herokuapp.com` is allowed too).
- Heroku apps: `attnd-web` = production (attnd.app), `attnd-dev` = dev (deployed from `develop`).
- Changing **production** config needs the user's go-ahead. Full key and setup checklist: `SETUP.md`.

## Dependency policy

Avoid third-party libraries. Use Django and the Python standard library first; the organizer console uses
its own small design system (`console/static/console/console.css`, see Console UX), inline SVG icons
(`console_tags.icon`) and vanilla JS (`console.js`) — no CSS/JS frameworks or CDNs. The MCP server (`mcp_server/`, `docs/mcp_plan.md`) uses the official MCP Python SDK
`mcp` and `django-oauth-toolkit` (its OAuth server), approved exceptions, always the latest version, pinned in `uv.lock`, bumped with
`scripts/upgrade_deps.py`. Existing server dependencies are kept where the standard
library has no equivalent (Postgres driver, Pillow, the official Anthropic SDK `anthropic` (every AI call, stage C),
the OpenAI SDK (Studio's images only), Google auth, HTTP/2 for APNs via
`httpx[http2]`, CBOR for App Attest and passkeys; `cryptography`, installed with google-auth, verifies App Attest and passkey signatures). The Stripe SDK `stripe` is the one addition (batch 7 billing, off until its keys exist). The public site (`home/`, `join/`) uses one hand-written
stylesheet (`home/static/home/site.css`) — no vendored frameworks, icon fonts or web fonts.
Upgrade Python packages with `uv run python scripts/upgrade_deps.py` (also runs weekly in CI).

## Tech Stack

- **Python 3.15** managed with `uv` (`.python-version`; Heroku reads the same file)
- **Django 6.1** (no DRF)
- **PostgreSQL 18 everywhere**: Heroku Postgres (essential-0), CI (`postgres:18` service) and local dev/tests (`scripts/dev_db.sh`, Docker). No SQLite: without `DATABASE_URL` the settings point at the local `postgres://attnd:attnd@localhost:5432/attnd`
- **Cloudinary** for media storage in production (falls back to local filesystem)
- **Whitenoise** for static files
- **Gunicorn** with its own **ASGI worker** (no extra dependency; batch 7): `attnd/asgi.py` = Django's WSGI app on a
  fixed pool of `WEB_THREADS` (4) threads (`attnd/wsgi_bridge.py`: the same middleware and views as under gthread)
  behind `attnd/longpoll_asgi.py`, which lets the preview phones' `changes/` and Ask Attnd's `?wait=` long-polls wait
  on the event loop (no thread, no DB connection; one poller per process checks every waiter in two queries every
  0.5 s; ≤ 500 per process) and then hands them to Django marked as waited. `attnd/wsgi.py` still works (the views keep
  their thread-based wait, ≤ 2). Numbers: `docs/db_review.md` → Async server. Tests: `attnd/tests_asgi.py`.
- **psycopg 3** with Django's connection pool (`psycopg[binary,pool]`, `DB_POOL` on by default, `DB_POOL_MAX` 8 per
  process; essential-0 allows 20 connections in all); **database cache** (`django_cache` table `attnd_cache`, made by
  `createcachetable` in the release phase) for rate limits and shared caches, `caches['local']` (LocMemCache) for
  per-process rebuildable ones (the bundle, the diagnostics size)
- **Heroku** (Cedar generation, `heroku-26` stack, Basic dynos) deployment via `Procfile`

## Project Structure

```
attnd/          # Django project: settings, root urls.py, wsgi/asgi
events/         # Core models + entire REST API (apis.py, urls.py)
console/        # Organizer console (/console/): AI create, editors, review, layout, push, history, team; components/ (pieces drawn once for pages and the Ask Attnd chat)
home/           # Marketing/landing pages
join/           # /join/<code>/ redirect + AASA & assetlinks well-known files
scrape/         # AI import/refresh: crawl + Claude extraction, jobs, diff/changesets
attestation/    # Apple App Attest + Google Play Integrity + JWT issuance
credit/         # AI credit: the AiSpend ledger, monthly limits, overrides, the staff AI spend page
diagnostics/    # API-013/014: debug log uploads, crash reports, R8 retrace, request log middleware, staff panel
studio/         # Attnd Studio (admins only): AI-designed event websites hosted on SITES_HOST, flyer art, app look
prospects/      # Staff only: sales pipeline (web-search discovery of events that suit the app, organizer contacts, test events)
agent/          # Ask Attnd: the console's AI agent (registry of console views, internal requests, run loop, panel)
background/     # django.tasks on our own Postgres queue (F5): DatabaseBackend, QueuedTask, run_tasks
public/         # Files served at root by Whitenoise (robots.txt, favicon.ico + icons + site.webmanifest from scripts/make_icons.py)
brand/          # app-icon-1024.png: the source of every website icon (scripts/make_icons.py)
```

## Console UX

- **Components** (`console/components/`: `__init__.py` = registry, rendering, the generic form component; `catalog.py`
  = the data functions; templates `console/templates/components/`): the most-used pieces are drawn once and used by the
  pages **and** rendered, interactive, in the Ask Attnd chat. Each has a **data function** `(user, **ids or objects) →
  JSON` (permission checks like the views: `Http404` for someone else's event; pages pass objects their view already
  loaded), a **template** that reads only that JSON (`d`; `surface` = `page` / `chat`; `uid` prefixes ids in the chat;
  container queries in console.css, `.component` is `container: component / inline-size`; template partials let a page
  place parts apart: `{% component "event_stats" event=event part="numbers" %}`) and **actions** = registered Ask Attnd
  actions (`resolve_actions` adds url, fixed POST values and `confirm` from `agent/registry.py`). The set:
  `event_tile` (Your events; tap → `event_overview`), `event_header` (every event page's header; base.html),
  `event_stats` (timing, numbers, most liked), `live_on_phone`, `needs_review`, `event_overview` (a composite of those
  four), `location_row` / `location_form`, `schedule_item_row` / `schedule_item_form`, `notification_row` /
  `notification_form`, `event_form` (an event's details), `event_new_form` (no ids: the AI import as a background
  task, or an empty draft) and `object_history` (an event's or one item's history, see History). The **generic form
  component** (`form_json` + `_form.html`, `_form_rows.html`, `_field.html`,
  `_widget.html`) turns a bound Django form into JSON (fields, labels, help, values, choices, errors, required, the
  widget's exact attributes, a layout of rows) and draws it with the same names and ids as Django, so the agent's
  prefill and `maps.js` see the same form; the location map picker and address autocomplete stay page-only (the page's
  script), the chat gets plain address + latitude/longitude fields. `data-show-if="when:later"` (console.js) replaces
  per-page toggle scripts. Rules: a new component's actions must be registered agent actions and its `detail` /
  `parts` registered components (`console/tests_components.py`); bespoke screens (Studio editor, diagnostics, the AI
  review, prospects, history, usage, team) stay plain pages and are not shown in the chat. With the header
  `X-Attnd-Component: <name>` a page POST whose form has errors answers with just that component's chat rendering
  (`console.middleware.ComponentFragmentMiddleware`). `form_json` output may carry `hidden` (extra hidden inputs) and
  `background` (the form submits as a background task, `data-background`, instead of a chat form).
- **History of everything, the Django way** (`console/history.py`, owner Nov 21, 2026): every create / update /
  delete the Undo capture sees (console POSTs, Ask Attnd's internal requests, the chat's cards, undos; Event,
  Location, ScheduledItem, Notification, EventMember, EventInvite; also whole-event deletions, which Undo can't
  reverse) becomes a `django.contrib.admin.models.LogEntry` (ADDITION / CHANGE / DELETION, `change_message` in
  Django's JSON format with the changed fields' verbose names; deletions have none, like the admin), attributed to the
  signed-in person. `undo.finalize` writes them from the same operations as the UndoEntry, so both stay in sync. A
  `console.HistoryRecord` next to each LogEntry keeps `event_id` (also after the row or event is gone), `via` (console
  / `ask_attnd` / `chat` / `undo`), the values before → after (text trimmed, long JSON fields only named) and the
  UndoEntry. The admin's History pages (EventAdmin, LocationAdmin, ScheduledItemAdmin, NotificationAdmin, and the new
  EventMemberAdmin / EventInviteAdmin; `object_history_template = admin/attnd_object_history.html`) show Django's
  message plus "(Ask Attnd)" and old → new. The console's event **History** page starts with the `object_history`
  timeline (who, when, how, field old → new, **Undo** while possible) above the `EventRevision` snapshots (rollback,
  unchanged); location and schedule item pages and their details on the full-screen canvas show the item's own
  history. Not recorded: `QuerySet.update()` writes (drag-to-reorder) and background jobs outside a request (website
  watching). Tests: `console/tests_history.py`.
- **Undo for every change** (`console/undo.py`, owner Nov 21, 2026): per change, never a whole-event rollback.
  `UndoMiddleware` (after MessageMiddleware) wraps every console POST (also Ask Attnd's internal requests and the
  chat's component forms; not `/console/undo/` or `/console/agent/`) in a `Recorder` (context variable) that the
  `pre_save` / `post_save` / `pre_delete` signals fill for `Event`, `Location`, `ScheduledItem`, `Notification`,
  `EventMember`, `EventInvite`: the serialized row before, create / update / delete (a deleted location remembers its
  items). At the end the changed fields (bookkeeping like `content_version` ignored) become one `console.UndoEntry`
  (user, event, label = the success message, operations, 24 h, single use, `reverses` = the entry it undid). The
  success message gets `extra_tags` `undo-<id>` → base.html draws an **Undo** button (a POST form; after undoing,
  "Undone: The Nobleman Hotel: name “The Nobleman Hotel” again." with **Redo**); the response carries
  `X-Attnd-Undo: <id>`. `POST /console/undo/<id>/` (the owner of the entry only; JSON for fetch) checks every
  operation first and refuses cleanly: a sent update ("This update was already sent — it can’t be unsent."), a field
  changed again since (names who, from the newer entry), something deleted since or a row already back; then restores
  in one transaction with `save()` / `delete()` (signals: `content_version` bumps, pushes) and records the redo.
  `POST /console/undo/last/` undoes the person's latest. Both are agent writes (`undo_change`, `undo_last_change`: "undo
  that"). Ask Attnd steps store `AgentStep.undo_id` (from the header) and the chat shows **Undo** next to every changing
  step, component form save (the next page's `data-undo-id`) and confirmed chat button (`step_json.undo` = id + state
  available / used / expired). The run-level "Undo all" (History snapshot) stays. Not covered: `QuerySet.update()`
  writes (drag-to-reorder) and deleting a whole event. Expired entries are purged by `run_scheduled_tasks`. Tests:
  `console/tests_undo.py`.
- **Live editing:** every attendee-visible change bumps `content_version` and schedules a silent push (`events.push.content_changed`: the first change at once, later ones within 15 s grouped into one follow-up; API-026); it goes to every joined device with a push token, `subscribed` or not (API-007), attendees with `jitter_ms` scaled to the event (< 100 phones 1 s, < 1,000 5 s, else 10 s), preview phones with 0. **Preview phones** (API-026, `events/preview.py`): the QR card's **Preview on my phone** QR / Open as preview = the join link + a signed token (person + event, 7 days); `join/` with `preview_token` marks `Device.preview_until` / `preview_by` (not an attendee: `Device.objects.attendees()` for every count, no free spot; ≤ 3 live per event); `GET events/<id>/changes/?since=` long-polls ≤ 25 s for them only (batch 7: a 4th preview phone joins as an ordinary attendee, `preview: false`, never replacing one; the card is a guided flow: 1 scan · 2 your phone shows changes as you make them, drafts too · 3 attendees see them when you Publish, and each preview phone says "Live · seen 10 s ago") (on the event loop under ASGI, `attnd/longpoll_asgi.py`, ≤ 500 per process; under WSGI in a thread, DB connection released while sleeping, ≤ 2 per process shared with Ask Attnd's long-poll in `events/longpoll.py`; over a cap `retry_after: 15`); Remove on the card (`remove_preview_phone`). `bundle/` records `Device.last_version`, so the card's live line says "Updated on 31 of 37 phones" (attendee phones seen in the last day, `console:event_phones`, polled every 10 s). Attendee live connections: the async server is in place (ASGI), the attendee side is still ROADMAP. Open apps refresh on that push, on resume and pull-to-refresh, and poll every 60 s only without a push token (5 min safety poll with one). App layout autosaves. The "Live on your phone" panel (QR) replaces the old phone mock-up; `_phone_preview.html` (`console.views_events._preview_html`) is now only Studio's app-look preview.
- **Draft and Publish** (batch 6 §4, owner Nov 21, 2026; `events/drafts.py`): the database rows are the **draft**
  (every console save, Ask Attnd change, chat card, AI review); the organizer's preview phones (API-026) see it live
  (draft-only pushes reach only them, `push.notify_previews`; preview phones may even join a draft event). Attendees
  get the **published copy** (`PublishedBundle`: version, `source` = `content.snapshot` + the app look, `bundles` per
  catalogue version built at Publish on `SITE_URL`'s origin) until **Publish changes** (`publish_changes`, always asks:
  "Publish 5 changes to 214 phones?"), then the usual `content_updated` push. **Bypass** (never wait): sent
  notifications, the event's status / paid / listing, the Studio website's address (`drafts.sync` moves the attendees'
  version; `attendee_bundle` overlays them live). An event that becomes published gets its first copy; `bundle/` makes
  one lazily for an event without it, and the release phase runs `publish_snapshots`. **Bar** "N unpublished changes ·
  Review · Discard · Publish" sticky on every event page and the canvas (`console:event_changes?bar=1`, fetched by
  console.js, refreshed on `attnd:saved` and every 30 s), **Review** = the field rows (`unpublished_changes`
  component), **Discard** restores the published copy (`discard_changes`, asks, undoable). Website watching in
  **auto** mode publishes straight only while nobody has unpublished changes, else its changes join the draft (the
  task's step says so). The apps' contract is unchanged; ETags: attendees as before, preview phones end in `d`.
  **Autosave**: edit forms of places, schedule items and the event's details (`data-autosave-edit`) save ~1.5 s after
  typing or on leaving a field through the same view (JSON via `X-Attnd-Component`), "Saving… · ✓ Saved · Undo",
  errors under their field and nothing invalid saved, Cmd/Ctrl+S saves now, leaving with a save pending asks; saves of
  the same rows within 2 minutes are one Undo step and one History entry (`undo._grouped`, `history.regroup`).
  Creating stays explicit (Add / Save & add another), as do always-ask actions. Tests: `events/tests_drafts.py`.
- **Theme** (Nov 21, 2026, owner: Layla-like): a soft lavender tint on a light base, large rounded cards (`--radius-lg`
  18px, soft violet shadows), pill buttons, badges and chips (`.chip`), bold section titles (system font stack, no web
  fonts), tabs as text with an underline indicator, breadcrumbs with a back arrow on event pages (`.crumbs` in
  base.html; detail pages fill `{% block crumbs_more %}` / `{% block crumb_back %}`), a sticky bottom **action bar** on
  detail forms (`.action-bar` + `.action-bar__summary`; the generic form component uses it on pages, a plain row in
  the chat). Brand purple `#431f86` is the accent (`--color-brand` for fills, `--color-brand-text` for text and links).
  **Dark mode** follows `prefers-color-scheme` (tokens redefined under `:root:not([data-theme="light"])` and
  `:root[data-theme="dark"]`); use tokens, never literal colours (`--color-on-brand`, `--color-surface-2`,
  `--color-overlay`, `--color-strong`, `--color-*-text`, `--select-arrow`). **Full width**: `.page` has no max width,
  only side padding (`--gutter`); long text and forms keep `--read-width` (46rem: `.prose`, `.field`, paragraphs in
  panels and stacks; fields in grids and clusters are free). Phones: the event sections are one scrolling row of pills.
- **Design system** (`console/static/console/console.css`; the public site's `home/static/home/site.css` uses the same names): CSS custom properties for colour (`--color-brand` `#431f86`, `--color-muted`, `--color-success/danger/warning/info` + `-soft`/`-line`), spacing (`--space-1`…`--space-7`), radius, type (`--text-xs`…`--text-2xl`) and shadow. Layout: `page`, `section`, `stack` (`--tight`/`--loose`), `cluster` (wrapping row), `grid` (`grid--2/3/4/cards/stats`: at most N columns, collapses by itself), `split` (`split__main` + `split__aside`), `event-shell` + `sidenav`, `grow`. Components: `button` (`--primary`, `--outline`, `--secondary`, `--danger`, `--success`, `--ghost`, `--small`, `--large`, `--block`), `panel` (`--roomy`, `--flush`, `--accent`, `--warning`, `--danger`, `--narrow`), `list`, `notice` (`--success/warning/error/info/neutral`), `badge`, `field` (`field__label`, `field__help`, `field__error`, `field--check`), `tabs` (links use `aria-current="page"`), `data-table` (`--compact`), `code-block` (pre), `link-button`, `meter`. Text: `muted`, `eyebrow`, `small`, `strong`, `mono`, `title-2…4`, `tone-*`. Form controls are styled by element (`input`, `select`, `textarea`), so widgets need no classes (`StyledFormMixin` only rounds coordinates). JS hooks are `data-*` attributes (`data-dismiss`, `data-tabs`/`data-tab-target`, `data-confirm`, `data-copy`, `data-autosubmit`); no framework-style grid or utility classes.
- **Background tasks:** forms with `data-background` (AI import, Check for updates) submit with fetch; `/console/tasks/` is the feed; `console/static/console/sw.js` (served at `/console/sw.js`, scope `/console/`) watches running tasks and notifies; a corner tray and button spinners show progress. The tray is a button (`aria-expanded`) that opens a panel of running and recent tasks; each task expands to its **friendly steps** (`IngestJob.steps`, written by `scrape.jobs.Steps`: "Read 3 web pages", "Understood the event", "Found 12 locations and 34 schedule items", "Checked addresses on the map", "Found 4 changes: waiting for your review"; states running / done / failed / waiting, with status marks and a spinner), updated live from the same feed (`task_json.steps`, `result_label`); a finished task links to its result ("Open the event" / "Review the changes"). On the full-screen chat the tray sits at the canvas's top-right (away from the composer) and opens downward, and result links open on the canvas (`window.attndCanvasOpen`); Esc closes it. Web app manifest at `/console/manifest.webmanifest`.
- **Colour theme** (API-023, owner Nov 21, 2026; component `event_theme` in `catalog_more.py`, on the Overview page
  after the map, inside the `event_overview` composite (so the canvas Overview tab), and its own page
  `console:event_theme`): the six colours as exact hex fields (`EventThemeForm`; console.js puts a colour picker next
  to each `input[data-hex]` and keeps both in sync), a contrast note under each (`theme.checks`), a live preview per
  mode (`--pv-*` properties on the preview), **Save colours** (`update_event_theme`, prefill; locks the theme, Undo),
  **Generate with AI** (`generate_event_theme`: an `IngestJob` of kind `theme`, a background task with its step in the
  tray, "See the colours"; it uses what the event already has without asking: the import's website colours, else its
  `source_urls` and published Studio site read now through Studio's SSRF-guarded fetcher, plus up to three images: flyer,
  logo, location photos; only an event with none of these gets a note inviting a link or image; Add with AI on the
  card and the agent call it instead of asking), **Reset to Attnd's colours** (`reset_event_theme`) and two autosaving switches "Use custom
  theme on the website" / "Use custom theme in the apps" (`set_event_theme_switches`). Applying it:
  `{% event_theme_style %}` in base.html emits a nonce'd `<style data-event-theme>` overriding the tokens in the
  three places console.css defines them (`:root`, the dark media query, `:root[data-theme="dark"]`) on the event's own
  pages (`event` + `section`, not Studio) and the full-screen chat with `?event=` (`agent_event`; another event's
  tile reloads the page, so the block follows); Your events, Account, Studio, Diagnostics, Prospects keep the purple.
  `--color-secondary` / `--color-tertiary` are new tokens (decorative; `--color-brand-mid` is the readable highlight).
  The join and shared-plan pages (`join/landing.html`, `<html data-scheme="auto">`, the only pages with site.css's
  dark mode) get `theme.site_css` (brand, `--brand-gradient` primary → tertiary, the QR panel from secondary).
- **Screens** (API-024, owner Nov 21, 2026; component `app_screens` in `console/components/catalog_screens.py`, on the
  App layout page under `#screens`): per app screen (Home tab, event info, schedule item, place, map, Options) the
  sections from `events/layout.py`'s catalogue in order, with on / off switches, the header's parts, an own heading and
  a list size (`limit`), drag or ↑ / ↓ to reorder (console.js `enhanceSectionLists`, hidden `order`), autosave (the
  status line gets an **Undo** from `X-Attnd-Undo`), orders of their own for a category or tag on the detail screens
  (add / remove), **Reset to automatic**, and a phone-shaped preview beside each order drawn by one small template per
  section type (`components/screen/<type>.html`, `_screen_preview.html`) from a sample subject (the place / item with
  the most data; the apps' visibility rules in Python: `layout.visible`). The view is `console:event_screens`
  (`event_screens`; JSON answers carry the new preview); Ask Attnd: `update_screen_layout` (screen, match, sections as
  JSON), `reset_screen_layout`, `add_screen_override`, `remove_screen_override`. A screen saved once stores its whole
  spec (with the automatic per-category orders of that moment) in `Event.layout_config`; untouched screens follow the
  automatic layout. The Home tab is first and the default tab automatically for apps that send `X-Attnd-Sections` (Nov 21, 2026), and automatic tabs leave out tabs with nothing to show (`events.display.empty_tabs`: Schedule without visible items (ended ones count), Places without visible locations, Map without pins, Flyer without a flyer, Updates without a sent notification); an organizer's own tab list wins and App layout notes tabs that would be empty. **At most 5 tabs, Home included** (iOS shows five; owner, Nov 21, 2026): the tabs editor counts "5 of 5 tabs" and disables the rest ("The apps show at most 5 tabs. Turn one off first.", also `DisplayForm.clean`, so Ask Attnd gets it); an older saved list over five is cut by `events.display.fit_tabs` (Flyer first, then Places when the Map is there, never Home or Updates) and App layout says "Not shown in the apps: Flyer (5-tab limit)" (`events/tests_display_tabs.py`). Tests: `console/tests_screens.py`. **Catalogue 2** (API-027, Nov 21, 2026): new screens `schedule_tab`, `places_tab`, `updates_tab`, `onboarding`, banner sections (`status`, `update_banner`) on Home, the map and the tabs, `my_schedule` (v1 apps get `plan_today` + `liked` instead, `layout.for_version`), the tabs' defaults from `schedule_style` / `locations_style`, `featured` / `pinned` picks (`ids`) and the welcome card's `text` / `image` in the editor; `ask_about` is valid but in no default. **Live Activity** card under Screens (`live_activity_settings`, `console:event_live_activity`, Ask Attnd `update_live_activity`): `Event.live_activity` → bundle `live_activity` (`events/live_activity.py`; urgent always on).
- **Likes** (API-009 hearts; the apps say "like", the heart icon stays): net likes (hearts − unhearts, ≥ 0) from the anonymous usage counts, `console.usage.net_hearts` / `most_hearted` (code and metric names keep "heart"; labels say "likes" / "liked"): Usage page, counts on the Locations and Schedule lists, "Most liked" on the overview. **Urgent** notifications: a checkbox with a warning on the Notifications form, red rows in the list.
- **Status badges** (`_status_badges.html`): draft/live/archived, **Paid** on paid events (no "Demo" badge anywhere in
  the console since Nov 21, 2026: unpaid events keep their "14 of 20 free spots" line), happening now
  (`Event.timing`), ended, watching, unassigned.
- **Your events in sections** (batch 6, owner Nov 21, 2026; `catalog.sectioned` / `event_section`): **Happening now**
  (started, not ended, soonest first), **Upcoming** (soonest first; drafts by their dates), **Past** (most recent first,
  6 then "Show all past events") and, for staff only, **Showcase & demo** (showcase events + DEMO); counts in
  the headings, empty sections hidden. The same rules everywhere: the dashboard, the canvas Events tab and chat answers
  (`event_tile` carries `group` / `section`; `agent/canvas.py` and agent.js `appendGrouped` draw one block per section,
  `.agent__groupsec`: its heading spanning the width, then its own cards in the grid's columns). **Every section starts
  open** (owner, Nov 21, 2026); the heading's toggle folds one by hand and nothing remembers it. Also the agent's
  `list_events` / `events_json` (each event's `section`).
- **Maps and addresses** (`console/static/console/maps.js` with `GOOGLE_MAPS_BROWSER_KEY`, else console.js's
  OpenStreetMap `pinPicker`; both wired by `window.attndEnhance(scope)` for every form on a page and every chat /
  canvas card): every form with address + latitude + longitude gets Places autocomplete, or a **Find on the map** button
  that searches on the server (`console:place_search` → `console/places.py`: Google Places with the server key, else
  Nominatim, one search per click, cached a day). The location form's map shows this place as the main pin and the
  event's other locations as small muted pins (names on hover / focus), framed to fit, rounded and themed (map tokens
  `--map-*`, dark tiles dimmed, Google `colorScheme`). The **event map** (`event_map` component, on the Overview page
  and in `event_overview`) shows every pinned location; a tap opens a small themed popup with View location.
  **Fill from this website?** (`console:location_fill`, new or empty locations): Studio's SSRF-guarded fetcher + Claude
  Haiku structured output (`attnd/claude.py`) → suggestions for the empty fields only, including the Details for the
  assistant (API-018, through `events/context.normalize`); nothing saves, the fill has its own Undo.
- **Add with AI** (every form component, owner Nov 21, 2026): `console.components.finish()` stamps every form JSON
  with its component, ids and an Add with AI / Edit with AI offer; `_form.html` draws the sparkle button and
  `data-form-component` / `data-form-ids`. The button opens Ask Attnd with a prompt for that thing and remembers the
  form (`AgentRun.target`, "FORM ON SCREEN" in the context); the agent's `fill_form(component, ids, values, submit)`
  tool (agent/components.py: validated against the form's fields, ctx_ details only when empty and not hand-edited)
  is applied by the panel to that form in a chat or canvas card (never a page's own form): highlighted, one after
  another; nothing saves until they press Save or say "save it" (`submit`), and forms that always ask (sending a
  notification, team invites) are never submitted by the agent: the person presses Send and gets the confirm card. New form components get it
  for free (`agent/tests_round3.py` checks every one).
- **One confirm, floating toasts** (batch 7 Track B): no `window.confirm` / `alert` / `prompt` anywhere
  (`console/tests_confirm_toasts.py`); `window.attndConfirm(message, {confirm, danger})` → Promise<boolean> is a native
  `<dialog>` (focus on the primary button, Esc = Cancel, focus back, a pulse on open; delete / remove / unpublish /
  leave / transfer wording makes it a danger button) and every `data-confirm` button goes through it
  (`data-confirm-label` names the primary). The chat's always-ask card has the same look (summary in bold, "Nothing
  happens until you press Confirm", pulse + focus on Confirm). Messages are **toasts** (`[data-toasts]` in base.html:
  top-right on desktop, bottom on phones; info / success hide after 6 s, 10 s with Undo, errors stay; hover / focus
  pauses) and `window.attndToast(text, {level})` adds one from JS; the markup is still `.notice`, so Undo / Redo and
  Ask Attnd's page reading are unchanged.
- **Overview by importance** (batch 7 Track B, `console/tests_overview.py`): `event_status_line` first (status, plan,
  "14 of 20 free phones · Remove the limit ($299)" for unpaid events, the one next step = the first problem of
  `catalog_chat.checks`, Publish for a ready draft), then Live on your phone, AI updates to review, timing, numbers,
  the map, colours, Visibility (lifecycle) beside Share it; website watching, most liked, recent AI updates and App
  layout fold into "Settings and recent activity". The canvas Overview composite (`event_overview`) has the same
  order. `free_limit.status` is kept on the event object for the request.
- **Page ↔ chat parity** (batch 7; `console/parity.py`, `docs/parity.md`, `console/tests_parity.py`): the table is
  generated from the code: every console URL with its read action, the components its page really draws (their
  `component--<name>` classes in the page's HTML, fetched as a superuser on the DEMO event) and, per write tool, how the
  chat reaches it (a component's declared action, or a button / form URL in a component's JSON). The test fails when a
  write has no way in from a component and isn't in `TOOL_ONLY` (Ask Attnd calls it; e.g. the top bar's chat switch,
  Undo in toasts) or `SHOWN_WHEN` (a button a component draws only in some states), when a page is drawn by no
  component and isn't `BESPOKE` / in `BESPOKE_AREAS` (diagnostics, Platform, Prospects, Studio's code editor), or when
  `docs/parity.md` is stale (`UPDATE_PARITY=1 uv run python manage.py test console.tests_parity`). A new feature =
  a registered action + a component that offers it, or an entry there with its reason.
- **Width and phones** (batch 7): every component's root is `component component--<name>` (a container; container
  queries decide its layout, so the 26rem chat, a phone and a wide canvas use one template; `console/tests_phone.py`
  fails a root without it). Phones (≤ 600px, the block at the end of console.css): tap targets ≥ 44 × 44 px (buttons,
  fields, tabs, chips, summaries, checkbox labels; table checkboxes 24px per WCAG 2.5.8), fields 16px (no iOS zoom),
  map controls 44px, wide tables in `.table-scroll`. **Platform → Components** (`platform:components`, staff) draws
  every component of one event at 360 px (chat) and 768 / 1280 px (page) side by side. **Browser check**:
  `scripts/ci/phone_check.cjs` (Chrome + the `playwright` npm package) reports page-wide horizontal scroll, components
  sticking out of their gallery frame, small tap targets and small fields; `ATTND_BROWSER_CHECKS=1 uv run python
  manage.py test console.tests_phone` runs it on the console's pages (skipped otherwise, so not in CI).
- **One responsive codebase** (owner, Nov 22, 2026): layouts follow the width, never the device. **Three breakpoints**,
  written once at the top of console.css: **phone** ≤ 600px `(max-width: 600px)`, **medium** 601–1023px, **wide**
  ≥ 1024px `(min-width: 1024px)` (the only `@media` widths in the console's, Ask Attnd's and Platform's stylesheets;
  `@media` can't read custom properties); components use container queries. JavaScript asks
  `window.attndScreen.is('phone' | 'medium' | 'wide')` (`console/static/console/screen.js`, loaded first in `<head>`),
  the only width check in JS. One preference opens the console in the chat (`ConsolePreference.chat_default`; the
  phones' own `phone_chat`, picked by user agent, was merged into it by migration 0016 and `is_phone` removed). The
  floating launcher's clearance is one set of tokens in console.css (`--fab-x` for wide sticky bars, `--fab-bar-y` for
  phone sticky bars and toasts, `--fab-y` for the task tray), set only on pages with the launcher. Ask Attnd decides
  where a card, detail or form goes in one function (`placement()` in agent.js: the canvas on the wide full-screen
  page, inline everywhere else; cards render the same either way). **Guard** `console/tests_breakpoints.py`: fails on
  any other breakpoint, a `matchMedia` width check outside screen.js, or a user-agent check outside the OS-capability
  allowlist (Open in Attnd, the home page's app buttons, passkey names, sign-in session labels, join redirects, the
  retired API's update link).
- **Draw the eye** (`window.attndReveal(el, kind)` in console.js: `slide`, `in`, `pulse`, `glow`, `collapse`, `scroll`,
  `flip`; ~200–320 ms, nothing with reduced motion): canvas tab switches slide in from their side of the tab order,
  changed cards pulse, new rows fade in, deleted ones collapse, re-sorted lists glide (Scheduled → Sent), AI-filled
  fields glow in sequence, chat lines and cards fade up, task tray items appear and pulse when they finish.
- **Live numbers**: `[data-live]` blocks (the Overview's numbers, Metrics) refresh every 60 s while visible (paused
  when hidden; pages re-fetch themselves, cards in agent.js), changed numbers pulse, "Updated 3:41 PM".
- **Brand colour** purple `#431f86` (from the app icon) across console, site and apps.
- **Site icons** (every page includes `home/_icons.html`): `uv run python scripts/make_icons.py` regenerates them from `brand/app-icon-1024.png` (copy of the iOS 1024 app icon; iOS itself went back from the Icon Composer icon to a PNG app icon set on Nov 19, 2026 because `Assets.car` was ~5 MB, and the website's icons stay as they are) into `public/` — `favicon.ico` (16–256), `icon-32/96/192/512.png` (rounded corners, transparent, so Safari and other tabs show the tile without a plate), `apple-touch-icon.png` 180 (full bleed, opaque; iOS/macOS round it), `icon-maskable-512.png`, `site.webmanifest` — and the console manifest's icons. `home/tests.py` checks the sizes and links.
- **Homepage screenshots** (`home/static/home/img/demo-map.webp`, `demo-locations.webp`, 768×1670): the raw 6.3" captures `raw/iphone-63/1-map.png` and `4-locations.png` from Attnd-iOS's `store-screenshots` artifact, resized with Pillow (WebP, quality 82). Refresh them after a new screenshot run.
- **Demo data:** `python manage.py load_demo_event [--owner email]` builds the DEMO event (happening now, every feature populated).
- **Assistant eval** (`docs/assistant_eval/`, Nov 22, 2026): `questions.json` (247 platform-neutral questions attendees
  ask, silly and risky ones included, with checks) and `demo_bundle.json` (the DEMO bundle at a fixed moment, from
  `manage.py export_assistant_eval_bundle`; nothing is saved). The apps run it (iOS: router mode in its unit tests'
  critical gate and opt-in, model mode on a Mac / iPhone with Apple Intelligence; Android: router mode, the same critical gate and opt-in, model mode ROADMAP). DEMO changes the
  questions rely on are pinned by `events/tests_demo_facts.py → test_assistant_eval_facts`; after a DEMO change
  re-export the bundle and sync the apps. How to run, grading, adding questions: its README.
- **Assistant eval** (`docs/assistant_eval/`, Nov 22, 2026): `questions.json` (247 platform-neutral questions attendees
  ask, silly and risky ones included, with checks) and `demo_bundle.json` (the DEMO bundle at a fixed moment, from
  `manage.py export_assistant_eval_bundle`; nothing is saved). The apps run it (iOS: router mode in its unit tests'
  critical gate and opt-in, model mode on a Mac / iPhone with Apple Intelligence; Android: router mode, the same critical gate and opt-in, model mode ROADMAP). DEMO changes the
  questions rely on are pinned by `events/tests_demo_facts.py → test_assistant_eval_facts`; after a DEMO change
  re-export the bundle and sync the apps. How to run, grading, adding questions: its README.

## Onboarding: the home page and "Try it on your event" (owner, Nov 21, 2026; `home/`)
- **Home page** (`home/views.home`, `home/templates/home/home.html` + `onboarding.css` + `home.js`; our own design and
  words): a greeting with the Attnd mark, the **composer** (`_composer.html`: typed placeholder examples, attach a
  flyer / PDF / spreadsheet checked by its first bytes, speak (Web Speech), **Build my event**), the chips (Import from a
  website · Start from a flyer · Plan a festival · Plan a market · Just looking; they fill the composer and switch the
  **live phone preview** beside the hero, `_phone.html`: HTML/CSS drawn from the showcase looks with their themes,
  crossfades only without reduced motion), **See it live** (`_gallery.html`, `_card.html`: kind pills, Grid | Map (an
  SVG of the pins), cards with cover or theme colours, dates, swatches and a join QR; phones get Open in Attnd), feature
  sections, a second composer, SEO (`schema.org` JSON-LD with the nonce, Open Graph + Twitter card, `og.png` from
  `scripts/make_og_image.py`) and anonymous counts of its own taps (`home.HomeMetric` per day: chip, submit, qr_view,
  open_app, claim, gallery_filter; `POST /home/m/` by sendBeacon, no identifier). Works without JS. `<html
  data-scheme="auto">` (dark mode).
- **The composer** (`POST /start/`, `home.onboarding.save_pending`, `pending_import_ip` 10/h): the text and file wait in
  a `home.PendingImport` (its id in the session; purged after a day by `run_scheduled_tasks`); signed out → sign-up
  (`?building=1`) or sign-in; the console's dashboard then `consume`s it: an `IngestJob` (`trigger='home'`, a demo event
  they own) → the job page (steps) → the event in the full-screen chat with its QR. New accounts (< 7 days) start at most
  `NEW_ACCOUNT_DAILY_IMPORTS` (3) AI imports a day.
- **Signed in with no events**: the dashboard shows the same hero, composer and gallery (`console/onboarding.html`, "Try
  one on your phone first") instead of the chat; staff get **Preview onboarding** on the Platform page (`?onboarding=1`,
  nothing of theirs changes). **Chat as the default view**: `ConsolePreference.chat_default` is on for new accounts and
  saved preferences that never touched it (`CHAT_DEFAULT_ON`, off in tests; migration `0014`).
- **Showcase events** (`events/showcase.py`, `manage.py load_showcase_events [--if-enabled] [--images]`): eight demo
  events (festival, farmers market, tech conference, 5K, art walk, food truck rally, holiday market, county fair) with
  places, schedule, details, a theme and two updates, `Event.showcase` (no free limit, never nearby, no usage counts),
  kept happening now by `run_scheduled_tasks`; the release phase loads them where `SHOWCASE_EVENTS` is on (default:
  dev.attnd.app and DEBUG only). `--images` asks Studio's image model for covers (stored as the flyer). DEMO is
  unchanged and is one more card.
- **Happening near <place>** (`home.onboarding.gallery`): published prospect test events (`testing_only`, linked to a
  prospect, not yet claimed). With `GOOGLE_MAPS_BROWSER_KEY` set, `home.js` asks Google's **Geolocation API from the
  browser** (`POST https://www.googleapis.com/geolocation/v1/geolocate?key=…`, `{"considerIp": true}`; the browser, so
  Google sees the visitor's IP and not Heroku's) at most once a day per browser (`localStorage attnd.home.geo`, rounded), no permission
  prompt, rounded to 0.1° before `GET /home/near/?lat&lng` (`home_near_ip` 30 / 10 min; never stored or logged) picks the
  cards within 150 miles and names the nearest card's city ("Happening near Fort Worth"). No key, a failed call or
  nothing near → every card with the prospects region as the label (`clean_region`: first line, empty parts dropped).
  CSP `connect-src` has `https://*.googleapis.com` only with the key. No "Use my location" / `navigator.geolocation`.
  Owner task: enable the Geolocation API on the browser key (SETUP.md). Each card says **Is this your event? Claim it**
  (`/claim/<code>/`, sign-in required): email, role, note → `prospects.ClaimRequest` with a domain hint (the email's
  domain is the event's website or a contact's); staff decide in **Prospects → Claims** (`approve_event_claim` always
  asks, `decline_event_claim`): approving hands the event over (owner = them, no longer testing-only, still unpaid),
  the prospect → Claimed, History records it. Never automatic.
- **Layout** (owner, Nov 21, 2026): a tight hero (small phone preview that never makes it taller) so the gallery
  ("See it live" / the console's "Try one on your phone first") starts within a 1440×900 screen; its header carries the
  **instant App Clip** in three steps (Scan → App Clip opens → You're in) and the accurate Android line (the code opens
  Google Play; once installed Attnd opens on the event through the install referrer; no Instant App promise).
- **Cards** (`_card.html`, `onboarding.card`): every card has a cover: the event's flyer (not a PDF) or a place photo,
  else `Event.cover` from **`events/covers.py`**: a licensed Wikimedia Commons photo near the event's pin (1 km) or named
  like its venue / event in its city, landscape, ≥ 600 px, confirmed by Claude Haiku's vision, credit ("author · licence
  · Wikimedia Commons") shown small on the cover; else an AI picture (Studio's image model, "AI-generated image";
  showcase events always AI, they're made up). **Never** an image from the organizer's own website. Runs 2 min after a
  prospect test event's import finishes (`scrape.jobs` → `covers.schedule`), catches up in `run_scheduled_tasks`
  (`covers.refresh_due`), backfill `manage.py find_event_covers [--event CODE] [--no-ai] [--force]`;
  `EVENT_COVERS_DAILY_CAP_USD` (2) caps every look per UTC day (`Event.cover_cost_usd`). Not in the API, no
  `content_version` bump. **Event website ↗** (`target=_blank rel="noopener nofollow"`): the published Studio site, else
  `source_urls[0]`, https only; none for showcase events.
- The old home page's anonymous trial import (`POST /scrape/`, `scrape:job_status`, sign-up `?claim=` for `trialuser`
  events, `TRIAL_IMPORT_OPEN`) is gone; `expire_trial_events` still clears leftover trial events.
  Tests: `home/tests_onboarding.py` (incl. `GalleryFeedbackTests`, `EventCoverTests`), `events/tests_free_limit.py`.

## Diagnostics (API-013, API-014)

App logs, crash reports and server timings for staff; nothing organizer-facing. The shared record format and every
event name the apps may log are in **`LOG_EVENTS.md`** (add a row there before an app logs a new name).
- **Uploads** (`diagnostics/api.py`, `ingest.py`, `records.py`): `POST /api/v2/diagnostics/logs/` (a debug session's
  gzip JSON batch: ≤ 500 entries, ≤ 256 KB sent / 2 MB decompressed; each record validated on its own and scrubbed
  again server-side) and `POST /api/v2/diagnostics/reports/` (anonymous crash/hang/ANR/exit reports). A session is
  keyed only by its debug code `D-XXXXX` (Crockford base32), never a device id. Caps: 20,000 entries per session per
  10 min, uploads per IP (`RATE_LIMITS['diagnostics_ip']`, `diagnostics_reports_ip`) and the storage cap, all `429` +
  `Retry-After`. CI keys get the normal answer and nothing is stored.
- **Storage** (`models.py`): `DiagnosticSession`, `DiagnosticEntry` (indexes session+ts, level, cat, name, a pg_trgm
  GIN on `UPPER(msg)`, BRIN on `received_at`; needs `django.contrib.postgres` and the `pg_trgm` extension from
  migration 0001), `DiagnosticReport`, `SymbolFile` (gzip R8 mappings, newest 20), `EndpointTiming` (hourly
  histograms). `storage.used_bytes()` = `pg_total_relation_size` of the three log tables, cached 60 s.
- **Retention** (`purge.py`, every `run_scheduled_tasks` run, also `manage.py purge_diagnostics`): entries from dev
  builds 7 days, debug mode 14 days (`DIAGNOSTICS_RETENTION_DAYS`), reports and timings 30 days; above
  `DIAGNOSTICS_MAX_BYTES` (250 MB of the 1 GB essential-0 database) the oldest sessions go first, then `VACUUM FULL`
  so the measured size drops (skipped inside transactions, i.e. tests).
- **Request log** (`middleware.py`, first in `MIDDLEWARE`): one JSON line per `/api/` request on logger
  `attnd.request` (method, path *template* with `{id}`, status, ms, `req_id`, `diag`, platform; never the query
  string), `X-Request-Id` echoed or made; with `X-Diag-Session` on an authenticated, non-CI request (not the uploads
  themselves) the line is also stored in that session as `server.request` (category `server`). `ApiKeyRequired` sets
  `request.api_authenticated`. Timings go to `timing.py` (in memory, flushed once a minute; `DIAGNOSTICS_TIMING`
  is off in tests unless a test turns it on).
- **Symbols:** Android CI uploads `mapping.txt` to `POST /api/diagnostics/symbols/` (CI key, or the static key where
  enabled); `retrace.py` is a pure-Python R8 retrace (line ranges, inline chains, ambiguous frames with `<OR>`,
  exception class names), applied automatically to Android reports of a build with a mapping. iOS: the panel's
  **Symbolicate** dispatches `symbolicate.yml` in Attnd-iOS through the GitHub REST API (`GITHUB_SYMBOLICATE_TOKEN`,
  SETUP.md step 14) with a one-time token (2 h, SHA-256 stored); the workflow reads the frames from and posts
  symbols to `/api/diagnostics/symbolicate/<id>/` (`symbolicate.py`).
- **Panel** (`views.py`, `templates/diagnostics/`, `static/diagnostics/diagnostics.js`): `/console/diagnostics/`
  (`is_staff` or superuser, else 404; the console's 2FA middleware applies; a Platform sub-tab). Sessions
  list + search, session page (summaries, canvas lanes timeline, filters, entry list, network waterfall, server lines
  joined by `req_id`, `.jsonl` export, delete), reports grouped by signature, endpoint p50/p95 table, Android mappings
  with paste-and-retrace, API-014 performance buckets. App text is untrusted: templates escape it, the JS only uses
  `textContent`; no inline scripts.
- **API-014:** `perf_*` usage metrics with bucket details (`events/usage.py` `PERF_METRICS`); not interactions.

## Emails (batch 7 Track B; `console/mail.py`, `templates/emails/`, `platform_admin/views_email.py`)

- **One way to send**: `console.mail.send(kind, to, subject, heading=, paragraphs=, code=, details=, button=, after=,
  reply_to=, attachments=)` renders `templates/emails/message.html` (branded card, logo, dark mode via
  `prefers-color-scheme`; the only place literal colours are allowed: email clients ignore CSS custom properties) and
  `message.txt` (the plain-text part) from the same parts. No `send_mail` / `EmailMessage` anywhere else
  (`platform_admin/tests_emails.py` fails on one). Password reset goes through `views_auth.BrandedPasswordResetForm`.
- **Kinds** (`mail.KINDS`: label + category): **optional** (import done, review waiting, notification sent, AI credit
  warnings, website form entries) = one message per person, only when `ConsolePreference.notify[kind]` isn't off, with
  RFC 8058 one-click unsubscribe (`List-Unsubscribe: <…/email/unsubscribe/<signed token>/>` + `List-Unsubscribe-Post`;
  the page asks on GET, a POST unsubscribes without a session, "Turn them back on" undoes it; Account → Email me when…
  lists the same kinds); **transactional** (invites, receipts, claim approved, website review), **security** (password
  reset, sign-in codes, new sign-in) and **internal** (to `ADMINS`: upgrade requests, claims, help requests, platform
  alerts) can't be unsubscribed. A new email = a kind in `KINDS` + `mail.send`.
- **Emails log** (`platform_admin.EmailLog`, Platform sub-tab **Emails**, `platform_admin.view_emaillog` = Staff): one
  row per recipient (to, kind, subject with 6-digit codes masked, status sent / logged / blocked (allow list) / skipped
  (turned off) / failed, Mailgun's later events); **never the body**. Kept `EMAIL_LOG_DAYS` (90), purged by
  `run_scheduled_tasks`. `EMAIL_ALLOWED_RECIPIENTS` still applies (blocked rows are logged; the SMTP backend still
  drops them too). **Mailgun webhook** `POST /email/mailgun/` (HMAC of timestamp + token with
  `MAILGUN_WEBHOOK_SIGNING_KEY`, ≤ 15 min old; 404 without the key, 406 on a bad signature): delivered → delivered,
  failed → bounced (permanent) / delayed, complained, unsubscribed (also turns that kind off). Our own `Message-ID`
  joins them. The Architecture page's Mailgun status reads the log (24 h sent vs failed / bounced).

## AI credit (`credit/`, batch 6 §2, owner Nov 21, 2026)

Every AI call goes into one ledger and counts toward a monthly credit per person.
- **Ledger** (`credit/ledger.py`, `AiSpend`): `ledger.record(cost, model, provider, tokens, request_id)` is called by the
  shared helpers (`attnd/claude.py` `_ledger` for every Claude call, Ask Attnd's loop, Studio's text and image calls);
  who and what come from `spend_context(user, event, feature, background, visitor, exempt)`, a contextvar set by
  `SpendContextMiddleware` (the signed-in person, else a lazy visitor key; the feature by URL name) and by the
  background entry points (`scrape.jobs.run_job`, `place_images.find_for`, `plan_suggestions.generate`, `covers.ensure`,
  `prospects.discover.execute`). A new AI entry point outside a request must open a `spend_context` itself.
- **Counting rules**: a person's own requests count to them; background work for an event (photos, plan suggestions,
  website watching) to the event's owner; not counted (`counted=False`): Prospects, covers, showcase / DEMO / App Review
  sample events, CI. Staff are counted but have no monthly limit.
- **Limits** (`AiLimits`, pk 1, staff-editable live, cached 30 s; first values from `AI_MONTHLY_CREDIT_USD` 30,
  `AI_VISITOR_CREDIT_USD` 5, `AI_PLATFORM_ALERT_USD` 100 and the old per-day caps, which now read from it too);
  `AiLimitOverride` per person (an amount or no limit). Calendar month in UTC.
- **Enforcement**: `@ai_credit_required` (`credit/decorators.py`) on the AI views (website check, paste import, colours,
  Fill from website, Studio design / start / bring in), the AI path of New event, the home composer's `consume` and
  Ask Attnd's start: "You've used this month's AI credit ($30). It renews on Dec 1." (429 JSON or a message); editing,
  publishing and notifications keep working. Background work checks `ledger.owner_allowed(event)`.
- **Warnings**: 80 % and 100 % once a month each (`AiCreditNotice`): an email and a line above the Ask Attnd composer
  (`run_json.credit_note`); the platform alert emails `ADMINS` once a month.
- **Screens**: Account → **AI credit** (`ai_credit_meter`: used / left / renews, by feature); staff **AI spend**
  (`/console/ai-spend/`, `credit:spend`, the Platform sub-tab "AI spend" and the canvas Platform tab): `ai_spend_summary` (month, by feature /
  model / person / event, latest calls, CSV `credit:export`), `ai_limits_form` and `ai_limit_overrides`. Changing a
  limit or an override always asks (page `data-confirm`, Ask Attnd `save_ai_limits` / `set_ai_limit_override` /
  `remove_ai_limit_override`, confirm=True) and is a History `LogEntry`.
- **Anonymous visitors**: AI still needs an account (the home composer waits for sign-up); their visitor key is
  tracked anyway, and any spend under it moves into the account at sign-in (`credit/signals.py`).
- Tests: `credit/tests.py`.

## Prospects (staff only; `prospects/`)

A sales pipeline for the growth plan (offer organizers the free app first, then sell Attnd Studio): find upcoming
events in a region that suit the app (outdoor or mostly outdoor, people walk between several stops), keep the
organizer contacts their own pages publish, and make testing-only copies of them.
- **Access**: `/console/prospects/` (Platform sub-tabs "Prospects" and "Claims" for staff), `is_staff` or superuser, else 404
  (`diagnostics.views.staff_required`; the console's 2FA middleware applies). Tabs: Prospects (search, filters, sort,
  bulk actions, CSV), Runs (Run now, log, cost), Settings.
- **Models** (`prospects/models.py`): `ProspectSettings` (singleton pk 1: region, time zone, themes one per line,
  horizon days, new events per run, schedule on/off + interval days, last run), `ProspectRun` (trigger, status,
  found/new/updated/skipped, cost, log), `Prospect` (event, organizer, dates, city, venue, urls, fit 0–100 + reasons,
  traits, pipeline status, notes, dedupe keys `url_key` (host without www + path) and `name_key` (normalised name +
  year), linked `Event` and `IngestJob`), `ProspectContact` (`source_url` required, `do_not_contact`),
  `ProspectActivity`.
- **Discovery** (`prospects/discover.py`): A) **Claude Sonnet with Anthropic's web search** (`web_search_20260209`,
  `PROSPECT_MODEL`; Haiku can't search; JSON in the reply, `pause_turn` resumed), 5 themes per call;
  B) each unknown candidate (not a known prospect by either key, not an `Event.source_urls`) is read with Studio's
  SSRF-guarded `studio.importer.fetch` + `scrape.service._page_text` (its page plus ≤ 2 same-site about / contact /
  sponsor / vendor pages), then one Claude Haiku structured call (`PROSPECT_SCORE_MODEL`) scores the fit and lists contacts. **Contacts are checked in code**:
  the `source_url` must be a page we fetched and an email / phone is kept only when it is written on that page (never
  guessed). Known prospects are merged (dates, links, blanks), never their status or notes. Cost = `studio.ai.token_cost`
  (`STUDIO_PRICES`) + `PROSPECT_WEB_SEARCH_CALL_USD` per `web_search_call`. A run stops at `PROSPECT_RUN_MAX_MINUTES`,
  at its cap of new events, or at `PROSPECT_DAILY_SPEND_CAP_USD` (all runs, UTC day). `claim` locks the settings row,
  so runs never overlap (a running one older than the time cap + 10 min counts as interrupted).
- **Running it**: "Run now" (a daemon thread in the web dyno, like AI imports), `python manage.py find_prospects
  [--max N]` (inline), or the schedule: `run_scheduled_tasks` calls `prospects.schedule.maybe_run()` (inline, only
  when enabled and `interval_days` have passed since the last run started; off by default; a scheduled run stops
  after 8 minutes so it ends before the next scheduler dyno).
- **Test events** (`prospects/testing.py`): "Create test event" (detail or bulk) queues the normal AI import
  (`IngestJob`, `trigger='prospect'`, `testing_only=True`) owned by the staff member who clicked. The event is
  published, `public=False`, `paid=False` (Demo) and `Event.testing_only=True`: never in `nearby` (`events.apis`),
  never website-watched (`refresh_schedule` off, `watch.due_events` excludes it), never brought into Studio; lookup by
  code / QR and join work. Console badge "Testing only". `testing_only` is not in the API (the contract is unchanged).
  When the job is done the prospect links the event and moves to "Test event created". Nobody is invited or emailed.
- **Outreach rules**: Attnd never emails prospects itself. People who reach out follow CAN-SPAM (identify the sender,
  a physical mailing address, an easy opt-out that is honoured) and never contact a contact marked do not contact (the
  CSV leaves out their email, phone and profile). The dashboard says so. CSV export is formula-safe (`csv_safe`).

## Ask Attnd (console agent; `agent/`)

An AI agent in the bottom-right corner of every signed-in console page (`agent/templates/agent/_panel.html`, included
by `console/base.html` through `{% agent_panel %}`; never on the public site, Studio sites, `/accounts/`, the Account
page or the admin). For every console user. **Every tool runs the real console view on the server as the signed-in
person**, so permissions, validation, History, `content_version` bumps, pushes and signals are exactly what a click
on Save does; there is no second copy of any business logic.
- **Registry** (`agent/registry.py`): `@agent_action(name, description, kind='write'|'read', confirm=False,
  summary=callable, form=FormClass, prefix=, fixed={'action': 'delete'}, params={…}, build=callable, url_name=,
  prefill=True|'url:name', json=True, file_params=, returns_page=, audience=)` stacked on the view (outermost). A
  write is a tool: its strict schema = URL kwargs + the form's fields (from an unbound instance, so fields added in
  `__init__` count; types, choices ≤ 40 as enums, help text; null = keep / leave empty) + hand-written `params`.
  A read is a page `read_page` may open (and `list_pages` lists). Who sees a tool is its view's declared permission
  (`Action.permission`, see Declaring permissions; `audience` is only a label): platform tools (Prospects,
  Diagnostics, AI spend, Studio reports, the Platform admin, `admin_usage`, `assign_event_owner`) aren't even in the
  tool list for anyone without that permission.
- **The rule: every new console URL must be registered (read and/or write) or listed in `registry.EXCLUDED` with a
  reason, and every view that handles POST needs a write action (or `POST_EXCLUDED`)**, else
  `agent.tests.RegistryTests.test_every_console_url_is_registered_or_excluded` fails CI. A new `data-confirm` button
  fails `test_every_data_confirm_button_is_an_always_ask_action` until its action is `confirm=True`.
- **Internal requests** (`agent/internal.py`): a `RequestFactory` request through the full middleware stack
  (`BaseHandler`) with the person's own session loaded read-only (`agent.middleware.SessionMiddleware` replaces
  Django's: an in-process `agent_session` is used and never saved; signing out or a new 2FA step stops the run).
  CSRF is waived (`_dont_enforce_csrf_checks`, server-side only). Writes with `prefill` first GET the page and post
  the form's current values like a browser, with the model's arguments on top; results: redirect + messages, form
  errors by field (`agent/pages.py` reads `.field__error`, `errorlist`, error notices), or the endpoint's JSON.
  Requests carry `X-Attnd-Agent: <run>/<step>`, log one line on `attnd.agent`, and History revisions made meanwhile
  get "(Ask Attnd)" (`agent.context`). `agent/pages.py` turns pages into compact text (headings, `key: value`,
  table rows, form fields with values, buttons, console links as `(→ /console/…)`), trimmed with an offset cursor.
- **Run loop** (`agent/loop.py`): `AgentRun` / `AgentStep` / `AgentUsage` / `AgentAttachment`; a daemon thread per
  run (after commit), the panel polls `GET /console/agent/runs/<id>/?after=<seq>` every 1.5 s. Tools: the
  registered writes allowed for the person + `read_page`, `list_pages`, `list_events`, `search_events`, `ask_user`,
  `finish`; strict schemas, no parallel tool calls, **one** reasoning effort `AGENT_EFFORT` (default `medium`) for
  every call of a run and across the conversation (Nov 21, 2026: switching effort between calls missed the prompt
  cache each time; `tests_claude` checks every request's parameters are identical apart from the messages; the old
  `AGENT_PLANNING_EFFORT` is still read as a fallback, `AGENT_STEP_EFFORT` is ignored). **Provider** (`agent/providers.py`, separate from Studio's
  `STUDIO_AI_PROVIDER`): `AGENT_PROVIDER` = `anthropic` (default when `ANTHROPIC_API_KEY` is set) → `ClaudeProvider`,
  the official `anthropic` SDK, Messages API with our own tool loop, `AGENT_ANTHROPIC_MODEL` `claude-haiku-5-5`
  (owner, Nov 21, 2026): no `thinking` (adaptive is the default), `output_config.effort` = `AGENT_EFFORT`, no
  temperature/top_p/top_k, no prefill, `strict` only on Ask Attnd's own small tools (`STRICT_TOOLS`, not show / get_component / fill_form: found on dev, the API
  takes ≤ 20 strict tools, ≤ 24 optional and ≤ 16 union-typed parameters across strict schemas, and the ~80 form-derived write schemas are a "compiled grammar too large"; the views validate
  writes; a 400 about strict → resent without, remembered per process), tool schemas sent through `tool_input_schema`
  ("null = keep" becomes an optional plain-typed property, so no union types; `agent/tests_limits.py` checks every
  audience and area stays ≤ 8 unions and ≤ 24 strict optionals) and `claude_schema` (min/max/length/pattern dropped); API errors reach the chat
  only as a friendly line (`providers.FRIENDLY_ERROR`; status, type and request id logged on `attnd.agent`, kept in
  `AgentRun.error_details` and shown to staff under a folded Details), `tool_choice` auto + `disable_parallel_tool_use`, cache breakpoints on the stable system prompt
  (tools + system) and on the newest message, every assistant block (thinking included) passed back unchanged,
  append-only history; `stop_reason` checked first: `refusal` → the run continues on Claude Sonnet
  (`AGENT_REFUSAL_MODEL` claude-sonnet-5-5, `loop._refusal_fallback`, from the same context block + what was done),
  `max_tokens` → one retry at 32K, `pause_turn` → continued; the SDK's typed errors become messages. No OpenAI
  provider since stage C (paused runs from before continue on Claude). A paused
  run stores `provider_name` so it resumes on the same provider. Every call's cost via `studio.ai.token_cost`
  (cache reads ×0.1, Anthropic cache writes ×1.25) into `AgentUsage`. Each run starts with the **context block**
  (`agent.prompts.context_block`, in the first user message after the cached prefix): now in UTC and in the event's
  zone, the current event as JSON (id, name, code, status, zone, start/end in its zone, each event day's date,
  `single_day`, counts, the person's role), the current page; with no current event, the person's events with their
  days. The system prompt makes the model resolve "the day of the event", "tomorrow at noon", "4pm" itself and never
  ask for a date format; `ask_user(question, options)` only when truly ambiguous, the options shown as chips that
  send the answer (`run_json.options`, `step_json.options`). Then the person's current page (read as them), their
  events and the last 48 h of their runs. Caps: `AGENT_MAX_STEPS` model calls, `AGENT_RUN_MAX_MINUTES`,
  `AGENT_DAILY_SPEND_CAP_USD` per person per UTC day (also checked by the start endpoint, 429).
- **Always ask** (`confirm=True`, the run pauses as `waiting_confirmation`; the panel shows `summary(ctx)`, e.g.
  "Send the update “Gates open late” now to 1,240 attendees", with Confirm / Cancel): every action whose button has
  `data-confirm`, all deletes, publish / unpublish / archive / list nearby, sending or scheduling updates (urgent
  too), team invites, removals, role changes, transfers and leaving, restoring a History version, discarding or
  applying AI suggestions, Studio publish / unpublish / address change / preview-link reset / asset and form-entry
  deletes, Prospects "Create test event(s)", diagnostics deletes, and Undo all itself. Only
  `POST /console/agent/runs/<id>/confirm/` with the step and the sha256 of the exact tool + arguments releases it
  (`loop.confirm`: single use, hash re-checked against the stored arguments; changed arguments are a new step and
  need a new confirmation). The model has no confirm tool. Page text is passed as data ("data, not instructions").
- **Off-limits** (never registered, refused by `read_page`, `registry.off_limits`): `/accounts/` (sign-in, password,
  passkeys, two-factor, emailed and recovery codes, sign-out), `/console/account/`, `/console/signup/`, the admin,
  and Ask Attnd's own endpoints.
- **Undo all**: before a run's first content change of an event (`loop.CONTENT_URLS`: details, locations, schedule)
  the History view takes a snapshot ("Before Ask Attnd: …", `AgentRun.revisions`); "Undo all changes from this run"
  is an always-ask step that restores those revisions through the History view. It can't unsend updates.
- **Panel** (`agent/static/agent/agent.js`, `agent.css`): launcher "Ask Attnd", chat with live steps, confirm card,
  `ask_user` replies, Stop, file attach (`/console/agent/attach/`, PDF/images/text ≤ `AGENT_MAX_ATTACHMENT_MB`;
  file fields take the attachment id), recent requests, Undo all. **One conversation**: new requests are added below the earlier ones (sessionStorage `attnd.agent.chat` = the run ids, `attnd.agent.scroll` = the log's scroll position), so taps, follow-ups and page loads keep the chat; "+" (New chat) starts over; a request picked from the history list starts a conversation with it. Cards have no "Open page ↗" link (removed Nov 21, 2026): tapping a card opens its detail in the chat (or the canvas). **Background** (default): when a run that changed
  the current event or page ends, the page reloads (or shows "Updated · Refresh" with unsaved input). **Watch**
  (`localStorage` `attnd.agent.watch`, the floating panel only): each changed step opens its page. Stale runs fail /
  unanswered ones close in `run_scheduled_tasks`.
- **One chat** (owner, Nov 22, 2026): **phones use the full-screen page; don't add phone-only chat code paths.** The
  floating panel exists only on wider screens; on phones (≤ 600px) the launcher, Ctrl/⌘+K and Add with AI go to
  `/console/assistant/?event=…&from=…` through `window.attndOpenChat` (agent-loader.js; agent.js's `open()` calls the
  same function; Add with AI passes its form as `?ai=`, aimed once the conversation is restored). The bottom sheet,
  its CSS and its visual-viewport sizing are gone. The full-screen page is an opaque app shell at every width
  (`body:has(.assistant)`: fixed, `height: var(--app-height, 100dvh)`; agent.js passes the visual viewport's height
  for iOS Safari, whose keyboard only shrinks that), so nothing of a page shows through and the page behind never
  scrolls; on phones it hides the top bar (Exit returns to the page), runs edge to edge with safe-area padding, puts
  toasts at the top, 44px targets and a 16px composer, and the section chips step aside while typing. The log keeps
  its bottom anchored when it changes height (keyboard, chips; a ResizeObserver while following) and a saved scroll
  position is `bottom` when the person was at the bottom. **Recent requests** drop over the log as a sheet (never in
  its flow), closed by a pick, Esc, × or a tap outside; a pick continues that run's thread. **Session jump** (the
  owner's "jumps to some other session"): a send while an earlier run waited for a confirmation or an answer was
  refused (409) and the chat replaced the whole log with that run; now `agent:start` stops runs that only wait
  (their confirmations expire) and starts the request in the conversation on screen; only a run still working is
  refused, and the chat then keeps its log (another tab's run is added below). `agent:runs?chat=` returns the
  conversation's own `thread`, so an active run of another conversation appended to the restore no longer takes the
  thread over. Tests: `agent/tests_phone_chat.py` (and, with `ATTND_BROWSER_CHECKS=1`, `scripts/ci/chat_check.cjs` on an
  emulated iPhone 15 Pro and 1440 × 900: open, send, keyboard, history, close and reopen; `ATTND_CHAT_SHOTS=<dir>`
  saves screenshots).
- **Full screen** (`/console/assistant/`, `console:assistant` → `agent.views.assistant_page`, template
  `agent/assistant.html`; in `registry.EXCLUDED` and `OFF_LIMITS_PREFIXES`, so the floating panel isn't drawn there):
  the panel header's **Full screen** button (≥ 1024px only, `data-agent-full`) opens it with `?event=` (checked with
  `can_edit`) and `?from=` (a console path, not off-limits; the requests are about that page); **Exit** goes back (wide screens:
  and reopens the floating panel). Phones reach it from every opener (see One chat). Same conversation (sessionStorage chat ids, scroll). Layout (Layla's chat + canvas):
  the chat column on the left (~36%), the **canvas** on the right (`data-canvas`): what the chat `show`s appears there
  at full size, grouped into underline tabs Events · Overview · Locations · Schedule · Updates (arrow keys move
  between tabs; with `?event=` the event's overview, locations, schedule and updates are preloaded), while the chat
  shows each card as a compact tile (initial, title, kind, ›). Tapping an item (tile, card body or title) opens its
  `detail` component full width as an **overlay** on the canvas (`data-canvas-overlay`, `role=dialog`): a breadcrumb
  (Locations › Main Stage › …; earlier levels are buttons) and a back arrow; nested opens push, Esc / the arrow pop,
  focus moves to the overlay, Tab is trapped inside and focus returns to the opener. Cards there are the chat's
  interactive components (forms save in place through the views, always-ask buttons still confirm). A pick is
  recorded in the run (`components/pick`) when there is one, else the detail is fetched directly (`detail_ids` on
  each rendered card). Narrower than 1024px the page works like the panel (cards in the chat, no canvas).
  Each tab holds **sections**, one per component (`data-section`): with `?event=` the canvas preloads Overview,
  Locations, Schedule and **Updates** (the `notification_form` compose card, then "Sent and scheduled": every
  `notification_row`; sending and scheduling still go through the confirm), empty lists say so. A list section keeps
  its parent ids and reloads as a whole when anything of that event changes (one request; new rows appear, deleted
  ones go). Tab toolbars add things in the overlay (Add location, Add schedule item); Locations can be dragged into
  order (`reorder_locations`, the page's endpoint). Links inside canvas cards to events, locations, the schedule and
  updates open on the canvas (`ROUTES` in agent.js, `window.attndCanvasOpen`), not as pages; a new location / item
  saved in the overlay goes back to the list. **Back animation**: open slides in from the right with a fade; Back
  (arrow, Esc, a crumb) slides the level out to the right with a fade and brings the one below back, nested levels
  animate between them, none with `prefers-reduced-motion`. Always-ask buttons work before anything was asked: with
  no run, `components/act` starts a quiet one (`BUTTONS_GOAL`, status done, no model call; `run_json.buttons`, its
  goal isn't drawn as a message) to hold the confirmation.
  **Everything in full screen** (owner, Nov 21, 2026): the Events tab (every event, toolbar **New event** → the
  `event_new_form` in the overlay: the AI import runs as a background task with its steps in the tray, an empty event
  opens full screen for it); Overview (the overview with publish / unpublish behind the confirm and the live-on-your-
  phone QR; toolbar **Edit details** → `event_form`, **History** → `object_history`); Locations and Schedule (add,
  edit with the item's history under the form, hide / show, delete with confirm, reorder locations); Updates
  (compose, schedule, send with confirm, send now / delete scheduled ones). Tapping another event's tile opens full
  screen for that event; links in cards, the top bar's Your events / New event and the task tray's results stay on the
  canvas. Bespoke screens (Studio, the AI review, diagnostics, prospects, the map pin picker) still open as pages.
- **Canvas layout rules** (owner, Nov 21, 2026; `agent/tests_canvas_width.py`): **any canvas tab must use the full
  width**: no `--read-width` or other max-width on a pane, its grid or a section (`.canvas__pane, .canvas__grid,
  .canvas__section { width: 100%; max-width: none }`); a section with one card spans the row; tabs with several sections
  put them in columns on wide canvases (container `pane` ≥ 48rem: Notifications compose | Sent and scheduled; Settings,
  Metrics and History in auto-fit columns, App layout across the row; Team: members beside the invite form); forms
  inside keep readable field widths. A new canvas tab with several sections needs its column rule. **Tab bar** (owner, Nov 21, 2026): scrolls sideways only (`overflow-y: hidden`, vertical wheel → sideways), a themed edge fade (mask) and a chevron on each side with more tabs (ResizeObserver), the active tab kept in view; toolbar buttons get "+" only for adding (Edit details has a pencil). **Add / Edit with AI** shows its form as a pending line in the chat (✎ "Filling in X · tell Attnd what to change", × cancels) instead of a pill above the composer; once sent, the run's thinking line and steps take over. **Tab counts**:
  `canvas.ALERT_COUNTS` (Review) are filled badges (`.tab-count--alert`, `--color-danger` / `--color-on-danger`,
  defined for light and dark), informational counts are muted (`.tab-count`); agent.js `setTabCount`. **Height**: on
  desktop the full-screen chat fills exactly `100dvh` under the top bar (body flex column, no outer scroll).
  **Background tasks in the chat** (batch 5, Nov 21, 2026): a step that started a background job (its JSON answer
  `{"task": …}` → `step_json.task`) is drawn as a **live task card** at that step (agent.js `taskCard`): title,
  spinner, each friendly step (✓ / running / ×), the current step as a hint, then the result link ("Review 4
  changes") or the reason in plain words + **Retry** (`console:task_retry`, agent write `retry_background_task`: the
  same sources again; a failed paste with a file needs the file again). It polls `console:tasks?ids=` (those tasks
  whatever their age) every 2 s while running. Tasks without a card keep the working line above the composer
  (`[data-agent-tasks]`, console.js `renderChatLines`), then a done line with its result link; the full-screen
  chat (desktop and narrow) shows no task tray at all (owner, Nov 21, 2026: the work is in the chat: live task cards and
  these lines); the tray stays on ordinary console pages. Task cards update progress steps in place (one row that counts
  up, then one ✓ with its done text: Studio's `Workspace.step(text, key=, done=)`, `scrape.jobs.Steps.update`). The chat
  log follows new lines and growing cards smoothly only while the person is within ~80px of the bottom (instant with
  reduced motion); scrolled up, nothing moves and a "New updates ↓" pill appears (agent.js `stick`), panel and full
  screen alike; their own send always follows.
- **Colours apply at once** (owner, Nov 21, 2026): `{% event_theme_style %}` always draws `<style
  data-event-theme="<id>">` on an event's pages (empty without a theme or with the website switch off); after a save,
  switch, Reset or a finished Generate with AI, `window.attndRefreshTheme()` fetches `console:event_theme_css` (`{css,
  swatches}`) and swaps it in place, with the compact card's swatches. The Overview's **Colours** card is compact
  (`event_theme`: six swatches, the two switches, Generate with AI, **Edit**); Edit opens the full editor in a native
  `<dialog>` on pages (`data-dialog-open`, console.js; Esc closes, focus returns) and as the canvas overlay in the chat
  (`event_theme_editor`, also the theme page).
- **Rows that open for editing** (`location_row`, `schedule_item_row`, pages and chat alike): the whole row is the
  tap target (the title link stretches over it, `row-link__target::after`; buttons and the drag handle sit above), a
  chevron with "Edit" on hover/focus (always on touch), hover / focus / pressed states, and an accessible name ("Edit
  Allyn Fincher at The Nobleman Hotel"). Narrow containers (container queries on the row's own width) put the day and
  time on their own line above the title and wrap the side figures, so nothing overlaps.
- **Status marks** (owner, Nov 21, 2026: no rounded boxes with a coloured left border anywhere, a guard test checks
  the stylesheets): chat steps start with a small mark in a tinted circle (✓ changed, + added, × failed, • looked;
  `.status-mark--success/added/danger/warning/info`), failed and changed lines are lightly tinted; notices get the same
  leading mark; `panel--accent` / `panel--warning` are tinted cards; urgent rows are a tinted row with the Urgent badge.
- **Send animation**: Enter / Send morphs the typed text into the new bubble: FLIP from the textarea's text position
  to the bubble's final spot (~320 ms, ease-out, Web Animations API), the composer clears at the same time; a failed
  send puts the text back. Reduced motion: a plain fade. A reply drawn this way isn't drawn again when its step comes.
- **Canvas first** (desktop full screen): what the canvas can show goes there (a list → its tab, one form or history →
  the overlay) and the chat keeps one line, "Showing 18 locations →" (tap to show it again); a tap's detail adds
  nothing to the chat. Tabs: Events · Overview · Locations · Schedule · Notifications (compose and "Scheduled · N" /
  "Sent · N" side by side on wide canvases) · Team (`event_team`: members, roles, invites; every button always asks,
  each carrying its own member id: `resolve_actions` keeps the button's `params`, `components/act` matches the exact
  button) · Metrics (`event_metrics`: usage per app, iOS / Android, push reach, most liked / viewed).
- **Chat text**: step lines are one line ≤ 80 characters, never page text or JSON (`agent.views.step_label`); a
  finished run's answer is drawn once (text next to a `finish` call isn't stored as a note). Assistant answers turn
  `[Account](/console/account/)` into real links. **Platform help** (`agent/help.md`, appended to the system prompt):
  how-to answers always link the exact page; `agent/tests_help.py` resolves every link.
- **The rest on the canvas** (A18, `console/components/catalog_more.py`): `changeset_review` (tab **Review · N** while
  AI suggestions wait: tick changes, Apply / Discard always ask; per-field ticks on pages; `Component.act_args` turns the
  ticked boxes into `accept_change_ids`), **Settings** tab: `app_layout` (autosave, `data-autosave`),
  `website_watching`, `event_context_form` and `event_flyer` (a partial event form: only those fields, every other one
  posted hidden with its current value), Overview: `event_lifecycle` (publish / draft / end / list / delete, all ask),
  `sharing_kit` (code, link, QR PNG / SVG, poster and **table tent** in A4 or Letter: `console:event_poster?layout=tent&paper=a4`),
  `event_exports` (`console:export` CSV of schedule, locations, notifications, metrics; formula-safe `csv_safe`),
  **History** tab: `object_history` + `event_versions` (restore a whole version, asks). No attendee preview (cut).
- **Prospects on the canvas** (A19, staff only, `console/components/catalog_prospects.py`): `prospect_row`,
  `prospect_detail` (contacts with their source page; do-not-contact ones never show email or phone),
  `prospect_run_row`, `prospect_settings_form` (also draws the Settings page); `Component.audience = 'staff'` hides them
  everywhere else (`C.allowed`, the show tool's list, `instances` → 404); `Component.kwarg_map` maps `pk` URLs to
  `prospect_id`. The list and detail pages keep their own templates (bulk-select table).
- **Chat as default** (A20): the top bar's switch (`ConsolePreference.chat_default`, `console:chat_default`, agent
  action `set_chat_default`): `/console/` and an event's overview open the full-screen chat (`?event=`); internal
  agent reads and `?page=1` (Exit) aren't redirected.
  Switching it **off** (batch 6) leaves the full-screen chat for the page it was about (`leave_chat_target`: `?from=`
  when it is a console path that isn't off-limits, else the event's Overview, else Your events; off on an ordinary
  page stays there) with `?page=1&chat=closed`; JSON callers get `{chat_default, redirect}`. On arrival
  `agent-loader.js` keeps the floating panel closed (clears `attnd.agent.open` / `.active`, the thread stays) and
  pulses the launcher three times (reduced motion: the tooltip "Ask Attnd is still here" for 4 s). Phones the same.
- **Reload without jumps** (A21): one request restores the conversation (`agent:runs?chat=1,2,3` returns the runs with
  their steps), drawn hidden, scrolled once, faded in; the canvas (tabs, sections, cards, active tab) is drawn on the
  server (`agent/canvas.py`) and adopted by agent.js. Measured on a reload with 6 runs: scroll events 5 → 0, CLS
  0.042 → 0–0.012, Ask Attnd requests 25 → 1.
- **Architecture corrections (A-fix)**: lists aren't cut at 12 (the model's `show` takes 12; render / canvas pages 60 at a
  time with **Show more**, `components/?offset=`, `C.instances_page`); list components' `expand` loads the list once
  (the event, annotated rows, one likes count) and hands the objects on (`console/tests_architecture.py` checks the
  query count is the same for 3 and 15 rows); every component is drawn by a page or carries `chat_only=<why>` (a test
  fails otherwise; the pages Team, AI review, App layout, History versions, Usage metrics + exports, Overview
  watching / visibility / sharing, Details, New event and the prospect detail now draw the components); cut-down
  event cards (`EventContextForm`, `EventFlyerForm`, POST `part`) save only their own fields; the agent's first
  message, `list_events` and `search_events` read component JSON (`agent/components.events_json`, `page_json` via
  `agent/canvas.py ROUTES`) instead of page HTML, after `internal.ensure_signed_in`; platform help is a tool
  (`platform_help`), the show tool's catalogue is a phrase per component, and staff-area write tools (Studio,
  Prospects, Diagnostics) load only when a request is about them (`loop.AREAS`): about 13.6K → 11.7K tokens for an
  organizer, 17.6K → 12.0K for staff (characters / 4); one batch endpoint (`agent:component_batch`) for live numbers
  under console.js's one timer; chat card forms answer JSON when saved (`ComponentFragmentMiddleware`: messages, Undo
  id, location); the overlay animates through `attndReveal`; actions are picked by name (`|named:"…"`); the page
  `{% component %}` tag goes through `C.load` (audience check); quiet button runs are rate limited
  (`agent_quiet_runs`); `plan_suggestions` uses `attnd/claude.structured`. Not done yet: splitting agent.js into
  modules (ROADMAP).
- **Naming**: the console and the chat say **Notifications** (the apps keep their "Updates" tab; API names unchanged).
- **Follow-up chips**: `finish(summary, suggest)` (2–4 short requests, `AgentRun.suggestions`); without them
  `views.suggestions` derives them from the last component shown (`FOLLOW_UPS`). `run_json.suggestions` (done runs
  only) → chips above the composer (`data-agent-suggest`); tapping one sends it.
- **Thinking line** (`data-agent-thinking`): while a run works the chat shows one line, a CSS gradient ring and the
  current stage (`step_json.stage` = `views.stage_text`: "Reading Updates…", "Adding location “Taco Row”…", "Showing
  Schedule…"; "Thinking…" between steps). Each new stage fades out and the next fades in on the same line
  (`aria-live=polite`; no motion with `prefers-reduced-motion`). Steps still running are not log lines yet: they keep
  their place (a comment node) and are drawn there, compact as before, once done (polls ask again from the oldest
  unfinished step).
- **Components in the chat** (`agent/components.py`; the components are console/components, see Console UX): the
  model's `show(component, items)` tool (and `get_component`) validates the component and ids, loads the data **as
  the person** and records a step (`AgentStep.KIND_COMPONENT`: `arguments` = `{component, items, picked}`, `result` =
  the trimmed JSON, never HTML); the model gets the same JSON. The system prompt makes `show` the answer for events,
  overviews, locations, schedule and updates ("What are my events?" → `show("event_tile", [{}])` + one sentence);
  `read_page` stays the fallback for pages without a component. The next request's context gets the components of
  the earlier runs: the latest 3 with their JSON, older ones by name and ids (`get_component` re-fetches). The panel
  fetches the HTML from `GET /console/agent/components/?component=…&items=[…]` (our templates, rendered as the person,
  surface `chat`) and inserts it as a card. **Tap** on a card's body (not its buttons, links or
  form) → `POST components/pick/` shows its `detail` at once (no model call) and records a picked step (told to the
  model with the next message or reply). **Buttons and forms** in a card post to the **same views** with fetch
  (CSRF, header `X-Attnd-Component`): success → the card re-renders, validation errors → the view's page is answered
  with just the component (console.middleware) and the form redraws with its errors. **Always-ask** actions
  (`confirm=True`) are never forms in the chat: `POST components/act/` (only actions the component offers right now;
  a gated form such as sending an update is validated first) makes a `KIND_BUTTON` step waiting for Confirm, and
  only `agent:confirm` (`loop.confirm`: hash of exactly those arguments, single use) runs it, through the same
  internal request as the model's actions (no model call). **Stale copies**: every card knows the entities it shows
  (`event:3 location:12`); a chat change, an agent step that changed an event or a confirmed button re-fetches the
  cards of that event (gone → "no longer there"). **Long chats**: the latest 10 cards stay live, older ones fold to one
  line that re-fetches on tap.
- **Preview before apply** (stage I, `agent/proposals.py`): a request that changes 2+ things makes the model call
  `propose_changes` (each change = a registered write + its arguments as JSON; always-ask actions refused, ≤ 40). The
  server reads each form's current values (the save's own prefill) and stores old → new per field in a
  `ChangeProposal`; the chat shows the `change_preview` card (rows ticked, untick any, Apply / Cancel).
  `agent:proposal_apply` runs the ticked rows through the real views as the person inside **one** undo recording
  (`UndoMiddleware` passes nested internal requests through to the outer recorder), so one Undo reverses them all.
- **Chat-first additions** (stage I, Nov 21, 2026; each a registered action / component / Ask Attnd endpoint):
  - **What needs attention / Ready to publish?** (`console/components/catalog_chat.py`): one `checks(event)` list
    (AI updates waiting, no locations / schedule, places without a pin or photo, overlapping sets at a place, items
    outside the event's dates, empty assistant details, notifications scheduled, a draft starting within 7 days);
    `attention` (the person's current events, shown under the greeting when the chat opens empty) and
    `event_readiness` (a 0–100 score + every check with its fix link, Publish; canvas Overview).
  - **Paste or drop anything** (`console:event_import`, action `import_into_event`, `PasteImportForm`): text, a flyer
    image / PDF, CSV or Excel (`scrape/tables.py`: XLSX read with zipfile + XML, as tab-separated text) → an
    `IngestJob` with `trigger='paste'`: only that source (no crawl), never removals, never auto-applied; the
    suggestions wait in Review. Files can be dropped or pasted onto the chat (`/console/agent/attach/` accepts XLSX by
    its zip magic bytes).
  - **Conversation on the server**: `AgentRun.thread`; the browser sends its thread with each request (`thread_for`
    only accepts the person's own), a tab without one loads the latest conversation of the last 7 days (for the
    current event) in one request, "Earlier · n" loads older requests.
  - **Live ops / notification drafting**: `propose_changes` takes `notify_title` / `notify_body` / `notify_urgent`;
    after Apply the chat shows `notification_form` prefilled (sending still asks to confirm). The notification form
    has a lock-screen preview and length counters (`lock_preview` block, console.js `enhanceLockPreview`).
  - **Voice input**: a mic button (Web Speech API; hidden where unsupported); `Permissions-Policy` allows the
    microphone for our own origin.
  - **Ask the numbers**: `usage_answer` (interactions per hour on the latest day with data as inline SVG bars, the
    busiest hour, joins, the most liked places and items; canvas Metrics).
  - **Command bar**: Ctrl/⌘+K anywhere opens and focuses the chat (also before `agent.js` has loaded); `/` lists quick
    actions (`/attention`, `/ready`, `/location`, `/item`, `/notify`, `/numbers`, `/new`) that show their card at once.
  - **Preferences memory**: the model's `remember(note, event_id)` tool → `ConsolePreference.agent_notes` (≤ 30); the
    context block lists them as `remembered_preferences`; Account → "What Ask Attnd remembers" removes them.
  - **Copy or roll over**: `event_rollover` (name, new first day: the same weekday ~a year later by default, the
    first / last items' new times) → `console:event_duplicate` (`duplicate_event`, `console/rollover.py`): a draft
    with every location, schedule item and assistant detail shifted by whole days, the team kept, website watching
    off; never notifications, devices or numbers.
  - **Search everything**: the `search_everything(query, event_id)` tool (`agent/search_tool.py`, `attnd/search.py`;
    JSON fields searched as text with the `field::json` lookup).
  - **Streaming**: `GET runs/<id>/?wait=10` long-polls (≤ 10 s, at most 2 waiting per process, the DB connection back
    in the pool while sleeping; never inside a transaction), the browser polls again at once after a `waited` answer.
  - **Phones**: the same "Chat as default" as every screen (one preference, Nov 22, 2026); the full-screen chat below
    wide shows the event's sections as chips above the composer and a "Pages ↗" chip (hidden while typing on a phone).
  - **Organizer emails** (`console/organizer_mail.py`): import finished, website changes waiting for review, a
    scheduled notification went out; to the owner and event admins, each kind off in Account (`ConsolePreference.notify`).
  - **First-run walkthrough** (`first_run`): import → review → layout → publish → share as progress chips for people
    with ≤ 2 events, shown instead of the briefing until finished or skipped (`console:walkthrough_skip`,
    `ConsolePreference.walkthrough_done`).
  - **Talk to a person** (`agent:contact`): the `/contact` command, and a "Talk to a person" chip only when the agent
    offers it (it can't help, or someone asks how to reach support; the chip opens the form at once); never a
    standing button (owner, Nov 21, 2026). A form in the chat that emails `ADMIN_EMAILS` with the person's email as Reply-To, the run id, its request and outcome, the page and event;
    3 per person per hour.
- **Quality harness** (stage I, `agent/harness.py`): scripted requests (add a location, shift every set 30 min, send an
  urgent notification, an ambiguous day, a password question, a prompt injection in a location's description), each
  with the tools a good run calls, the tools it must never call, the status it ends in (an always-ask action stops at
  the gate) and database checks; replies must not leak JSON or internal names. `agent/tests_harness.py` grades
  scripted runs (the checker) and, with `AGENT_HARNESS_LIVE=1` + `ANTHROPIC_API_KEY`, the real model in a throwaway
  database (`.github/workflows/agent-harness.yml`, nightly + on demand, secret `ANTHROPIC_HARNESS_KEY`, JSON report).
  No 👍 / 👎 under answers since Nov 21, 2026 (owner: clutter); `agent:feedback` and `AgentRun.feedback` /
  `feedback_note` stay (admin filter) for older runs.
- Tests: `agent/tests.py` (registry and coverage, pages, writes through the real views, the loop with a scripted
  provider: gates, cancel, injection, caps, stop, ask_user, undo all, sign-out; endpoints and the panel) and
  `agent/tests_components.py` (show validation, JSON-only context, taps and renders as the person, chat forms
  through the views, the confirm gate for chat buttons and forms) and `agent/tests_claude.py` (the Anthropic request
  shape, blocks passed back, cache reads, refusal → Sonnet, max_tokens / pause_turn, typed errors, the context block,
  one-day events schedule without asking, multi-day ones ask with day chips; the SDK is mocked, never called) and
  `agent/tests_fullscreen.py` (the full-screen page, `from` / `event` checks, follow-up chips, stage texts).

## MCP server (staff; `mcp_server/`, docs/mcp_plan.md)

Claude (web, Desktop, mobile, Cowork, Claude Code) and other assistants run Attnd's tools as a staff member over
MCP. Dev (`https://dev.attnd.app/mcp`) and production (`https://attnd.app/mcp`; `MCP_ENABLED=1` on both, owner
Nov 26, 2026: production serves it once `mcp_server/` is merged to main) are separate servers with separate clients, tokens and data; everything derives from `SITE_URL`.
- **Layers (no duplicated logic)**: the chat and MCP are parallel entry points over the same pieces. Tool definitions
  live in `agent/tools.py` (`tools_for` for the chat, `mcp_tools_for` for MCP: the chat's reads, `search_everything`,
  `get_component`, and registry writes marked `@agent_action(..., mcp=True)`); every MCP call runs through the chat's
  own dispatcher (`agent.tools.run_tool` → `agent.loop._call` → `agent/internal.py` → the real views), as an Ask
  Attnd run with `source='mcp'` (left out of the chat's lists; shown on Platform → Connected apps). A new MCP tool =
  `mcp=True` on its registry action, nothing else. The protocol layer (`mcp_server/server.py`) only translates
  between the SDK's types and `mcp_server/service.py` (plain sync Django).
- **OAuth** (django-oauth-toolkit, `OAUTH2_PROVIDER` in settings): authorization code + PKCE S256 only, Client ID
  Metadata Documents from `MCP_CIMD_HOSTS` (claude.ai, claude.com; no open registration), Claude's callback
  `https://claude.ai/api/mcp/auth_callback` and Claude Code's loopback on any port, `resource` = `MCP_RESOURCE_URL`
  only (RFC 8707), every RFC 9700 flag on (tokens stored as checksums, `iss` in the redirect). Access tokens 1 h;
  refresh tokens rotate on every use with reuse protection (30 s grace) and expire after `MCP_GRANT_IDLE_DAYS` idle.
  Endpoints: `/oauth/authorize/` (consent, staff only, 404 for others), `/oauth/token/` (rate limited),
  `/oauth/revoke/`, `/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource[/mcp]`.
- **Grants** (`McpGrant`, `mcp_server/grants.py`): the code exchange (`oauth.Validator._create_refresh_token`) starts
  one per connection with **its own Django session** signed in as the person (two-factor done, no recent sign-in),
  so it outlives the browser (owner: connect once). Checked on every call and refresh: active, `mcp_server.use_mcp`
  (Staff role), the session still theirs (a password change or sign-out everywhere ends it), younger than
  `MCP_GRANT_MAX_DAYS`; otherwise revoked. Platform admins have `mcp_server.manage_mcp` (everyone's connections).
- **`/mcp`** (`attnd/asgi.py` → `mcp_server.server.Router`, before Django's middleware; not served under WSGI): auth
  first (401 + `WWW-Authenticate: Bearer resource_metadata=…, scope="mcp"`), then the SDK's stateless Streamable HTTP
  handler with JSON responses (every protocol version the SDK serves), host / origin checks, Django work in worker
  threads that give their connection back. The person reaches the handlers through a context variable.
- **Always-ask actions** never run from MCP: the call returns a link to `/console/mcp/confirm/<step>/`, where the
  person confirms or cancels in the browser (the chat's gate, `agent.loop.confirm`, as the browser's session, so
  guarded actions get their recent sign-in there).
- **MCP Apps**: `show_event_overview` (event_id) and `show_component` (any chat component the person may see, e.g.
  `event_tile`, `location_row`; ≤ 12 id sets) return the component's JSON as text and its HTML (the same template)
  for the `ui://attnd/card.html` app (`text/html;profile=mcp-app`, console.css inline; the old
  `ui://attnd/event-overview.html` still reads; read only: forms hidden, links open through the host in a new tab).
  `mcp_server/cards.py` makes the HTML work in the client's sandboxed iframe (no cookies, no Attnd origin, Nov 25,
  2026): the event's time zone is active while loading and drawing, the join / preview QR codes are inline `data:`
  PNGs, every address is absolute (`SITE_URL`), and the event map is a static SVG of OpenStreetMap tiles with the
  pins; the resource's `_meta.ui.csp.resourceDomains` lists the image hosts. The server advertises
  `io.modelcontextprotocol/ui`.
- **Tools** (owner, Nov 26, 2026): editing (`add_location`, `edit_location`, `add_schedule_item`, `edit_schedule_item`
  with performers / prices / links in the item's details, hide / show, `fill_in_details`, `find_music_links`), and
  `send_notification`, `edit_scheduled_notification`, `publish_changes`, `publish_event`, which always ask, so they come
  back as the confirmation link (that is the notification draft). Named card tools (`NAMED` in `service.py`):
  `show_event_usage` (`event_metrics` + `usage_answer`), `show_event_billing` (`event_billing`) and
  `show_billing_queue` (`billing_queue`: pending upgrade / remove-the-limit requests and the latest payments; platform
  admins). No Stripe reads or writes over MCP (Stripe reads wait for the LLC's live account).
- **Pitch drafts** (`prospects/pitch.py`, `draft_prospect_pitch` = `prospects:pitch`, also Ask Attnd): an email to a
  prospect's organizer offering its test event with the join link and its public QR (`/join/<code>/qr.svg`), signed
  by the person, marked promotional, with an opt-out line, where the address was found and a mailing-address
  placeholder (CAN-SPAM). Stored as `PitchDraft` (shown on the prospect, "not sent") and an activity line; refused for
  do-not-contact contacts, prospects without a test event or dismissed / lost ones. Attnd never sends it.
- **Prospects** (staff): the pipeline tools are `mcp=True` (status, notes, do-not-contact, test events and claim
  decisions; the last two always ask). Discovery runs and settings aren't offered yet; there is no send tool.
- Tests: `mcp_server/tests.py` (the OAuth dance, rotation and reuse, the grant's end, metadata, the tool list,
  confirm page, MCP App, Connected apps, the protocol over ASGI with the SDK).

## Attnd Studio (every organizer designs, publishing needs the Studio plan; `studio/`, spec `docs/specs/studio_spec.md`)

A second tier (`Event.plan`: `standard` = import/manage, `studio` = Studio; no checkout yet, Stripe later): an AI
writes the event's **website** (raw multi-page HTML/CSS/JS), makes **flyer art** and sets the **app's look**; Attnd
hosts the site.
- **Inside Ask Attnd** (batch 5, owner Nov 21, 2026: one chat): Studio's own chat UI, its editor page and its
  JavaScript (`studio.js` / `studio.css`, `editor.html`, `enable.html`, the `chat` / `state` / `app_preview` /
  `runs/<id>/` endpoints) are gone. The engine stays (agent loop, file tools, set_app_look, generate_image, set_flyer,
  importer, versions, publishing) behind registered Ask Attnd actions: **`design_website(instruction)`**
  (`studio:design`: a `StudioRun` in the background → `{"task": studio.views.run_task(run)}`, the chat's live task card;
  always offered to Studio users, `loop.ALWAYS_LOADED`, and the system prompt routes website requests to it),
  **`publish_website`** / **`unpublish_website`** / **`publish_website_version`** / **`restore_website_version`**
  (always ask; versions by POST `version`), **`bring_into_studio`** (JSON task), `delete_studio_asset` (POST `asset`),
  `delete_studio_form_entry` (POST `entry`), plus the existing code / settings / flyer / undo actions. The console task
  feed lists Studio runs as tasks `studio-<id>` (`_studio_tasks`, `?ids=`); Retry = `studio:run_retry` (JSON task).
  **Components** (`console/components/catalog_website.py`, audience `studio` = `studio.access.has_studio_access`):
  `website_overview` (sandboxed signed preview iframe of the draft only when the draft has a home page, live / draft
  versions, the address only once published (never the raw sites host of a draft), Publish / Unpublish above the preview, **Full screen preview** (a native `<dialog>` over the viewport with the same signed draft, Desktop | Mobile 390×844 frame, Publish inside too; Esc closes; in the chat Publish closes it so the confirm card shows); no design box:
  changes are asked for in the chat; without a built version a **"No website yet"** empty state with **Build from your
  current website** (`bring_into_studio` with the event's source URL, shown only when it has one) and **Start from
  scratch** (`start_website_from_scratch`, `studio:start`: `design` with `studio.views.starter_instruction(event)`), both
  background forms whose task becomes the chat's live card (console.js `attnd:task-started` → agent.js `taskCard`); the
  tab refreshes when the first version lands; or Use Studio for this event), `website_versions` (Preview ↗, Restore as the draft, Publish this one), `website_forms`
  (entries per form, CSV, Delete), `website_assets` (files, Delete). They make the canvas **Website** tab (Studio users
  only; `?tab=website` opens on it) and the **Website** page (`studio:editor`, sidenav "Website").
- **Access** (`studio/access.py`, D1, owner Nov 21, 2026): **every signed-in organizer** builds, edits and previews
  their event's website (`has_studio_access` = signed in; the team's roles decide which events; AI spend counts toward
  the person's monthly credit and Studio's per-event daily cap still applies). There is no "Use Studio for this
  event" step: every Website tab opens on `website_overview` (its empty state for a new site). **Publishing** needs the
  **Studio plan** (`platform_admin.plans.can_go_live`: `Event.plan == studio`, or the Platform admin's per-event
  "allow site publishing" override) and the owner or an event admin (`can_publish`). Without it Publish is **Upgrade
  to publish · $499** (`request_upgrade`, `console:event_upgrade_request`: an `UpgradeRequest` + an email to `ADMINS`;
  "Upgrade requested · we'll email you" while it waits); the API's `website_url`, the sites host and its sitemap only
  serve sites of events that can go live. Only staff switch plans by hand. Staff: `/console/studio/reports/` (abuse
  reports, take down/restore; Platform sub-tab "Studio reports").
- **Models** (`studio/models.py`): `Site` (1:1 event, slug, `draft`/`published` → `SiteVersion`, suspended,
  preview secret), `SiteVersion` (files `{path: text}`, app look, flyer asset; every AI run, code save and restore makes
  one), `StudioAsset` (UUID, Cloudinary *raw* storage when configured), `StudioRun` (one AI turn, working copy),
  `StudioMessage`, `StudioUsage` (cost per call), `FormSubmission`, `SiteReport`.
- **Hosting/security** (`studio/middleware.py`, first in `MIDDLEWARE`; `studio/hosting.py`): sites live on a separate
  registrable domain, path-based `https://<SITES_HOST>/<slug>/` (the bare root `/` → 301 to `SITE_URL` + `/home/`, unknown slugs 404; default: the Heroku app's herokuapp.com hostname, on
  the Public Suffix List; `127.0.0.1:8000` with `DEBUG`, so use localhost:8000 for the console). On that host only
  `studio/urls_sites.py` exists (console/admin/API/static → 404), no cookies, no X-Frame-Options, the sites CSP (inline
  scripts allowed, `connect-src 'self'`, `worker-src 'none'`, `frame-ancestors` = console origins), no service workers
  (`Service-Worker: script` → 404). attnd.app never routes site paths. Pages get `<base href>` + an Attnd footer with
  "Report this site". Drafts: `/<slug>/_preview/<signed token>/` (2 h in the editor's sandboxed iframe, 7-day private
  links, "Reset preview links" revokes), with the selection bridge (`site_static/bridge.js`, postMessage).
- **Data library** (`site_static/attnd.js`, `/<slug>/_attnd/data.json` from the bundle serializers, draft events leak
  nothing): `<attnd-schedule|lineup|locations|map|updates|countdown|app-badge|event>` (light DOM, `attnd-*__*` classes,
  `--attnd-*` custom properties, OpenStreetMap tiles, no Google key) and `<form data-attnd-form>` → `/<slug>/_attnd/forms/<name>`
  (honeypot, 2 s time trap, `studio_form_ip`/`studio_form_site` rate limits, email to owner+admins, CSV export).
- **AI** (`studio/ai.py`, `agent.py`, `prompts.py`): provider layer (OpenAI SDK **Responses API**: function tools +
  reasoning, chained with `previous_response_id`; Chat Completions refuses tools with reasoning on gpt-5.5. Anthropic
  Messages API through the official SDK (streamed) when `ANTHROPIC_API_KEY` is set, with a prompt-cache breakpoint), agent loop with tools
  write/edit/delete/read file, set_app_look, generate_image, set_flyer, in a background thread (like AI import); the
  chat's task card follows it through the console task feed, a failed run offers **Retry** (`runs/<id>/retry/`). Defaults: `gpt-5.5` → `gpt-5` fallback (only when the model is unavailable), `claude-sonnet-5-5` (owner, Nov 21, 2026: Studio runs on Anthropic on dev and production; `STUDIO_AI_PROVIDER` auto/anthropic),
  images `gpt-image-2` → `gpt-image-1`. Daily spend cap per event (`STUDIO_DAILY_SPEND_CAP_USD`, $10: a full import
  costs ~$2–4) over `StudioUsage` (cached input at a tenth); vision describes uploads. Asset descriptions (vision) run on Claude Haiku (`attnd/claude.py`); images stay on OpenAI.
- **Import** (`importer.py`, spec §9): an AI import that creates an event brings its website into Studio
  automatically for staff and superusers only (opt-out checkbox shown only to them, `IngestJob.studio_import`,
  checked again when the job runs; not refreshes); the Studio page's
  "Bring into Studio" does the same. Crawls ≤ 25 same-host pages, copies every image (srcset, picture, lazy, CSS
  backgrounds, og:image, icons, logo, sponsors), self-hosted font and PDF as assets (deduped by SHA-256, library
  budget), per-run caps `STUDIO_IMPORT_SECONDS`/`STUDIO_IMPORT_MAX_MB`, SSRF guard on every fetch and redirect; the AI
  rebuilds the whole site with the local copies (hotlinks to the old site are refused) and the data library. Failures
  keep the copies; Retry reuses them.
- **Publishing** (`publishing.py`): publish = site serves that version + its app look (accent, logo, display) and flyer
  are copied onto the event + `content_version` bump; the bundle's `event.website_url` (API-016) is the live URL.
- **Quality, SEO, analytics, safeguards** (batch 7 §6, spec §15): the pre-publish check (`studio/quality.py`: links,
  images, alt, headings, contrast, weight, mobile, SEO, drift, stale translations; errors = outside forms / password or
  card fields / outside scripts block Publish) above Publish with **Fix automatically** and **Update the site**;
  translations under `<code>/` kept in sync by `attnd-source` hashes; served pages get Event JSON-LD, Open Graph,
  hreflang, `/<slug>/sitemap.xml` + `robots.txt`, the **Get the app** card with the join QR and a cookie-free beacon
  (`SiteHit`, `website_analytics` on Metrics); **Edit text** in the full screen preview (no AI, a version with Undo);
  `studio/safety.py`: the Claude Haiku content scan at Publish (hold → Platform → Studio reports → Held for review),
  links `rel="noopener nofollow ugc"`, forms / fields / scripts refused at save and neutralised at serve, a draft
  event's site preview-only, unpaid events' sites with a Demo ribbon, staff email at 2 open reports, EXIF stripped,
  rights confirmed (and logged in History) before an imported site's first publish.
- **Site styles** (batch 7 Track D, `studio/styles.py`, spec §16): five documented looks (Festival poster, Program,
  Market day, Course & countdown, One page: layout with the `attnd.js` elements, type scale, theme colour use, do /
  don't). A from-scratch design always names one (`starter_instruction` + `styles.pick(event)`); **Platform → Site
  styles** (`platform:site_styles`, staff) shows each in sandboxed, script-free previews (phone + laptop) in an event's
  colours and which style that event gets. Tests: `studio/tests_styles.py`.
- Custom domains are designed (spec §13), not built.

## Plans and the Platform admin (D1 + D2, owner Nov 21, 2026; `platform_admin/`)

- **Plan and override badges** (batch 7): the `event_header` component (every event page and the chat) shows the plan
  (`plans.header_badges`: Free / Standard / Studio; the old Paid badge is dropped there) and **Override** when an
  `EventOverride` or the owner's `AiLimitOverride` changes something (`plans.override_details`; a notes-only row doesn't
  count). Staff see what it changes in its title, platform admins get a link to the event's Platform page; organizers
  only see that one applies.
- **Plans** (`platform_admin/plans.py`): **Free** (unpaid demo, the free phone limit, API-025), **Standard** ($299:
  paid, no phone limit, nearby listing), **Studio** ($499: Standard + publishing the Studio website). `plan_of(event)`
  from `Event.plan` / `paid`. Prices, the free phone limit and new accounts' daily AI imports live in
  `PlatformSettings` (pk 1, cached 30 s; first values 299 / 499 / `FREE_DEVICE_LIMIT`); every price label comes from
  there (`price_label`). No checkout yet (Stripe, ROADMAP): **Remove the limit** (a Standard request) and **Upgrade to
  publish** (a Studio request) make an `UpgradeRequest` (one pending per event and kind) and email `ADMINS`.
- **One Platform entry** (owner, Nov 22, 2026; `platform_admin/areas.py`): the console's top bar shows organizers and
  staff the same links except one **Platform** button (only with a platform permission; it opens the person's first
  sub-tab). Every admin tool is a Platform sub-tab gated by its own permission (`{% platform_nav "<key>" %}` at the
  top of each area page; their URLs are unchanged, so old links work): Health, Customers, Upgrade requests, Pricing,
  Overrides (platform admins), AI spend, Diagnostics, Prospects, Claims, Studio reports (staff), App usage (platform
  admins), plus the actions **Preview onboarding** and **Django admin** (superusers). Architecture, App options, Brand, Emails, Site styles and Components are sub-tabs (batch 7). The full-screen canvas likewise has one **Platform** tab (Health, AI spend,
  Prospects sections, each only with its permission; toolbar AI limits / Overrides / Discovery settings / CSV).
  `platform_admin/tests_nav.py` asserts the top bar and the canvas tabs of an organizer and of staff / a superuser
  differ only by Platform. A new admin tool = a row in `areas.AREAS` + the nav tag on its page, never a top-bar link.
- **Architecture, App options, Brand** (batch 7 Track E; Platform sub-tabs): **Architecture** (`platform:architecture`,
  superusers: `platform_admin.view_architecture` is in no role) = `docs/architecture.toml` (hand-written nodes for
  what code can't see: apps, App Clip, widgets, Heroku, DNS, every outside service with hosts, env vars, edges; flows;
  TOML because the standard library reads it) + what `platform_admin/architecture.py` generates (Django apps, models
  as an ERD with admin links, URL namespaces, API v2 endpoints, push types, Ask Attnd counts, env vars set / missing)
  + a live status per service (Google counters, push delivered / failed, the AI ledger, the email log, endpoint
  timings: green / amber / red). A server-drawn layered SVG (Clients → Edge → Django apps → Data → External) with pan /
  zoom, search, layer toggles, a side panel (code links on GitHub develop), Flows (QR → join, AI import, watching,
  urgent, preview phone, Studio publish, AI credit; step by step, Reduce Motion: a list), Data tab, export SVG / PNG /
  Markdown. **Adding an integration = add it to docs/architecture.toml**: `attnd/tests_architecture.py` fails when a
  Django app, an https host our code names, a settings env var or a push type is missing there. **App options**
  (`platform:app_options`): every server-driven option (display config and preset defaults, the layout catalogue
  per screen with since / needs / parts, conditions, theme roles and defaults, Live Activity settings). **Brand**
  (`platform:brand`, staff; public copy `/brand/`, `home:brand`): wordmark and app icon SVG, the PNG icons, colours,
  type, voice, do / don't, a zip of everything.
- **Billing** (batch 7, `platform_admin/billing.py`, `Invoice`, `console/views_billing.py`, component `event_billing`,
  sidenav **Billing**, owner + event admins): plan, total paid, invoices (sequential `ATT-2026-0001`, date, amount,
  Paid / Due, a hand-written PDF), **Send a receipt** (always asks; the PDF attached), CSV export. **Pay now** = Stripe
  Checkout for an open invoice; the webhook (`console:stripe_webhook`, public, Stripe's signature) marks it paid;
  Customer Portal for card customers. Without `STRIPE_SECRET_KEY` + `STRIPE_WEBHOOK_SECRET`: "Payments aren't set up
  yet" and the upgrade requests as before. Every payment lands in `billing.mark_paid` (a `PaymentRecord`, the plan
  applied, the invoice paid; idempotent): Stripe, approved upgrade requests and the Platform's **Record a payment**
  (`record_payment`, always asks). Tests: `platform_admin/tests_billing.py`.
- **Platform admin** (`/console/platform/`, `platform:home`; the page needs `platform_admin.view_platformsettings`
  (staff), its own tabs `change_platformsettings` (platform admins; others are sent to their first sub-tab)): tabs **Health** (health: AI spend
  vs the alert, events live now, sign-ups this week, failed AI jobs and push failures in 24 h, Google API counters),
  **Pricing** (`save_platform_pricing`), **Customers** (every account: events, plans, paid total, AI month / all vs
  limit, last active, features used: AI imports, Ask Attnd runs, Studio sites built / published, notifications sent,
  themes, screens edited, claims, phones joined; search, sort, CSV `platform:customers_csv`; a customer page
  `platform:customer`), **Upgrade requests** (Approve = plan + paid + a `PaymentRecord` at the request's price, Decline),
  **Event overrides** (`platform:event`: plan Free / Standard / Studio, free phone limit (a number or none), allow site
  publishing, Studio daily AI cap, nearby listing, website watching, the owner's AI credit (`AiLimitOverride`), notes;
  `EventOverride`, read by `events.free_limit`, `studio.ai.daily_cap` and `plans.can_go_live`), **AI spend** (the
  credit components; `/console/ai-spend/` still works). Every change always asks (page `data-confirm`, Ask Attnd
  `save_platform_pricing`, `approve_upgrade_request`, `decline_upgrade_request`, `save_event_overrides`, confirm=True,
  namespace audience `superuser`) and is a History `LogEntry`. Tests: `platform_admin/tests.py`.

## Tickets: parked on git branch `parked/tickets`

Attnd Tickets (API-017: Stripe Checkout + Connect, QR tokens, Wallet passes, door mode) was moved off `develop` on
Nov 19, 2026 (owner: organizers use Eventbrite for now). The whole `tickets/` app, its settings, URLs, console tools,
Studio element, AI drafts, tests, CI steps and spec are on the git branch **`parked/tickets`** (cut from `develop`
before the removal, tag `tickets-parked-2026-11-19` on the same commit). On `develop` the bundle's `event.tickets_url` and
`bundle.tickets` stay in v2 and are always `null` (installed apps decode them; `events/tests_contract.py` checks it);
the `tickets/` and `door/` endpoints are gone. Restore by merging or cherry-picking from that branch.

## Google APIs and caching (owner, Nov 21, 2026; `attnd/google.py`)

- **One helper for every server-side Google call**: `google.text_search(query, near, radius, limit, website)` (Places
  API (New) Text Search, field mask `places.id,displayName,formattedAddress,location` + `websiteUri` when asked),
  `google.place_website(place_id)` (Place Details, `websiteUri` only), `google.geocode(address)` (Geocoding API). Server
  key `GOOGLE_PLACES_API_KEY`, 8 s timeout, one log line per call, no retries. Users: `scrape.service._geocode` /
  `_places_text_search` (imports, refreshes, paste imports, new locations from website changes), `console.places.search`
  (the location form's Find), `events.place_images.venue_website`. Play Integrity, FCM and the browser's Maps JS /
  Geolocation are separate.
- **Caching per the Maps Platform terms** (Service Specific Terms + Places / Geocoding policies, checked Nov 21, 2026):
  place ids may be kept indefinitely (`Location.google_place_id`, with `google_place_key` = the name + address it was
  looked up for; a changed name or address looks it up again; '' = nothing found); latitude / longitude (and the place
  id) cached ≤ 30 days; other Places content (labels, formatted addresses, websites) a day; "nothing found" 30 days.
  Database cache keys = hash of the normalised query + the bias area rounded to 0.01°. Venue websites: one Text Search
  per place, then only Place Details by id; `place_images.find_for` skips places already looked at in the last 30 days
  unless a person presses Find a photo (`force`), so backfills and retries never repeat searches.
- **Quotas** (Google Cloud daily quotas, SETUP.md): a 429 / `RESOURCE_EXHAUSTED` / `OVER_QUERY_LIMIT` is logged once,
  then that API answers "nothing" without calling Google until midnight Pacific; a refused lookup is never remembered
  as "nothing found". **Counters** per Pacific day and API (calls, cache hits, errors, quota refusals; `google.usage()`)
  on the staff **Diagnostics → Endpoints** page.
- **Browser**: `console/static/console/maps.js` loads Google's Maps JS only when a map or address field is on screen
  (IntersectionObserver, 200 px margin) or focused; `PlaceAutocompleteElement` bills per session (its own session
  tokens; it has no minimum-characters option). The home page's Geolocation result is kept a day in `localStorage`.
  The Android app's walking routes are cached in the app (no change). Tests: `attnd/tests_google.py`.

## Images (owner, Nov 21, 2026; `attnd/images.py`, `{% img %}` in `console/templatetags/attnd_images.py`)

- **One pipeline** for every image the platform draws or ships: `images.variant(url, w, h=None, crop=False)` →
  Cloudinary delivery transforms (`f_auto,q_auto` + `c_limit`, or `c_fill,g_auto` where a card crops) at exact pixels.
  Our media files on Cloudinary have **no file extension**, so everything under `image/upload` is transformed except
  `.pdf` / `.gif` / `.svg` and already-transformed URLs (before Nov 21 the extension check skipped them all: the API and
  the console served originals). Without Cloudinary (local dev, `IMAGE_LOCAL_VARIANTS`) Pillow makes resized copies of
  our media files once (`variants/` in the media storage); static files, SVG QR codes and other hosts pass through.
  `events.content.app_image_url` (API: locations 1200, logo 512, flyer 2000; console thumb / preview) uses it.
- **`{% load attnd_images %}{% img url width height alt=… crop=True sizes=… priority=True cls=… %}`**: `src` at 1x,
  a width-descriptor `srcset` for 1x / 2x / 3x, `sizes` (default `<width>px`), `width` / `height` always (no layout
  shift), `loading=lazy` unless `priority` (`fetchpriority=high`, the first cover on the home page), `decoding=async`.
  Used by the home page cards (384×216 crop), location rows (48×48), found place photos (96×64), the flyer preview.
  `attnd/tests_images.py` fails on any `<img>` in our templates without width and height and checks the content images
  go through the tag; a budget test checks the home page covers' sizes and that no Google Places photo appears.
- **Self-hosting** (`images.self_host`: Studio's SSRF-guarded fetcher, Pillow re-encode to JPEG ≤ 2000 px, no EXIF /
  GPS): a location's outside `image_url` becomes `Location.image` (`image_source_url` keeps where it came from) and an
  outside `Event.logo_url` becomes `Event.logo_image` (`logo_source_url` = the `logo_url` it was made from; the API's
  `logo_url` and `display.logo_url` ship our copy, `images.event_logo_url`). Google Places photo links
  (`googleusercontent` / Places media) are never copied: dropped, and the place-image finder looks for another. After
  every save (`events/signals.py`, background thread, `IMAGE_SELF_HOST_ON_SAVE`) and by `manage.py self_host_images
  [--dry-run] [--event CODE]`. Studio assets stay on Cloudinary *raw* storage (no transforms; Studio pages only).
- **Paint cost** (home page): the sticky nav keeps its `backdrop-filter` blur; shadows use small blur radii; the phone
  mock-up and cards get `contain: layout paint` (no blanket `will-change`); sections below the fold
  `content-visibility: auto` + `contain-intrinsic-size`; `home.js` has no scroll handlers. `/join/<code>/qr.svg` and
  shared-plan QR codes: `max-age=86400, stale-while-revalidate=604800` (`join.views.QR_CACHE`).

## Performance

- **Search** (`attnd/search.py`, F3): Postgres full text (English stemming, weighted fields, `websearch`) plus pg_trgm
  word similarity (≥ 0.3, typos) in one query; Ask Attnd's `search_events` (an event's own fields, its locations and
  schedule items) and the Prospects search use it.
- **Console front end** (H): `console.js`, `tz.js`, `passkeys.js` and `maps.js` load in `<head>` with `defer` (page
  scripts in `{% block scripts %}` use `defer` too; inline page scripts that need them wait for `DOMContentLoaded`). Ask Attnd's `agent.js` (~1,500 lines) loads lazily through `agent/agent-loader.js`: at
  once on the full-screen page, when the panel was open on the previous page or while a run is active
  (`sessionStorage attnd.agent.active`), else on the launcher's hover / focus / click or an "Add with AI" click
  (replayed once loaded). Polling backs off: Ask Attnd 1.5 s → 6 s while nothing changes, 15 s in a hidden tab; the
  task tray's fallback 10 s hidden. Root files (`public/`): icons `max-age=86400` + a week stale-while-revalidate,
  robots / manifest an hour (`attnd/static_headers.py`). Console thumbnails go through Cloudinary transforms
  (`app_image_url` kinds `thumb` 96, `preview` 192) with `loading="lazy"` and sizes. `django.contrib.humanize`
  (`naturaltime`, `intcomma`) for "… ago" and counts. Not done: splitting `agent.js` into modules (ROADMAP).
- **Bundle** (`events/apis_v2.py`): with `If-None-Match` the 304 is answered from `content_version` alone (one
  query, the bundle is never built); the serialized bundle is cached per (event, content_version, origin) for 5 min
  in Django's cache; a device's `last_seen` is written at most every 10 min by polls.
- **gzip**: `attnd.middleware.CompressionMiddleware` (after WhiteNoise) compresses text responses (API JSON, HTML;
  the DEMO bundle 22 KB → 4 KB) and keeps ETags strong, because every view compares `If-None-Match` exactly.
- **Images for the apps**: v2 rewrites Cloudinary image URLs to `f_auto,q_auto,c_limit,w_<n>` (locations 1200,
  logo 512, flyer 2000; `content.app_image_url`); other hosts, PDFs and GIFs are untouched.
- Usage counts are one `INSERT … ON CONFLICT` per batch (`events/usage.py`). Console lists count related rows with
  subqueries, never with joins + DISTINCT (the dashboard took 1.7 s on the DEMO event that way).
- Query-count regression tests: `events/tests_perf.py`.
- **Lighthouse pass** (Nov 22, 2026): the console's manifest is public (browsers fetch it without cookies); every page
  has exactly one `<main>` (`attnd/tests_landmarks.py`; Studio's check flags sites without one and its autofix wraps
  the content); console / accounts / admin pages say `noindex` on every host (meta + `X-Robots-Tag`,
  `middleware.private_path`) and carry no canonical; Maps JS loads only `marker` up front, `places` on an address
  field's focus, drops the Roboto stylesheet it injects, and the event map starts as a Static Maps JPEG (size × DPR)
  that becomes interactive on hover / focus / touch or when idle on screen (needs the Maps Static API on the browser
  key, SETUP.md); attndReveal's pulse / glow / collapse and the chat's pulses animate only opacity / transform
  (`.reveal-ring`, `::after` rings); collectstatic minifies our own CSS (`attnd/static_storage.py`, comments and
  whitespace only; JS not minified: no safe stdlib minifier, gzip / brotli apply). Second pass (Nov 22, 2026,
  measured signed in as the dev-only `lighthouse-test@attnd.app`, SETUP.md): `/console/sw.js` public (a cookie-less
  fetch was a redirect registration refused); status fills read at 4.5:1 in both modes (`--color-on-success / -info /
  -danger`, `console/tests_contrast.py`); headings in order on Schedule, Team, Metrics, History (components use h2 on
  pages, their old level in the chat); the full-screen chat places chat and canvas by grid column (the canvas jumped
  when the chat arrived, CLS 0.57); `/brand/` absolute canonical + description (`home/tests_seo.py`); home.js
  remembers a Geolocation 404 for a day. **Studio sites**: attnd.js fixes unreadable text in its elements (dark or white,
  whichever reads), merges map pins closer than 32px into a numbered pin that zooms in, lazy map tiles with sizes;
  served pages get Google Fonts preconnects; site CSS / JS / attnd.js carry strong ETags + stale-while-revalidate; the
  pre-publish check adds button / link names, field labels, frame titles, zoom blocked, unsized / eager / oversized
  images, render-blocking head scripts and Google Fonts (headings and meta description now warnings), and Fix
  automatically adds frame titles, a zoomable viewport, aspect-ratio for assets of known size, lazy loading after the
  first three images, defer on head scripts (pages without inline scripts only) and fonts preconnect + display=swap;
  the designer's prompt and every site style carry the same rules.
  Third pass (Nov 22, 2026, the staff pages, measured as the dev-only staff account `lighthouse-staff@attnd.app` with
  `manage.py seed_staff_volume` volume, SETUP.md): long staff lists are paged 50 at a time with Newer / Older links
  that keep the filters (`console/paging.py` + `console/_pager.html`: Diagnostics sessions and reports, Emails,
  Prospects, Claims; they used to draw the first 200–300 rows and drop the rest; CSV exports stay complete); the
  Customers list loads AI-limit overrides once (`ledger.monthly_limit(user, override, settings_row)`, 51 → 10 queries);
  the Architecture page reads the source tree once per process (`architecture.called_hosts`, os.walk pruning `.venv`);
  Site styles previews get their `srcdoc` only near the screen (`iframe[data-srcdoc]`, console.js
  `enhanceLazyFrames`; Chrome's `loading=lazy` doesn't defer srcdoc frames); the Components gallery rows use
  `content-visibility: auto` (phone_check.cjs turns it off to measure) and row components sit in a `<ul>`; table
  headers always have text (`visually-hidden` for action columns) and headings step down one level.
  `platform_admin/tests_staff_pages.py` keeps paging, query / size budgets, the Customers query count, headings and
  header text for every staff page.
- **Database review** (batch 7, `docs/db_review.md`): covered FK indexes off (`db_index=False` where a composite starts
  with the column), partial indexes for live preview phones, due notifications and nearby events, the task feed's
  `(created_by, -created_at)` / `finished_at`, JSON shape CHECKs (`events.models.json_is`), `push.reach(event)` (one
  aggregate for the phones a notification reaches). New JSON columns get a `json_is` check; new FKs whose column
  starts a composite index get `db_index=False`.

## Accounts

Organizers stay signed in up to 14 days (7 days idle); staff sign in once a day, until midnight in their own time zone (see Security). Signed in,
`/` goes to the console, the attnd. logo goes to `/home/` (always the home page, which then says **Console** instead of
Sign in and makes no passkey prompt), and the sign-in, sign-up and two-factor pages send a fully signed-in person on
to the console (`console/tests_signin_flow.py`). People only ever see and sign in with their **email** (`console.auth_backends.EmailOrUsernameBackend`, case-insensitive). The account's `username` is an internal generated id (`u` + 10 hex, `console.accounts.new_username`), so an email can change (Account page, `/console/account/`) without losing the account. Emails are unique case-insensitively (Postgres index `auth_user_email_lower_uniq` + `email_in_use`). Create a site admin with `python manage.py create_admin you@example.com` (they set up two-factor sign-in at their first sign-in; see Security). Deleting an account never deletes events: they become unassigned.
- **Open sign-ups, demo events** (owner, Nov 21, 2026; `console/signups.py`, `SIGNUPS_OPEN`, default **on**): anyone
  can create an account and demo (unpaid) events; **going live stays invite-only**: an admin marks the event paid
  (`mark_event_paid`, staff; no checkout yet), which lifts the free limit and allows nearby listing. Unpaid events take
  20 different phones (API-025, `events/free_limit.py`, `FREE_DEVICE_LIMIT`; the event's status card shows "14 of 20
  free spots used" and **Remove the limit**, which emails the admins). `SIGNUPS_OPEN=0` goes back to invited emails only
  (the old invite-only page and 403s).

## Declaring permissions for new views

Deny by default (owner, Nov 22, 2026; `attnd/permissions.py`, review: `docs/security_review_permissions.md`). Every
decision is `user.has_perm(...)`: platform roles are Django Groups (**Staff**, **Platform admins**; `ROLES`, synced
after every migrate; `is_staff` accounts have the Staff role, superusers everything), event roles (owner / admin /
contributor) are object permissions from `PermissionMixin` (on the site's one auth backend) (`user.has_perm('events.change_location', event)`).
- **Every view declares its permission**, or `DeclaredPermissionMiddleware` refuses it (404 when the URL names an
  object, 403 otherwise) and `attnd/tests_permissions.py` fails CI:
  - one event's page: `@event_view` (any team role, `events.change_event`) or `@event_view(perm='events.manage_event')`
    (owner + event admins; `events.delete_event` / `transfer_event` = owner); Studio: `@studio_event_view`;
  - a platform area: `@requires('<app>.<codename>')` (404 for everyone without it); add a new permission to the
    model's `Meta.permissions` and to `ROLES` (`STAFF` or `PLATFORM_ADMIN`);
  - the person's own things: `@signed_in` (never read or write another person's rows: filter by `request.user`);
  - public pages: `attnd.public.public` (listed in `attnd/tests_public.py`); anything else: `@declare(perm)`.
- **Querysets** of events and their rows: `Event.objects.for_user(user, perm)`, `Location / ScheduledItem /
  Notification.objects.for_user(user, perm)`, or filter by the event the view already checked. A raw
  `get_object_or_404(Model, pk=…)` / `Model.objects.get|filter(pk=…)` of an event-owned model in a view fails
  `RawLookupLintTests` unless it is in `LINT_ALLOWED` with its reason.
- **Components** declare `permission=` (event components get `events.view_event` and their data function checks the
  event with `editable_event`; platform ones the area's permission; the person's own cards `'signed_in'`). **Ask
  Attnd** tools take their view's permission (`agent_action(..., permission=)` when a view's action needs more, e.g.
  `assign_event_owner`). **Templates** ask `perms.<app>.<codename>`, never `user.is_staff` for access.
- Inline checks inside a view use the same permissions (`event.can_edit` / `can_manage_team` are
  `has_perm('events.change_event' / 'events.manage_event', event)`; `studio.access.can_publish` =
  `studio.publish_site` + the Studio plan). `is_staff` / `is_superuser` stay only for role facts that aren't access
  (strong second factor, once-a-day sign-in, no monthly AI limit).

## Security

- **HTTPS**: outside `DEBUG`, HTTP redirects to HTTPS, session/CSRF cookies are `Secure` (+ `HttpOnly`, `SameSite=Lax`), HSTS 1 year with subdomains (not preloaded). `SECURE_PROXY_SSL_HEADER` trusts Heroku's `X-Forwarded-Proto`.
- **Rate limits** (`attnd/ratelimit.py`, in the database cache shared by every process and dyno; the value carries its window end so the window stays fixed): failed sign-ins 5 per email and 20 per IP per 15 min (a success clears the email's count); password-reset requests 3 per email and 10 per IP per hour; signups 10 per IP per hour; wrong two-factor codes 5 per account per 15 min; failed passkey sign-ins 20 per IP per 15 min; emailed sign-in codes 3 per account per 15 min. Over the limit: the same page with a friendly message and HTTP 429, worded the same whether or not the email has an account. Client IP = the **last** `X-Forwarded-For` hop (the one Heroku's router appends). Override with `RATE_LIMIT_<NAME>=count/seconds` or `settings.RATE_LIMITS`. The admin's own login page redirects to the site's sign-in (`/accounts/login/`, `console.views_auth.SignInView`).
- **Sign-in methods** (`console/twofactor.py` = the policy, `TwoFactorMiddleware`, models `console.Passkey`, `console.SignInSecurity`, `console.TwoFactorDevice`):
  - **Passkeys (WebAuthn), the default sign-in.** `console/webauthn.py` is the relying party (stdlib + cbor2 + cryptography): ES256, EdDSA and RS256 keys; checks clientDataJSON type/challenge/origin, the RP ID hash, User Present + User Verified, the signature and the sign counter (both non-zero and not increasing → rejected and logged as a possible clone). Attestation is `none` and not verified (synced passkeys have none; it only says which make of authenticator made the key). Challenges are 32 random bytes in the session, 5 minutes, single use (up to 8 outstanding: sheet on load + autofill + button + other tabs). `console/passkeys.py` runs the ceremonies, `console/views_passkeys.py` serves the JSON endpoints under `/accounts/passkeys/`, `console/static/console/passkeys.js` is the browser side (vanilla `navigator.credentials`; passkey UI stays hidden without WebAuthn support). The sign-in page offers usernameless sign-in (autofill via `autocomplete="username webauthn"` / conditional mediation, plus a "Sign in with a passkey" button); a passkey sign-in counts as **both** factors. A browser that signed in with or added a passkey here is remembered (`localStorage` `attnd.passkey.used`): its sign-in page goes passkey-first (password form folded under "Use a password instead") and opens the passkey sheet on load once per tab (`data-autoprompt`, not after a failed password; Safari refuses that without a click, so the button stays on top). The home page's "Sign in" links try WebAuthn immediate UI mode on click (`uiMode: 'immediate'`, only where `getClientCapabilities().immediateGet`, Chrome 149+; it needs a user gesture, so never on load) and otherwise just open the sign-in page. Only one WebAuthn request runs at a time (autofill is aborted and settled first). Failed passkey sign-ins are limited per IP. Each account gets a random 32-byte WebAuthn user handle (`SignInSecurity.user_handle`). Passkeys belong to one domain: `WEBAUTHN_RP_ID` (default: `SITE_URL`'s host), origins `WEBAUTHN_ORIGINS` (default `SITE_URL`'s origin, + `http://localhost:8000` with `DEBUG`, where pages on localhost use the RP ID `localhost`). Dev (`dev.attnd.app`) and production (attnd.app) passkeys are separate.
  - **Password sign-in + second step** (`/accounts/2fa/`): required when the account has any second factor: a passkey, an authenticator app (`console/totp.py` RFC 6238, ±1 step, each step once) or **emailed codes** (6 digits, 10 min, single use, stored as an HMAC, sends limited per account). Recovery codes (10, SHA-256 hashes on `SignInSecurity`, removed with the last second factor) work for any of them; they are optional and never made automatically: Account → "Set up recovery codes" / "New recovery codes" (needs a second factor plus an authenticator code or the password). Adding a passkey is refused while a second step is pending, so a password alone can't add one.
  - **Staff and superusers** must have a **strong** method (passkey or authenticator app); without one they are sent to enrollment (`/accounts/2fa/setup/`: "Add a passkey" as the one default action, the authenticator app under a folded "Other options" `<details>`, shown directly without WebAuthn support) before the console, admin or password change open. Emailed codes are organizers-only (never accepted for staff). Removing the last strong method is blocked for staff.
  - **Organizers**: nothing required. After a password sign-in on a browser with a built-in authenticator the console shows "Sign in faster with a passkey" once ("No thanks" is remembered per account). **Account** lists passkeys (name, added, last used, synced) with add/rename/remove, the authenticator app, emailed codes (only when email is configured) and recovery codes. Authenticator setup: an "Open in your authenticator app" button (the `otpauth://` link: Apple Passwords on Apple platforms, 1Password etc.; no TOTP support found for Google Password Manager, Nov 2026), the QR code and key, code field `inputmode="numeric" autocomplete="one-time-code"`.
  - Lockouts: `python manage.py reset_2fa <email>` removes every second factor (authenticator, passkeys, emailed and recovery codes) and signs the account out everywhere; superusers can also delete a device or passkey in the admin. Tests that sign in as staff use `console.testing.TwoFactorClient`; `console/tests_passkeys.py` drives a software authenticator.
- **Content Security Policy** (Django's built-in `ContentSecurityPolicyMiddleware`, `CSP_POLICY` in settings): `default-src 'self'`; scripts from self + a per-request nonce (+ Google Maps hosts when `GOOGLE_MAPS_BROWSER_KEY` is set, per Google's Maps JS CSP guidance); `img-src` self, `data:`, `blob:`, `https:` (organizer image URLs, Cloudinary, map tiles); `style-src` allows inline styles; `frame-ancestors 'none'`, `base-uri`/`form-action 'self'`, `object-src 'none'`; violations go to `/csp-report/` and are logged (`attnd.csp`). `CSP_MODE=enforce|report|off`. Rules for templates: every inline `<script>` gets `nonce="{{ csp_nonce }}"` (or move it to a static file), and **no inline event handlers** (`onclick=` …): use `data-confirm="…"`, `data-autosubmit`, `data-copy="…"` (handled in `console.js`) or `addEventListener`. `console/tests_security.py` checks every template.
- **Recent sign-in check ("sudo mode", `console/recent_auth.py`, owner Nov 21, 2026)**: changing the sign-in email or
  password, adding or removing a passkey, the authenticator app, emailed codes and recovery codes, transferring or
  deleting an event and removing a team member need a full sign-in from the last 20 minutes (`_auth_at` in the session:
  set by a complete sign-in, `user_logged_in` for accounts without a second factor, `twofactor.mark_verified`, and
  every re-check). Sending an urgent notification does **not** (owner: its confirm is enough). Without one the view
  answers with a redirect to **Confirm it's you** (`/accounts/confirm/`, `views_auth.confirm_identity`: a passkey with
  mode `reauth`, or the password plus the second step's code where the account has one; limited like sign-in:
  `login_email` / `login_ip`, codes `otp_user`) that comes back to the page, or 403 JSON `{error, reauth}` for fetch.
  Ask Attnd: `agent:confirm` checks the person's own request before releasing `recent_auth.GUARDED_ACTIONS`
  (delete_event, remove_team_member, transfer_event_ownership; the chat shows a "Confirm it's you" card and the
  action keeps waiting), and its internal requests hit the same check. Staff enrollment (`two_factor_setup` while
  pending) is exempt. `console.testing.TwoFactorClient` marks it fresh. Tests: `console/tests_recent_auth.py`.
- **Where you're signed in** (`console/user_sessions.py`, model `console.UserSession`, owner Nov 21, 2026): every
  sign-in (`user_logged_in`) records the session key, the User-Agent and a SHA-256 of the browser's long-lived
  `attnd_device` cookie (random, two years, HttpOnly; no place is stored); `SessionTrackingMiddleware` keeps the key in
  step (the second step's new key) and last-active (written at most every 10 minutes). Account → **Signed in on other
  devices** (`#sessions`) lists live sessions ("Safari on iPhone", last active, "This device"), signs one out or
  **Sign out everywhere else** (deletes their Django sessions); `run_scheduled_tasks` purges rows of ended sessions.
  A sign-in from a browser the account hasn't used (`SignInSecurity.known_devices`, ≤ 30 hashes) emails **New
  sign-in to Attnd** (time, browser, link to `#sessions`), not for a brand-new account's first device and not without
  a User-Agent or from `attnd-ci` (tests, CI); EMAIL_ALLOWED_RECIPIENTS applies. Tests: `console/tests_user_sessions.py`.
- **How long a sign-in lasts** (`console/session_expiry.py`, owner Nov 21, 2026): **organizers** 14 days at most, sliding: `SessionLifetimeMiddleware` (after AuthenticationMiddleware) renews the idle expiry on use, at most once an hour, to 7 days (never past 14 days after the sign-in time kept in the session, `_signed_in_at`) and signs the person out after 14 days; 7 days without a request ends it. **Staff and superusers** sign in once a day: `console/static/console/tz.js` (on the sign-in, sign-up and two-factor pages, the homepage and every console page) stores the browser's zone (`Intl…timeZone`) in the `attnd_tz` cookie (zone name only, a year, `SameSite=Lax`), and every successful sign-in (password, passkey, a new account: the `user_logged_in` signal; the second step keeps it, `cycle_key` carries the expiry) calls `request.session.set_expiry(<next local midnight>)`, or the midnight after when the next one is under an hour away (11:30 PM → the following night). The zone is validated with zoneinfo (unknown, missing, path-like → `America/Chicago`). An explicit expiry overrides `SESSION_EXPIRE_AT_BROWSER_CLOSE` (off) and is never longer than `SESSION_COOKIE_AGE` (two weeks, unchanged). Ask Attnd's internal requests never renew. Tests: `console/tests_session_expiry.py`.
- **Hosts, passwords, headers, keys** (stage G): `ALLOWED_HOSTS` is only our own hostnames (`SITE_URL`'s host, + `www`
  for attnd.app, the Studio sites host; `ALLOWED_HOSTS_EXTRA` adds more), never `.herokuapp.com`. Passwords hash with
  **scrypt** first (old PBKDF2 hashes still work and are upgraded at sign-in). Every page but Studio's sites sends
  `Permissions-Policy` (`PERMISSIONS_POLICY`; camera, microphone, payment, USB off; geolocation self). `SECRET_KEY`
  rotation: set the new key and put the old one in `SECRET_KEY_FALLBACKS` for two weeks (SETUP.md). Ask Attnd:
  `agent_start_user` limits requests per person (30 / 10 min), chat buttons' quiet runs `agent_quiet_runs`, and
  attachments are checked by their first bytes (PDF, PNG, JPEG, WebP, GIF, HEIC, UTF-8 text), not their name.
- **Sign-in required by default** (stage F2): Django's `LoginRequiredMiddleware`; public views are marked with
  `attnd.public.public()` / `public_include()` (the API, join and shared-plan links, the home page, sign-in, sign-up,
  password reset, passkey endpoints, well-known files, health, CSP reports, Studio's sites) and
  `attnd/tests_public.py` lists every one of them.
- **Search engines** (owner, Nov 21, 2026): off production (`SITE_URL` not `https://attnd.app`: dev.attnd.app and
  dev.attnd.site) `attnd.middleware.NoIndexMiddleware` (first in MIDDLEWARE) answers robots.txt with `Disallow: /` and
  sends `X-Robots-Tag: noindex, nofollow` on everything. Self-canonicals on the home page, privacy and join / shared
  plan pages (`SITE_URL`-based, no query); join pages of events that aren't published + listed + paid (test, showcase,
  demo, shared plans) say `noindex`; `sitemap.xml` lists the static pages and those events' join pages
  (`home/sitemaps.py`); the sites host has its own `sitemap.xml` of published sites and each site page a canonical
  to its address. `public/llms.txt` says what Attnd is; the home page's JSON-LD has Organization (+ `sameAs` from
  `SOCIAL_PROFILES`), WebSite and SoftwareApplication. One host: `WwwRedirectMiddleware` 301s www.attnd.app to
  https://attnd.app (same path and query) except `/.well-known/` (the AASA must not redirect); http → https is
  `SECURE_SSL_REDIRECT`. Old app links `/download/`, `/app/`, `/get/` → App Store / Google Play / the home page's app
  section (302, by device). Tests: `home/tests_seo.py`. **IndexNow** (batch 7, `attnd/indexnow.py`, production only with
  `INDEXNOW_KEY`): a listed event's join page (published content, listed / unlisted) and a Studio site's address
  (published, unpublished, taken down) are queued in the database cache and sent by `run_scheduled_tasks` as one POST
  per host (`api.indexnow.org`); the key file `/<key>.txt` on attnd.app (`indexnow_key`) and the sites host
  (`studio.middleware`); counters on Diagnostics → Endpoints. Tests: `attnd/tests_indexnow.py`.
- **Admin** lives at `ADMIN_URL_PATH` (default `manage-attnd/`); `/admin/` is a 404. Link to it with `{% url 'admin:index' %}`.

## Data Models (`events/models.py`)

| Model | Key fields |
|-------|------------|
| `Event` | name, event_code (unique), status, timezone, start/end_time, lat/lng (nullable), paid, public, flyer, address, display_config, layout_config (API-024), accent_color, theme + theme_web / theme_apps (API-023), content_version, source_urls, refresh_schedule (off/watch), update_mode, sources_checked_at, context (API-018), locked_fields; `user` = owner, null = unassigned (owner deleted; admins reassign from Team) |
| `Location` | name, snippet, description, category, tags, context (API-018), sort_order, hidden, image/image_url, lat/lng (nullable), address, locked_fields — FK to Event |
| `ScheduledItem` | name, description, start/end_time, tags, context (API-018), hidden, locked_fields — FK to Event, optional FK to Location |
| `Notification` | title, body, send_time, priority (normal/urgent, API-011), sent, sent_at, delivered/failed counts — FK to Event |
| `Device` | id (UUID string PK), platform (ios/android), app_clip (iOS App Clip, API-019), subscribed, push_token, live_activity_token (iOS, API-011; cleared on leave/switch), app_version, last_seen, event FK |
| `EventMember` / `EventInvite` | team: admin or contributor; pending invites by email |
| `scrape.SourcePage` | a watched page: etag/last-modified, content hash, pending hash |
| `EventRevision` | full content snapshot for rollback |
| `scrape.IngestJob` / `ChangeSet` / `Change` | AI runs and the reviewable edits they propose |

- `status`: `draft` (invisible to every API) → `published` → `archived` (still viewable, apps show "ended").
- `paid=False` means demo/trial — mobile clients must show a "Demo Event" banner.
- `public` = listed in nearby search. Prospect test events and showcase events are published but unlisted, reachable by code/QR.
- Coordinates are nullable; `null` means unknown (never `0,0`).
- Times are stored in UTC; `Event.timezone` is the IANA zone used for entry and display.
- **Context** (API-018): `context` JSON on events, locations and items holds what the on-device assistants answer from
  offline (about, FAQ, amenities, policies, getting there, weather plan; menus, hours, age limits; performers with bios
  and genres; links). One normaliser, `events/context.py` (limits, word-boundary cuts, https links, empties dropped,
  the ~150 KB bundle budget), used on save, in the bundle (`bundle/` only, `null` when empty), by the AI diff (one
  `context` field per entity, merged key by key, lockable) and the console's "Details for the assistant" section
  (`console/forms/assistant_context.py`: text fields + validated line formats). Contract: `API_V2.md` → Context.
- **Apple Music links** (API-030, `events/music.py`): performers in schedule items' context get
  `apple_music_url` in the bundle when the Apple Music catalog search (storefront `us`, ES256 developer token from the
  MusicKit key) gives a confident match (equal normalised names, overlapping genres when known; ties only when the
  genres decide) or the organizer set one (the performers line ends `| https://music.apple.com/…` or `| no link`;
  locked). `ScheduledItem.music` keyed by normalised name; looked up once per name in a background thread after a
  save, or by **Find music links** (`console:event_music_links`, Ask Attnd `find_music_links`, a readiness check);
  cached (30 / 7 days), counted per UTC day on Diagnostics → Endpoints, a 429 pauses an hour; off without
  `APPLE_MUSIC_KEY_ID` / `APPLE_MUSIC_PRIVATE_KEY`. No Spotify (ROADMAP). Tests: `events/tests_music.py`.
- **Plan suggestions** (API-021, `events/plan_suggestions.py`): `Event.plan_suggestions` holds the "Plan your day"
  examples written for the event (≤ 4 `{text, kind: plan|question}`, the content version they were made for, source
  `ai` / `fallback`, cost). Claude Haiku 5.5 (`PLAN_SUGGESTIONS_MODEL`, default `claude-haiku-5-5`; official SDK,
  JSON-schema structured output, cached system prompt, effort `low`) writes them in the background ~90 s after a
  content push (`schedule()`, at most once per event per 10 min; `run_scheduled_tasks` → `refresh_due()`), never in the
  bundle request; `clean()` drops weekdays the event doesn't have, kid texts without family-tagged items, long or
  duplicate texts. New AI ones bump `content_version` once. The bundle's `plan_suggestions` = the stored ones while
  they all still fit, else `fallback()` from the event's real days, parts of the day, categories and tags.
- **Place images** (API-022, `events/place_images.py`): a new location without an image gets a photo found in the
  background (one worker thread, after commit, `events/signals.py`): the event's own pages (images whose alt / caption /
  file name name the place), the place's website (og:image; the site from `Location.website` or Google Places'
  `websiteUri`, never Places photos), Wikimedia Commons (near the pin or by name, author + licence). ≥ 600 px, no logos /
  maps / icons, no duplicate of another place's picture, Claude Haiku's vision must confirm it (≥ 0.75); Studio's
  SSRF-guarded `fetch`; Pillow re-encode (JPEG ≤ 2000 px, no EXIF / GPS) into `Location.image` with `image_auto`,
  `image_source_url`, `image_credit` (in the bundle), `image_license`. Never touches organizer or locked images; the
  location form shows "Found automatically · credit" with **Replace photo** / **Remove photo** (`image_never_refetch`)
  / **Find a photo** (agent actions `find_location_image`, `remove_location_image`; undoable). Every look is a
  `PlaceImageAttempt` (cost); `PLACE_IMAGES_DAILY_CAP_USD` (default 1) caps all of them per UTC day.
  `manage.py find_place_images [--event CODE]` backfills and drops old Google Places photo links. **Live Google
  photos** (API-031, owner: OK): when nothing free is found, `Location.google_photo` is set (only the flag and the
  place id are kept) and `resolved_image_url` becomes `/img/places/<id>/` (`events/views_photos.py`: Place Details
  `photos` + the media URL on every request, never cached server-side; a `302` with `max-age=3600` for the
  browser / app cache; `?format=json` = the authors for the credit; `GOOGLE_PLACE_PHOTOS`, `GOOGLE_PHOTOS_DAILY_MAX`,
  `place_photo_ip`). Tests: `events/tests_place_photos.py`.
- **Colour themes** (API-023, `events/theme.py`, `events/theme_ai.py`): `Event.theme` = `{light, dark}` × `primary`
  (fills, links) / `secondary` (soft tints) / `tertiary` (highlights) as `#RRGGBB`, plus `source` (ai / site / manual /
  default), `reason`, `page_colors`; `{}` = Attnd's purple. `normalize` validates it (a missing mode derived from the
  other); `tokens` maps a mode to the console / site tokens with contrast guaranteed (WCAG 2.2 maths: the fill nudged in
  lightness until white or near-black text on it is ≥ 4.5:1, link text ≥ 4.5:1 on the page and the soft tint,
  highlights ≥ 3:1; never refused); `checks` gives the form's notes. The AI import's crawl counts its pages' colours
  (`attnd/colors.py`, shared with Studio's importer) and the job's last step "Picked colours for the event" asks Claude
  Haiku (structured output, the logo / flyer as an image), falling back to the website's main colour, else Attnd's;
  a theme a person edited (`'theme'` in `locked_fields`) is kept. `theme_web` / `theme_apps` switch it on for the website
  and the bundle (`event.theme`, adjusted colours or `null`); `theme_web` is bookkeeping (no `content_version` bump).
  Backfill: `manage.py generate_themes [--event CODE] [--force]`; DEMO has a fixed one.
- **Screen layouts** (API-024, `events/layout.py`): `bundle.layout` for apps that send `X-Attnd-Sections: <catalogue
  version>` (none → `null`, and `display.tabs` drops `home`; the ETag and the cached bundle are per version, `-s1`).
  `CATALOGUE` (screens × section types: label, `since` version, needed facts, parts) mirrors iOS `SectionCatalogue`;
  `DEFAULTS` are the apps' built-in screens; `automatic()` leaves out sections nothing in the event could fill, leads
  food places with menus with the menu and tags with performers with the performers, and picks Home by preset;
  `clean_config` validates `Event.layout_config`; `resolve_layout` puts organizer screens over automatic ones and
  filters by version. A new section type = a new catalogue version (`since`), an app release, and `API_V2.md` →
  Layout. Tests: `events/tests_layout.py`.
- **Tags** (API-010) on locations and schedule items are always normalised by `events.models.normalize_tags` (lower case, ≤ 8 × 32 chars; `save()` and the bundle apply it). The AI keeps one small vocabulary per event (`scrape/tags.py` folds spelling variants); `manage.py backfill_tags` tags old events (only things with < 2 tags, never locked ones).

## API Authentication

All API views mix in `ApiKeyRequired` (`events/apis.py`). Three accepted token forms:

1. **Static API key** — `Authorization: Bearer <API_KEY>` env var. For dev/CI builds only. Keys listed (by SHA-256) in `REVOKED_API_KEY_SHA256` in `events/apis.py` are always rejected.
2. **Attestation JWT** — short-lived JWT issued by `/api/v1/attest/ios/` or `/api/v1/attest/android/` after verifying Apple App Attest or Google Play Integrity. Used by production mobile builds. The secret must be ≥ 32 bytes (system check `attnd.W001`, an error with `check --deploy`).
3. **CI key** (API-008) — `CI_API_KEYS`, for the live integration tests. Works with the static key off, but only for the DEMO event: views opt in with `ci_scope` (`event` → other ids 404, `lookup`/`nearby` → results limited to DEMO, `device`); `ci-` device ids only; usage is answered normally and discarded; anything else 401. Devices with a `ci-` id are excluded everywhere counts are shown (`Device.objects.real()`) and from pushes.

On 401 the mobile clients invalidate their stored JWT and re-attest before retrying once.

## API v1 (retired)

The v1 events API (`/api/v1/events/…`) was retired on Nov 19, 2026 (API-020): `SUPPORTED_API_VERSIONS` defaults to
`2`, so every `/api/v1/events/…` path answers `410` `update_required` (`events.app_status.retired_api_view`). Only
`/api/v1/attest/` (nonce, iOS, Android) stays, because both current apps attest there. `Device.api_version = 1` rows
remain from older builds.

## Attestation (`attestation/`)

- `ios_attest.py` — validates Apple App Attest CBOR attestation objects
- `android_attest.py` — calls Google Play Integrity API via service account
- `tokens.py` — issues/verifies short-lived JWTs signed with `ATTESTATION_JWT_SECRET`
- `views.py` — `/api/v1/attest/ios/` and `/api/v1/attest/android/` endpoints

Set `APPLE_ATTEST_ENVIRONMENT=development` locally to accept sandbox attestations from Xcode/TestFlight.

## Deep Linking (`join/`)

`/join/<event_code>/` detects OS via User-Agent and redirects:
- Android → Play Store with `referrer=event_code%3D<code>`
- iOS → App Store
- Desktop → landing page with both store links

`/p/<event_code>/<payload>` (API-015, trailing `/` optional) is a **shared plan link** from the apps: `payload` =
base64url (no padding) of `{"v":1,"ids":[…],"d":"YYYY-MM-DD"?}`, ≤ 1,500 chars (`join.views.encode_share_payload` /
`decode_share_payload`). Same routing as `/join/` (`SharedPlanRedirect`); the landing page (`join/landing.html` with
`shared_plan`) lists the items in the event's time zone with a QR of the link (`/p/<code>/<payload>/qr.svg`). Invalid
payload → the plain join page; unknown event → the join 404; opens count as `link_open`.

Link origin (`attnd/links.py`, Nov 20, 2026): join and shared-plan links (QR codes, posters, `join_url` in the API, share pages) are `https://attnd.app/...`, except on the dev server (`SITE_URL` https://dev.attnd.app), which makes `https://dev.attnd.app/...` links so a dev event opened in a browser reaches the dev server instead of production's 404. iOS lists both domains as associated domains; Android App Links are verified for attnd.app only, so dev links open in the browser on Android.

**Open in Attnd** (Nov 21, 2026): on phones the console's QR card (`live_on_phone`, page and chat) shows an **Open in
Attnd** button (people can't scan their own screen); the code and QR stay as they are. `attnd.links.open_in_app_links`:
iPhone → `attnd://join/<code>` (iOS doesn't open a Universal Link to the domain Safari is showing; the iOS app, not the
App Clip, registers the scheme) and, if the page is still visible ~1.5 s later (`visibilitychange`), the join page
(App Store / App Clip); Android → Chrome's `intent://<host>/join/<code>/#Intent;scheme=https;package=<ANDROID_PACKAGE_NAME>;S.browser_fallback_url=<join link>;end`.
`console.js` picks the platform from the User-Agent (`data-open-app`, `window.attndOpenApp` for chat cards); desktop
shows no button.

Well-known files served from `join/views.py`:
- `/.well-known/apple-app-site-association` — iOS Universal Links + App Clips (`/join/*` and `/p/*`, QR images excluded)
- `/.well-known/assetlinks.json` — Android App Links (domain-wide; the app's intent filter picks `/join/` and `/p/`)

## Local Development

PostgreSQL 18 runs in Docker, the same major version as Heroku and CI:

```bash
uv sync
scripts/dev_db.sh                     # start Postgres 18 (Docker Desktop on Mac; `dockerd &` first in a Claude cloud session)
export DEBUG=1 SECRET_KEY=dev-secret API_KEY=dev-key
uv run python manage.py migrate
uv run python manage.py runserver
```

`scripts/dev_db.sh stop` stops it; `scripts/dev_db.sh reset` deletes it and its data.

## Background work

- **AI jobs** run in a daemon thread of the web dyno by default (`INGEST_RUN_INLINE=1`). To use a worker dyno instead: `heroku ps:scale worker=1` and set `INGEST_RUN_INLINE=0`.
- **django.tasks** (F5, `background/`): Django's tasks framework on our own Postgres queue
  (`background.backend.DatabaseBackend`, `QueuedTask`; claims with `SKIP LOCKED`, priority, `run_after`, results,
  3 attempts, stale RUNNING requeued after 30 min). Off by default (`TASKS_BACKEND=threads`: today's in-process threads,
  TASKS = Django's immediate backend). With `TASKS_BACKEND=database`, `background.enqueue` / the tasks in
  `events/tasks.py` (place images, plan suggestions with a 95 s `run_after`) go to the queue, run by the `tasks`
  dyno (`manage.py run_tasks`, Procfile, scaled to 0 by default) or, without one, by `run_scheduled_tasks`
  (`run_tasks --once --max-seconds 240`). AI imports keep their own `IngestJob` queue, Ask Attnd and Studio runs their
  threads (they need seconds, not minutes). Tests: `background/tests.py`.
- **Heroku Scheduler** (every 10 min): `python manage.py run_scheduled_tasks` — sends due notifications, checks watched event websites (AI only on change), re-queues interrupted jobs, purges diagnostics (retention + 250 MB cap), expires leftover trial events, finds gallery card covers, keeps the DEMO event happening now and runs a scheduled prospect discovery when it is on and due, and refreshes out-of-date "Plan your day" suggestions (API-021).
- **Push**: `events/push.py` (APNs token auth over HTTP/2, FCM HTTP v1). Platforms without credentials are skipped and logged. Urgent notifications (API-011) go to every device with a token (time-sensitive APNs alert, high-priority FCM data message) plus an APNs `liveactivity` update to every `Device.live_activity_token` in the event; at most `URGENT_MAX_PER_HOUR` (3) per event, beyond that they go out as normal. iOS tokens are sent per APNs topic (API-019, `send_apns_groups`): App Clip devices (`Device.app_clip`) on `APNS_CLIP_TOPIC` (default `<APNS_TOPIC>.clip`, Live Activities `+ .push-type.liveactivity`), the app's on `APNS_TOPIC`.

## Running Tests

```bash
scripts/dev_db.sh && DEBUG=1 SECRET_KEY=x API_KEY=k uv run python manage.py test
```
Tests always run on PostgreSQL 18 (locally via `scripts/dev_db.sh`, in CI via the `postgres:18` service). Add `--parallel auto` to run them across CPUs (CI does; ~3x faster).

Tests live in `<app>/tests.py` (`diagnostics/tests.py`: uploads, caps, purge, retrace, request log, symbols, symbolication, panel, API-014, `LOG_EVENTS.md`), `events/tests_ci_keys.py` (API-008), `events/tests_hearts_tags_urgent.py` (API-009–011), `events/tests_demo_facts.py` (the DEMO facts the live suites rely on), `events/tests_v2.py` (v2 API, display, push), `console/tests_security.py` (rate limits, 2FA, CSP, admin path), `console/tests_passkeys.py` (WebAuthn with a software authenticator, emailed codes, sign-in policy), `agent/tests.py` (Ask Attnd: registry coverage, writes through the real views, confirm gates, caps), `prospects/tests.py` (staff-only pages, discovery with OpenAI and HTTP mocked, contact source rule, caps, schedule, test events, CSV), `studio/tests.py` (Studio access, sites-host isolation, pages, previews, data.json, forms, assets, versions, reports) + `studio/tests_ai.py` (agent loop, providers, images, importer) and `events/tests_contract.py` (the v2 contract snapshot).

Test settings (`TESTING` in `attnd/settings.py`): WhiteNoise uses the finders (no `staticfiles/` needed), `django.request` logs only errors (tests hit 4xx on purpose), and a 32+ byte test JWT secret. CI also runs `manage.py check` with `-W error::DeprecationWarning`; run the whole suite that way when upgrading Django/Python.

**Live integration tests** (`live_tests/`, stdlib only, not collected by `manage.py test`): `ATTND_IT_SERVER=<host> ATTND_IT_KEY=<key> python3 live_tests/run.py` checks a deployed server: the v2 edge cases (status codes per `API_V2.md`), the DEMO facts shared with the iOS/Android suites, `/join/` per user agent, well-known files, `/health/`, security headers, `/admin/` 404. Skipped without both variables. CI: "Integration · dev" after the dev deploy (`ATTND_DEV_API_KEY`), "Integration · prod" alongside the tests (`ATTND_PROD_CI_KEY`, informational). Device ids `ci-web-…`; user agent contains `attnd-ci`.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | Yes | Django secret key (startup fails without it unless `DEBUG`/`CI`) |
| `SECRET_KEY_FALLBACKS` | No | Comma-separated old secret keys while rotating `SECRET_KEY` (remove after two weeks) |
| `ALLOWED_HOSTS_EXTRA` | No | Comma-separated extra hostnames to serve (default: only `SITE_URL`'s host, www for attnd.app, `SITES_HOST`) |
| `PERMISSIONS_POLICY` | No | The Permissions-Policy header (default switches off camera, microphone, payment, USB…) |
| `DEBUG` | No | Set to any truthy value to enable debug mode |
| `API_KEY` | Yes | Static bearer token for mobile API auth |
| `STATIC_API_KEY_ENABLED` | No | Default `1`; `0` on production after the first release (SETUP 4d) turns `API_KEY` off |
| `CI_API_KEYS` | No | Comma-separated CI keys (≥ 32 chars) for the live integration tests: DEMO event only, also with the static key off (API-008, SETUP "CI keys") |
| `DATABASE_URL` | Prod | PostgreSQL connection string |
| `CLOUDINARY_URL` | Prod | Cloudinary connection string |
| `GOOGLE_PLACES_API_KEY` | No | Location image fetching during scrape |
| `INDEXNOW_KEY` | No | IndexNow key (8–128 letters, digits, `-`), production only: ping search engines about changed public pages (attnd/indexnow.py) |
| `GOOGLE_PLACE_PHOTOS` / `GOOGLE_PHOTOS_DAILY_MAX` | No | API-031 live Google place photos (default on with the key; `0` = off) / loads per Pacific day (2000; each load is 2 billed calls) |
| `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | Email | Mailgun SMTP credentials for the sending domain (`postmaster@mg.attnd.app` on prod; dev uses its sandbox). No password = mail goes to the log. They feed Django 6.1's `MAILERS` (settings `EMAIL_ENABLED`, `EMAIL_SMTP_OPTIONS`); the deprecated `EMAIL_*` Django settings aren't used |
| `MAILGUN_WEBHOOK_SIGNING_KEY` / `EMAIL_LOG_DAYS` | No | Mailgun's webhook signing key (delivered / bounced events in the Emails log; unset = the webhook is off) / the log's retention (90) |
| `DEFAULT_FROM_EMAIL` | No | Default `Attnd <noreply@mg.attnd.app>` |
| `SITE_URL` | No | Base URL for links in emails, default `https://attnd.app` (dev: `https://dev.attnd.app`) |
| `SIGNUPS_OPEN` | No | Default on (Nov 21, 2026): anyone signs up and makes demo events (20 phones each, API-025). `0` = invited emails only (console.signups) |
| `FREE_DEVICE_LIMIT` / `NEW_ACCOUNT_DAILY_IMPORTS` | No | API-025 phones per unpaid event (20) / AI imports a day for accounts under a week old (3) |
| `SHOWCASE_EVENTS` | No | The home page's showcase events: unset = dev.attnd.app and DEBUG only; `1` / `0` overrides |
| `CHAT_DEFAULT_ON` | No | Default on: new accounts open the console in the full-screen chat (off in tests) |
| `ADMIN_EMAILS` | No | Comma-separated; get server error emails |
| `EMAIL_ALLOWED_RECIPIENTS` | No | Comma-separated addresses/@domains; when set, nobody else is emailed (for dev) |
| `MAILGUN_*` | — | Set by the Mailgun add-on (SMTP server/port, API key, sandbox domain) |
| `APPLE_APP_BUNDLE_ID` | Prod | For App Attest verification |
| `APPLE_TEAM_ID` | Prod | For App Attest verification |
| `APPLE_ATTEST_ENVIRONMENT` | No | `production` (default) or `development` |
| `ANDROID_PACKAGE_NAME` | No | Default `com.arcu.attnd`; Play Integrity, Play links |
| `IOS_APP_STORE_URL` | No | Default `https://apps.apple.com/us/app/attnd/id1470627406`; join links, download badges, the update screen. Google Play is `https://play.google.com/store/apps/details?id=<ANDROID_PACKAGE_NAME>` (`ANDROID_PLAY_STORE_URL`); both are in every template's context (`console.context_processors.store_links`) |
| `APP_MIN_VERSION_IOS` / `_ANDROID` | No | Older builds get the blocking update screen (API-003) |
| `APP_LATEST_VERSION_IOS` / `_ANDROID` | No | Older builds get a dismissible "update available" banner |
| `APP_UPDATE_MESSAGE` | No | Custom text for the update screen and banner |
| `SUPPORTED_API_VERSIONS` | No | Default `2` (v1 retired, API-020); apps on other versions are told to update and `/api/vN/` returns 410 (`/api/v1/attest/` is unaffected) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Prod | Google service account for Play Integrity |
| `ATTESTATION_JWT_SECRET` | Prod | Secret for signing attestation JWTs |
| `ATTESTATION_JWT_TTL_HOURS` | No | JWT lifetime in hours, default 24 |
| `OPENAI_API_KEY` | No | Only Studio's image generation (and its optional OpenAI text provider) |
| `AI_MODEL` / `AI_SMART_MODEL` | No | Claude for everything outside Ask Attnd's loop and Studio (`attnd/claude.py`): default `claude-haiku-5-5` (imports, website checks, tags, vision, form fills, scores) / `claude-sonnet-5-5` |
| `INGEST_RUN_INLINE` | No | `1` (default) run AI jobs in web dyno; `0` = worker dyno only |
| `APNS_AUTH_KEY` / `APNS_KEY_ID` | Push | APNs .p8 key contents and key ID |
| `APNS_TEAM_ID` / `APNS_TOPIC` | No | Default to `APPLE_TEAM_ID` / `APPLE_APP_BUNDLE_ID` |
| `APNS_CLIP_TOPIC` | No | The App Clip's bundle id for its pushes (API-019), default `<APNS_TOPIC>.clip` |
| `APNS_USE_SANDBOX` | No | Truthy for development-signed builds |
| `FCM_PROJECT_ID` | Push | Firebase project ID |
| `FCM_SERVICE_ACCOUNT_JSON` | No | Defaults to `GOOGLE_SERVICE_ACCOUNT_JSON` (needs Firebase Messaging permission) |
| `DIAGNOSTICS_MAX_MB` | No | Size cap of the diagnostics tables, default `250` (API-013) |
| `DIAGNOSTICS_DEV_DAYS` / `DIAGNOSTICS_DEBUG_DAYS` | No | Log retention for dev builds / production debug mode, default `7` / `14` |
| `DIAGNOSTICS_SESSION_MAX_ENTRIES` | No | Entries per debug session per 10 minutes, default `20000` |
| `DIAGNOSTICS_MAX_SYMBOL_FILES` | No | Android R8 mappings kept, default `20` |
| `GITHUB_SYMBOLICATE_TOKEN` | No | Fine-grained GitHub token (Attnd-iOS, Actions: read and write) for the panel's Symbolicate (SETUP.md step 14) |
| `GITHUB_SYMBOLICATE_REPO` / `GITHUB_SYMBOLICATE_REF` | No | Default `apbeers/Attnd-iOS` / `main` |
| `SITES_HOST` | No | Attnd Studio's sites domain (no scheme): `attnd.site` (production) / `dev.attnd.site` (dev) once their DNS and certificates are live (SETUP.md). Default: the Heroku app's herokuapp.com hostname for attnd.app / dev.attnd.app, `127.0.0.1:8000` with `DEBUG`. With a real domain set, the old herokuapp.com host (`OLD_SITES_HOST`) only answers 301s from a site's path to the same path on `SITES_HOST` (`studio.middleware`; console, API, accounts, static: 404). Every address (`website_url`, the console) follows it; after a change run `manage.py refresh_site_addresses` (bumps the content version of events with a published site, so the apps' cached bundles refresh) |
| `STUDIO_BETA_EMAILS` | No | Comma-separated emails that also get Studio (an explicit override; Studio is otherwise staff/superusers only), default empty |
| `ANTHROPIC_API_KEY` | Yes | Every AI call (stage C): Ask Attnd on Claude Haiku 5.5; Studio uses Claude (`STUDIO_ANTHROPIC_MODEL`, default `claude-sonnet-5-5`) per `STUDIO_AI_PROVIDER=auto/openai/anthropic` (auto = when the key is set). Set on dev and production (Nov 21, 2026): both Studio and Ask Attnd run on Anthropic |
| `STUDIO_ANTHROPIC_MODEL` | No | Studio's Claude model, default `claude-sonnet-5-5` ($2 / $10 per MTok; dev and production set it explicitly) |
| `STUDIO_OPENAI_MODEL` / `STUDIO_OPENAI_FALLBACK_MODEL` | No | Default `gpt-5.5` / `gpt-5` |
| `STUDIO_IMAGE_MODEL` / `STUDIO_IMAGE_FALLBACK_MODEL` | No | Default `gpt-image-2` / `gpt-image-1` (asset descriptions use `AI_MODEL`) |
| `STUDIO_DAILY_SPEND_CAP_USD` | No | Studio AI spend per event per UTC day, default `10` (a full import is ~$2–4) |
| `STUDIO_MAX_STEPS` / `STUDIO_PREVIEW_LINK_DAYS` | No | Model calls per AI run (24) / private preview link lifetime (7 days) |
| `STUDIO_IMPORT_MAX_STEPS` / `STUDIO_RUN_MAX_MINUTES` | No | Model calls per import run (40) / wall clock per run (25 min; stops and keeps the work) |
| `STUDIO_IMPORT_SECONDS` / `STUDIO_IMPORT_MAX_MB` | No | Per import run: time (300 s) and bytes (150 MB) for crawling the old site and copying its files |
| `PROSPECT_MODEL` / `PROSPECT_SCORE_MODEL` | No | Prospects discovery (staff): the web search on `claude-sonnet-5-5`, the fit score on `claude-haiku-5-5` |
| `PROSPECT_DAILY_SPEND_CAP_USD` | No | AI spend of all prospect runs per UTC day, default `5` |
| `PROSPECT_RUN_MAX_MINUTES` | No | Wall clock per prospect run, default `20` (stops and keeps what it found) |
| `AGENT_ANTHROPIC_MODEL` | No | Ask Attnd on Anthropic, default `claude-haiku-5-5` (owner, Nov 21, 2026) |
| `AGENT_REFUSAL_MODEL` | No | The model a run continues on when Claude Haiku declines it, default `claude-sonnet-5-5` |
| `APPLE_MUSIC_KEY_ID` / `APPLE_MUSIC_PRIVATE_KEY` | No | API-030 artist links: the MusicKit key's id and the whole `.p8` (team = `APPLE_TEAM_ID`); unset = no lookups (SETUP step 20) |
| `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` | No | Billing (batch 7): Stripe Checkout, Customer Portal and the signed webhook; unset = "Payments aren't set up yet" (SETUP open tasks) |
| `PLAN_SUGGESTIONS_MODEL` | No | API-021 "Plan your day" suggestions, default `claude-haiku-5-5` (needs `ANTHROPIC_API_KEY`; without it the deterministic fallback is used) |
| `AI_MONTHLY_CREDIT_USD` / `AI_VISITOR_CREDIT_USD` / `AI_PLATFORM_ALERT_USD` | No | First values of the staff-editable AI limits (credit/): monthly credit per person (30), visitor allowance (5), monthly platform alert (100); the database row wins once saved, as for the per-day caps below |
| `AGENT_DAILY_SPEND_CAP_USD` | No | Ask Attnd AI spend per person per UTC day, default `2` |
| `AGENT_MAX_STEPS` / `AGENT_RUN_MAX_MINUTES` | No | Model calls per run (40) / wall clock per run while it works (15 min) |
| `AGENT_EFFORT` | No | Ask Attnd's reasoning effort, one for every call of a run and the whole conversation (so the prompt cache holds), default `medium`. Replaces `AGENT_PLANNING_EFFORT` (still read as a fallback) and `AGENT_STEP_EFFORT` (ignored) |
| `AGENT_MAX_ATTACHMENT_MB` | No | Files attached in the Ask Attnd chat, default `15` |
| `MCP_ENABLED` | No | The MCP server and its OAuth endpoints (docs/mcp_plan.md): on in DEBUG and tests; `1` on dev.attnd.app; production off until the owner signs off |
| `MCP_GRANT_MAX_DAYS` / `MCP_GRANT_IDLE_DAYS` | No | An MCP connection's maximum age (90) / how long an unused refresh token lasts (30) |
| `MCP_CIMD_HOSTS` | No | Hosts whose Client ID Metadata Documents may identify an MCP client, default `claude.ai,claude.com` |
| `TASKS_BACKEND` | No | `threads` (default) or `database` (django.tasks on the Postgres queue, F5; scale the `tasks` dyno or rely on the scheduler) |
| `DB_POOL` / `DB_POOL_MAX` | No | psycopg 3 connection pool on (default `1`) / its size per process (default `8`) |
| `CACHE_BACKEND` | No | `database` (default, the `attnd_cache` table) or `locmem` |
| `EVENT_COVERS_DAILY_CAP_USD` | No | The home page gallery's card covers (events/covers.py): Wikimedia checks + AI pictures per UTC day, default `2` |
| `PLACE_IMAGES_ENABLED` / `PLACE_IMAGES_DAILY_CAP_USD` | No | Place images found automatically (API-022), default on (needs `ANTHROPIC_API_KEY`) / `1` USD per UTC day for every look |
| `CSP_MODE` | No | `enforce` (default), `report` (Report-Only header, nothing blocked) or `off` |
| `ADMIN_URL_PATH` | No | Where the Django admin lives, default `manage-attnd/` (`/admin/` is a 404) |
| `RATE_LIMIT_<NAME>` | No | `count/seconds` overrides for `DIAGNOSTICS_IP` (600/600, log uploads), `DIAGNOSTICS_REPORTS_IP` (60/3600, report uploads), `LOGIN_EMAIL` (5/900), `LOGIN_IP` (20/900), `RESET_EMAIL` (3/3600), `RESET_IP` (10/3600), `SIGNUP_IP` (10/3600), `OTP_USER` (5/900), `PASSKEY_IP` (20/900, failed passkey sign-ins), `EMAIL_CODE_USER` (3/900, emailed sign-in codes), `CSP_REPORT_IP` (60/60), `STUDIO_FORM_IP` (10/600, form posts per IP per site), `STUDIO_FORM_SITE` (300/3600), `STUDIO_REPORT_IP` (5/3600), `ATTEST_NONCE_IP` (30/60, attestation challenges) |
| `RATE_LIMIT_TRUST_X_FORWARDED_FOR` | No | Default `1` (behind Heroku's router); `0` = use the socket address |
| `TWO_FACTOR_ISSUER` | No | Name shown in authenticator apps, default `Attnd` |
| `WEBAUTHN_RP_ID` | No | Passkey domain (relying party ID), default `SITE_URL`'s host (`attnd.app`; dev: `dev.attnd.app`). Changing it makes existing passkeys stop working |
| `WEBAUTHN_ORIGINS` | No | Comma-separated origins allowed to use passkeys, default `SITE_URL`'s origin (+ `http://localhost:8000` with `DEBUG`) |
| `WEBAUTHN_RP_NAME` | No | Name shown by the browser when creating a passkey, default `Attnd` |

## Deployment

CI/CD for all three repos (develop → dev, main → production) is documented in `SETUP.md`.

Heroku `Procfile`:
```
release: python manage.py migrate --noinput && python manage.py createcachetable && python manage.py collectstatic --noinput && python manage.py seed_test_data && python manage.py load_showcase_events --if-enabled
web: gunicorn attnd.asgi --worker-class asgi --workers 1 --timeout 300
worker: python manage.py process_ingest_jobs   # optional, scaled to 0 by default
```
`seed_test_data` keeps the App Review sample events FWFM2026 / ATIS2026 / DACN2026 current (start times rolled forward
on every deploy). They stay on production on purpose: App Review uses them (owner, Nov 19, 2026).

