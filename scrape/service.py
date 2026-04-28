"""
Event scraping service.

Crawls a URL with crawl4ai (BFS, max 2 deep / 10 pages), then sends the
combined markdown to OpenAI to extract a structured Event + Locations payload.
"""

import asyncio
import json
import os
import textwrap
from datetime import datetime, timedelta, timezone

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy

from openai import OpenAI

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


async def _crawl(url: str) -> str:
    """
    Crawl the given URL (BFS depth 2, max 10 pages) and return combined markdown.
    """
    strategy = BFSDeepCrawlStrategy(max_depth=2, max_pages=10)
    browser_cfg = BrowserConfig(headless=True, verbose=False)
    run_cfg = CrawlerRunConfig(deep_crawl_strategy=strategy)

    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        results = await crawler.arun(url, config=run_cfg)

    if isinstance(results, list):
        pages = results
    else:
        pages = [results]

    parts = []
    for r in pages:
        if r.success and r.markdown:
            md = r.markdown.strip()
            if md:
                parts.append(f"## Source: {r.url}\n\n{md}")

    return "\n\n---\n\n".join(parts)


def _extract(markdown: str) -> dict:
    """
    Send the crawled markdown to OpenAI and return parsed event dict.
    Raises ValueError if the response cannot be parsed.
    """
    client = OpenAI()  # reads OPENAI_API_KEY from environment automatically

    # Trim to ~120k chars to stay within context limits
    content = markdown[:120_000]

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
            {"role": "user", "content": f"Extract event data from the following scraped content:\n\n{content}"},
        ],
        temperature=0,
    )

    raw = response.choices[0].message.content
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"OpenAI returned non-JSON: {raw[:200]}") from exc

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
    markdown = asyncio.run(_crawl(url))
    if not markdown.strip():
        raise ValueError("No content could be retrieved from that URL.")

    data = _extract(markdown)

    # Fill in start_time default if missing or null
    if not data.get("start_time"):
        data["start_time"] = _default_start_time()

    return data
