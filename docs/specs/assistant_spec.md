# Attnd event assistant spec (iOS + Android; small web parts) — agreed Nov 15, 2026

> **iOS update, Nov 19, 2026 (owner):** the App Clip is now the same as the app, and App Clips can't use App Intents,
> background tasks or WeatherKit, so iOS **removed Siri / App Intents / Shortcuts / Spotlight, the "Now & Next" Home
> Screen widget, Add to Calendar (the `add_to_calendar` action and `calendar` block) and "Tell me when I arrive"**.
> On iOS the plan is a full page from a floating button, not a tab (planner_view_spec.md). Those parts below are
> marked "Android only / removed on iOS Nov 19, 2026"; the rest is unchanged and shared.

The planner grows into an **event assistant**: one screen where a person can do everything the app can do — search, ask,
explore, like, plan, tweak the plan, follow on the Lock Screen, set reminders, share — and every answer is shown with
the best-fitting native mini UI. The manual tabs stay exactly as they are. Everything runs on the device; the on-device
model only *understands* and *chooses components*; plain, tested code does the work. Phones without the model get the
same screen with rule-based understanding and layout. Read with: glance_plan_spec.md (planner rules 1–12, hearts →
"likes", live features) and search_spec.md. Android's `domain/` is the reference implementation; iOS ports it 1:1 with
the same fixtures and expected outputs (as was done for the planner).

## 0. Rename "hearted" → "liked" (user-facing only)
All UI text, accessibility labels, chips ("Liked"), reasons ("Because you liked X", "You liked this", "At X, which you
liked"), onboarding, Siri phrases ("What have I liked in Attnd") and intent titles (removed on iOS Nov 19, 2026), Gemini AppFunctions descriptions
(function names may stay; descriptions say "liked"), store text if it mentions hearts, the console ("likes" instead of
hearts in labels). The heart icon stays. Storage keys, Kotlin/Swift identifiers where renaming is risky, and the server
usage names (`location_heart`, …) do NOT change.

## 1. Action layer (one registry, used everywhere)
Pure description + one executor per platform. Every action: `name`, typed params, `mutates` (bool), `confirm` (none /
chip / sheet), result type. Used by the assistant (model tool calls and rule-based routing), the manual screens' buttons
(where practical), Siri/App Intents (removed on iOS Nov 19, 2026; only the Live Activity buttons are App Intents), Android AppFunctions, Live Activity / Live Update buttons and widgets (iOS: Live Activity only). Names:
- read: `search(query, kinds?)`, `get_item(id)`, `get_location(id)`, `get_update(id)`, `event_info()`, `now_next(at?)`,
  `whats_on(range: {from,to} | day)`, `liked()`, `plan()`, `near_me(kind?)` (needs location), `why_not(item)`,
  `route(to_location, from?)`, `day_route(day?)` (the plan's walking legs)
- write (mutates): `like(kind,id)`, `unlike(kind,id)`, `plan_make(request)`, `plan_edit(ops[])` (§4), `plan_undo()`,
  `plan_version(kind: chill|everything)`, `checkoff(item, done)`, `follow_lock_screen(on)` (confirm chip),
  `reminders(on)` (confirm chip), `notifications(on)` (confirm chip; may prompt OS permission), `add_to_calendar(items|plan)` (Android only / removed on iOS Nov 19, 2026)
  (OS sheet), `share_plan()` / `share_item(id)` (OS share sheet), `open(screen, id?)` (navigate to a manual screen:
  map, locations, schedule, updates, flyer, options, tour), `directions(location)` (opens Maps app; confirm none).
- Never available to the assistant: leave event, debug mode, account/support copy (manual only).

## 2. Response blocks (what the assistant shows)
A response = ordered `blocks`. The model (or the rule-based router) produces blocks with **ids and options only**; the
app resolves data from the bundle, validates every id, drops unknown blocks/ids, and falls back to `text` + `list`.
Block types (component catalogue; native SwiftUI / Compose, reused by the manual screens):
| type | props | renders |
|---|---|---|
| `text` | `text` (≤ 400 chars, plain + **bold**) | assistant sentence |
| `item_card` | `id`, `show`: [time, place, image, distance, like, actions] | one schedule item, rich |
| `place_card` | `id`, `show`: [image, distance, now_next, like, actions] | one location, rich |
| `update_card` | `id` | organizer update (urgent styling) |
| `list` | `ids[]` (mixed `s12`/`l3`/`u9`), `title?`, `limit?` | compact rows |
| `carousel` | `ids[]`, `title?` | horizontal cards (2–8) |
| `timeline` | `ids[]` or `source: plan|whats_on`, `range?` | time-ordered with now line |
| `map` | `pins[]` (location ids), `route?`: [loc ids in order] | mini map; tap → full map |
| `gallery` | `ids[]` (locations with images) or `flyer` | images / flyer pages |
| `now_next` | `location?` | live strip |
| `countdown` | `id` | counts to an item's start |
| `compare` | `a`, `b` (item ids or plan versions) | side by side |
| `plan_diff` | `diff` (from `plan_edit` preview) | − / + rows, Apply, Undo |
| `plan` | `day?` | the plan timeline with walk legs + mini map |
| `setting` | `setting`: follow_lock_screen | reminders | notifications | toggle card |
| `summary` | `kind`: walking | recap | liked_counts | stats card |
| `share` | `target`: plan | item:<id> | QR + share button |
| `calendar` | `ids[]` or plan | "Add to Calendar" card (Android only / removed on iOS Nov 19, 2026) |
| `chips` | `chips[]`: {label, action, params} (≤ 5) | follow-up quick actions |
| `confirm` | `action`, `params`, `prompt` | Yes/No for a mutating action |
Layout rules for the rule-based renderer (and validation caps for the model): 1 result → `item_card`/`place_card`;
2–8 → `carousel` (cards) or `list` if > 8 or mixed kinds; places with coordinates add a `map` (≤ 12 pins); time-range
questions → `timeline`; any mutating action → `confirm` (except like/unlike/checkoff/plan edits, which apply with Undo);
always end with ≤ 5 `chips`. Max 8 blocks per response.

## 3. Understanding (two paths, same blocks)
- **Rule-based router (every phone; pure, tested):** classifies input → intent + params using the existing search
  parser, time words, `PlanRequest.looksLikePlan`/`heuristic`, and a small command grammar:
  plan edits ("replace X with Y", "remove X", "add X", "move X later/earlier", "keep X", "I already ate", "I'm tired",
  "done by 9", "short walks", "I'm at <place>", "more <tag>", "less <tag>"), like/unlike ("like X"), toggles ("remind
  me", "follow on lock screen", "turn on notifications"), navigation ("show the map", "open flyer"), share/calendar
  ("share my plan", "add my plan to calendar"), "why isn't X in my plan", "what's on now/next/tonight", "near me",
  questions (fallback → search results + "Ask" disabled note), otherwise search. Entity names resolve with the search
  index (best match ≥ threshold; ambiguity → `chips` to pick).
- **Model path (when available):** iOS Foundation Models tool calling + `@Generable` `AssistantTurn` {blocks, actions};
  Android Gemini Nano JSON (same schema; parse first `{`…last `}`). The model sees: event facts summary, the plan
  summary, likes, the last 6 turns (summarised beyond), and only retrieved items (≤ 12) — never the whole bundle. It
  may call read actions (tools) and propose write actions; write actions always go through the executor (validation,
  confirm/Undo). Invalid output → the rule-based path for that turn. Streaming: render blocks as they complete.
- Both paths produce the same block JSON; tests assert router outputs for a fixed list of inputs (§9).

## 4. Plan editing (deterministic; extends the planner)
- `plan_edit(ops[])` ops: `replace{item, with?: item | like: item | tag}`, `remove{item}`, `add{item}`, `move{item,
  earlier|later}` (re-solve with the item pinned to the next/previous feasible slot), `keep{item}` (pin), `unkeep`,
  `constraint{...}`: `no_food_until` (time; "I already ate" = now + 3 h; marks the next meal break done), `done_by`
  (time), `start_at` (time), `max_walk_min` (int), `pace` (relaxed|balanced|packed, from now), `from_location`
  (location id: the plan restarts from there now), `more_tag` / `less_tag` (tag weight ±1 on score M/D).
- Re-solve rule: items already started/ended and checked-off stay; pinned (`keep`, user-added) items are must-includes
  like liked items (rule 7); constraints feed the window (start_at/done_by), candidate filter (no_food_until drops food
  items/meals before the time; max_walk_min drops gaps whose walk exceeds it), pace and score. Everything else is the
  existing planner (rules 1–12). Same inputs → same plan on both platforms.
- Every edit returns a **diff** (removed, added, moved items; meal changes) shown as `plan_diff` with Apply; applying
  pushes onto an undo stack (≤ 20) per plan; `plan_undo()` pops. Plan versions: `chill` = relaxed pace + max_walk 10,
  `everything` = packed pace; `compare` shows both.
- Conversation + constraints are stored with the plan (per event, on device, kept after leaving like the plan).

## 5. Walking map
`day_route(day)`: legs between consecutive planned items with locations: straight-line estimate (existing `Walking`)
immediately, upgraded per leg to a real route (iOS MKDirections walking; Android Routes API — one computeRoutes call
with intermediates for the whole day when ≤ 25 stops, cached for the day; fall back per §3 of glance spec when the route
is > 2.5× straight or fails). Renders: mini map (numbered stops, route in the accent colour, user dot), leg rows in the
timeline ("6 min walk · 0.3 mi"), day total ("2.8 mi walking today"). Scrolling the timeline highlights the leg; tapping
opens the full map with that leg.

## 6. Live features (all local)
- **Running late**: at departure time (next.start − walk − 5 min) if the user's location is known and not within 100 m
  of the next item's location (or the walk from where they are is longer than the time left) → in-app banner + the Live
  Activity/Update "Leave now" state; if they can't make the start → offer a swap (`plan_edit replace`) as chips.
- **Check-off**: an item is auto-checked when the user's location was within 150 m of its location for ≥ 10 min while it
  ran (foreground/background location only if already permitted; no new permission prompts), or by tap; a planned item
  that ended unchecked while the user was elsewhere → "You skipped X — fill the gap?" chip.
- **Gap filler**: a gap ≥ 30 min in the plan → best candidate that fits (score, walk ≤ 10 min from the previous stop):
  "40 free minutes near Trinity Stage: Kolache masterclass is 3 min away" chip.
- **Organizer changes**: planned items changed/removed → inline card in the plan with "Re-plan" (exists) and the diff.
- **Like clash**: liking an item that overlaps a planned one → chip "Add to plan instead of X?".
- Live Activity / Live Update show the plan's next item + walk leg + running-late "Leave now"; widgets
  (Android Glance widget; the iOS WidgetKit "Now & Next" widget was removed Nov 19, 2026): "Now / Next in my plan" with
  walk time; Gemini (Siri: removed on iOS Nov 19, 2026): "What's next on my plan", "Swap my next thing", "I already
  ate" (plan_edit).

## 7. Extras
- **Share** (`share_plan`, `share_item`): link `https://attnd.app/p/<EVENT_CODE>/<payload>` where payload = base64url of
  a compact JSON {v:1, ids:[schedule ids], d?: day} (≤ 1,500 chars; no personal data). Opening it in the app (Universal
  Link / App Link) shows a read-only "Shared plan" with "Use this plan" and "Merge with mine". **Group plan**: merging
  several shared plans + own likes = union of likes weighted by how many people liked each item (+1 per person), then the
  normal planner. QR in the `share` block. SERVER (web): `/p/<code>/<payload>` route (same OS detection as /join/: app
  open via AASA/assetlinks paths, store redirect with the event code, desktop landing listing the items by name), AASA +
  assetlinks include `/p/*`.
- **Calendar** (Android only / removed on iOS Nov 19, 2026): iOS EventKit (`EKEventEditViewController` for one item, write-only access for the plan) / Android
  `CalendarContract` insert intent (one per item; plan → a chooser that inserts each); event tz, location name.
- **Why not X?**: explains with the planner's own reasons: clashes with <planned/liked item>, outside your window,
  filtered (food constraint, kid-friendly, walk limit), score too low → "Swap it in" chip (`plan_edit add`).
- **Recap** (end of each event day or on request): items checked-off, liked count, walking distance from day_route of
  checked items; "Tomorrow's plan is ready" chip when the event continues.
- **Versions**: Chill / See everything side by side (`compare`).

## 8. Screens
- **Assistant screen** (replaces the search sheet/full-screen search as the floating button's target; the old Search |
  Ask toggle goes away): top = the pinned plan (collapsible; empty → "Make a plan" card with examples when the model is
  available, else the guided plan request card from today); middle = the conversation (blocks); bottom = one input bar
  (focus on open, keyboard up) with intent chips ("Searching…", "Asking…", "Editing plan…" — tap to switch). Typing
  shows live search results as you type (debounced) without sending; Enter/send runs the full turn. Empty state:
  suggestion chips (Happening now, Up next, Liked, categories, examples).
- My plan in the Schedule tab stays (same plan, same components); "Open in assistant" from it. Every manual detail
  screen gets **Ask about this** (opens the assistant with that item as context: "Tell me about X" with item_card).
- Assistant cards → "Open in Schedule / on Map". Usage: existing metrics only (search when a search runs, views when
  details open). Logging: new event names `assistant.turn`, `assistant.block`, `assistant.action`, `plan.edit`,
  `plan.undo`, `live.running_late`, `live.checkoff`, `share.created`, `share.opened` (add to LOG_EVENTS.md); never the
  typed text in production (lengths only).
- Screenshot mode: assistant not shown; App Clip: rule-based assistant (no model, no planner write actions except
  like), no share import.

## 9. Shared test cases (both platforms, same fixture as PlannerFixture)
Router: "tacos" → search → place_card Taco Row + chips; "what's on tonight" → timeline; "food near me" (with location)
→ map + carousel of food places by walk; "like prairie lights" → like action + item_card; "replace the 3pm thing with
something like neon armadillo" → plan_edit replace; "I already ate" → constraint no_food_until now+3h + plan_diff
without the next meal; "done by 9" → done_by 21:00; "share my plan" → share block; "why isn't neon armadillo in my
plan" → why_not → "clashes with The Prairie Lights (liked)"; "turn on reminders" → confirm; unknown gibberish → search
fallback with empty-state chips. Plan edits: expected plans after each op on the fixture (Android writes them first,
iOS must match). Share codec round trip and a known payload string. Block validation: unknown ids dropped, > 8 blocks
truncated, model JSON with an invalid block → rule-based fallback.
