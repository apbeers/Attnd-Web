"""
Tests for the scrape app.

httpx and openai are mocked — no real network calls.
"""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import TestCase, Client

from events.models import Event, Location
from django.contrib.auth.models import User


def _sample_data(**overrides):
    """Return a fresh deep copy of sample extracted data, with optional overrides."""
    base = {
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
                "image_url": "https://example.com/stage.jpg",
                "address": "123 Fair Ln, Testville, TX 75001",
                "latitude": 32.7768,
                "longitude": -96.7971,
                "scheduled_items": [
                    {
                        "name": "Opening Act",
                        "description": "Kick off the fair with live music.",
                        "start_time": "2026-07-04T18:00:00+00:00",
                    }
                ],
            },
            {
                "name": "Food Court",
                "snippet": "All your favourite fair foods.",
                "description": "Over 30 food vendors serving local favourites.",
                "website": None,
                "image_url": None,
                "address": "124 Fair Ln, Testville, TX 75001",
                "latitude": 32.7769,
                "longitude": -96.7972,
                "scheduled_items": [],
            },
        ],
    }
    base.update(overrides)
    return base


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
        # Rate limit uses Django's cache — clear it so tests don't bleed into each other.
        cache.clear()

    def _post(self, payload):
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
            secure=True,
        )

    def _patch_service(self, return_value=None, side_effect=None):
        """Patch scrape_event at the view import level."""
        kwargs = {}
        if side_effect:
            kwargs["side_effect"] = side_effect
        else:
            kwargs["return_value"] = return_value if return_value is not None else _sample_data()
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
        resp = self.client.post(self.url, data="not json", content_type="application/json", secure=True)
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
        self.assertTrue(event.public)  # trial events are public so users can test the app
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
        with self._patch_service(return_value=_sample_data(name="Fair One")):
            r1 = self._post({"url": "https://example.com"})

        with self._patch_service(return_value=_sample_data(name="Fair Two")):
            r2 = self._post({"url": "https://other.com"})

        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)
        self.assertNotEqual(r1.json()["event_code"], r2.json()["event_code"])

    def test_null_start_time_uses_default(self):
        with self._patch_service(return_value=_sample_data(start_time=None)):
            resp = self._post({"url": "https://example.com"})

        self.assertEqual(resp.status_code, 201)
        event = Event.objects.get(event_code=resp.json()["event_code"])
        # Should be roughly 30 days in the future
        delta = event.start_time - datetime.now(timezone.utc)
        self.assertGreater(delta.days, 25)

    def test_rate_limit(self):
        """After 5 requests from the same IP, the 6th is rejected."""
        # Use distinct names to avoid the Event.name unique constraint.
        for i in range(5):
            with self._patch_service(return_value=_sample_data(name=f"Rate Limit Fair {i}")):
                resp = self._post({"url": "https://example.com"})
            self.assertNotEqual(resp.status_code, 429, f"Request {i+1} was unexpectedly rate-limited")

        # 6th request — rate limiter fires before scrape_event is even called
        resp = self._post({"url": "https://example.com"})
        self.assertEqual(resp.status_code, 429)


class ScrapeServiceTests(TestCase):
    """Unit tests for service.py internals (crawl + extract)."""

    @patch("scrape.service._crawl", return_value="## Source: https://example.com\n\n# Test Event\n\nSome content.")
    @patch("scrape.service.OpenAI")
    def test_scrape_event_returns_dict(self, mock_openai_cls, mock_crawl):
        data = _sample_data()
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response(data)
        mock_openai_cls.return_value = mock_client

        from scrape.service import scrape_event
        result = scrape_event("https://example.com")

        self.assertEqual(result["name"], "Test County Fair")
        self.assertEqual(len(result["locations"]), 2)

    @patch("scrape.service._crawl", return_value="")
    def test_empty_crawl_raises_value_error(self, mock_crawl):
        from scrape.service import scrape_event
        with self.assertRaises(ValueError):
            scrape_event("https://empty.com")

    @patch("scrape.service._crawl", return_value="## Source: https://example.com\n\nSome content.")
    @patch("scrape.service.OpenAI")
    def test_bad_openai_json_raises_value_error(self, mock_openai_cls, mock_crawl):
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
