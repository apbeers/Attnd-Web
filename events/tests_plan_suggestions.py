"""API-021: "Plan your day" suggestions written for each event (events/plan_suggestions.py)."""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from events import plan_suggestions as P
from events.models import Event, Location, ScheduledItem

CHI = ZoneInfo('America/Chicago')


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=CHI)


class FakeMessages:
    def __init__(self, reply, stop='end_turn'):
        self.reply, self.stop, self.requests = reply, stop, []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        usage = SimpleNamespace(input_tokens=600, output_tokens=90, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        return SimpleNamespace(stop_reason=self.stop, usage=usage,
                               content=[SimpleNamespace(type='text', text=json.dumps({'suggestions': self.reply}))])


def fake_client(reply, stop='end_turn'):
    return SimpleNamespace(messages=FakeMessages(reply, stop))


class PlanSuggestionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('o', 'o@example.com', 'pw')

    def art_walk(self):
        """A Thursday-only art walk (Nov 19, 2026 is a Thursday), evening, galleries."""
        event = Event.objects.create(name='Third Thursday Art Walk', event_code='ARTWALK', user=self.user,
                                     timezone='America/Chicago', start_time=at(19, 17), end_time=at(19, 21),
                                     status=Event.STATUS_PUBLISHED)
        for n, name in enumerate(['Blue Door Gallery', 'Kiln Studio', 'Mural Alley']):
            loc = Location.objects.create(event=event, name=name, category='Gallery', tags=['art', 'free'],
                                          sort_order=n)
            ScheduledItem.objects.create(event=event, name=f'Opening at {name}', start_time=at(19, 17 + n),
                                         end_time=at(19, 18 + n), location=loc, tags=['art', 'painting'])
        return event

    def festival(self):
        """Saturday + Sunday, with family-tagged items."""
        event = Event.objects.create(name='Prairie Fest', event_code='PRAIRIE', user=self.user,
                                     timezone='America/Chicago', start_time=at(14, 10), end_time=at(15, 22),
                                     status=Event.STATUS_PUBLISHED)
        stage = Location.objects.create(event=event, name='Main Stage', category='Stage', tags=['live music'])
        food = Location.objects.create(event=event, name='Taco Row', category='Food', tags=['tacos'])
        ScheduledItem.objects.create(event=event, name='Prairie Lights', start_time=at(14, 19), location=stage,
                                     tags=['folk', 'live music'])
        ScheduledItem.objects.create(event=event, name='Kids Parade', start_time=at(15, 13), location=food,
                                     tags=['kid-friendly', 'family'])
        ScheduledItem.objects.create(event=event, name='BBQ Cook-off', start_time=at(15, 14), location=food,
                                     tags=['bbq', 'food'])
        return event

    def test_one_day_event_never_says_another_weekday(self):
        event = self.art_walk()
        out = P.fallback(event)
        texts = [o['text'] for o in out]
        self.assertEqual(texts[0], 'Plan my Thursday evening around art')
        self.assertTrue(all('Saturday' not in t and 'Sunday' not in t for t in texts))
        self.assertEqual(out[-1], {'text': 'What’s on near me right now?', 'kind': 'question'})
        self.assertIn('I’ve got 2 hours — show me the highlights', texts)
        self.assertFalse(any('kid' in t.lower() for t in texts))  # no family-tagged items
        self.assertLessEqual(len(out), 4)

    def test_multi_day_event_uses_its_days_and_the_family_rule(self):
        out = P.fallback(self.festival())
        texts = [o['text'] for o in out]
        self.assertTrue(texts[0].startswith('Plan my Saturday evening around '), texts)
        self.assertTrue(any(t.startswith('Plan my Sunday afternoon') for t in texts), texts)
        self.assertIn('Plan a kid-friendly evening', texts)
        self.assertEqual(len(out), 4)
        self.assertEqual([o['kind'] for o in out].count('question'), 1)

    def test_categories_when_there_are_no_tags(self):
        event = self.art_walk()
        event.scheduleditem_set.update(tags=[])
        event.location_set.update(tags=[])
        self.assertEqual(P.fallback(event)[0]['text'], 'Plan my Thursday evening around the galleries')

    def test_the_bundle_has_them(self):
        event = self.art_walk()
        with override_settings(API_KEY='k'):
            response = Client().get(f'/api/v2/events/{event.pk}/bundle/', HTTP_AUTHORIZATION='Bearer k', secure=True)
        bundle = response.json()
        self.assertEqual(bundle['plan_suggestions'][0], {'text': 'Plan my Thursday evening around art', 'kind': 'plan'})
        self.assertTrue(all(set(s) == {'text', 'kind'} for s in bundle['plan_suggestions']))

    def test_the_ai_path_is_checked_and_stored(self):
        event = self.art_walk()
        event.refresh_from_db()
        before = event.content_version
        client = fake_client([
            {'text': 'Plan my Thursday evening around the galleries', 'kind': 'plan'},
            {'text': 'Plan my Saturday around art', 'kind': 'plan'},  # not an event day: dropped
            {'text': 'Plan a kid-friendly evening', 'kind': 'plan'},  # no family items: dropped
            {'text': 'I’ve got 2 hours — show me the highlights', 'kind': 'plan'},
            {'text': 'Which galleries have openings tonight?', 'kind': 'question'},
        ])
        record = P.generate(event, client=client)
        self.assertEqual(record['source'], 'ai')
        self.assertEqual([i['text'] for i in record['items']], [
            'Plan my Thursday evening around the galleries', 'I’ve got 2 hours — show me the highlights',
            'Which galleries have openings tonight?'])
        request = client.messages.requests[0]
        self.assertEqual(request['model'], 'claude-haiku-5-5')
        self.assertEqual(request['output_config']['effort'], 'low')
        self.assertEqual(request['output_config']['format']['type'], 'json_schema')
        self.assertEqual(request['system'][0]['cache_control'], {'type': 'ephemeral'})
        self.assertNotIn('thinking', request)
        self.assertIn('"weekday": "Thursday"', request['messages'][0]['content'])
        event.refresh_from_db()
        self.assertEqual(event.content_version, before + 1)  # apps see the new ones (ETag changes)
        self.assertEqual(event.plan_suggestions['version'], event.content_version)
        self.assertFalse(P.due(event))
        self.assertGreater(float(record['cost_usd']), 0)
        locations = list(event.location_set.all())
        items = list(event.scheduleditem_set.all())
        self.assertEqual(P.for_bundle(event, locations, items)[0]['text'], 'Plan my Thursday evening around the galleries')
        # Moved to a Friday: the stored Thursday text no longer fits, the bundle falls back at once.
        for it in items:
            it.start_time += timedelta(days=1)
            it.end_time += timedelta(days=1)
        self.assertEqual(P.for_bundle(event, locations, items)[0]['text'], 'Plan my Friday evening around art')

    def test_a_failed_or_refused_ai_keeps_the_fallback(self):
        event = self.art_walk()
        record = P.generate(event, client=fake_client([], stop='refusal'))
        self.assertEqual((record['source'], record['items']), ('fallback', []))
        event.refresh_from_db()
        self.assertEqual(event.plan_suggestions['version'], event.content_version)  # no bump, no retry loop
        broken = SimpleNamespace(messages=SimpleNamespace(create=mock.Mock(side_effect=RuntimeError('down'))))
        self.assertEqual(P.generate(event, client=broken)['source'], 'fallback')

    @override_settings(ANTHROPIC_API_KEY='')
    def test_without_a_key_nothing_runs(self):
        event = self.art_walk()
        self.assertEqual(P.refresh_due(), 0)
        record = P.generate(event)
        self.assertEqual(record['source'], 'fallback')

    def test_due_is_debounced(self):
        event = self.art_walk()
        now = timezone.now()
        event.content_updated_at = now
        self.assertFalse(P.due(event, now))  # still being edited
        self.assertTrue(P.due(event, now + timedelta(minutes=2)))
        event.plan_suggestions = {'version': event.content_version - 1, 'at': (now + timedelta(minutes=1)).isoformat()}
        self.assertFalse(P.due(event, now + timedelta(minutes=2)))  # tried a minute ago
        event.plan_suggestions = {'version': event.content_version}
        self.assertFalse(P.due(event, now + timedelta(hours=1)))  # up to date
