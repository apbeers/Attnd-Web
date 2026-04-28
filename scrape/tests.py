"""
Tests for the scrape app.

crawl4ai and openai are mocked — no real network calls.
"""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import TestCase, Client

from events.models import Event, Location
from django.contrib.auth.models import User


SAMPLE_EXTRACTED = {
    "name": "Test County Fair",
    "description": "The best fair in the county.",
    "address": "123 Fair Ln, Testville, TX 75001",
    "latitude": 32.7767,
    "longitude": -96.7970,
    "start_time": "2026-07-04T18:00:00+00:00",
    "locations": [
        {
            "name": "Main Stage",
            "snippet": "Live music all day long.",
            "description": "The main performance area with seating for 500.",
            "website": "https://example.com/stage",
            "address": "123 Fair Ln, Testville, TX 75001",
            "latitude": 32.7768,
            "longitude": -96.7971,
        },
        {
            "name": "Food Court",
            "snippet": "All your favourite fair foods.",
            "description": "Over 30 food vendors serving local favourites.",
            "website": None,
            "address": "124 Fair Ln, Testville, TX 75001",
            "latitude": 32.7769,
            "longitude": -96.7972,
        },
    ],
}


def _make_crawl_result(url="https://example.com", markdown="# Test Event\n\nSome content."):
    r = MagicMock()
    r.success = True
    r.url = url
    r.markdown = markdown
    return r


def _mock_openai_response(data: dict):
    """Return a mock openai ChatCompletion response that yields data as JSON."""
    choice = MagicMock()
    choice.message.content = json.dumps(data)
    resp = MagicMock()
    resp.choices = [choice]
    return resp


class ScrapeViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = "/scrape/"

    def _post(self, payload):
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    # ------------------------------------------------------------------ helpers

    def _patch_service(self, return_value=None, side_effect=None):
        """Patch scrape_event at the view import level."""
        kwargs = {}
        if side_effect:
            kwargs["side_effect"] = side_effect
        else:
            kwargs["return_value"] = return_value or SAMPLE_EXTRACTED
        return patch("scrape.views.scrape_event", **kwargs)

    # ------------------------------------------------------------------ tests

    def test_missing_url_returns_400(self):
        resp = self._post({})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    def test_invalid_scheme_returns_400(self):
        resp = self._post({"url": "ftp://example.com"})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_json_body_returns_400(self):
        resp = self.client.post(self.url, data="not json", content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    def test_happy_path_creates_event_and_locations(self):
        with self._patch_service():
            resp = self._post({"url": "https://example.com"})

        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertIn("event_code", data)
        self.assertEqual(data["location_count"], 2)

        event = Event.objects.get(event_code=data["event_code"])
        self.assertEqual(event.name, "Test County Fair")
        self.assertEqual(event.user.username, "trialuser")
        self.assertFalse(event.paid)
        self.assertFalse(event.public)
        self.assertEqual(Location.objects.filter(event=event).count(), 2)

    def test_trialuser_created_with_unusable_password(self):
        with self._patch_service():
            self._post({"url": "https://example.com"})

        user = User.objects.get(username="trialuser")
        self.assertFalse(user.has_usable_password())

    def test_scrape_value_error_returns_422(self):
        with self._patch_service(side_effect=ValueError("No content found.")):
            resp = self._post({"url": "https://example.com"})

        self.assertEqual(resp.status_code, 422)
        self.assertIn("No content found", resp.json()["error"])

    def test_scrape_unexpected_error_returns_500(self):
        with self._patch_service(side_effect=RuntimeError("boom")):
            resp = self._post({"url": "https://example.com"})

        self.assertEqual(resp.status_code, 500)

    def test_event_code_is_unique(self):
        """Two requests produce two different event codes."""
        with self._patch_service():
            r1 = self._post({"url": "https://example.com"})

        # Change name so Event.name unique constraint doesn't collide
        alt = {**SAMPLE_EXTRACTED, "name": "Another Fair"}
        with self._patch_service(return_value=alt):
            r2 = self._post({"url": "https://other.com"})

        self.assertNotEqual(r1.json()["event_code"], r2.json()["event_code"])

    def test_null_start_time_uses_default(self):
        data = {**SAMPLE_EXTRACTED, "start_time": None}
        with self._patch_service(return_value=data):
            resp = self._post({"url": "https://example.com"})

        self.assertEqual(resp.status_code, 201)
        event = Event.objects.get(event_code=resp.json()["event_code"])
        # Should be roughly 30 days in the future
        delta = event.start_time - datetime.now(timezone.utc)
        self.assertGreater(delta.days, 25)

    def test_rate_limit(self):
        """After 5 requests from the same IP, the 6th is rejected."""
        with self._patch_service():
            for _ in range(5):
                resp = self._post({"url": "https://example.com"})
                # Give each a unique name to avoid unique constraint issues
                SAMPLE_EXTRACTED["name"] = f"Fair {_}"

            # 6th request (still patched but rate limiter fires first)
            resp = self._post({"url": "https://example.com"})

        self.assertEqual(resp.status_code, 429)


class ScrapeServiceTests(TestCase):
    """Unit tests for service.py internals (crawl + extract)."""

    @patch("scrape.service.AsyncWebCrawler")
    @patch("scrape.service.OpenAI")
    def test_scrape_event_returns_dict(self, mock_openai_cls, mock_crawler_cls):
        # Setup crawler mock
        mock_result = _make_crawl_result()
        mock_crawler = AsyncMock()
        mock_crawler.arun = AsyncMock(return_value=[mock_result])
        mock_crawler.__aenter__ = AsyncMock(return_value=mock_crawler)
        mock_crawler.__aexit__ = AsyncMock(return_value=False)
        mock_crawler_cls.return_value = mock_crawler

        # Setup OpenAI mock
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response(SAMPLE_EXTRACTED)
        mock_openai_cls.return_value = mock_client

        from scrape.service import scrape_event
        result = scrape_event("https://example.com")

        self.assertEqual(result["name"], "Test County Fair")
        self.assertEqual(len(result["locations"]), 2)

    @patch("scrape.service.AsyncWebCrawler")
    def test_empty_crawl_raises_value_error(self, mock_crawler_cls):
        mock_result = _make_crawl_result(markdown="")
        mock_result.success = True
        mock_crawler = AsyncMock()
        mock_crawler.arun = AsyncMock(return_value=[mock_result])
        mock_crawler.__aenter__ = AsyncMock(return_value=mock_crawler)
        mock_crawler.__aexit__ = AsyncMock(return_value=False)
        mock_crawler_cls.return_value = mock_crawler

        from scrape.service import scrape_event
        with self.assertRaises(ValueError):
            scrape_event("https://empty.com")

    @patch("scrape.service.AsyncWebCrawler")
    @patch("scrape.service.OpenAI")
    def test_bad_openai_json_raises_value_error(self, mock_openai_cls, mock_crawler_cls):
        mock_result = _make_crawl_result()
        mock_crawler = AsyncMock()
        mock_crawler.arun = AsyncMock(return_value=[mock_result])
        mock_crawler.__aenter__ = AsyncMock(return_value=mock_crawler)
        mock_crawler.__aexit__ = AsyncMock(return_value=False)
        mock_crawler_cls.return_value = mock_crawler

        mock_client = MagicMock()
        bad_choice = MagicMock()
        bad_choice.message.content = "not valid json {{{"
        bad_resp = MagicMock()
        bad_resp.choices = [bad_choice]
        mock_client.chat.completions.create.return_value = bad_resp
        mock_openai_cls.return_value = mock_client

        from scrape.service import scrape_event
        with self.assertRaises(ValueError):
            scrape_event("https://example.com")
