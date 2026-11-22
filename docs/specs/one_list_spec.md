# One list: My schedule is the only stored list (iOS first, then Android)

Owner-approved, Nov 22, 2026. Replaces the stored `Plan` and every path that kept it in sync with
My schedule (iOS: `MySchedule.apply(plan:replacing:)`, `reconcile`, `PlanStore.onApply` →
`applyPlanToSchedule`, `mealsCovered`, "Your plan changed" / keep-changed-plan, stored plan versions).
No API or server change.

## 1. Data model
- **My schedule** (per event, as today): entries `item` / `place` (timed or saved), reminder, fingerprint,
  plus two new optional fields:
  - `source`: `you` (default, also for old entries) | `planner` | `assistant` | `shared`.
  - `reason`: short text ("Because you like jazz", "Lunch break", "In a shared plan"), ≤ 80 chars.
  Lenient decoding: unknown source → `you`, missing reason → none.
- **Planner preferences** (per event, small record, stored with the personal state): `pace`
  (relaxed / balanced / packed), `kid_friendly`, `max_walk_minutes`, `liked_tags`, `disliked_tags`,
  `meals` (lunch / dinner wanted), `earliest`, `latest` (HH:MM). Everything optional / defaulted.
- **No stored Plan.** `Plan` / `PlanDay` / `PlanEntry` may remain as in-memory value types (the planner's
  working result and the derived day view) but are never persisted.

## 2. The planner is a function
`propose(schedule, preferences, request?, bundle, now) → ScheduleProposal`
- `ScheduleProposal` = edits to My schedule: `add` (item, or place at a time for a meal break) and
  `remove`, each with a reason; plus a summary line and the diff pill text ("+5 items, +1 lunch break").
- Planner rules 1–20 (scores, TF-IDF similarity, gap/walk, pace cap, DP choice and tie-breaks, meals,
  reasons, swap alternatives) are unchanged. They run as today and the result is diffed against My
  schedule. **My schedule's items are the must-includes** (rule 7, replacing "hearted"); its timed food
  places cover that day's meal break (the rule formerly called `mealsCovered`, now just an input).
- Removals are proposed only for entries with `source: planner` (never what the person added) unless the
  request asked to remove them ("drop the BBQ").
- Same fixture numbers as today (`PlannerFixture`): the chosen items, scores and times don't change; only
  the output shape does.

## 3. One flow for every change
New plans, edits (add / remove / move / constraints), versions (Chill / See everything), "swap the next
thing", sequences ("music, food, then music"), shared plans (use / merge) and like-clash / running-late
chips all produce a `ScheduleProposal` → **preview** (proposal card in the chat; marks on the day
timeline: dashed suggested rows, crossed-out rows, "Moves to 8:00 PM") → **Apply** → one entry on the
**Undo history** (per event, ≤ 20 snapshots of My schedule). Hearts from rows / cards add the same kind of
history entry and show the Undo toast. "undo" typed, the Undo card and the toast all pop that history.
Cancel / "no" / any other turn drops a pending proposal.

## 4. Derived, never stored
Computed on demand from My schedule (+ bundle + position + preferences):
the day timeline (Your Day / `my_day`), walking legs and day routes, gap fillers, running late / leave
now, the recap, auto check-off hints, WhyNot ("why isn't X in my schedule"), the Live Activity content,
reminders, the planning board (Duo). Check-offs stay a per-event set of item ids in the personal state.

## 5. Organizer changes
Unchanged: My schedule's fingerprint notices ("X moved to 8:00 PM" · Keep / Remove / Find another;
"X was cancelled"). "Find another" is a swap proposal (§3). The separate "Your plan changed" banner goes.

## 6. Migration (once per event, idempotent)
Read the old `pref.plan.<id>` (and the newest of `pref.planHistory.<id>` / `plan-history-<id>.json`):
- every planned item not in My schedule → added with `source: planner` and the plan's reason;
- each meal break with a food place not in My schedule → a timed place entry (`source: planner`,
  reason "Lunch break" / "Dinner break");
- preferences from `Plan.preferences` + `Plan.constraints` (pace, kid-friendly, max walk, tags, meals,
  earliest / latest);
- check-offs from `Plan.checkedOff`.
Then delete the old keys / files. Re-running finds nothing and changes nothing. No usage metrics.

## 7. Naming
The floating button and the chat are **Ask Attnd** (icon `sparkles`): it is the assistant, not a plan,
and matches the organizer console's Ask Attnd so the brand has one name for "talk to Attnd". The list is
always "My schedule" / "schedule" in user-facing text ("Plan my Saturday" still works as a request; its
proposal says "Add 6 things to your schedule"). Usage metric names and storage keys are unchanged.

## 8. What stays the same
The router grammar and the assistant eval questions; actions keep their names, mapped onto §3:
`plan_make` / `plan_edit` / `plan_version` / `plan_swap` → a proposal; `plan_undo` → the history;
`schedule_*` as today. Share links (API-015 payload `{"v":1,"ids":[…],"d":…}`) unchanged: built from My
schedule's items. The Live Activity (follows My schedule; the "plan" source and `follows_plan` go, the
`attnd-live://…/plan` link opens Ask Attnd), reminders, API-009 metrics, the API and the server.
attnd-web: no code change; wording only (docs that say "My plan" for the apps; `planner_view_spec.md`
is superseded on iOS by this spec). `API_V2.md` needs no change.

## 9. Test plan
- **Planner**: fixture cases keep their chosen items / scores / times; new cases for `propose` (adds with
  reasons, meal places, removals only of `planner` entries, My schedule's items as must-includes).
- **My schedule**: source / reason codec (lenient), migration (plan → entries, meals, preferences,
  check-offs, idempotent, keys deleted), undo history (≤ 20, hearts included).
- **Proposals / editing**: every proposal kind (§3) previews without changing My schedule and applies as
  one history entry; cancel leaves it unchanged; "do it" / "the second one" replies.
- **Router**: same JSON per input, `plan_*` actions now proposals; assistant eval router mode ≥ 242/247.
- **Live / flow**: Live Activity, running late, gap fillers, recap, WhyNot and Your Day from My schedule only.
- **On device**: migration from a real older build, Undo toast + card, Duo planning board, Live Activity.
