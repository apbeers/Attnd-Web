"""Tests for API v2, display resolution, version bumping and push delivery."""

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from events import display, push
from events.models import Device, Event, Location, Notification, ScheduledItem

AUTH = {'HTTP_AUTHORIZATION': 'Bearer test-key'}


@override_settings(API_KEY='test-key')
class V2Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('o', 'o@example.com', 'pw')
        self.event = Event.objects.create(
            name='Fair', event_code='FAIR2026', start_time=timezone.now() + timedelta(hours=5),
            latitude=32.75, longitude=-97.33, user=self.user, public=True, timezone='America/Chicago')
        self.loc = Location.objects.create(event=self.event, user=self.user, name='Stage', category='Stage',
                                           latitude=32.751, longitude=-97.331)
        self.unpinned = Location.objects.create(event=self.event, user=self.user, name='Mystery Tent')
        self.item = ScheduledItem.objects.create(event=self.event, user=self.user, location=self.loc, name='Band',
                                                 start_time=self.event.start_time, tags=['Music'])
        self.floating = ScheduledItem.objects.create(event=self.event, user=self.user, location=None,
                                                     name='Welcome', start_time=self.event.start_time)
        self.client = Client()

    def get(self, path, **extra):
        return self.client.get(path, secure=True, **AUTH, **extra)

    def post(self, path, body):
        return self.client.post(path, json.dumps(body), content_type='application/json', secure=True, **AUTH)


class BundleTests(V2Base):
    def test_requires_auth(self):
        self.assertEqual(self.client.get(f'/api/v2/events/{self.event.id}/bundle/', secure=True).status_code, 401)

    def test_bundle_shape(self):
        data = self.get(f'/api/v2/events/{self.event.id}/bundle/').json()
        self.assertEqual(data['schema_version'], 2)
        self.assertEqual(data['event']['timezone'], 'America/Chicago')
        self.assertTrue(data['event']['is_demo'])
        self.assertEqual(data['categories'], ['Stage'])
        names = {l['name']: l for l in data['locations']}
        self.assertIsNone(names['Mystery Tent']['latitude'])
        welcome = next(i for i in data['schedule'] if i['name'] == 'Welcome')
        self.assertIsNone(welcome['location_id'])
        self.assertIn('tabs', data['display'])

    def test_etag_304_and_version_bump(self):
        url = f'/api/v2/events/{self.event.id}/bundle/'
        first = self.get(url)
        self.assertEqual(self.get(url, HTTP_IF_NONE_MATCH=first['ETag']).status_code, 304)
        self.loc.name = 'Main Stage'
        self.loc.save()
        second = self.get(url, HTTP_IF_NONE_MATCH=first['ETag'])
        self.assertEqual(second.status_code, 200)
        self.assertNotEqual(second['ETag'], first['ETag'])

    def test_hidden_content_excluded(self):
        self.loc.hidden = True
        self.loc.save()
        data = self.get(f'/api/v2/events/{self.event.id}/bundle/').json()
        self.assertNotIn('Stage', [l['name'] for l in data['locations']])
        self.assertNotIn('Band', [i['name'] for i in data['schedule']])

    def test_drafts_are_invisible(self):
        self.event.status = Event.STATUS_DRAFT
        self.event.save()
        self.assertEqual(self.get(f'/api/v2/events/{self.event.id}/bundle/').status_code, 404)
        self.assertEqual(self.get('/api/v2/events/lookup/?q=FAIR2026').status_code, 404)
        self.assertEqual(self.get(f'/api/v1/events/lookup/?q=FAIR2026').status_code, 404)

    def test_lookup_case_insensitive(self):
        self.assertEqual(self.get('/api/v2/events/lookup/?q=fair2026').json()['event']['id'], self.event.id)

    def test_nearby_excludes_unlisted(self):
        self.assertEqual(self.get('/api/v2/events/nearby/?lat=32.75&lng=-97.33').json()['count'], 1)
        self.event.public = False
        self.event.save()
        self.assertEqual(self.get('/api/v2/events/nearby/?lat=32.75&lng=-97.33').json()['count'], 0)
        self.assertEqual(self.get('/api/v2/events/nearby/?lat=x').status_code, 400)

    def test_join_device_and_leave(self):
        resp = self.post(f'/api/v2/events/{self.event.id}/join/',
                         {'device_id': 'dev-1', 'platform': 'ios', 'push_token': 'abc', 'app_version': '2.0'})
        self.assertEqual(resp.status_code, 200)
        device = Device.objects.get(pk='dev-1')
        self.assertEqual((device.event, device.platform, device.push_token), (self.event, 'ios', 'abc'))
        self.post('/api/v2/devices/', {'device_id': 'dev-1', 'subscribed': False, 'push_token': 'def'})
        device.refresh_from_db()
        self.assertFalse(device.subscribed)
        self.assertEqual(device.event, self.event)  # devices/ never changes the joined event
        self.post(f'/api/v2/events/{self.event.id}/leave/', {'device_id': 'dev-1'})
        device.refresh_from_db()
        self.assertIsNone(device.event)

    def test_join_requires_device_id(self):
        self.assertEqual(self.post(f'/api/v2/events/{self.event.id}/join/', {}).status_code, 400)


class V1CompatTests(V2Base):
    def test_v1_locations_fall_back_to_event_pin(self):
        data = self.get(f'/api/v1/events/{self.event.id}/locations/').json()
        tent = next(l for l in data['results'] if l['name'] == 'Mystery Tent')
        self.assertEqual(tent['latitude'], 32.75)

    def test_v1_schedule_skips_items_without_location(self):
        data = self.get(f'/api/v1/events/{self.event.id}/schedule/').json()
        self.assertEqual([i['name'] for i in data['results']], ['Band'])


class DisplayTests(V2Base):
    def test_small_event_is_intimate(self):
        d = display.resolve_display(self.event)
        self.assertEqual(d['resolved_preset'], 'intimate')
        self.assertNotIn('flyer', d['tabs'])  # no flyer uploaded

    def test_large_event_is_festival(self):
        Location.objects.bulk_create([Location(event=self.event, user=self.user, name=f'Booth {i}',
                                               category=['Food', 'Art', 'Music'][i % 3]) for i in range(45)])
        d = display.resolve_display(self.event)
        self.assertEqual(d['resolved_preset'], 'festival')
        self.assertTrue(d['map_clustering'])
        self.assertTrue(d['group_by_category'])

    def test_empty_schedule_hides_tab(self):
        ScheduledItem.objects.all().delete()
        self.assertNotIn('schedule', display.resolve_display(self.event)['tabs'])

    def test_overrides_win_and_invalid_values_dropped(self):
        self.event.display_config = {'preset': 'standard', 'tabs': ['schedule', 'bogus', 'map'],
                                     'default_tab': 'locations', 'map_clustering': 'yes',
                                     'locations_style': 'cards'}
        d = display.resolve_display(self.event)
        self.assertEqual(d['tabs'], ['schedule', 'map'])
        self.assertEqual(d['default_tab'], 'schedule')  # not in tabs → first tab
        self.assertEqual(d['locations_style'], 'cards')
        self.assertFalse(d['map_clustering'])  # invalid override ignored; standard default

    def test_spread(self):
        self.assertGreater(display.spread_km([(32.75, -97.33), (32.85, -97.33)]), 10)


class PushTests(V2Base):
    def setUp(self):
        super().setUp()
        Device.objects.create(id='i1', event=self.event, platform='ios', push_token='tok-ios')
        Device.objects.create(id='a1', event=self.event, platform='android', push_token='tok-and')
        Device.objects.create(id='a2', event=self.event, platform='android', push_token='')
        Device.objects.create(id='a3', event=self.event, platform='android', push_token='x', subscribed=False)

    @patch('events.push.send_fcm')
    @patch('events.push.send_apns')
    def test_send_notification_targets_subscribed_devices(self, apns, fcm):
        apns.return_value = push.SendResult(delivered=1)
        fcm.return_value = push.SendResult(delivered=1, invalid_tokens=['tok-and'])
        n = Notification.objects.create(event=self.event, user=self.user, title='Hi', body='There',
                                        send_time=timezone.now())
        push.send_notification(n)
        self.assertEqual(apns.call_args.args[0], ['tok-ios'])
        self.assertEqual(fcm.call_args.args[0], ['tok-and'])
        self.assertEqual(apns.call_args.args[1]['type'], 'notification')
        n.refresh_from_db()
        self.assertTrue(n.sent)
        self.assertEqual(n.delivered_count, 2)
        self.assertEqual(Device.objects.get(pk='a1').push_token, '')  # invalid token forgotten
        push.send_notification(n)  # idempotent
        self.assertEqual(apns.call_count, 1)

    @patch('events.push.deliver')
    def test_due_notifications(self, deliver):
        deliver.return_value = push.SendResult()
        Notification.objects.create(event=self.event, user=self.user, title='due', body='',
                                    send_time=timezone.now() - timedelta(minutes=1))
        Notification.objects.create(event=self.event, user=self.user, title='later', body='',
                                    send_time=timezone.now() + timedelta(hours=1))
        self.assertEqual(push.send_due_notifications(), 1)

    def test_unconfigured_platforms_are_skipped(self):
        result = push.deliver(self.event, alert={'title': 't', 'body': 'b'})
        self.assertEqual(result.skipped, 2)

    @override_settings(APNS_AUTH_KEY='k', APNS_KEY_ID='KID', APNS_TEAM_ID='TEAM', APNS_TOPIC='com.arcu.attnd')
    @patch('events.push._apns_token', return_value='jwt')
    @patch('events.push.httpx.Client')
    def test_apns_marks_unregistered_tokens(self, client_cls, _tok):
        client = client_cls.return_value.__enter__.return_value
        ok, gone = MagicMock(status_code=200), MagicMock(status_code=410)
        gone.json.return_value = {'reason': 'Unregistered'}
        client.post.side_effect = [ok, gone]
        result = push.send_apns(['a', 'b'], {'aps': {}})
        self.assertEqual((result.delivered, result.invalid_tokens), (1, ['b']))
        self.assertEqual(client.post.call_args.kwargs['headers']['apns-topic'], 'com.arcu.attnd')


class RevokedKeyTests(V2Base):
    def test_revoked_static_key_is_rejected(self):
        import hashlib
        from events import apis
        revoked = {hashlib.sha256(b'test-key').hexdigest()}
        with patch.object(apis, 'REVOKED_API_KEY_SHA256', revoked):
            self.assertEqual(self.get('/api/v2/events/lookup/?q=FAIR2026').status_code, 401)
        self.assertEqual(self.get('/api/v2/events/lookup/?q=FAIR2026').status_code, 200)
