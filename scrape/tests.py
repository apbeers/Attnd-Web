"""
Tests for AI import / refresh. OpenAI, httpx and Google Places are mocked — no network calls.
"""

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone as django_timezone

from events.content import restore
from events.models import Event, EventRevision, Location, ScheduledItem
from scrape import diff, jobs
from scrape.models import Change, ChangeSet, IngestJob
from scrape.service import _model_kwargs, _normalize, scrape_event


def _sample_data(**overrides):
    base = {
        "name": "Test County Fair",
        "description": "The best fair in the county.",
        "address": "123 Fair Ln, Testville, TX 75001",
        "latitude": 32.7767,
        "longitude": -96.7970,
        "timezone": "America/Chicago",
        "start_time": "2026-07-04T18:00:00",
        "end_time": "2026-07-04T23:00:00",
        "locations": [
            {"existing_id": None, "name": "Main Stage", "snippet": "Live music all day long.",
             "description": "The main performance area.", "category": "Stage",
             "website": "https://example.com/stage", "image_url": "https://example.com/stage.jpg",
             "address": "123 Fair Ln", "latitude": 32.7768, "longitude": -96.7971},
            {"existing_id": None, "name": "Food Court", "snippet": "All your favourite fair foods.",
             "description": "Over 30 vendors.", "category": "Food", "website": None, "image_url": None,
             "address": "", "latitude": None, "longitude": None},
        ],
        "schedule": [
            {"existing_id": None, "name": "Opening Act", "description": "Kick off the fair.",
             "start_time": "2026-07-04T18:00:00", "end_time": "2026-07-04T19:00:00",
             "location_name": "Main Stage", "tags": ["Music"]},
            {"existing_id": None, "name": "Welcome", "description": "",
             "start_time": "2026-07-04T17:30:00", "end_time": None, "location_name": None, "tags": []},
        ],
        "removed_location_ids": [],
        "removed_item_ids": [],
        "notes": "",
    }
    base.update(overrides)
    return base


def _mock_openai_response(data: dict):
    choice = MagicMock()
    choice.message.content = json.dumps(data)
    resp = MagicMock()
    resp.choices = [choice]
    return resp


class RunJobsInline:
    """Mixin: run queued jobs synchronously when the transaction commits."""

    def run_queued(self, fn, *args, **kwargs):
        with patch("scrape.jobs._start_background", side_effect=lambda jid: jobs.run_job(jid)):
            with self.captureOnCommitCallbacks(execute=True):
                return fn(*args, **kwargs)


# ---------------------------------------------------------------------------
# Public trial endpoints
# ---------------------------------------------------------------------------

@override_settings(SIGNUPS_OPEN=True)
class TrialImportTests(RunJobsInline, TestCase):
    def setUp(self):
        self.client = Client()
        cache.clear()

    def _post(self, payload):
        return self.client.post("/scrape/", data=json.dumps(payload), content_type="application/json", secure=True)

    def _post_and_run(self, payload, data=None, side_effect=None):
        kwargs = {"side_effect": side_effect} if side_effect else {"return_value": data or _sample_data()}
        with patch("scrape.jobs.scrape_event", **kwargs):
            return self.run_queued(self._post, payload)

    def test_missing_url_returns_400(self):
        self.assertEqual(self._post({"url": ""}).status_code, 400)

    def test_invalid_scheme_returns_400(self):
        self.assertEqual(self._post({"url": "ftp://x"}).status_code, 400)

    def test_invalid_json_body_returns_400(self):
        resp = self.client.post("/scrape/", data="nope", content_type="application/json", secure=True)
        self.assertEqual(resp.status_code, 400)

    def test_happy_path_creates_unlisted_published_trial_event(self):
        resp = self._post_and_run({"url": "https://example.com", "timezone": "America/Chicago"})
        self.assertEqual(resp.status_code, 202)
        job = IngestJob.objects.get(pk=resp.json()["job_id"])
        self.assertEqual(job.status, IngestJob.DONE)
        event = job.event
        self.assertEqual(event.user.username, "trialuser")
        self.assertEqual(event.status, Event.STATUS_PUBLISHED)
        self.assertFalse(event.public)
        self.assertFalse(event.paid)
        self.assertEqual(event.timezone, "America/Chicago")
        self.assertEqual(event.location_set.count(), 2)
        self.assertEqual(event.scheduleditem_set.count(), 2)
        self.assertEqual(EventRevision.objects.filter(event=event).count(), 1)

    def test_naive_times_are_event_local(self):
        resp = self._post_and_run({"url": "https://example.com", "timezone": "UTC"})
        event = IngestJob.objects.get(pk=resp.json()["job_id"]).event
        # 18:00 in Chicago (CDT, UTC-5) == 23:00 UTC
        self.assertEqual(event.start_time.astimezone(django_timezone.UTC).hour, 23)

    def test_unknown_coordinates_stay_null(self):
        resp = self._post_and_run({"url": "https://example.com"})
        event = IngestJob.objects.get(pk=resp.json()["job_id"]).event
        food = event.location_set.get(name="Food Court")
        self.assertIsNone(food.latitude)

    def test_item_without_location_is_kept(self):
        resp = self._post_and_run({"url": "https://example.com"})
        event = IngestJob.objects.get(pk=resp.json()["job_id"]).event
        self.assertIsNone(event.scheduleditem_set.get(name="Welcome").location)
        self.assertEqual(event.scheduleditem_set.get(name="Opening Act").location.name, "Main Stage")

    def test_status_endpoint_returns_result(self):
        resp = self._post_and_run({"url": "https://example.com"})
        status = self.client.get(resp.json()["status_url"], secure=True).json()
        self.assertEqual(status["status"], "done")
        self.assertEqual(status["event_name"], "Test County Fair")
        self.assertEqual(status["location_count"], 2)
        self.assertTrue(status["qr_code"].startswith("data:image/png;base64,"))
        self.assertIn(status["event_code"], status["join_url"])
        self.assertIn("claim=", status["claim_url"])

    def test_missing_start_time_falls_back_to_first_item_and_notes_it(self):
        resp = self._post_and_run({"url": "https://example.com"}, data=_sample_data(start_time=None))
        job = IngestJob.objects.get(pk=resp.json()["job_id"])
        self.assertIn("start time", job.result["notes"])
        self.assertEqual(job.event.start_time, job.event.scheduleditem_set.order_by("start_time").first().start_time)

    def test_value_error_marks_job_failed(self):
        resp = self._post_and_run({"url": "https://example.com"}, side_effect=ValueError("No content found."))
        status = self.client.get(resp.json()["status_url"], secure=True).json()
        self.assertEqual(status["status"], "failed")
        self.assertIn("No content", status["error"])

    def test_unexpected_error_marks_job_failed(self):
        resp = self._post_and_run({"url": "https://example.com"}, side_effect=RuntimeError("boom"))
        job = IngestJob.objects.get(pk=resp.json()["job_id"])
        self.assertEqual(job.status, IngestJob.FAILED)

    def test_rate_limit(self):
        with patch("scrape.jobs._start_background"):
            for _ in range(5):
                self.assertEqual(self._post({"url": "https://example.com"}).status_code, 202)
            self.assertEqual(self._post({"url": "https://example.com"}).status_code, 429)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class ServiceTests(TestCase):
    @patch("scrape.service._crawl", return_value="Some event page text")
    @patch("scrape.service.OpenAI")
    def test_scrape_event_returns_normalized_dict(self, mock_openai_cls, _crawl):
        mock_openai_cls.return_value.chat.completions.create.return_value = _mock_openai_response(_sample_data())
        data = scrape_event("https://example.com")
        self.assertEqual(data["name"], "Test County Fair")
        self.assertEqual(len(data["schedule"]), 2)

    @patch("scrape.service._crawl", return_value="text")
    @patch("scrape.service.OpenAI")
    def test_refresh_sends_current_state(self, mock_openai_cls, _crawl):
        create = mock_openai_cls.return_value.chat.completions.create
        create.return_value = _mock_openai_response(_sample_data())
        scrape_event(urls=["https://example.com"], current={"event": {"name": "X"}, "locations": [], "schedule": []})
        messages = create.call_args.kwargs["messages"]
        self.assertIn("REFRESHING", messages[0]["content"])
        self.assertIn("CURRENT DATA", messages[1]["content"][0]["text"])

    @patch("scrape.service.OpenAI")
    def test_attachments_are_sent_as_image_and_file_parts(self, mock_openai_cls):
        create = mock_openai_cls.return_value.chat.completions.create
        create.return_value = _mock_openai_response(_sample_data())
        scrape_event(attachments=[{"name": "a.png", "mime": "image/png", "data": b"x"},
                                  {"name": "b.pdf", "mime": "application/pdf", "data": b"y"}])
        types = [p["type"] for p in create.call_args.kwargs["messages"][1]["content"]]
        self.assertEqual(types, ["image_url", "file"])

    @patch("scrape.service._crawl", return_value="")
    def test_empty_crawl_raises_value_error(self, _crawl):
        with self.assertRaises(ValueError):
            scrape_event("https://example.com")

    @patch("scrape.service._crawl", return_value="text")
    @patch("scrape.service.OpenAI")
    def test_bad_openai_json_raises_value_error(self, mock_openai_cls, _crawl):
        choice = MagicMock()
        choice.message.content = "not json"
        mock_openai_cls.return_value.chat.completions.create.return_value = MagicMock(choices=[choice])
        with self.assertRaises(ValueError):
            scrape_event("https://example.com")

    def test_normalize_accepts_legacy_nested_items_and_zero_coords(self):
        data = _normalize({"name": "X", "latitude": 0, "longitude": 0, "locations": [
            {"name": "Stage", "latitude": 0, "longitude": 0,
             "scheduled_items": [{"name": "Act", "description": "", "start_time": ""}]}]})
        self.assertIsNone(data["latitude"])
        self.assertIsNone(data["locations"][0]["latitude"])
        self.assertEqual(data["schedule"][0]["location_name"], "Stage")
        self.assertIsNone(data["schedule"][0]["start_time"])

    def test_model_kwargs(self):
        self.assertEqual(_model_kwargs("gpt-5-mini"), {"reasoning_effort": "low"})
        self.assertEqual(_model_kwargs("gpt-4o-mini"), {"temperature": 0})


# ---------------------------------------------------------------------------
# Diff / apply / rollback
# ---------------------------------------------------------------------------

class DiffTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("org", "org@example.com", "pw")
        self.event, _ = diff.create_event_from_extraction(
            _sample_data(), self.user, status=Event.STATUS_PUBLISHED, public=True)
        self.stage = self.event.location_set.get(name="Main Stage")
        self.food = self.event.location_set.get(name="Food Court")
        self.act = self.event.scheduleditem_set.get(name="Opening Act")

    def _refreshed(self, **overrides):
        data = _sample_data(**overrides)
        for loc in data["locations"]:
            loc["existing_id"] = {"Main Stage": self.stage.id, "Food Court": self.food.id}.get(loc["name"])
        return data

    def test_identical_extraction_produces_no_changes(self):
        self.assertEqual(diff.compute_changes(self.event, self._refreshed()), [])

    def test_time_change_is_proposed_and_not_minor(self):
        data = self._refreshed()
        data["schedule"][0]["existing_id"] = self.act.id
        data["schedule"][0]["start_time"] = "2026-07-04T18:30:00"
        changes = diff.compute_changes(self.event, data)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["op"], "update")
        self.assertIn("start_time", changes[0]["data"])
        self.assertFalse(changes[0]["minor"])

    def test_description_change_is_minor(self):
        data = self._refreshed()
        data["locations"][0]["description"] = "Now with more seating."
        (change,) = diff.compute_changes(self.event, data)
        self.assertTrue(change["minor"])

    def test_empty_values_never_blank_existing_data(self):
        data = self._refreshed()
        data["locations"][0]["website"] = None
        data["locations"][0]["description"] = ""
        self.assertEqual(diff.compute_changes(self.event, data), [])

    def test_locked_field_is_a_conflict(self):
        self.stage.description = "Hand-written"
        self.stage.locked_fields = ["description"]
        self.stage.save()
        (change,) = diff.compute_changes(self.event, self._refreshed())
        self.assertEqual(change["conflict_fields"], ["description"])

    def test_new_location_and_item_linked_by_ref(self):
        data = self._refreshed()
        data["locations"].append({"existing_id": None, "name": "Kids Zone", "snippet": "", "description": "",
                                  "category": "Kids", "website": None, "image_url": None, "address": "",
                                  "latitude": None, "longitude": None})
        data["schedule"].append({"existing_id": None, "name": "Face painting", "description": "",
                                 "start_time": "2026-07-04T19:00:00", "end_time": None,
                                 "location_name": "Kids Zone", "tags": []})
        changes = diff.compute_changes(self.event, data)
        cs = diff.save_changeset(self.event, None, changes)
        self.assertEqual(cs.summary, "1 new location, 1 new schedule item")
        applied = diff.apply_changeset(cs, diff.default_decisions(cs), user=self.user)
        self.assertEqual(applied, 2)
        item = self.event.scheduleditem_set.get(name="Face painting")
        self.assertEqual(item.location.name, "Kids Zone")
        cs.refresh_from_db()
        self.assertEqual(cs.status, ChangeSet.APPLIED)

    def test_rejecting_new_location_leaves_item_unlinked(self):
        data = self._refreshed()
        data["locations"].append({"existing_id": None, "name": "Kids Zone", "snippet": "", "description": "",
                                  "category": None, "website": None, "image_url": None, "address": "",
                                  "latitude": None, "longitude": None})
        data["schedule"].append({"existing_id": None, "name": "Face painting", "description": "",
                                 "start_time": "2026-07-04T19:00:00", "end_time": None,
                                 "location_name": "Kids Zone", "tags": []})
        cs = diff.save_changeset(self.event, None, diff.compute_changes(self.event, data))
        item_change = cs.changes.get(entity="item")
        diff.apply_changeset(cs, {item_change.id: {"accept": True, "fields": None}})
        self.assertIsNone(self.event.scheduleditem_set.get(name="Face painting").location)
        self.assertFalse(self.event.location_set.filter(name="Kids Zone").exists())

    def test_removal_requires_evidence_and_applies(self):
        data = self._refreshed(removed_location_ids=[{"id": self.food.id, "reason": "Listed as cancelled"}])
        data["locations"] = [data["locations"][0]]
        changes = diff.compute_changes(self.event, data)
        (change,) = changes
        self.assertEqual(change["op"], "delete")
        cs = diff.save_changeset(self.event, None, changes)
        diff.apply_changeset(cs, diff.default_decisions(cs))
        self.assertFalse(Location.objects.filter(pk=self.food.pk).exists())

    def test_field_level_accept(self):
        data = self._refreshed()
        data["locations"][0]["description"] = "New description"
        data["locations"][0]["snippet"] = "New snippet"
        cs = diff.save_changeset(self.event, None, diff.compute_changes(self.event, data))
        ch = cs.changes.get()
        diff.apply_changeset(cs, {ch.id: {"accept": True, "fields": ["snippet"]}})
        self.stage.refresh_from_db()
        self.assertEqual(self.stage.snippet, "New snippet")
        self.assertEqual(self.stage.description, "The main performance area.")

    def test_apply_bumps_version_once_and_saves_revision(self):
        before = self.event.content_version
        data = self._refreshed()
        data["locations"][0]["description"] = "A"
        data["locations"][1]["description"] = "B"
        cs = diff.save_changeset(self.event, None, diff.compute_changes(self.event, data))
        diff.apply_changeset(cs, diff.default_decisions(cs))
        self.event.refresh_from_db()
        self.assertEqual(self.event.content_version, before + 1)
        self.assertEqual(self.event.revisions.count(), 2)

    def test_restore_revision(self):
        revision = self.event.revisions.get()
        self.stage.delete()
        self.event.scheduleditem_set.all().delete()
        restore(revision)
        self.assertEqual(self.event.location_set.count(), 2)
        self.assertEqual(self.event.scheduleditem_set.count(), 2)
        self.assertEqual(self.event.scheduleditem_set.get(name="Opening Act").location.name, "Main Stage")


class RefreshJobTests(RunJobsInline, TestCase):
    def setUp(self):
        self.user = User.objects.create_user("org", "org@example.com", "pw")
        self.event, _ = diff.create_event_from_extraction(
            _sample_data(), self.user, status=Event.STATUS_PUBLISHED, public=True,
            source_urls=["https://example.com"])
        self.stage = self.event.location_set.get(name="Main Stage")

    def _run_refresh(self, data):
        job = IngestJob.objects.create(kind=IngestJob.KIND_REFRESH, event=self.event)
        with patch("scrape.jobs.scrape_event", return_value=data):
            self.run_queued(jobs.enqueue, job)
        job.refresh_from_db()
        return job

    def test_refresh_creates_changeset(self):
        data = _sample_data()
        data["locations"][0]["existing_id"] = self.stage.id
        data["locations"][0]["description"] = "Updated"
        Event.objects.filter(pk=self.event.pk).update(update_mode=Event.UPDATE_REVIEW)
        self.event.refresh_from_db()
        job = self._run_refresh(data)
        self.assertEqual(job.status, IngestJob.DONE)
        cs = ChangeSet.objects.get(pk=job.result["changeset_id"])
        self.assertEqual(cs.status, ChangeSet.PENDING)
        self.assertEqual(job.result["pending"], 1)

    def test_new_events_watch_and_apply_automatically_by_default(self):
        event = Event.objects.create(name="New", event_code="DEFLT1", start_time=django_timezone.now(), user=self.event.user)
        self.assertEqual((event.refresh_schedule, event.update_mode), (Event.REFRESH_WATCH, Event.UPDATE_AUTO))

    def test_auto_accept_minor(self):
        self.event.update_mode = Event.UPDATE_MINOR
        self.event.save()
        data = _sample_data()
        data["locations"][0]["existing_id"] = self.stage.id
        data["locations"][0]["description"] = "Updated"
        data["schedule"][0]["start_time"] = "2026-07-04T20:00:00"  # not minor → stays pending
        job = self._run_refresh(data)
        self.stage.refresh_from_db()
        self.assertEqual(self.stage.description, "Updated")
        self.assertEqual(job.result["auto_applied"], 1)
        self.assertEqual(job.result["pending"], 1)

    def test_refresh_without_sources_fails_cleanly(self):
        self.event.source_urls = []
        self.event.save()
        job = IngestJob.objects.create(kind=IngestJob.KIND_REFRESH, event=self.event)
        jobs.run_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, IngestJob.FAILED)

    def test_stale_jobs_are_requeued_then_failed(self):
        job = IngestJob.objects.create(kind=IngestJob.KIND_REFRESH, event=self.event, status=IngestJob.RUNNING,
                                       started_at=django_timezone.now() - timedelta(hours=1))
        with patch("scrape.jobs._start_background"):
            jobs.recover_stale()
            job.refresh_from_db()
            self.assertEqual(job.status, IngestJob.QUEUED)
            IngestJob.objects.filter(pk=job.pk).update(status=IngestJob.RUNNING,
                                                       started_at=django_timezone.now() - timedelta(hours=1))
            jobs.recover_stale()
            job.refresh_from_db()
            self.assertEqual(job.status, IngestJob.FAILED)


# ---------------------------------------------------------------------------
# expire_trial_events management command
# ---------------------------------------------------------------------------

class ExpireTrialEventsCommandTests(TestCase):

    def _make_trial_user(self):
        user, _ = User.objects.get_or_create(
            username="trialuser",
            defaults={"email": "trial@attnd.app"},
        )
        user.set_unusable_password()
        user.save()
        return user

    def _make_event(self, user, name, days_old):
        event = Event.objects.create(
            name=name,
            event_code=f"CODE{name[:8].upper().replace(' ', '')}",
            description="desc",
            latitude=0, longitude=0,
            start_time=django_timezone.now(),
            address="123 Main St",
            user=user,
        )
        # Back-date created_at to simulate age
        Event.objects.filter(pk=event.pk).update(
            created_at=django_timezone.now() - timedelta(days=days_old)
        )
        return event

    def test_deletes_expired_trial_events(self):
        trial_user = self._make_trial_user()
        old_event = self._make_event(trial_user, "Old Fair", days_old=8)
        call_command("expire_trial_events", verbosity=0)
        self.assertFalse(Event.objects.filter(pk=old_event.pk).exists())

    def test_keeps_recent_trial_events(self):
        trial_user = self._make_trial_user()
        new_event = self._make_event(trial_user, "New Fair", days_old=3)
        call_command("expire_trial_events", verbosity=0)
        self.assertTrue(Event.objects.filter(pk=new_event.pk).exists())

    def test_does_not_delete_non_trial_events(self):
        other_user = User.objects.create_user("organizer2", "org2@test.com", "pass")
        event = self._make_event(other_user, "Organizer Old Fair", days_old=10)
        call_command("expire_trial_events", verbosity=0)
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())

    def test_no_trialuser_exits_gracefully(self):
        # Should not raise even if trialuser doesn't exist
        call_command("expire_trial_events", verbosity=0)

    def test_outputs_success_message(self):
        import io
        trial_user = self._make_trial_user()
        self._make_event(trial_user, "Old Output Fair", days_old=8)
        out = io.StringIO()
        call_command("expire_trial_events", stdout=out)
        self.assertIn("Deleted", out.getvalue())


class WatchTests(TestCase):
    """The website watcher: cheap fingerprints, AI only on change, rate limits."""

    def setUp(self):
        from django.contrib.auth.models import User
        self.user = User.objects.create_user("w", "w@example.com", "pw")
        now = django_timezone.now()
        self.event = Event.objects.create(
            name="Fest", event_code="WATCH1", start_time=now + timedelta(hours=2), user=self.user,
            source_urls=["https://fest.example/"], refresh_schedule=Event.REFRESH_WATCH)
        self.pages = {"https://fest.example/": '<html><body><h1>Fest</h1><p>Main stage 7pm</p>'
                                              '<script>var t=12345</script></body></html>'}
        self.requests = []

    def http(self):
        import httpx

        def handler(request):
            self.requests.append(request)
            body = self.pages.get(str(request.url))
            if body is None:
                return httpx.Response(404)
            etag = '"' + str(abs(hash(body))) + '"'
            if request.headers.get("if-none-match") == etag:
                return httpx.Response(304, headers={"etag": etag})
            return httpx.Response(200, text=body, headers={"content-type": "text/html", "etag": etag})
        return httpx.Client(transport=httpx.MockTransport(handler))

    def check(self, minutes=0):
        from scrape import watch
        return watch.check_event(self.event, django_timezone.now() + timedelta(minutes=minutes), client=self.http())

    def test_first_check_baselines_without_ai(self):
        with patch("scrape.jobs._start_background"):
            result = self.check()
        self.assertEqual(result["changed"], [])
        self.assertFalse(IngestJob.objects.exists())

    def test_unchanged_pages_use_304_and_volatile_bits_are_ignored(self):
        with patch("scrape.jobs._start_background"):
            self.check()
            self.check(10)
            self.assertEqual(self.requests[-1].headers.get("if-none-match") is not None, True)
            # Only a script changed: what readers see is the same, so no AI.
            self.pages["https://fest.example/"] = self.pages["https://fest.example/"].replace("12345", "99999")
            self.assertEqual(self.check(20)["changed"], [])
        self.assertFalse(IngestJob.objects.exists())

    def test_a_real_change_queues_one_ai_update_for_that_page_only(self):
        with patch("scrape.jobs._start_background"):
            self.check()
            self.pages["https://fest.example/"] = self.pages["https://fest.example/"].replace("7pm", "8pm")
            result = self.check(10)
            self.assertTrue(result["queued"])
            job = IngestJob.objects.get()
            self.assertEqual((job.trigger, job.urls), (IngestJob.TRIGGER_WATCH, ["https://fest.example/"]))
            # Still unread and a job is queued: no second job.
            self.assertFalse(self.check(20)["queued"])
        self.assertEqual(IngestJob.objects.count(), 1)

    def test_rate_limits(self):
        from scrape import watch
        now = django_timezone.now()
        IngestJob.objects.create(kind=IngestJob.KIND_REFRESH, event=self.event, trigger=IngestJob.TRIGGER_WATCH,
                                 status=IngestJob.DONE, created_at=now - timedelta(minutes=5))
        self.assertEqual(watch._may_run_ai(self.event, now)[0], False)  # minimum gap
        self.assertEqual(watch._may_run_ai(self.event, now + timedelta(minutes=20))[0], True)
        with self.settings(WATCH_MAX_AI_RUNS_PER_DAY=1):
            self.assertEqual(watch._may_run_ai(self.event, now + timedelta(minutes=20))[0], False)

    def test_watch_job_reads_only_changed_pages_and_marks_them_read(self):
        from scrape import watch
        with patch("scrape.jobs._start_background"):
            self.check()
            self.pages["https://fest.example/"] = self.pages["https://fest.example/"].replace("7pm", "8pm")
            self.check(10)
        job = IngestJob.objects.get()
        data = {"name": "Fest", "locations": [], "schedule": [], "notes": "", "source_pages": []}
        with patch("scrape.jobs.scrape_event", return_value=data) as scrape:
            jobs.run_job(job.id)
        self.assertEqual(scrape.call_args.kwargs["crawl"], False)
        page = self.event.source_pages.get()
        self.assertFalse(page.has_pending_change)
        with patch("scrape.jobs._start_background"):
            self.assertEqual(watch.check_event(self.event, django_timezone.now() + timedelta(minutes=40),
                                               client=self.http())["changed"], [])

    def test_check_interval_follows_the_event(self):
        from scrape import watch
        start = self.event.start_time
        self.assertEqual(watch.check_interval(self.event, start - timedelta(days=30)), timedelta(hours=6))
        self.assertEqual(watch.check_interval(self.event, start - timedelta(days=3)), timedelta(hours=1))
        self.assertEqual(watch.check_interval(self.event, start), timedelta(minutes=10))
        self.assertIsNone(watch.check_interval(self.event, start + timedelta(days=3)))
