"""Tests for API v2, display resolution, version bumping and push delivery."""

import json
import time
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


class ApiVersionTrackingTests(V2Base):
    def test_devices_record_the_api_version_they_use(self):
        self.client.post(f'/api/v1/events/{self.event.id}/join/old-phone/', '{"platform": "ios"}',
                         content_type='application/json', secure=True, **AUTH)
        self.post(f'/api/v2/events/{self.event.id}/join/', {'device_id': 'new-phone', 'platform': 'android',
                                                             'api_version': 9})
        self.assertEqual(Device.objects.get(id='old-phone').api_version, 1)
        self.assertEqual(Device.objects.get(id='new-phone').api_version, 2)
        self.get(f'/api/v2/events/{self.event.id}/bundle/', HTTP_X_DEVICE_ID='old-phone')
        self.assertEqual(Device.objects.get(id='old-phone').api_version, 2)


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


    @patch('events.push.send_fcm')
    @patch('events.push.send_apns')
    def test_content_updated_reaches_every_device_with_a_token(self, apns, fcm):
        """API-007: silent pushes ignore `subscribed`, alerts don't."""
        apns.return_value = fcm.return_value = push.SendResult()
        Device.objects.create(id='i2', event=self.event, platform='ios', push_token='tok-ios-off', subscribed=False)
        Device.objects.create(id='i3', event=self.event, platform='ios', push_token='')
        Device.objects.create(id='other', platform='android', push_token='elsewhere', subscribed=True)
        push.notify_content_updated(self.event)
        self.assertCountEqual(apns.call_args.args[0], ['tok-ios', 'tok-ios-off'])
        self.assertCountEqual(fcm.call_args.args[0], ['tok-and', 'x'])
        payload = apns.call_args.args[1]
        self.assertEqual((payload['type'], payload['aps']), ('content_updated', {'content-available': 1}))
        self.assertEqual(payload['content_version'], self.event.content_version)
        self.assertEqual(apns.call_args.kwargs['push_type'], 'background')
        self.assertEqual(apns.call_args.kwargs['priority'], '5')
        self.assertEqual(apns.call_args.kwargs['collapse_id'], f'content-{self.event.id}')
        self.assertAlmostEqual(apns.call_args.kwargs['expiration'], time.time() + 3600, delta=60)
        self.assertEqual(fcm.call_args.kwargs['priority'], 'normal')
        self.assertEqual(fcm.call_args.kwargs['collapse_key'], f'content_{self.event.id}')
        self.assertEqual(fcm.call_args.kwargs['ttl'], 3600)
        self.assertIsNone(fcm.call_args.kwargs.get('notification'))

        push.deliver(self.event, alert={'title': 't', 'body': 'b'}, data={'type': 'notification'})
        self.assertEqual(apns.call_args.args[0], ['tok-ios'])
        self.assertEqual(fcm.call_args.args[0], ['tok-and'])
        self.assertNotIn('collapse_id', apns.call_args.kwargs)
        self.assertNotIn('priority', fcm.call_args.kwargs)  # default 'high'

    @override_settings(APNS_AUTH_KEY='k', APNS_KEY_ID='KID', APNS_TEAM_ID='TEAM', APNS_TOPIC='com.arcu.attnd')
    @patch('events.push._apns_token', return_value='jwt')
    @patch('events.push.httpx.Client')
    def test_apns_collapse_and_expiration_headers(self, client_cls, _tok):
        client = client_cls.return_value.__enter__.return_value
        client.post.return_value = MagicMock(status_code=200)
        push.send_apns(['a'], {'aps': {'content-available': 1}}, push_type='background', priority='5',
                       collapse_id='content-7', expiration=1800000000.7)
        headers = client.post.call_args.kwargs['headers']
        self.assertEqual(headers['apns-collapse-id'], 'content-7')
        self.assertEqual(headers['apns-expiration'], '1800000000')
        self.assertEqual((headers['apns-push-type'], headers['apns-priority']), ('background', '5'))
        push.send_apns(['a'], {'aps': {}})
        headers = client.post.call_args.kwargs['headers']
        self.assertNotIn('apns-collapse-id', headers)
        self.assertNotIn('apns-expiration', headers)
        self.assertEqual((headers['apns-push-type'], headers['apns-priority']), ('alert', '10'))

    @patch('events.push._fcm_access_token', return_value=('at', 'proj'))
    @patch('events.push.httpx.Client')
    def test_fcm_collapse_key_priority_and_ttl(self, client_cls, _tok):
        client = client_cls.return_value.__enter__.return_value
        client.post.return_value = MagicMock(status_code=200)
        push.send_fcm(['t'], {'type': 'content_updated', 'content_version': 3}, priority='normal',
                      collapse_key='content_7', ttl=3600)
        message = client.post.call_args.kwargs['json']['message']
        self.assertEqual(message['android'], {'priority': 'normal', 'collapse_key': 'content_7', 'ttl': '3600s'})
        self.assertEqual(message['data'], {'type': 'content_updated', 'content_version': '3'})
        self.assertNotIn('notification', message)
        push.send_fcm(['t'], {'type': 'notification'}, notification={'title': 't', 'body': 'b'})
        message = client.post.call_args.kwargs['json']['message']
        self.assertEqual(message['android'], {'priority': 'high'})
        self.assertEqual(message['notification'], {'title': 't', 'body': 'b'})

    def test_content_push_debounce_defaults(self):
        from django.conf import settings
        self.assertEqual((settings.CONTENT_PUSH_DELAY, settings.CONTENT_PUSH_MIN_INTERVAL), (3, 15))

    def test_devices_stores_push_token_when_not_subscribed(self):
        resp = self.post('/api/v2/devices/', {'device_id': 'quiet', 'platform': 'ios',
                                              'push_token': 'silent-tok', 'subscribed': False})
        self.assertEqual(resp.json()['subscribed'], False)
        device = Device.objects.get(pk='quiet')
        self.assertEqual((device.push_token, device.subscribed), ('silent-tok', False))


class RevokedKeyTests(V2Base):
    def test_revoked_static_key_is_rejected(self):
        import hashlib
        from events import apis
        revoked = {hashlib.sha256(b'test-key').hexdigest()}
        with patch.object(apis, 'REVOKED_API_KEY_SHA256', revoked):
            self.assertEqual(self.get('/api/v2/events/lookup/?q=FAIR2026').status_code, 401)
        self.assertEqual(self.get('/api/v2/events/lookup/?q=FAIR2026').status_code, 200)


@override_settings(APP_MIN_VERSION_IOS='2.0.50', APP_LATEST_VERSION_IOS='2.0.90', APP_MIN_VERSION_ANDROID='',
                   APP_LATEST_VERSION_ANDROID='', IOS_APP_STORE_URL='https://apps.apple.com/app/id123',
                   SUPPORTED_API_VERSIONS=[1, 2])
class AppStatusTests(TestCase):
    def status(self, **params):
        return self.client.get('/api/app-status/', params, secure=True).json()

    def test_no_auth_needed_and_versions_compared_numerically(self):
        self.assertEqual(self.status(platform='ios', version='2.0.9', api=2)['update'], 'required')
        self.assertEqual(self.status(platform='ios', version='2.0.50', api=2)['update'], 'available')
        self.assertEqual(self.status(platform='ios', version='2.0.100', api=2)['update'], 'none')
        self.assertEqual(self.status(platform='ios', version='2.1', api=2)['update'], 'none')
        body = self.status(platform='ios', version='2.0.9', api=2)
        self.assertEqual(body['update_url'], 'https://apps.apple.com/app/id123')
        self.assertEqual(body['min_version'], '2.0.50')

    def test_platform_without_settings_never_prompts(self):
        body = self.status(platform='android', version='1.0', api=2)
        self.assertEqual(body['update'], 'none')
        self.assertIn('play.google.com/store/apps/details?id=', body['update_url'])

    def test_unsupported_api_version_requires_update(self):
        self.assertEqual(self.status(platform='android', version='9.9', api=1)['update'], 'none')
        with self.settings(SUPPORTED_API_VERSIONS=[2]):
            self.assertEqual(self.status(platform='android', version='9.9', api=1)['update'], 'required')

    def test_unparseable_versions_do_not_prompt(self):
        self.assertEqual(self.status(platform='ios', version='', api=2)['update'], 'none')
        self.assertEqual(self.status(platform='ios', version='beta', api='x')['update'], 'none')

    def test_retired_api_version_returns_410(self):
        resp = self.client.get('/api/v1/events/', secure=True, HTTP_USER_AGENT='Attnd/85 CFNetwork Darwin/27.2.0')
        self.assertEqual(resp.status_code, 404)  # v1 is still supported: an unknown path is just a 404
        resp = self.client.get('/api/v3/events/', secure=True)
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.json()['error'], 'update_required')
        with self.settings(SUPPORTED_API_VERSIONS=[2]):
            resp = self.client.get('/api/v1/events/9/', secure=True, HTTP_USER_AGENT='Attnd/85 CFNetwork Darwin/27.2.0')
        # Routed v1 endpoints still exist until their urls are removed; the catch-all covers removed ones.
        self.assertIn(resp.status_code, (401, 404, 410))

    def test_attestation_routes_are_not_swallowed_by_the_catch_all(self):
        self.assertEqual(self.client.get('/api/v1/attest/nonce/', secure=True).status_code, 200)


class UsageTests(V2Base):
    def counts(self, **filters):
        from events.models import UsageCount
        return {(u.metric, u.detail, u.platform): u.count for u in UsageCount.objects.filter(**filters)}

    def test_ingest_validates_and_buckets_anonymously(self):
        other = Event.objects.create(name='Other', event_code='OTHER1', start_time=timezone.now(), user=self.user)
        foreign = Location.objects.create(event=other, user=self.user, name='Elsewhere')
        resp = self.post(f'/api/v2/events/{self.event.id}/usage/', {'platform': 'ios', 'device_id': 'should-be-ignored', 'events': [
            {'name': 'tab_view', 'detail': 'map', 'count': 3},
            {'name': 'tab_view', 'detail': 'Map'},
            {'name': 'tab_view', 'detail': 'settings-hack'},
            {'name': 'location_view', 'detail': str(self.loc.id)},
            {'name': 'location_view', 'detail': str(foreign.id)},
            {'name': 'search', 'detail': 'my secret search'},
            {'name': 'made_up', 'count': 9},
            {'name': 'app_open', 'count': 10 ** 9},
            'garbage',
        ]})
        self.assertEqual(resp.json(), {'status': 'ok', 'accepted': 5})
        counts = self.counts(event=self.event)
        self.assertEqual(counts[('tab_view', 'map', 'ios')], 4)
        self.assertEqual(counts[('location_view', str(self.loc.id), 'ios')], 1)
        self.assertEqual(counts[('search', '', 'ios')], 1)  # the search text is never stored
        self.assertEqual(counts[('app_open', '', 'ios')], 500)  # capped
        self.assertNotIn(('location_view', str(foreign.id), 'ios'), counts)
        from events.models import UsageCount
        self.assertFalse(any('should-be-ignored' in str(v) for v in UsageCount.objects.values()))

    def test_bad_bodies_never_fail(self):
        for body in ({}, {'events': 'nope'}, {'events': [None, 1, []]}):
            self.assertEqual(self.post(f'/api/v2/events/{self.event.id}/usage/', body).json()['accepted'], 0)
        self.assertEqual(self.post('/api/v2/events/999999/usage/', {'events': []}).status_code, 404)

    def test_join_install_and_leave_are_counted_once(self):
        body = {'device_id': 'phone-1', 'platform': 'android'}
        self.post(f'/api/v2/events/{self.event.id}/join/', body)
        self.post(f'/api/v2/events/{self.event.id}/join/', body)  # silent rejoin on every launch: not a new join
        self.post(f'/api/v2/events/{self.event.id}/leave/', {'device_id': 'phone-1'})
        counts = self.counts(event=self.event)
        self.assertEqual(counts[('join', '', 'android')], 1)
        self.assertEqual(counts[('leave', '', 'android')], 1)
        self.assertEqual(self.counts(event=None)[('install', '', 'android')], 1)


class ContentPushTests(V2Base):
    def test_changes_schedule_one_debounced_push(self):
        from events import push
        with self.settings(CONTENT_PUSH_ENABLED=True, CONTENT_PUSH_DELAY=60, CONTENT_PUSH_MIN_INTERVAL=0), \
                patch('events.push.threading.Timer') as timer, self.captureOnCommitCallbacks(execute=True):
            self.loc.name = 'Main Stage'
            self.loc.save()
            self.item.name = 'Headliner'
            self.item.save()
        self.assertEqual(timer.call_count, 2)
        self.assertEqual(timer.return_value.cancel.call_count, 1)  # the second change replaced the first timer
        self.assertEqual(timer.call_args.args[1], push._send_content_push)
        with patch('events.push.notify_content_updated') as notify:
            push._send_content_push(self.event.id)
        notify.assert_called_once()


class DemoEventCommandTests(TestCase):
    def test_loads_every_feature_and_reruns_in_place(self):
        from django.core.management import call_command
        from io import StringIO

        from events.models import EventInvite, EventRevision, UsageCount
        from scrape.models import ChangeSet, SourcePage
        call_command('load_demo_event', '--devices', '30', stdout=StringIO())
        event = Event.objects.get(event_code='DEMO')
        self.assertEqual(event.timing, 'now')
        self.assertGreater(event.location_set.count(), 10)
        self.assertTrue(event.location_set.filter(latitude__isnull=True).exists())
        self.assertGreater(event.scheduleditem_set.count(), 30)
        self.assertTrue(event.notification_set.filter(sent=True).exists())
        self.assertTrue(event.notification_set.filter(sent=False).exists())
        self.assertEqual(set(ChangeSet.objects.filter(event=event).values_list('status', flat=True)), {'applied', 'pending'})
        self.assertTrue(EventRevision.objects.filter(event=event).exists())
        self.assertTrue(SourcePage.objects.filter(event=event).exists())
        self.assertTrue(EventInvite.objects.filter(event=event).exists())
        joins = sum(UsageCount.objects.filter(event=event, metric='join').values_list('count', flat=True))
        self.assertEqual(joins, 30)
        self.assertFalse(Device.objects.filter(event=event).exclude(push_token='').exists())  # no real pushes
        Device.objects.create(id='real-phone', event=event, platform='ios')
        call_command('load_demo_event', '--devices', '30', stdout=StringIO())
        self.assertEqual(Event.objects.get(event_code='DEMO').pk, event.pk)
        self.assertTrue(Device.objects.filter(id='real-phone', event=event).exists())
        self.assertEqual(sum(UsageCount.objects.filter(event=event, metric='join').values_list('count', flat=True)), 30)


class ReviewSupportTests(TestCase):
    def test_demo_is_reloaded_only_when_it_is_ending(self):
        from io import StringIO

        from django.core.management import call_command

        from events.management.commands.run_scheduled_tasks import keep_demo_current
        call_command('load_demo_event', '--devices', '5', stdout=StringIO())
        event = Event.objects.get(event_code='DEMO')
        self.assertFalse(keep_demo_current(StringIO()))  # happening now, ends in > 12 h
        Event.objects.filter(pk=event.pk).update(start_time=timezone.now() - timedelta(days=5),
                                                 end_time=timezone.now() - timedelta(days=3))
        self.assertTrue(keep_demo_current(StringIO()))
        event.refresh_from_db()
        self.assertEqual(event.timing, 'now')
        with self.settings(DEMO_EVENT_CODE=''):
            self.assertFalse(keep_demo_current(StringIO()))

    def test_review_account_joins_the_demo_team(self):
        from io import StringIO

        from django.core.management import call_command

        from events.models import EventMember
        call_command('load_demo_event', '--devices', '5', stdout=StringIO())
        out = StringIO()
        call_command('create_review_account', stdout=out)
        password = [l.split(':', 1)[1].strip() for l in out.getvalue().splitlines() if 'Password' in l][0]
        user = User.objects.get(email='appreview@attnd.app')
        self.assertTrue(user.check_password(password))
        self.assertEqual(EventMember.objects.get(user=user).role, 'contributor')
        call_command('create_review_account', stdout=StringIO())  # resets, doesn't duplicate
        self.assertEqual(User.objects.filter(email='appreview@attnd.app').count(), 1)
