"""
Event scraping service.

Crawls a URL with httpx + BeautifulSoup (BFS, max 2 deep / 10 pages), then
sends the combined text to Claude (Haiku, structured output) to extract a structured Event + Locations
payload.  No browser required — works on Heroku and any plain Python env.
"""

import json
import logging
import re
import textwrap
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON schema for Claude's structured output (attnd/claude.structured)
# ---------------------------------------------------------------------------

def _nullable(t, description):
    return {"type": [t, "null"], "description": description}


def _text(description):
    return {"type": "string", "description": description + "; empty string if the sources don't say"}


def _rows(item, description):
    return {"type": "array", "items": item, "description": description + "; empty list if none"}


def _object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


# API-018: details an attendee's on-device assistant answers questions from, offline (events.context).
_LINK = _object({"title": {"type": "string", "description": "Short link title"},
                 "url": {"type": "string", "description": "Absolute https URL that appears in the sources"}})

_EVENT_CONTEXT = _object({
    "about": _text("Long attendee-facing description of the event beyond `description` (max 4000 chars)"),
    "faq": _rows(_object({"q": {"type": "string"}, "a": {"type": "string"}}),
                 "Questions attendees ask, answered from the sources (max 40): parking, shuttles, bag policy, re-entry, "
                 "rules, weather, tickets at the door, pets, smoking, strollers, cash/cards"),
    "amenities": _rows(_object({
        "kind": {"type": "string", "enum": ["restrooms", "water", "atm", "first_aid", "charging", "lockers", "info",
                                            "accessible_entrance", "food", "merch", "parking", "shuttle", "other"]},
        "name": {"type": "string", "description": "e.g. Restrooms by the north gate"},
        "location_name": _nullable("string", "Exact name of the location in locations[] where it is, if any"),
        "note": {"type": "string", "description": "Short note (hours, cost); may be empty"},
    }), "Amenities on site (max 60)"),
    "accessibility": _text("Accessibility: step-free routes, seating, viewing areas, interpreters, sensory (max 1500)"),
    "policies": _text("Policies: bags, re-entry, age limits, prohibited items (max 1500)"),
    "getting_there": _text("Getting there: parking, transit, rideshare, shuttles, bikes (max 1500)"),
    "weather_plan": _text("Rain / heat plan (max 600)"),
    "links": _rows(_LINK, "Useful attendee links from the sources: tickets, map, FAQ (max 10)"),
})

_LOCATION_CONTEXT = _object({
    "about": _text("Vendor / venue story beyond the description (max 2000)"),
    "menu": _rows(_object({"name": {"type": "string"},
                           "price": {"type": "string", "description": "As written, e.g. $8; may be empty"},
                           "note": {"type": "string", "description": "e.g. vegetarian, gluten-free; may be empty"}}),
                  "Menu or products with prices (max 40)"),
    "hours": _text("Opening hours (max 300)"),
    "accessibility": _text("Accessibility of this place (max 300)"),
    "age_limit": _text("Age limit such as 21+ (max 300)"),
    "links": _rows(_LINK, "Links for this place (max 5)"),
})

_ITEM_CONTEXT = _object({
    "about": _text("More about this session / performance beyond the description (max 2000)"),
    "performers": _rows(_object({
        "name": {"type": "string"},
        "bio": {"type": "string", "description": "Short bio from the sources (max 600); may be empty"},
        "genres": {"type": "array", "items": {"type": "string"}, "description": "Up to 5 lower-case genres/styles"},
    }), "Performers, speakers or hosts (max 10)"),
    "age_limit": _text("Age limit (max 300)"),
    "price": _text("Price if separate from entry, as written (max 300)"),
    "accessibility": _text("Accessibility notes (max 300)"),
    "links": _rows(_LINK, "Links for this item (max 5)"),
})

_LOCATION = {
    "type": "object",
    "properties": {
        "existing_id": _nullable("integer", "id of the matching CURRENT location when refreshing; null if new"),
        "name": {"type": "string", "description": "Location name (max 256 chars)"},
        "snippet": {"type": "string", "description": "One-sentence summary (max 200 chars)"},
        "description": {"type": "string", "description": "Longer description (max 1024 chars)"},
        "category": _nullable("string", "Short category such as Stage, Food, Vendor, Venue, Exhibit, Restroom, Parking, Info"),
        "website": _nullable("string", "URL if available"),
        "image_url": _nullable("string", "Absolute URL of an image of this location found in the sources"),
        "address": {"type": "string", "description": "Street address or empty string (max 512 chars)"},
        "latitude": _nullable("number", "Decimal latitude only if stated in the sources"),
        "longitude": _nullable("number", "Decimal longitude only if stated in the sources"),
        "tags": {"type": "array", "items": {"type": "string"},
                 "description": "2-6 short lower-case tags from tag_vocabulary: what it offers / its genre / "
                                "audience, e.g. food, bbq, drinks, live music, kid-friendly, free, accessible"},
        "context": _LOCATION_CONTEXT,
    },
    "required": ["existing_id", "name", "snippet", "description", "category", "website",
                 "image_url", "address", "latitude", "longitude", "tags", "context"],
    "additionalProperties": False,
}

_ITEM = {
    "type": "object",
    "properties": {
        "existing_id": _nullable("integer", "id of the matching CURRENT schedule item when refreshing; null if new"),
        "name": {"type": "string", "description": "Activity / session / performance name (max 256 chars)"},
        "description": {"type": "string", "description": "Description (max 1024 chars), may be empty"},
        "start_time": _nullable("string", "Local wall-clock time, ISO 8601 without offset, e.g. 2026-07-04T18:00:00"),
        "end_time": _nullable("string", "Local wall-clock end time, ISO 8601 without offset"),
        "location_name": _nullable("string", "Exact name of the location (from locations[]) where it happens"),
        "tags": {"type": "array", "items": {"type": "string"},
                 "description": "2-6 short lower-case tags from tag_vocabulary: genre / category / audience, "
                                "e.g. live music, country, workshop, kid-friendly, free, food"},
        "context": _ITEM_CONTEXT,
    },
    "required": ["existing_id", "name", "description", "start_time", "end_time", "location_name", "tags",
                 "context"],
    "additionalProperties": False,
}

_REMOVAL = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "reason": {"type": "string", "description": "Evidence from the sources (e.g. 'listed as cancelled')"},
    },
    "required": ["id", "reason"],
    "additionalProperties": False,
}

EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Full event name (max 256 chars)"},
        "description": {"type": "string", "description": "Event description for attendees (max 2048 chars)"},
        "address": {"type": "string", "description": "Street address of the event (max 512 chars)"},
        "latitude": _nullable("number", "Decimal latitude only if stated in the sources"),
        "longitude": _nullable("number", "Decimal longitude only if stated in the sources"),
        "timezone": _nullable("string", "IANA time zone of the event location, e.g. America/Chicago"),
        "start_time": _nullable("string", "Local wall-clock start, ISO 8601 without offset"),
        "end_time": _nullable("string", "Local wall-clock end, ISO 8601 without offset"),
        "context": _EVENT_CONTEXT,
        "locations": {"type": "array", "items": _LOCATION,
                      "description": "Venues, stages, booths, vendors and points of interest within the event"},
        "schedule": {"type": "array", "items": _ITEM,
                     "description": "Every scheduled activity, session or performance"},
        "removed_location_ids": {"type": "array", "items": _REMOVAL,
                                 "description": "CURRENT locations the sources say are gone or cancelled"},
        "removed_item_ids": {"type": "array", "items": _REMOVAL,
                             "description": "CURRENT schedule items the sources say are gone or cancelled"},
        "notes": {"type": "string", "description": "Short notes for the organizer about anything ambiguous; empty if none"},
        "tag_vocabulary": {"type": "array", "items": {"type": "string"},
                           "description": "The event's small controlled tag vocabulary (about 8-30 lower-case tags). "
                                          "Every tag on a location or schedule item comes from this list"},
    },
    "required": ["name", "description", "address", "latitude", "longitude", "timezone", "start_time",
                 "end_time", "context", "locations", "schedule", "removed_location_ids", "removed_item_ids", "notes",
                 "tag_vocabulary"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = textwrap.dedent("""
    You turn event websites, flyers and notes into structured data for Attnd, an app attendees
    use at live events (festivals, fairs, conferences, markets, art walks, sports days).

    Extract:
    - The event itself: name, a helpful attendee-facing description, address, time zone, start and end.
    - Locations: every place an attendee might want to find — stages, venues, rooms, booths, vendors,
      food, exhibits, amenities. Give each a category. Write a one-sentence snippet and a description.
    - Schedule: every timed activity, linked to a location by its exact name when known.

    Rules:
    - Use only facts from the sources. Never invent times, addresses, coordinates or URLs.
    - Times are local wall-clock times at the event, ISO 8601 without a UTC offset. Use null when unknown.
      If only a date is known for an all-day event, use 00:00 for start_time.
    - Coordinates: null unless explicitly given in the sources.
    - Image URLs must be absolute and appear in the sources.
    - Keep within the stated max lengths. Write in the language of the sources.
    - Put anything ambiguous or conflicting in notes, briefly.
""").strip()

TAG_RULES = textwrap.dedent("""
    Tags (attendees filter and search by them):
    - First choose a small controlled vocabulary for the whole event (tag_vocabulary): lower-case, 1-3 words,
      singular unless the plural is the usual name ("live music", "country", "bbq", "tacos", "kid-friendly",
      "free", "food", "drinks", "workshop", "21+", "accessible").
    - Give every location and schedule item 2-6 tags from that vocabulary: genre, category, audience, cost.
      The same concept always gets the same tag. Tags may be inferred from names and descriptions (a
      "Brisket 101" demo is "bbq"), but never claim facts the sources contradict (only "free" when it is).
    - Don't repeat the event's name, a location's own name or its category word for word as a tag unless it
      helps filtering (a "Food" category location can still be tagged "food").
""").strip()

CONTEXT_RULES = textwrap.dedent("""
    Context (an assistant on attendees' phones answers questions from it offline):
    - Fill `context` on the event, each location and each schedule item with what the sources say beyond the
      description: the event's FAQ (parking, shuttles, bags, re-entry, rules, weather, tickets at the door, pets,
      smoking), amenities (restrooms, water, ATMs, first aid, charging, lockers, info, accessible entrances),
      accessibility, policies, getting there, rain/heat plan; menus and prices of food and drink places, opening
      hours, age limits; performers' and speakers' short bios and genres.
    - Only facts from the sources, written for attendees, concise. Don't repeat the description. Never include
      private details (organizer contacts not published for attendees, staff notes, internal prices).
    - Empty strings and empty lists when the sources say nothing. Links: absolute https URLs from the sources.
""").strip()

SYSTEM_PROMPT += "\n\n" + TAG_RULES + "\n\n" + CONTEXT_RULES

REFRESH_RULES = textwrap.dedent("""
    You are REFRESHING an event that already exists. CURRENT DATA (JSON) is provided below.
    - For every location / schedule item you output that corresponds to a CURRENT one, set existing_id
      to its id, even if its name changed slightly. Use null only for genuinely new things.
    - Output each location and schedule item as the sources describe it now (complete fields, not a diff).
    - You may omit CURRENT items the sources do not mention; omission means "no change", NOT removal.
    - Only list ids in removed_location_ids / removed_item_ids when the sources clearly show the thing was
      cancelled or removed. Give the evidence as the reason.
    - Reuse CURRENT DATA's tag_vocabulary and each thing's current tags unless the sources say otherwise;
      add a new tag only for a concept the vocabulary lacks.
    - Context: start from CURRENT DATA's context and change only what the sources change. Keep its wording when
      the sources still say the same. Leave a context field empty when these sources don't cover it (empty means
      "no change", never "remove").
""").strip()

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AttndBot/1.0; +https://attnd.app)",
}


def _page_text(html: str) -> str:
    """
    Extract visible text from HTML.
    Keeps application/ld+json script blocks (structured event data).
    Strips all other scripts, styles, and boilerplate.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Preserve JSON-LD blocks — many event/festival sites embed their full
    # schedule, lineup, and location data here.
    jsonld_blocks = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            jsonld_blocks.append(tag.get_text())
        except Exception:
            pass

    # Strip everything we don't want
    for tag in soup(["script", "style", "noscript", "meta", "head"]):
        tag.decompose()

    visible = " ".join(soup.get_text(separator=" ").split())

    if jsonld_blocks:
        return visible + "\n\n[JSON-LD structured data]\n" + "\n".join(jsonld_blocks)
    return visible


def _same_origin_links(html: str, base_url: str) -> list[str]:
    """Return same-origin href links found in the page."""
    soup = BeautifulSoup(html, "html.parser")
    base = urlparse(base_url)
    links = []
    for tag in soup.find_all("a", href=True):
        href = urljoin(base_url, tag["href"])
        parsed = urlparse(href)
        if parsed.scheme in ("http", "https") and parsed.netloc == base.netloc:
            links.append(parsed._replace(fragment="").geturl())
    return links


_PRIORITY_WORDS = ("schedule", "lineup", "line-up", "agenda", "program", "programme", "timetable",
                   "map", "venue", "location", "stage", "vendor", "exhibitor", "food", "speaker",
                   "artist", "performer", "event", "info", "faq", "parking")
_SKIP_WORDS = ("login", "signin", "cart", "checkout", "privacy", "terms", "cookie", "account",
               "wp-admin", "feed", ".jpg", ".png", ".pdf", ".zip", "mailto:", "tel:")


def _link_priority(link: str) -> int:
    lower = link.lower()
    if any(w in lower for w in _SKIP_WORDS):
        return 2
    return 0 if any(w in lower for w in _PRIORITY_WORDS) else 1


def _crawl(url: str, max_depth: int = 2, max_pages: int = 15, max_seconds: int = 60, on_page=None,
           colors=None) -> str:
    """
    BFS crawl starting at url.  Stays on the same origin, visits at most
    max_pages pages up to max_depth levels deep, returns combined text.
    Stops early if max_seconds wall-clock time is exceeded. `colors` (a Counter, optional) collects the colours
    of the pages' inline styles and theme-color (API-023: the event's theme).
    """
    logger.info("Crawl starting: %s (max_depth=%d, max_pages=%d, max_seconds=%d)", url, max_depth, max_pages, max_seconds)
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(url, 0)])
    parts: list[str] = []
    deadline = time.monotonic() + max_seconds

    with httpx.Client(headers=_HEADERS, timeout=12, follow_redirects=True) as client:
        while queue and len(visited) < max_pages:
            if time.monotonic() > deadline:
                logger.warning("Crawl deadline reached after %ds — stopping early (%d pages visited)", max_seconds, len(visited))
                break
            current_url, depth = queue.popleft()
            if current_url in visited:
                continue
            visited.add(current_url)

            logger.debug("Fetching [depth=%d]: %s", depth, current_url)
            try:
                resp = client.get(current_url)
                resp.raise_for_status()
                logger.debug("  -> %d (%d bytes)", resp.status_code, len(resp.content))
            except Exception as exc:
                logger.warning("  -> fetch failed: %s", exc)
                continue

            content_type = resp.headers.get("content-type", "")
            if "html" not in content_type:
                logger.debug("  -> skipped (content-type: %s)", content_type)
                continue

            html = resp.text
            if colors is not None:
                from attnd.colors import html_colors
                html_colors(html, colors)
            text = _page_text(html)
            if text:
                parts.append(f"## Source: {current_url}\n\n{text}")
                if on_page:
                    on_page(current_url)

            if depth < max_depth:
                new_links = _same_origin_links(html, current_url)
                logger.debug("  -> found %d same-origin links", len(new_links))
                # Visit the pages most likely to hold schedules and venues first
                new_links.sort(key=_link_priority)
                for link in dict.fromkeys(new_links):
                    if link not in visited:
                        queue.append((link, depth + 1))

    total_chars = sum(len(p) for p in parts)
    logger.info("Crawl complete: %d pages visited, %d chars collected", len(visited), total_chars)
    return "\n\n---\n\n".join(parts)


MAX_CONTENT_CHARS = 200_000


def _extract(content: str, current: dict | None = None, attachments: list | None = None,
             instructions: str = "") -> dict:
    """
    Send source text (+ optional image/PDF attachments) to Claude and return the parsed dict.

    current      existing event data (for refreshes) — enables existing_id matching
    attachments  list of {"name", "mime", "data": bytes}
    Raises ValueError if the response cannot be used.
    """
    if len(content) > MAX_CONTENT_CHARS:
        logger.warning("Content truncated: %d → %d chars", len(content), MAX_CONTENT_CHARS)
    trimmed = content[:MAX_CONTENT_CHARS]

    system = SYSTEM_PROMPT
    if current is not None:
        system += "\n\n" + REFRESH_RULES

    parts = []
    if current is not None:
        parts.append({"type": "text", "text": "CURRENT DATA:\n" + json.dumps(current, ensure_ascii=False)})
    if instructions:
        parts.append({"type": "text", "text": "Organizer instructions:\n" + instructions[:4000]})
    if trimmed.strip():
        parts.append({"type": "text", "text": "SOURCES:\n\n" + trimmed})
    from attnd import claude
    for att in attachments or []:
        mime = att.get("mime") or "application/octet-stream"
        if mime.startswith("image/") and mime in ("image/png", "image/jpeg", "image/webp", "image/gif"):
            parts.append(claude.image_block(att["data"], mime))
        elif mime == "application/pdf":
            parts.append(claude.document_block(att["data"], mime, att.get("name") or "flyer.pdf"))
    if not parts:
        raise ValueError("Nothing to read — add a URL, a file or some text.")

    model = claude.fast_model()
    logger.info("Sending %d chars + %d attachments to Claude (%s)", len(trimmed), len(attachments or []), model)
    try:
        data, cost = claude.structured(system=system, content=parts, schema=EVENT_SCHEMA, model=model,
                                       effort="medium", max_tokens=48000, timeout=600.0)
    except claude.ClaudeError as exc:
        raise ValueError(f"The AI couldn't read those sources ({exc}). Please try again.") from exc
    logger.info("Extraction cost $%s", cost)
    if not isinstance(data, dict) or not data.get("name"):
        raise ValueError("The AI could not find an event in those sources.")
    data = _normalize(data)
    from scrape.tags import canonicalize_extraction
    canonicalize_extraction(data, seed=(current or {}).get("tag_vocabulary") or [])
    logger.info("Extracted event %r: %d locations, %d schedule items",
                data.get("name"), len(data["locations"]), len(data["schedule"]))
    return data


def _normalize(data: dict) -> dict:
    """Fill defaults so downstream code can rely on every key. Accepts the legacy nested shape too."""
    data.setdefault("description", "")
    data.setdefault("address", "")
    for key in ("latitude", "longitude", "timezone", "start_time", "end_time"):
        data.setdefault(key, None)
    for key in ("locations", "schedule", "removed_location_ids", "removed_item_ids"):
        if not isinstance(data.get(key), list):
            data[key] = []
    data.setdefault("notes", "")
    if not isinstance(data.get("context"), dict):
        data["context"] = {}
    if not isinstance(data.get("tag_vocabulary"), list):
        data["tag_vocabulary"] = []
    # Treat (0, 0) as unknown
    if not data["latitude"] and not data["longitude"]:
        data["latitude"] = data["longitude"] = None
    for loc in data["locations"]:
        for key, default in (("existing_id", None), ("snippet", ""), ("description", ""),
                             ("category", None), ("website", None), ("image_url", None),
                             ("address", ""), ("latitude", None), ("longitude", None), ("tags", [])):
            loc.setdefault(key, default)
        if not isinstance(loc["tags"], list):
            loc["tags"] = []
        if not isinstance(loc.get("context"), dict):
            loc["context"] = {}
        if not loc["latitude"] and not loc["longitude"]:
            loc["latitude"] = loc["longitude"] = None
        # Legacy shape: items nested under locations
        for item in loc.pop("scheduled_items", []) or []:
            item.setdefault("location_name", loc.get("name"))
            data["schedule"].append(item)
    for item in data["schedule"]:
        for key, default in (("existing_id", None), ("description", ""), ("start_time", None),
                             ("end_time", None), ("location_name", None), ("tags", [])):
            item.setdefault(key, default)
        if item["start_time"] == "":
            item["start_time"] = None
        if not isinstance(item["tags"], list):
            item["tags"] = []
        if not isinstance(item.get("context"), dict):
            item["context"] = {}
    return data


def gather_sources(urls: list[str], crawl: bool = True, on_page=None, colors=None) -> str:
    """Read the URLs. With crawl=False only the pages themselves (the website watcher re-reads just
    the pages that changed). `on_page(url)` is called for every page read (the task tray's step)."""
    parts = []
    for url in urls:
        try:
            parts.append(_crawl(url, on_page=on_page, colors=colors) if crawl
                         else _crawl(url, max_depth=0, max_pages=1, on_page=on_page, colors=colors))
        except Exception as exc:  # one bad URL should not sink the rest
            logger.warning("Crawl failed for %s: %s", url, exc)
    return "\n\n---\n\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Google Places enrichment
# ---------------------------------------------------------------------------



def _geocode(query: str, api_key: str = "") -> tuple[float, float, str, None] | None:
    """
    Geocode a free-text address or place name (attnd/google.py: cached, counted, quota-safe).
    Returns (lat, lng, formatted_address, None) or None. The trailing None keeps the shape of _places_text_search.
    """
    from attnd import google
    found = google.geocode(query)
    if not found:
        logger.info("Geocode found nothing for %r", query[:80])
        return None
    return found["lat"], found["lng"], found.get("address", ""), None


def _places_text_search(
    query: str,
    api_key: str = "",
    bias_lat: float | None = None,
    bias_lng: float | None = None,
) -> tuple[float, float, str, str | None] | None:
    """
    Places Text Search for one place (attnd/google.py: cached, counted, quota-safe), biased within 50 km.
    Returns (lat, lng, formatted_address, None) or None. (No Google photos: their terms forbid storing them; place
    images come from events/place_images.py.)
    """
    from attnd import google
    near = (bias_lat, bias_lng) if bias_lat and bias_lng else None
    found = google.text_search(query, near=near, radius=50_000, limit=1)
    if not found:
        return None
    return found[0]["lat"], found[0]["lng"], found[0].get("address", ""), None


def _km(lat1, lng1, lat2, lng2):
    from math import asin, cos, radians, sin, sqrt
    lat1, lng1, lat2, lng2 = map(radians, (float(lat1), float(lng1), float(lat2), float(lng2)))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371 * asin(sqrt(h))


def _far(lat, lng, ref_lat, ref_lng, limit_km=50):
    """A 'match' 50 km from the event is almost certainly a different place with the same name."""
    return _km(lat, lng, ref_lat, ref_lng) > limit_km


def _enrich_with_places(data: dict, api_key: str) -> dict:
    """
    Enrich the AI-extracted event dict with accurate coordinates and
    addresses from Google Places / Geocoding API.
    """
    event_name = data.get("name", "")
    event_address = data.get("address", "")

    # ---- Event-level geocoding (only when the sources gave no coordinates) ----
    event_lat = data.get("latitude")
    event_lng = data.get("longitude")
    if event_lat is None or event_lng is None:
        geocode_query = f"{event_name} {event_address}".strip() or event_name
        result = _geocode(geocode_query, api_key) if geocode_query else None
        if result:
            event_lat, event_lng, resolved_address, _ = result
            data["latitude"], data["longitude"] = event_lat, event_lng
            if resolved_address and not event_address:
                data["address"] = resolved_address
            logger.info("Event geocoded: %.5f, %.5f", event_lat, event_lng)
        else:
            logger.warning("Could not geocode event %r", geocode_query)

    # ---- Location-level enrichment ----
    bias_lat = float(event_lat) if event_lat is not None else None
    bias_lng = float(event_lng) if event_lng is not None else None

    for loc in data.get("locations", []):
        if loc.get("existing_id") and not loc.get("_needs_geocode"):
            continue  # matched to an existing location; keep its stored pin
        if loc.get("latitude") is not None and loc.get("longitude") is not None:
            continue
        loc_name = loc.get("name", "")
        loc_address = loc.get("address", "")
        result = None
        if loc_address and len(loc_address) > 5 and loc_address != event_address:
            result = _geocode(loc_address, api_key)
        if result is None:
            places_query = f"{loc_name} {event_name}".strip() if event_name else loc_name
            result = _places_text_search(places_query, api_key, bias_lat, bias_lng)
        if result:
            lat, lng, address, photo_url = result
            if bias_lat is not None and _far(lat, lng, bias_lat, bias_lng):
                logger.warning("  Ignoring geocode for %r — %.1f km from the event", loc_name,
                               _km(lat, lng, bias_lat, bias_lng))
                continue
            if loc.get("latitude") is None or loc.get("longitude") is None:
                loc["latitude"], loc["longitude"] = lat, lng
            if address and not loc_address:
                loc["address"] = address
        else:
            # Unknown pins stay null; the apps fall back to the event's pin rather than 0,0
            logger.warning("  Could not geocode location %r", loc_name)

    return data


def scrape_event(url: str = "", google_api_key: str = "", *, urls: list | None = None, text: str = "",
                 attachments: list | None = None, current: dict | None = None,
                 instructions: str = "", enrich: bool = True, crawl: bool = True, progress=None) -> dict:
    """
    Read the sources and return a normalized extraction dict (see EVENT_SCHEMA).

    Geocoding enrichment fills coordinates only for things that have none.
    Raises ValueError when nothing usable can be extracted. `progress(text, done=…)` (optional) starts a friendly
    step for the task tray (scrape.jobs.Steps); `progress.update(text)` changes the current one.
    """
    from urllib.parse import urlparse
    urls = list(urls or ([url] if url else []))
    say = progress or (lambda *a, **k: None)
    update = getattr(progress, 'update', None) or (lambda *a, **k: None)
    read = []

    def on_page(page_url):
        read.append(page_url)
        update(f'Reading web pages: {len(read)} so far ({urlparse(page_url).netloc or page_url})…',
               done=f'Read {len(read)} web page{"s" if len(read) != 1 else ""}')
    if urls:
        hosts = list(dict.fromkeys(urlparse(u).netloc or u for u in urls))
        say(f'Reading {", ".join(hosts[:2])}{" and more" if len(hosts) > 2 else ""}…',
            done='Couldn’t read any web pages')
    logger.info("scrape_event: %d urls, %d chars text, %d attachments, refresh=%s",
                len(urls), len(text), len(attachments or []), current is not None)
    from collections import Counter

    from attnd.colors import most_used_hex
    page_colors = Counter()
    content = gather_sources(urls, crawl=crawl, on_page=on_page, colors=page_colors) if urls else ""
    # Every web page that was read, so the website watcher can fingerprint them (scrape.watch).
    pages = list(dict.fromkeys(re.findall(r"^## Source: (https?://\S+)", content, flags=re.M)))
    if text:
        content = (content + "\n\n---\n\n## Source: organizer notes\n\n" + text).strip()
    if not content.strip() and not attachments:
        raise ValueError("No content could be retrieved from that URL.")

    if text or attachments:
        what = ' and '.join(filter(None, ['your notes' if text else '', 'the file you attached' if attachments else '']))
        say(f'Reading {what}…', done=f'Read {what}')
    say('Finding the locations and the schedule…' if current is None else 'Looking for what changed…',
        done='Understood the event' if current is None else 'Read the latest information')
    data = _extract(content, current=current, attachments=attachments, instructions=instructions)

    if google_api_key and enrich:
        say('Checking addresses on the map…', done='Checked addresses on the map')
        data = _enrich_with_places(data, google_api_key)
    data["source_pages"] = pages
    data["page_colors"] = most_used_hex(page_colors)  # API-023: the event's theme starts from these
    return data


# ---------------------------------------------------------------------------
# Tags for existing content (API-010 backfill; no crawling)
# ---------------------------------------------------------------------------

_TAGGED = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["location", "item"]},
        "id": {"type": "integer"},
        "tags": {"type": "array", "items": {"type": "string"}, "description": "2-6 tags from tag_vocabulary"},
    },
    "required": ["kind", "id", "tags"],
    "additionalProperties": False,
}

TAGS_SCHEMA = {
    "type": "object",
    "properties": {
        "tag_vocabulary": EVENT_SCHEMA["properties"]["tag_vocabulary"],
        "tagged": {"type": "array", "items": _TAGGED, "description": "One entry per location and schedule item given"},
    },
    "required": ["tag_vocabulary", "tagged"],
    "additionalProperties": False,
}

TAGS_PROMPT = textwrap.dedent("""
    You tag the locations and schedule items of an event in Attnd, an app attendees use at live events.
    You get the event (name, description), its existing tag vocabulary, and the things to tag (kind + id,
    name, category, description, location). Return tags for every one of them, by kind and id.
""").strip() + "\n\n" + TAG_RULES + "\n- Reuse the existing vocabulary wherever it fits."


def suggest_tags(event_info: dict, entries: list[dict], vocabulary: list[str]) -> dict:
    """Ask the AI for tags. ``entries``: [{"kind": "location"|"item", "id", "name", ...}].

    Returns {"tag_vocabulary": [...], "tags": {("location"|"item", id): [tags]}} for the ids that were asked
    about (anything else the model returns is ignored). Raises ValueError on an unusable response."""
    from attnd import claude
    payload = {"event": event_info, "tag_vocabulary": vocabulary, "things_to_tag": entries}
    try:
        data, _cost = claude.structured(system=TAGS_PROMPT, content=json.dumps(payload, ensure_ascii=False),
                                        schema=TAGS_SCHEMA, effort="low", max_tokens=8000)
    except claude.ClaudeError as exc:
        raise ValueError(f"The AI returned no tags ({exc}).") from exc
    if not isinstance(data, dict):
        raise ValueError("The AI returned no tags.")
    wanted = {(e["kind"], e["id"]) for e in entries}
    tags = {}
    for row in data.get("tagged") or []:
        if not isinstance(row, dict):
            continue
        key = (row.get("kind"), row.get("id"))
        if key in wanted and isinstance(row.get("tags"), list):
            tags[key] = row["tags"]
    vocab = data.get("tag_vocabulary") if isinstance(data.get("tag_vocabulary"), list) else []
    return {"tag_vocabulary": vocab, "tags": tags}
