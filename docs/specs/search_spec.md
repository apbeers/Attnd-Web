# Attnd in-app search + assistant spec (shared by iOS and Android)

Goal: one search, reachable from anywhere in the app, that runs **locally on the device** over the joined
event's bundle (already on disk; works offline). No server call, no API change. Results show as much info
as possible inline and tapping one jumps straight to that place in the app.

## What is indexed (from the current bundle only)
| Kind | Fields (weight high → low) | Jump target |
|------|---------------------------|-------------|
| Schedule item | name; tags, location name; description | Schedule item detail (Schedule tab, or the hidden-tab overlay if the tab is hidden) |
| Location | name; category, snippet, address; description | Location detail (Locations tab or overlay); "Show on map" from the detail as today |
| Notification (organizer update) | title; body | Updates/Notifications tab, scrolled to and briefly highlighting that notification |
| Event | name, code; address; description/other text the bundle has | Options/event header (or whatever screen shows event info) |

## Matching (pure, unit-tested function in the domain layer: `search(bundle, query, now) -> [Result]`)
- Normalise both sides: lower-case, strip diacritics (é→e), treat punctuation/`&`/`-` as spaces, collapse spaces.
- Query split into tokens; **every** token must match somewhere in the item (AND across fields).
- A token matches a word when the word **starts with** it (prefix), or contains it (substring, ranks lower),
  or, for tokens of 5+ characters, is within edit distance 1 (typo, ranks lowest).
- **Time words** (event time zone, never the device's): `now` / `happening now` (items in progress),
  `next` / `up next` / `soon` (starting within the next 2 h), `today`, `tonight` (starts ≥ 17:00 today),
  `tomorrow`, weekday names (`saturday`, `sat`), and clock times `3pm`, `3 pm`, `3:30`, `15:00`, `noon`
  (items in progress at that time on the event's days, or starting within 30 min after it). A query may
  mix words and time words: "music tonight", "food 6pm". Time words filter schedule items; other kinds
  are excluded when the query is only time words.
- Ranking score: field weight × match quality (exact word > prefix > substring > typo), + boost for
  schedule items happening now (largest) or next, − penalty for ended items (still shown, last). Stable
  tie-break: kind order (schedule, location, notification, event), then name.
- Empty query → no results (show suggestions instead: "Happening now", "Up next", categories as chips).
- Never send the query text anywhere. Usage: reuse the existing `search` metric exactly as the existing
  call sites do (same name/detail rules, counted on submit or after the query settles 1.5 s). No new metric
  names (the server validates names; check API_V2.md in /home/user/Attnd-Web before using any detail).

## Result row
Kind icon; title with the matched part emphasised; one line of secondary info: schedule → day + time
range in event tz + location ("Sat 7:30–8:30 PM · Main Stage", plus a "Now"/"Next"/"Ended" label);
location → category · snippet; notification → time sent + start of body; event → dates · address.
Tapping jumps; the search closes (or stays in the back stack so Back returns to the results).

## Unit tests (both platforms, same cases)
Accent folding ("cafe" finds "Café"), prefix ("mari" → Roaming Mariachi), AND across fields
("taco food"), typo ("mariachy"), time words ("now", "tonight", "7pm", "saturday" in America/Chicago
with a fixed `now`), mixed ("music tonight"), ranking (now > next > later > ended), empty query, items
without location ("General"), locations without coordinates, notifications body match.
