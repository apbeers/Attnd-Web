"""
Event scraping service.

Crawls a URL with httpx + BeautifulSoup (BFS, max 2 deep / 10 pages), then
sends the combined text to OpenAI to extract a structured Event + Locations
payload.  No browser required — works on Heroku and any plain Python env.
"""

import json
import logging
import textwrap
from collections import deque
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from openai import OpenAI

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON schema sent to OpenAI as response_format
# ---------------------------------------------------------------------------
EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Full event name (max 256 chars)"},
        "description": {"type": "string", "description": "Event description (max 2048 chars)"},
        "address": {"type": "string", "description": "Street address of the event (max 512 chars)"},
        "latitude": {"type": "number", "description": "Decimal latitude; 0 if unknown"},
        "longitude": {"type": "number", "description": "Decimal longitude; 0 if unknown"},
        "start_time": {
            "type": "string",
            "description": "ISO 8601 datetime (e.g. 2025-07-04T18:00:00); null if not found",
        },
        "locations": {
            "type": "array",
            "description": "Notable sub-locations / points of interest within the event",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Location name (max 256 chars)"},
                    "snippet": {"type": "string", "description": "One-sentence description (max 200 chars)"},
                    "description": {"type": "string", "description": "Longer description (max 1024 chars)"},
                    "website": {"type": ["string", "null"], "description": "URL if available"},
                    "address": {"type": "string", "description": "Street address (max 512 chars)"},
                    "latitude": {"type": "number", "description": "Decimal latitude; 0 if unknown"},
                    "longitude": {"type": "number", "description": "Decimal longitude; 0 if unknown"},
                },
                "required": ["name", "snippet", "description", "address", "latitude", "longitude"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["name", "description", "address", "latitude", "longitude", "start_time", "locations"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = textwrap.dedent("""
    You are an event data extractor. Given scraped web content from an event website,
    extract structured information about the event and its notable locations / venues /
    stages / booths / points of interest.

    Rules:
    - Truncate strings to fit the stated max lengths.
    - If a field cannot be found, use an empty string or 0 for coordinates.
    - For start_time, output ISO 8601 with timezone offset if available, otherwise local naive.
    - Return only valid JSON matching the schema — no commentary.
""").strip()

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AttndBot/1.0; +https://attnd.app)",
}


def _page_text(html: str) -> str:
    """Extract visible text from HTML, stripping scripts/styles."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "meta", "head"]):
        tag.decompose()
    return " ".join(soup.get_text(separator=" ").split())


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


def _crawl(url: str, max_depth: int = 2, max_pages: int = 10) -> str:
    """
    BFS crawl starting at url.  Stays on the same origin, visits at most
    max_pages pages up to max_depth levels deep, returns combined text.
    """
    logger.info("Crawl starting: %s (max_depth=%d, max_pages=%d)", url, max_depth, max_pages)
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(url, 0)])
    parts: list[str] = []

    with httpx.Client(headers=_HEADERS, timeout=15, follow_redirects=True) as client:
        while queue and len(visited) < max_pages:
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
                for link in new_links:
                    if link not in visited:
                        queue.append((link, depth + 1))

    total_chars = sum(len(p) for p in parts)
    logger.info("Crawl complete: %d pages visited, %d chars collected", len(visited), total_chars)
    return "\n\n---\n\n".join(parts)


def _extract(content: str) -> dict:
    """
    Send the crawled text to OpenAI and return parsed event dict.
    Raises ValueError if the response cannot be parsed.
    """
    trimmed = content[:120_000]
    logger.info("Sending %d chars to OpenAI (gpt-4o-mini)", len(trimmed))

    client = OpenAI()
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "event_extraction",
                "strict": True,
                "schema": EVENT_SCHEMA,
            },
        },
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Extract event data from the following scraped content:\n\n{trimmed}"},
        ],
        temperature=0,
    )

    raw = response.choices[0].message.content
    logger.info("OpenAI response received (%d chars)", len(raw))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"OpenAI returned non-JSON: {raw[:200]}") from exc

    logger.info("Extracted event: %r, %d locations", data.get("name"), len(data.get("locations", [])))
    return data


def _default_start_time() -> str:
    """Return 30 days from now as ISO 8601 UTC string."""
    dt = datetime.now(timezone.utc) + timedelta(days=30)
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def scrape_event(url: str) -> dict:
    """
    Public entry point (sync). Crawls url and returns a dict with keys:
        name, description, address, latitude, longitude, start_time, locations[]
    Each location has: name, snippet, description, website, address, latitude, longitude
    Raises ValueError on extraction failure.
    """
    logger.info("scrape_event called for: %s", url)
    content = _crawl(url)
    if not content.strip():
        logger.warning("No content retrieved from %s", url)
        raise ValueError("No content could be retrieved from that URL.")

    data = _extract(content)

    if not data.get("start_time"):
        data["start_time"] = _default_start_time()

    return data
