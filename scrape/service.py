"""
Event scraping service.

Crawls a URL with httpx + BeautifulSoup (BFS, max 2 deep / 10 pages), then
sends the combined text to OpenAI to extract a structured Event + Locations
payload.  No browser required — works on Heroku and any plain Python env.
"""

import json
import logging
import textwrap
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from openai import OpenAI

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON schema sent to OpenAI as response_format (strict structured output)
# ---------------------------------------------------------------------------

def _nullable(t, description):
    return {"type": [t, "null"], "description": description}


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
    },
    "required": ["existing_id", "name", "snippet", "description", "category", "website",
                 "image_url", "address", "latitude", "longitude"],
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
        "tags": {"type": "array", "items": {"type": "string"}, "description": "0-3 short tags, e.g. Music, Kids, Workshop"},
    },
    "required": ["existing_id", "name", "description", "start_time", "end_time", "location_name", "tags"],
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
        "locations": {"type": "array", "items": _LOCATION,
                      "description": "Venues, stages, booths, vendors and points of interest within the event"},
        "schedule": {"type": "array", "items": _ITEM,
                     "description": "Every scheduled activity, session or performance"},
        "removed_location_ids": {"type": "array", "items": _REMOVAL,
                                 "description": "CURRENT locations the sources say are gone or cancelled"},
        "removed_item_ids": {"type": "array", "items": _REMOVAL,
                             "description": "CURRENT schedule items the sources say are gone or cancelled"},
        "notes": {"type": "string", "description": "Short notes for the organizer about anything ambiguous; empty if none"},
    },
    "required": ["name", "description", "address", "latitude", "longitude", "timezone", "start_time",
                 "end_time", "locations", "schedule", "removed_location_ids", "removed_item_ids", "notes"],
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

REFRESH_RULES = textwrap.dedent("""
    You are REFRESHING an event that already exists. CURRENT DATA (JSON) is provided below.
    - For every location / schedule item you output that corresponds to a CURRENT one, set existing_id
      to its id, even if its name changed slightly. Use null only for genuinely new things.
    - Output each location and schedule item as the sources describe it now (complete fields, not a diff).
    - You may omit CURRENT items the sources do not mention; omission means "no change", NOT removal.
    - Only list ids in removed_location_ids / removed_item_ids when the sources clearly show the thing was
      cancelled or removed. Give the evidence as the reason.
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


def _crawl(url: str, max_depth: int = 2, max_pages: int = 15, max_seconds: int = 60) -> str:
    """
    BFS crawl starting at url.  Stays on the same origin, visits at most
    max_pages pages up to max_depth levels deep, returns combined text.
    Stops early if max_seconds wall-clock time is exceeded.
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
            text = _page_text(html)
            if text:
                parts.append(f"## Source: {current_url}\n\n{text}")

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


def _model_kwargs(model: str) -> dict:
    """Reasoning models reject temperature; older chat models reject reasoning_effort."""
    if model.startswith(("gpt-5", "o1", "o3", "o4")):
        return {"reasoning_effort": "low"}
    return {"temperature": 0}


def _extract(content: str, current: dict | None = None, attachments: list | None = None,
             instructions: str = "") -> dict:
    """
    Send source text (+ optional image/PDF attachments) to OpenAI and return the parsed dict.

    current      existing event data (for refreshes) — enables existing_id matching
    attachments  list of {"name", "mime", "data": bytes}
    Raises ValueError if the response cannot be used.
    """
    import base64

    from django.conf import settings

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
    for att in attachments or []:
        b64 = base64.b64encode(att["data"]).decode()
        mime = att.get("mime") or "application/octet-stream"
        if mime.startswith("image/"):
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        elif mime == "application/pdf":
            parts.append({"type": "file", "file": {"filename": att.get("name") or "flyer.pdf",
                                                   "file_data": f"data:{mime};base64,{b64}"}})
    if not parts:
        raise ValueError("Nothing to read — add a URL, a file or some text.")

    model = getattr(settings, "OPENAI_MODEL", "gpt-5-mini")
    logger.info("Sending %d chars + %d attachments to OpenAI (%s)", len(trimmed), len(attachments or []), model)
    client = OpenAI()
    response = client.chat.completions.create(
        model=model,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "event_extraction", "strict": True, "schema": EVENT_SCHEMA},
        },
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": parts},
        ],
        **_model_kwargs(model),
    )

    raw = response.choices[0].message.content or ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"The AI returned an unreadable response: {raw[:200]}") from exc
    if not isinstance(data, dict) or not data.get("name"):
        raise ValueError("The AI could not find an event in those sources.")
    data = _normalize(data)
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
    # Treat (0, 0) as unknown
    if not data["latitude"] and not data["longitude"]:
        data["latitude"] = data["longitude"] = None
    for loc in data["locations"]:
        for key, default in (("existing_id", None), ("snippet", ""), ("description", ""),
                             ("category", None), ("website", None), ("image_url", None),
                             ("address", ""), ("latitude", None), ("longitude", None)):
            loc.setdefault(key, default)
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
    return data


def gather_sources(urls: list[str]) -> str:
    parts = []
    for url in urls:
        try:
            parts.append(_crawl(url))
        except Exception as exc:  # one bad URL should not sink the rest
            logger.warning("Crawl failed for %s: %s", url, exc)
    return "\n\n---\n\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Google Places enrichment
# ---------------------------------------------------------------------------

_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"


def _geocode(query: str, api_key: str) -> tuple[float, float, str, None] | None:
    """
    Geocode a free-text address or place name.
    Returns (lat, lng, formatted_address, None) or None on failure.
    The trailing None keeps the shape consistent with _places_text_search.
    """
    try:
        resp = httpx.get(
            _GEOCODE_URL,
            params={"address": query, "key": api_key},
            timeout=8,
        )
        resp.raise_for_status()
        body = resp.json()
        status = body.get("status", "UNKNOWN")
        results = body.get("results", [])
        if not results:
            error_msg = body.get("error_message", "")
            logger.warning(
                "Geocode zero results for %r (status=%s%s)",
                query, status, f": {error_msg}" if error_msg else "",
            )
            return None
        loc = results[0]["geometry"]["location"]
        address = results[0].get("formatted_address", "")
        return loc["lat"], loc["lng"], address, None
    except Exception as exc:
        logger.warning("Geocode failed for %r: %s", query, exc)
        return None


_PLACES_PHOTO_URL = "https://places.googleapis.com/v1/{name}/media"


def _places_photo_uri(photo_name: str, api_key: str) -> str | None:
    """
    Resolve a Places API photo resource name to a public image URL.
    Uses skipHttpRedirect=true so we get JSON back instead of a redirect.
    """
    try:
        resp = httpx.get(
            _PLACES_PHOTO_URL.format(name=photo_name),
            params={"key": api_key, "maxHeightPx": 600, "skipHttpRedirect": "true"},
            timeout=8,
        )
        resp.raise_for_status()
        uri = resp.json().get("photoUri")
        if uri:
            logger.debug("Places photo resolved: %s", uri[:80])
        return uri
    except Exception as exc:
        logger.warning("Places photo fetch failed for %r: %s", photo_name, exc)
        return None


def _places_text_search(
    query: str,
    api_key: str,
    bias_lat: float | None = None,
    bias_lng: float | None = None,
) -> tuple[float, float, str, str | None] | None:
    """
    Use the Places API Text Search to find a place.
    Returns (lat, lng, formatted_address, photo_url_or_None) or None on failure.
    """
    body: dict = {"textQuery": query, "pageSize": 1}
    if bias_lat and bias_lng:
        body["locationBias"] = {
            "circle": {
                "center": {"latitude": bias_lat, "longitude": bias_lng},
                "radius": 50_000,  # 50 km
            }
        }
    try:
        resp = httpx.post(
            _PLACES_SEARCH_URL,
            json=body,
            headers={
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location,places.photos",
            },
            timeout=8,
        )
        resp.raise_for_status()
        places = resp.json().get("places", [])
        if not places:
            return None
        place = places[0]
        loc = place.get("location", {})
        address = place.get("formattedAddress", "")
        # Attempt to fetch first photo
        photo_url = None
        photos = place.get("photos", [])
        if photos:
            photo_url = _places_photo_uri(photos[0]["name"], api_key)
        return loc.get("latitude", 0), loc.get("longitude", 0), address, photo_url
    except Exception as exc:
        logger.warning("Places text search failed for %r: %s", query, exc)
        return None


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
    Enrich the OpenAI-extracted event dict with accurate coordinates and
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
        if loc.get("latitude") is not None and loc.get("longitude") is not None and loc.get("image_url"):
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
            if photo_url and not loc.get("image_url"):
                loc["image_url"] = photo_url
        else:
            # Unknown pins stay null; the apps fall back to the event's pin rather than 0,0
            logger.warning("  Could not geocode location %r", loc_name)

    return data


def scrape_event(url: str = "", google_api_key: str = "", *, urls: list | None = None, text: str = "",
                 attachments: list | None = None, current: dict | None = None,
                 instructions: str = "", enrich: bool = True) -> dict:
    """
    Read the sources and return a normalized extraction dict (see EVENT_SCHEMA).

    Geocoding enrichment fills coordinates only for things that have none.
    Raises ValueError when nothing usable can be extracted.
    """
    urls = list(urls or ([url] if url else []))
    logger.info("scrape_event: %d urls, %d chars text, %d attachments, refresh=%s",
                len(urls), len(text), len(attachments or []), current is not None)
    content = gather_sources(urls) if urls else ""
    if text:
        content = (content + "\n\n---\n\n## Source: organizer notes\n\n" + text).strip()
    if not content.strip() and not attachments:
        raise ValueError("No content could be retrieved from that URL.")

    data = _extract(content, current=current, attachments=attachments, instructions=instructions)

    if google_api_key and enrich:
        data = _enrich_with_places(data, google_api_key)
    return data
