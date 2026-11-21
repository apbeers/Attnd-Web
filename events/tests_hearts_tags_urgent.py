"""API-009 (heart counts), API-010 (tags) and API-011 (urgent announcements + Live Activity push)."""
import json
from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from console.testing import TwoFactorClient
from events import push, usage
from events.models import Device, Event, Location, Notification, ScheduledItem, UsageCount, normalize_tags

AUTH = {'HTTP_AUTHORIZATION': 'Bearer k-glance'}
CI_KEY = 'ci-' + 'x' * 40


class Base(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('owner@example.com', 'owner@example.com', 'pw')
        start = timezone.now() - timedelta(hours=1)
        self.event = Event.objects.create(
            name='Fair', event_code='FAIR1', start_time=start, end_time=start + timedelta(hours=8),
            timezone='America/Chicago', user=self.owner, latitude=32.75, longitude=-97.33)
        self.stage = Location.objects.create(event=self.event, user=self.owner, name='Main Stage', category='Stage')
        self.food = Location.objects.create(event=self.event, user=self.owner, name='Taco Row', category='Food')
        self.band = ScheduledItem.objects.create(event=self.event, user=self.owner, location=self.stage,
                                                 name='Prairie Lights', start_time=start + timedelta(hours=2))
        self.api = Client()

    def post(self, path, body, **extra):
        return self.api.post(path, json.dumps(body), content_type='application/json', secure=True,
                             **{**AUTH, **extra})

    def bundle(self):
        resp = self.api.get(f'/api/v2/events/{self.event.id}/bundle/', secure=True, **AUTH)
        self.assertEqual(resp.status_code, 200)
        return resp.json()


# ---------------------------------------------------------------------------
# API-009: hearts
# ---------------------------------------------------------------------------

@override_settings(API_KEY='k-glance', CI_API_KEYS=[CI_KEY], DEMO_EVENT_CODE='FAIR1')
class HeartTests(Base):
    def usage(self, events, key='k-glance'):
        return self.api.post(f'/api/v2/events/{self.event.id}/usage/', json.dumps({'platform': 'ios', 'events': events}),
                             content_type='application/json', secure=True, HTTP_AUTHORIZATION=f'Bearer {key}')

    def counts(self, metric):
        return dict(UsageCount.objects.filter(event=self.event, metric=metric).values_list('detail', 'count'))

    def test_heart_metrics_are_accepted_with_valid_ids_only(self):
        other = Event.objects.create(name='Other', event_code='OTHER', start_time=timezone.now())
        elsewhere = Location.objects.create(event=other, name='Elsewhere')
        resp = self.usage([
            {'name': 'location_heart', 'detail': str(self.stage.id), 'count': 2},
            {'name': 'location_unheart', 'detail': str(self.stage.id)},
            {'name': 'schedule_item_heart', 'detail': str(self.band.id)},
            {'name': 'schedule_item_unheart', 'detail': str(self.band.id)},
            {'name': 'location_heart'},                                   # no id: dropped
            {'name': 'location_heart', 'detail': str(elsewhere.id)},      # another event's location
            {'name': 'schedule_item_heart', 'detail': str(self.stage.id)},  # a location id is not an item id
            {'name': 'location_heart', 'detail': 'main'},
        ])
        self.assertEqual(resp.json(), {'status': 'ok', 'accepted': 4})
        self.assertEqual(self.counts('location_heart'), {str(self.stage.id): 2})
        self.assertEqual(self.counts('location_unheart'), {str(self.stage.id): 1})
        self.assertEqual(self.counts('schedule_item_heart'), {str(self.band.id): 1})

    def test_ci_key_counts_but_discards_hearts(self):
        resp = self.usage([{'name': 'location_heart', 'detail': str(self.stage.id)}], key=CI_KEY)
        self.assertEqual(resp.json()['accepted'], 1)
        self.assertFalse(UsageCount.objects.filter(metric='location_heart').exists())

    def test_net_hearts_never_go_below_zero(self):
        from console.usage import most_hearted, net_hearts
        usage.record(self.event, 'location_heart', detail=self.stage.id, count=5)
        usage.record(self.event, 'location_unheart', detail=self.stage.id, count=2)
        usage.record(self.event, 'location_unheart', detail=self.food.id, count=3)  # no hearts: clamps to 0
        usage.record(self.event, 'schedule_item_heart', detail=self.band.id, count=4, platform='android')
        usage.record(self.event, 'schedule_item_heart', detail=self.band.id, count=1, platform='ios',
                     at=timezone.now() - timedelta(hours=3))
        self.assertEqual(net_hearts(self.event), {'location': {self.stage.id: 3}, 'item': {self.band.id: 5}})
        self.assertEqual([(r['kind'], r['obj'].name, r['count']) for r in most_hearted(self.event)],
                         [('item', 'Prairie Lights', 5), ('location', 'Main Stage', 3)])

    def test_console_shows_hearts(self):
        usage.record(self.event, 'location_heart', detail=self.stage.id, count=7)
        usage.record(self.event, 'schedule_item_heart', detail=self.band.id, count=4)
        usage.record(self.event, 'schedule_item_unheart', detail=self.band.id, count=1)
        client = TwoFactorClient()
        client.force_login(self.owner)
        url = lambda name: reverse(f'console:{name}', args=[self.event.id])  # noqa: E731
        page = client.get(url('usage'), secure=True).content.decode()
        self.assertIn('Most liked locations', page)
        self.assertIn('Likes given', page)
        self.assertInHTML('<td class="num">11</td>', page)  # likes given 7 + 4
        overview = client.get(url('event'), secure=True).content.decode()
        self.assertIn('Most liked', overview)
        self.assertLess(overview.index('Main Stage'), overview.index('Prairie Lights'))  # 7 before 3
        for name, needle in (('locations', '</svg> 7</span>'), ('schedule', '</svg> 3</span>')):
            self.assertIn(needle, client.get(url(name), secure=True).content.decode(), name)


# ---------------------------------------------------------------------------
# API-010: tags
# ---------------------------------------------------------------------------

@override_settings(API_KEY='k-glance')
class TagTests(Base):
    def test_normalize_tags(self):
        self.assertEqual(normalize_tags(['  Live  Music ', 'live music', '#BBQ', 'Kid-Friendly', 3, None, '']),
                         ['live music', 'bbq', 'kid-friendly'])
        self.assertEqual(normalize_tags('Food, Drinks,,food'), ['food', 'drinks'])
        self.assertEqual(normalize_tags(None), [])
        self.assertEqual(len(normalize_tags([f't{i}' for i in range(20)])), 8)
        self.assertEqual(len(normalize_tags(['x' * 50])[0]), 32)

    def test_locations_have_tags_in_the_bundle_and_item_tags_are_normalised(self):
        self.stage.tags = ['Live Music', 'FREE']
        self.stage.save()
        self.assertEqual(Location.objects.get(pk=self.stage.pk).tags, ['live music', 'free'])
        ScheduledItem.objects.filter(pk=self.band.pk).update(tags=['Music', 'Country'])  # stored before API-010
        b = self.bundle()
        locs = {l['name']: l for l in b['locations']}
        self.assertEqual(locs['Main Stage']['tags'], ['live music', 'free'])
        self.assertEqual(locs['Taco Row']['tags'], [])
        self.assertEqual(b['schedule'][0]['tags'], ['music', 'country'])

    def test_migration_normalises_stored_item_tags_and_bumps_the_version(self):
        import importlib
        from django.apps import apps
        migration = importlib.import_module('events.migrations.0034_normalize_item_tags')
        ScheduledItem.objects.filter(pk=self.band.pk).update(tags=['Music', 'music', 'Kids'])
        version = Event.objects.get(pk=self.event.pk).content_version
        migration.normalize(apps, None)
        self.assertEqual(ScheduledItem.objects.get(pk=self.band.pk).tags, ['music', 'kids'])
        self.assertEqual(Event.objects.get(pk=self.event.pk).content_version, version + 1)
        migration.normalize(apps, None)  # nothing left to change: no second bump
        self.assertEqual(Event.objects.get(pk=self.event.pk).content_version, version + 1)

    def test_vocabulary_folds_spellings_onto_the_events_own(self):
        from scrape.tags import Vocabulary, canonicalize_extraction, tag_key
        self.assertEqual(tag_key('Kids-Friendly'), tag_key('kid friendly'))
        self.assertEqual(tag_key('Food & Drinks'), tag_key('food and drink'))
        vocab = Vocabulary(['kid-friendly', 'bbq', 'live music'])
        self.assertEqual(vocab.canonical(['Kids Friendly', 'BBQs', 'Live-Music', 'country', 'Country']),
                         ['kid-friendly', 'bbq', 'live music', 'country'])
        data = {'tag_vocabulary': ['Tacos'], 'locations': [{'tags': ['taco', 'food']}],
                'schedule': [{'tags': ['Food', 'tacos', 'demo', 'a', 'b', 'c', 'd']}]}
        canonicalize_extraction(data, seed=['food'])
        self.assertEqual(data['locations'][0]['tags'], ['tacos', 'food'])
        self.assertEqual(data['schedule'][0]['tags'], ['food', 'tacos', 'demo', 'a', 'b', 'c'])  # at most 6

    @patch('scrape.service._crawl', return_value='page text')
    def test_ai_import_tags_locations_and_items(self, _crawl):
        from scrape.diff import create_event_from_extraction
        from scrape.service import EVENT_SCHEMA, scrape_event
        self.assertIn('tags', EVENT_SCHEMA['properties']['locations']['items']['required'])
        self.assertIn('tag_vocabulary', EVENT_SCHEMA['required'])
        reply = {
            'name': 'Fest', 'description': '', 'address': '', 'latitude': None, 'longitude': None,
            'timezone': 'America/Chicago', 'start_time': '2026-07-04T10:00:00', 'end_time': None, 'notes': '',
            'tag_vocabulary': ['live music', 'country', 'kid-friendly'],
            'locations': [{'existing_id': None, 'name': 'Main Stage', 'snippet': '', 'description': '', 'category': 'Stage',
                           'website': None, 'image_url': None, 'address': '', 'latitude': None, 'longitude': None,
                           'tags': ['Live Music', 'Kids friendly']}],
            'schedule': [{'existing_id': None, 'name': 'Band', 'description': '', 'start_time': '2026-07-04T12:00:00',
                          'end_time': None, 'location_name': 'Main Stage', 'tags': ['live-music', 'Country']}],
            'removed_location_ids': [], 'removed_item_ids': [],
        }
        from attnd.testing import fake_claude
        with fake_claude(reply) as claude:
            data = scrape_event('https://fest.example')
        prompt = claude.last['system'][0]['text']
        self.assertIn('controlled vocabulary', prompt)
        event, _ = create_event_from_extraction(data, self.owner, status=Event.STATUS_DRAFT, public=False)
        self.assertEqual(event.location_set.get().tags, ['live music', 'kid-friendly'])
        self.assertEqual(event.scheduleditem_set.get().tags, ['live music', 'country'])

    def test_refresh_proposes_tags_but_locked_tags_win(self):
        from scrape.diff import compute_changes, current_state
        self.stage.tags = ['stage']
        self.stage.locked_fields = ['tags']
        self.stage.save()
        state = current_state(self.event)
        self.assertEqual(state['tag_vocabulary'], ['stage'])
        self.assertEqual(state['locations'][0]['tags'], ['stage'])
        data = {'name': self.event.name, 'locations': [
            {'existing_id': self.stage.id, 'name': 'Main Stage', 'tags': ['live music']},
            {'existing_id': self.food.id, 'name': 'Taco Row', 'tags': ['food', 'tacos']},
        ], 'schedule': [{'existing_id': self.band.id, 'name': 'Prairie Lights', 'tags': []}]}
        changes = {c['label']: c for c in compute_changes(self.event, data) if c['entity'] != 'event'}
        self.assertEqual(changes['Main Stage']['conflict_fields'], ['tags'])  # proposed, unticked
        self.assertEqual(changes['Taco Row']['data']['tags'], {'old': [], 'new': ['food', 'tacos']})
        self.assertTrue(changes['Taco Row']['minor'])
        self.assertNotIn('Prairie Lights', changes)  # empty AI tags never blank anything

    def test_console_edits_tags_and_locks_them(self):
        client = TwoFactorClient()
        client.force_login(self.owner)
        url = reverse('console:location_edit', args=[self.event.id, self.food.id])
        page = client.get(url, secure=True).content.decode()
        self.assertIn('name="tags_text"', page)
        resp = client.post(url, {'name': 'Taco Row', 'category': 'Food', 'tags_text': 'Food, Tacos, food'}, secure=True)
        self.assertEqual(resp.status_code, 302)
        self.food.refresh_from_db()
        self.assertEqual(self.food.tags, ['food', 'tacos'])
        self.assertIn('tags', self.food.locked_fields)
        listing = client.get(reverse('console:locations', args=[self.event.id]), secure=True).content.decode()
        self.assertIn('>tacos</span>', listing)
        self.assertIn('Tags in this event: food, tacos',
                      client.get(reverse('console:item_new', args=[self.event.id]), secure=True).content.decode())


class BackfillTagsTests(Base):
    def fake_ai(self, tags_for):
        def suggest(info, entries, vocabulary):
            self.calls.append([(e['kind'], e['id']) for e in entries])
            return {'tag_vocabulary': ['live music', 'food'],
                    'tags': {(e['kind'], e['id']): tags_for(e) for e in entries}}
        self.calls = []
        return patch('scrape.service.suggest_tags', side_effect=suggest)

    def test_backfill_fills_missing_tags_skips_locked_and_is_safe_to_rerun(self):
        self.food.tags = ['Food']
        self.food.locked_fields = ['tags']
        self.food.save()
        ScheduledItem.objects.filter(pk=self.band.pk).update(tags=['Music'])
        version = Event.objects.get(pk=self.event.pk).content_version
        with self.fake_ai(lambda e: ['Live Music', 'country'] if e['kind'] == 'item' else ['live-music', 'free']):
            call_command('backfill_tags', 'fair1', stdout=StringIO())
        self.assertEqual(self.calls, [[('location', self.stage.id), ('item', self.band.id)]])  # not the locked one
        self.stage.refresh_from_db()
        self.band.refresh_from_db()
        self.food.refresh_from_db()
        self.assertEqual(self.stage.tags, ['live music', 'free'])
        self.assertEqual(self.band.tags, ['music', 'live music', 'country'])  # existing tags kept first
        self.assertEqual(self.food.tags, ['food'])  # locked: normalised by save() earlier, never sent or changed
        self.assertEqual(Event.objects.get(pk=self.event.pk).content_version, version + 1)  # one bump
        self.assertTrue(self.event.revisions.filter(summary='Before adding AI tags').exists())

        with self.fake_ai(lambda e: ['other']):
            out = StringIO()
            call_command('backfill_tags', '--all', stdout=out)
        self.assertEqual(self.calls, [])  # everything has 2+ tags now
        self.assertIn('nothing to change', out.getvalue())

    def test_dry_run_saves_nothing(self):
        with self.fake_ai(lambda e: ['food', 'tacos']):
            out = StringIO()
            call_command('backfill_tags', str(self.event.id), '--dry-run', stdout=out)
        self.assertIn('would change 3 tag lists', out.getvalue())
        self.assertEqual(Location.objects.get(pk=self.food.pk).tags, [])

    def test_needs_events_or_all(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command('backfill_tags', stdout=StringIO())


# ---------------------------------------------------------------------------
# API-011: urgent announcements and Live Activity tokens
# ---------------------------------------------------------------------------

@override_settings(API_KEY='k-glance')
class LiveActivityTokenTests(Base):
    def device(self):
        return Device.objects.get(pk='la-1')

    def test_devices_stores_and_clears_the_token(self):
        self.post(f'/api/v2/events/{self.event.id}/join/', {'device_id': 'la-1', 'platform': 'ios'})
        self.post('/api/v2/devices/', {'device_id': 'la-1', 'live_activity_token': 'ABCDEF0123'})
        self.assertEqual(self.device().live_activity_token, 'abcdef0123')
        self.post('/api/v2/devices/', {'device_id': 'la-1', 'subscribed': False})  # missing: unchanged
        self.assertEqual(self.device().live_activity_token, 'abcdef0123')
        resp = self.post('/api/v2/devices/', {'device_id': 'la-1', 'live_activity_token': 'not hex!'})
        self.assertEqual(resp.status_code, 200)  # ignored, never an error
        self.assertEqual(self.device().live_activity_token, 'abcdef0123')
        self.post('/api/v2/devices/', {'device_id': 'la-1', 'live_activity_token': None})
        self.assertEqual(self.device().live_activity_token, '')
        self.post('/api/v2/devices/', {'device_id': 'la-1', 'live_activity_token': 'aa'})
        self.post('/api/v2/devices/', {'device_id': 'la-1', 'live_activity_token': ''})
        self.assertEqual(self.device().live_activity_token, '')

    def test_token_is_cleared_on_leave_and_on_joining_another_event(self):
        other = Event.objects.create(name='Other', event_code='OTHER', start_time=timezone.now())
        join = f'/api/v2/events/{self.event.id}/join/'
        self.post(join, {'device_id': 'la-1', 'platform': 'ios', 'live_activity_token': 'aa11'})
        self.assertEqual(self.device().live_activity_token, 'aa11')
        self.post(join, {'device_id': 'la-1', 'platform': 'ios'})  # rejoining the same event keeps it
        self.assertEqual(self.device().live_activity_token, 'aa11')
        self.post(f'/api/v2/events/{other.id}/join/', {'device_id': 'la-1'})
        self.assertEqual(self.device().live_activity_token, '')
        self.post('/api/v2/devices/', {'device_id': 'la-1', 'live_activity_token': 'bb22'})
        self.post(f'/api/v2/events/{other.id}/leave/', {'device_id': 'la-1'})
        self.assertEqual(self.device().live_activity_token, '')


@override_settings(API_KEY='k-glance', APNS_TOPIC='com.arcu.attnd')
class UrgentTests(Base):
    def setUp(self):
        super().setUp()
        Device.objects.create(id='ios-on', event=self.event, platform='ios', push_token='t-ios-on',
                              live_activity_token='la-on')
        Device.objects.create(id='ios-off', event=self.event, platform='ios', push_token='t-ios-off', subscribed=False)
        Device.objects.create(id='ios-la-only', event=self.event, platform='ios', live_activity_token='la-only')
        Device.objects.create(id='and-off', event=self.event, platform='android', push_token='t-and-off',
                              subscribed=False)
        Device.objects.create(id='ci-ios-1', event=self.event, platform='ios', push_token='t-ci', live_activity_token='la-ci')

    def notification(self, priority='urgent', **kw):
        return Notification.objects.create(event=self.event, user=self.owner, title='Rain delay', body='Main Stage moves to 8 pm',
                                           send_time=timezone.now(), priority=priority, **kw)

    def send(self, n, apns_result=None):
        self.apns_calls = []

        def apns(tokens, payload, **kwargs):
            self.apns_calls.append((tokens, payload, kwargs))
            return apns_result(tokens, kwargs) if apns_result else push.SendResult(delivered=len(tokens))

        with patch('events.push.send_apns', side_effect=apns), \
                patch('events.push.send_fcm', return_value=push.SendResult(delivered=1)) as fcm, \
                patch('events.push._apns_config', return_value={'topic': 'com.arcu.attnd'}):
            n = push.send_notification(n)
        return n, fcm

    def test_urgent_goes_to_everyone_with_a_token_and_to_live_activities(self):
        n, fcm = self.send(self.notification())
        (ios_tokens, alert, alert_kw), (la_tokens, la, la_kw) = self.apns_calls
        self.assertEqual(sorted(ios_tokens), ['t-ios-off', 't-ios-on'])  # subscribed or not; never ci- devices
        self.assertEqual(alert['aps']['interruption-level'], 'time-sensitive')
        self.assertNotIn('critical', json.dumps(alert))
        self.assertEqual((alert['type'], alert['priority'], alert['notification_id']), ('notification', 'urgent', n.id))
        self.assertEqual(alert_kw.get('push_type', 'alert'), 'alert')

        self.assertEqual(sorted(la_tokens), ['la-on', 'la-only'])
        self.assertEqual((la_kw['push_type'], la_kw['priority'], la_kw['topic']),
                         ('liveactivity', '10', 'com.arcu.attnd.push-type.liveactivity'))
        aps = la['aps']
        self.assertEqual(aps['event'], 'update')
        self.assertEqual(set(aps['content-state']), {'urgent'})
        self.assertEqual(aps['content-state']['urgent']['id'], n.id)
        self.assertEqual(aps['content-state']['urgent']['title'], 'Rain delay')
        self.assertEqual(aps['content-state']['urgent']['sent_at'], n.sent_at.isoformat())
        self.assertEqual(aps['alert'], {'title': 'Rain delay', 'body': 'Main Stage moves to 8 pm', 'sound': 'default'})
        self.assertEqual(aps['timestamp'], int(n.sent_at.timestamp()))
        self.assertEqual(aps['stale-date'], aps['timestamp'] + push.URGENT_TTL)

        (tokens, data), kwargs = fcm.call_args
        self.assertEqual(tokens, ['t-and-off'])
        self.assertEqual((data['type'], data['priority'], data['title'], data['body']),
                         ('notification', 'urgent', 'Rain delay', 'Main Stage moves to 8 pm'))
        self.assertEqual(kwargs['priority'], 'high')
        self.assertIsNone(kwargs.get('notification'))  # data only: the app picks its urgent channel
        self.assertEqual(n.delivered_count, 2 + 2 + 1)
        self.assertEqual(self.bundle()['notifications'][0]['priority'], 'urgent')

    def test_normal_notification_is_unchanged_and_skips_live_activities(self):
        n, fcm = self.send(self.notification(priority='normal'))
        self.assertEqual(len(self.apns_calls), 1)
        tokens, payload, _ = self.apns_calls[0]
        self.assertEqual(tokens, ['t-ios-on'])  # subscribed only
        self.assertNotIn('interruption-level', payload['aps'])
        self.assertEqual(payload['priority'], 'normal')
        self.assertEqual(fcm.call_args.args[0], [])
        self.assertEqual(fcm.call_args.kwargs['notification'], {'title': 'Rain delay', 'body': 'Main Stage moves to 8 pm'})

    def test_invalid_live_activity_tokens_are_cleared(self):
        def result(tokens, kwargs):
            if kwargs.get('push_type') == 'liveactivity':
                return push.SendResult(delivered=1, failed=1, invalid_tokens=['la-only'])
            return push.SendResult(delivered=len(tokens))
        self.send(self.notification(), apns_result=result)
        self.assertEqual(Device.objects.get(pk='ios-la-only').live_activity_token, '')
        self.assertEqual(Device.objects.get(pk='ios-on').live_activity_token, 'la-on')
        self.assertEqual(Device.objects.get(pk='ios-on').push_token, 't-ios-on')

    def test_urgent_sends_are_limited_per_event(self):
        for _ in range(push.URGENT_MAX_PER_HOUR):
            self.send(self.notification())
        self.assertEqual(push.urgent_sends_left(self.event), 0)
        n, _ = self.send(self.notification())
        self.assertEqual(n.priority, 'normal')  # over the limit: sent, but as a normal update
        self.assertEqual(len(self.apns_calls), 1)
        later = timezone.now() + timedelta(minutes=61)
        self.assertEqual(push.urgent_sends_left(self.event, now=later), push.URGENT_MAX_PER_HOUR)

    def test_send_apns_uses_the_live_activity_topic(self):
        cfg = {'key': 'k', 'key_id': 'i', 'team': 't', 'topic': 'com.arcu.attnd', 'host': 'https://apns.test'}
        with patch('events.push._apns_config', return_value=cfg), patch('events.push._apns_token', return_value='jwt'), \
                patch('events.push.httpx.Client') as client:
            post = client.return_value.__enter__.return_value.post
            post.return_value = MagicMock(status_code=410, json=lambda: {'reason': 'Unregistered'})
            result = push.send_apns(['tok'], {'aps': {}}, push_type='liveactivity', priority='10',
                                    topic='com.arcu.attnd.push-type.liveactivity')
        headers = post.call_args.kwargs['headers']
        self.assertEqual((headers['apns-push-type'], headers['apns-topic']),
                         ('liveactivity', 'com.arcu.attnd.push-type.liveactivity'))
        self.assertEqual(result.invalid_tokens, ['tok'])

    def test_console_urgent_checkbox(self):
        client = TwoFactorClient()
        client.force_login(self.owner)
        url = reverse('console:notifications', args=[self.event.id])
        page = client.get(url, secure=True).content.decode()
        self.assertIn('name="urgent"', page)
        self.assertIn('Urgent goes to everyone in the event, right away', page)
        with patch('events.push.deliver', return_value=push.SendResult(delivered=3)) as deliver, \
                patch('events.push.send_live_activity_urgent', return_value=push.SendResult(delivered=1)) as la:
            resp = client.post(url, {'title': 'Cancelled', 'body': 'Storm', 'when': 'now', 'urgent': 'on'}, secure=True)
        self.assertEqual(resp.status_code, 302)
        n = Notification.objects.get(title='Cancelled')
        self.assertEqual((n.priority, n.sent, n.delivered_count), ('urgent', True, 4))
        self.assertTrue(deliver.call_args.kwargs['urgent'])
        la.assert_called_once()
        listing = client.get(url, secure=True).content.decode()
        self.assertIn('is-urgent', listing)

        Notification.objects.filter(pk=n.pk).update(sent_at=timezone.now())
        for _ in range(push.URGENT_MAX_PER_HOUR - 1):
            self.notification(sent=True, sent_at=timezone.now())
        resp = client.post(url, {'title': 'Again', 'body': 'x', 'when': 'now', 'urgent': 'on'}, secure=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('most urgent updates allowed in an hour', resp.content.decode())
        self.assertFalse(Notification.objects.filter(title='Again').exists())


class SuggestTagsTests(TestCase):
    def test_only_the_ids_asked_about_are_returned(self):
        from attnd.testing import fake_claude
        from scrape.service import suggest_tags
        answer = {'tag_vocabulary': ['food'], 'tagged': [
            {'kind': 'location', 'id': 1, 'tags': ['food', 'bbq']},
            {'kind': 'item', 'id': 99, 'tags': ['made up']},
            {'kind': 'item', 'id': 2, 'tags': 'not a list'}]}
        with fake_claude(answer) as claude:
            out = suggest_tags({'name': 'Fair'}, [{'kind': 'location', 'id': 1, 'name': 'BBQ'},
                                                  {'kind': 'item', 'id': 2, 'name': 'Band'}], ['food'])
        self.assertEqual(out, {'tag_vocabulary': ['food'], 'tags': {('location', 1): ['food', 'bbq']}})
        request = claude.last
        self.assertEqual(request['output_config']['format']['type'], 'json_schema')
        self.assertIn('"tag_vocabulary": ["food"]', request['messages'][0]['content'])
