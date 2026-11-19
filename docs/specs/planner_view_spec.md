# Planner as a trip-style page + offline AI context (plan, not built; Nov 19, 2026)

iOS first; Android later where Google has a native equivalent. Reuses the existing planner, plan edits, previews/undo,
swap alternatives, walk legs, live hints, shared links and calendar export.

## 1. Who sees the AI planner
Only devices with an on-device model: Apple Intelligence (Foundation Models) on iOS, Gemini Nano (ML Kit GenAI) on
Android. **No model → no AI assistant and no planner UI at all** (the plain search button and search stay). This
replaces the earlier "router-only planner without the model" decision. Everything the assistant does must work
**fully offline** from data already on the phone.

## 2. Layout
- **Phones (one screen)**: the plan is a full page (from the floating My plan button); the only control is the text
  box docked above the keyboard. Typing updates the page; results/proposals show in a small pane above the box and as
  marks in the plan. No chat transcript. The snapping bottom panel is retired; the closed state stays the button with a
  "Next: X · 7:00 PM" peek.
- **iPhone Duo unfolded / wide windows**: side by side: chat column (conversation, chips, input) on the left, the plan
  page on the right; one controller drives both.

## 3. The plan page (from the Layla reference)
- Sticky chips at the top: days ("1 Sat", "2 Sun") for multi-day events, else Morning / Afternoon / Evening; tap
  scrolls, the current one highlights.
- Timeline rail with icon per row:
  - **Day header**: date, **weather chip** (§4), count, a one-line on-device AI summary + "Read more".
  - **Item card**: location photo, time, name, place, tags, reason, like heart, Now/Next/"in 20 min"; **Change** and
    **Remove**.
  - **Walk leg** between items ("Main Stage → Taco Row · 6 min walk", route bar); Change → closer option / leave earlier.
  - **Meal break** ("Lunch · 12:15 · Taco Row") with Change; **gap row** ("45 min free · 3 ideas nearby").
- **Change** opens a sheet of alternatives for that slot (reason, time/walk difference, Compare); picking one previews
  in place (crossed-out / dashed rows), Apply commits with Undo. Remove offers "Also fill this gap?".
- **Bottom bar**: "8 items · 10 AM – 11 PM · 2.1 mi walking", Share, Add to calendar, Undo.
- Tapping a card opens the item's normal detail.

## 4. Weather (WeatherKit, on the device)
- iOS: WeatherKit (`WeatherService`) daily + hourly forecast for the event's coordinate, fetched when online and
  **cached on disk** so the last forecast shows offline ("as of 2:14 PM"). Requires Apple's attribution (Apple Weather
  mark + legal link) wherever weather shows.
- Capability: WeatherKit must be enabled on the App ID and in entitlements. CI signs with an App Store Connect API key
  and failed on the App Group capability; if WeatherKit breaks the archive the same way, CI moves to manually managed
  profiles (which also fixes the App Group / widget). Owner task: enable WeatherKit for `com.arcu.Attnd` in the
  developer portal (Capabilities + App Services).
- Android: no weather unless Google has a native equivalent (Google Maps Platform's Weather API exists, paid, keyed;
  decide later).

## 5. Rich event context for on-device AI (scraper → bundle)
- The AI import/refresh extracts, per event and per item, everything an assistant needs offline: long descriptions,
  performer/vendor bios, genres/styles, menus and prices, age limits, accessibility (step-free, seating, sensory),
  amenities (restrooms, water, ATMs, first aid, charging), event FAQ (parking, shuttles, bag policy, re-entry, rules,
  weather plan), venue notes, links. Same review/lock rules as today (organizers can edit; locked fields win).
- Sent to the apps as additive context in the bundle (new tracker entry, e.g. API-018: `event.context`,
  `location.context`, `schedule_item.context`, `faq[]`), compact text with a size budget; ETag/304 as today, cached on
  disk with the bundle.
- On the device: retrieval over that context (+ schedule, likes, plan) feeds Foundation Models / Gemini Nano; answers
  cite items. Nothing leaves the phone.

## 6. Order
1. Server: context extraction + bundle fields (API-018), console review/edit.
2. iOS: plan page, Change sheet, bottom bar, keyboard bar; hide AI without a model; WeatherKit + cache + attribution.
3. iOS wide layout with the chat column (iPhone Duo).
4. Retire the snapping panel.
5. Android (no weather) later.
