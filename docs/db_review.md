# Database review (batch 7, Track C, Nov 22, 2026)

Scope: every concrete model (61 in our apps), the indexes Postgres actually has (`pg_index`), and `EXPLAIN (ANALYZE,
BUFFERS)` of the hot paths. Measured locally on Postgres 18 with the DEMO event, the eight showcase events and
synthetic volume: 50,000 devices, 20,000 AI jobs, 20,000 Ask Attnd runs / 160,000 steps (`ANALYZE` after loading).
Production is far smaller today; the point is to fix the shapes before the rows arrive.

Fixed in the same change (migrations `*_db_review.py` in events, console, scrape, agent, credit, studio, diagnostics,
background); everything else below is a recommendation or an explanation of why it stays.

## 1. Hot paths (before → after)

| Path | Before | After | What changed |
|------|--------|-------|--------------|
| `bundle/` first fetch (DEMO) | 5 queries, ~170 ms (building the bundle; queries < 0.1 ms each) | same | Already right: PK lookups; the preview-phone check uses the device PK |
| `bundle/` 304 | 3 queries, 5 ms | same | Answered from `content_version` alone |
| Task feed `console:tasks` | seq scan of `scrape_ingestjob`, 309 buffers, 4.3 ms at 20k jobs (grows linearly) | bitmap OR of `status` + new `ingestjob_finished`, 8 buffers | Indexes `(created_by, -created_at)` and `(finished_at)` |
| Live preview phones (`bundle/`, `changes/`, the QR card) | bitmap scan of every device of the event, 882 buffers, 2.0 ms at 4k devices | `device_preview` partial index, 2 buffers, 0.01 ms | Partial index `(event, preview_until) WHERE preview_until IS NOT NULL` |
| Notifications page | 18 queries (reach counted twice: the page and its form component, 4 × ~2 ms over the event's devices) | 12 queries | `events.push.reach(event)`: one aggregate, shared by the page, `notification_form` (and `urgent_sends_left` memoised the same way) |
| Overview | 24 queries | 22 | Same helper; the remaining duplicates (source pages, change sets) come from separate components and are cheap |
| Due notifications (`run_scheduled_tasks`) | seq scan (fine at 22 rows; grows with every sent update) | `notification_due` partial index on `send_time WHERE NOT sent` | |
| Ask Attnd context (thread, last 48 h, steps) | index scans, < 0.05 ms | same | Already indexed (`(user, -created_at)`, `thread`, step `(run, seq)` unique) |
| Dashboard | 9 queries, 3 ms | same | Counting subqueries (stage F) already |

Query-count guards: `events/tests_perf.py` (`test_console_pages` now also bounds the notifications page at 12 and the
task feed at 5; `DbReviewTests`).

## 2. Indexes

- **Duplicate**: `events_device` had the FK index plus an identical `Meta.indexes = [Index(['event'])]`. Dropped the
  Meta one.
- **Redundant FK indexes** (a composite or unique index starting with the same column already serves every lookup;
  each extra index costs on every insert, which matters most for `diagnostics_diagnosticentry`, the biggest table):
  `db_index=False` on Location.event, ScheduledItem.event / .location, Notification.event, EventMember / EventInvite /
  EventJoin / UsageCount.event, SourcePage.event, UndoEntry.user, AgentRun.user, AgentStep.run, AgentUsage.user,
  AiSpend.user / .event, AiCreditNotice.user, SiteVersion.site, StudioAsset.event, FormSubmission.site,
  DiagnosticEntry.session, QueuedTask.status. Postgres still has an index usable for each FK (cascade deletes stay
  indexed).
- **Odd composites replaced**: `Event (user, name, event_code, latitude, longitude)` served nothing (the owner FK index
  covers `user`; codes use `UPPER(event_code)`), now `event_nearby_idx (start_time, latitude, longitude) WHERE public
  AND status = 'published'` for `nearby_events`. `Location (event, user)` and `Notification (event, user)` matched no
  query; now `(event, sort_order)` (every list's order) and `(event, send_time)`.
- **Not changed**: CharFields with `db_index=True` get Django's extra `*_like` (`varchar_pattern_ops`) index; useless
  for `status` columns but harmless at these sizes, and dropping it needs raw SQL. Revisit if write volume grows.

## 3. Foreign keys and `on_delete`

- **Bug fixed**: `console.Order.event` (legacy v1 in-app purchases) was `DO_NOTHING`: deleting an event with an old
  order failed on the database's FK constraint. Now nullable + `SET_NULL` (the purchase record stays).
- Everything else is deliberate: content and per-event rows `CASCADE` with the event; authorship (`user`,
  `created_by`, `published_by`, `decided_by` …) `SET_NULL`, so deleting an account never deletes events (CLAUDE.md →
  Accounts); per-person rows (passkeys, sessions, preferences, Ask Attnd runs) `CASCADE` with the account.
- **Loose ids kept on purpose** (an FK would delete or null them at the wrong moment):
  `console.HistoryRecord.event_id` (history outlives the event), `diagnostics.*.event_id` (anonymous logs, never
  joined to content), `agent.AgentStep.event_id` / `undo_id` and `ChangeProposal.undo_id` (UndoEntries are purged
  after 24 h; the chat then shows the Undo as expired), `scrape.Change.target_id` (one of three models),
  `EventJoin.device_id` (counts phones that left and whose Device row is gone). All are indexed where queried.

## 4. Null / blank

- `Location.website` and `Location.image_url` are `null=True` URLFields, so empty has two spellings (`NULL`, `''`).
  Every reader already normalises (`location.website or None`, `exclude(image_url='').exclude(image_url__isnull=True)`);
  left as is (a migration would rewrite every location row for no visible change). New URL fields: `blank=True`
  only.
- No other CharField / TextField is nullable. Money is `DecimalField` everywhere (AI ledger, Studio, agent usage).

## 5. Constraints and JSON checks

- New `CHECK (jsonb_typeof(col) = 'object' | 'array')` constraints (`events.models.json_is`) on the JSON columns every
  reader assumes a shape for: Event `display_config`, `layout_config`, `live_activity`, `theme`, `plan_suggestions`,
  `context` (objects), `source_urls`, `locked_fields` (arrays); Location and ScheduledItem `tags`, `locked_fields`
  (arrays), `context` (object), ScheduledItem `music` (object). The migration first resets any row of the wrong shape
  to the field's default, so it can't fail on production data.
- Existing uniques are right (event code, `(event, user)` members, invites per email, usage buckets, joins, source
  pages, site version numbers, agent step `(run, seq)`). Not added: `end_time >= start_time` on schedule items (AI
  imports produce both orders and the apps treat a bad end as start + 1 h, so a constraint would only reject
  imports); case-insensitive uniqueness of `event_code` (codes are generated upper case and looked up with
  `iexact`; a `UNIQUE (UPPER(event_code))` is worth adding once we verify production has no case-only duplicates:
  `SELECT UPPER(event_code), COUNT(*) FROM events_event GROUP BY 1 HAVING COUNT(*) > 1`).

## 6. Dead fields

- Removed `Event.order_id` (v1 purchases; written nowhere, read nowhere).
- Kept: `console.Order` (historic purchase records; no code reads it, the admin can), `AgentRun.previous_response_id`
  (still written, empty since stage C; drop with the next agent migration that touches the table).

## 7. Follow-ups (not done)

- `UPPER(event_code)` unique after the production check above.
- Device rows are never purged; at real scale add a retention job for devices not seen for a year (counts use
  `EventJoin`, so nothing visible changes).
- `events_usagecount` will be the largest table in production; it already has `(event, bucket)` and `(bucket)`, and the
  Usage page reads one event at a time. Partition by month only if it passes ~10 M rows.

## 8. Async server (ASGI)

Owner: "do it", with numbers. Done without a new dependency: gunicorn 27 ships an ASGI worker (`--worker-class
asgi`). Django's own ASGI handler was tried first and dropped: with our synchronous middleware it starts a new thread
per request (`ThreadSensitiveContext`), which cut `/health/` from ~820 to ~290 requests a second. So `attnd/asgi.py`
keeps Django's WSGI application on a fixed thread pool (`attnd/wsgi_bridge.py`) and only the long-polls are handled
on the event loop (`attnd/longpoll_asgi.py`).

Measured locally (one worker, Postgres 18 in Docker, 16 concurrent clients for throughput; N preview phones waiting
on `changes/` while a probe hits `/health/` every 100 ms for 15 s):

| | gthread, 4 threads (before) | ASGI + thread pool (after) |
|---|---|---|
| `/health/` throughput | 820 req/s (p50 18 ms) | 601 req/s (p50 22 ms) |
| DEMO `bundle/` throughput (cached bundle) | 139 req/s (p50 107 ms) | 136 req/s (p50 112 ms) |
| 10 waiting preview phones | 2 held, 8 told "poll again in 15 s" | 10 held |
| 100 waiting preview phones | 2 held, 98 told to poll again | 100 held, `/health/` p50 3.1 ms, max 7 ms |
| 300 waiting preview phones | — | 300 held, `/health/` p50 2.7 ms, max 9 ms |
| Wake-up after a change (50 waiting) | ≤ 1 s for the 2 held | all 50 answered within 0.9 s of the change |
| Database while waiting | 1 query per waiter per second | 2 queries per process every 0.5 s, for all waiters |

The ~0.5 ms per request the bridge adds only shows on a trivial endpoint; real pages are dominated by their own work.
Not measured on dev: the load script needs a preview phone and the dev API key, which this session doesn't use (owner
rule: secrets stay out of the session). `scripts/longpoll_load.py` (stdlib only, the key from `ATTND_LOAD_KEY`) runs the same test against any server.
