"""CI keys (API_CHANGES.md, API-008): live integration tests may use the DEMO event in production, nothing else."""
import json
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from events.models import Device, Event, UsageCount

CI_KEY = 'ci-key-for-tests-0123456789abcdefghijklmn'  # >= 32 characters
OTHER_CI_KEY = 'another-ci-key-for-tests-0123456789abcdefg'
STATIC_KEY = 'static-test-key'


@override_settings(API_KEY=STATIC_KEY, CI_API_KEYS=[OTHER_CI_KEY, CI_KEY], STATIC_API_KEY_ENABLED=False)
class CiKeyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('load_demo_event', '--devices', '3', stdout=StringIO())
        cls.demo = Event.objects.get(event_code='DEMO')
        cls.other = Event.objects.create(
            name='Other Fair', event_code='OTHER1', start_time=timezone.now(), timezone='America/Chicago',
            latitude=cls.demo.latitude, longitude=cls.demo.longitude, public=True)

    def setUp(self):
        self.client = Client()

    def get(self, path, key=CI_KEY, **extra):
        return self.client.get(path, secure=True, HTTP_AUTHORIZATION=f'Bearer {key}', **extra)

    def post(self, path, body, key=CI_KEY):
        return self.client.post(path, json.dumps(body), content_type='application/json', secure=True,
                                HTTP_AUTHORIZATION=f'Bearer {key}')

    # -- allowed ---------------------------------------------------------------------------------

    def test_app_status_needs_no_key(self):
        self.assertEqual(self.client.get('/api/app-status/?platform=ios&version=2.0.1&api=2', secure=True).status_code, 200)

    def test_lookup_demo_any_case(self):
        for q in ('DEMO', 'demo', ' Demo '):
            resp = self.get('/api/v2/events/lookup/', data={'q': q})
            self.assertEqual(resp.status_code, 200, q)
            self.assertEqual(resp.json()['event']['id'], self.demo.id)
        # every configured key works
        self.assertEqual(self.get('/api/v2/events/lookup/', key=OTHER_CI_KEY, data={'q': 'DEMO'}).status_code, 200)

    def test_lookup_other_event_is_404_like_unknown(self):
        other = self.get('/api/v2/events/lookup/', data={'q': 'OTHER1'})
        unknown = self.get('/api/v2/events/lookup/', data={'q': 'NOPE-NOT-AN-EVENT'})
        self.assertEqual(other.status_code, 404)
        self.assertEqual(other.json(), unknown.json())

    def test_nearby_only_lists_demo(self):
        resp = self.get('/api/v2/events/nearby/', data={'lat': '32.7495', 'lng': '-97.3316'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([r['event']['id'] for r in resp.json()['results']], [self.demo.id])
        # the static key would have seen both (sanity check of the fixture)
        with self.settings(STATIC_API_KEY_ENABLED=True):
            ids = {r['event']['id'] for r in self.get('/api/v2/events/nearby/', key=STATIC_KEY,
                                                       data={'lat': '32.7495', 'lng': '-97.3316'}).json()['results']}
        self.assertEqual(ids, {self.demo.id, self.other.id})

    def test_demo_bundle_join_leave(self):
        url = f'/api/v2/events/{self.demo.id}/bundle/'
        first = self.get(url)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(self.get(url, HTTP_IF_NONE_MATCH=first['ETag']).status_code, 304)
        joined = self.post(f'/api/v2/events/{self.demo.id}/join/', {'device_id': 'ci-ios-123', 'platform': 'ios'})
        self.assertEqual(joined.status_code, 200)
        self.assertEqual(Device.objects.get(id='ci-ios-123').event_id, self.demo.id)
        left = self.post(f'/api/v2/events/{self.demo.id}/leave/', {'device_id': 'ci-ios-123'})
        self.assertEqual(left.status_code, 200)
        self.assertIsNone(Device.objects.get(id='ci-ios-123').event_id)

    def test_device_registration_only_for_ci_ids(self):
        ok = self.post('/api/v2/devices/', {'device_id': 'ci-android-run-7', 'platform': 'android',
                                            'push_token': 'ci-token', 'subscribed': False})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()['status'], 'ok')
        refused = self.post('/api/v2/devices/', {'device_id': '6f1c2d3e-real-phone', 'platform': 'ios'})
        self.assertEqual(refused.status_code, 403)
        self.assertFalse(Device.objects.filter(id='6f1c2d3e-real-phone').exists())
        # join / leave with a real device id are refused too, so a CI key can't move real phones
        Device.objects.create(id='real-phone', event=self.demo, platform='ios')
        self.assertEqual(self.post(f'/api/v2/events/{self.demo.id}/leave/', {'device_id': 'real-phone'}).status_code, 403)
        self.assertEqual(self.post(f'/api/v2/events/{self.demo.id}/join/', {'device_id': 'real-phone'}).status_code, 403)
        self.assertEqual(Device.objects.get(id='real-phone').event_id, self.demo.id)

    def test_usage_answers_like_a_real_key_but_is_discarded(self):
        loc = self.demo.location_set.first()
        batch = {'platform': 'ios', 'events': [
            {'name': 'tab_view', 'detail': 'map', 'count': 3}, {'name': 'location_view', 'detail': str(loc.id)},
            {'name': 'app_open'}, {'name': 'not_a_metric'}, {'name': 'tab_view', 'detail': 'nope'}]}
        before = list(UsageCount.objects.order_by('pk').values_list('pk', 'count'))
        ci = self.post(f'/api/v2/events/{self.demo.id}/usage/', batch)
        self.assertEqual(ci.status_code, 200)
        self.assertEqual(ci.json(), {'status': 'ok', 'accepted': 3})
        self.assertEqual(list(UsageCount.objects.order_by('pk').values_list('pk', 'count')), before)
        with self.settings(STATIC_API_KEY_ENABLED=True):
            real = self.post(f'/api/v2/events/{self.demo.id}/usage/', batch, key=STATIC_KEY)
        self.assertEqual(real.json(), ci.json())
        self.assertNotEqual(list(UsageCount.objects.order_by('pk').values_list('pk', 'count')), before)

    def test_ci_devices_are_not_counted(self):
        before = UsageCount.objects.filter(metric__in=('install', 'join', 'leave')).count()
        self.post('/api/v2/devices/', {'device_id': 'ci-ios-a', 'platform': 'ios'})
        self.post(f'/api/v2/events/{self.demo.id}/join/', {'device_id': 'ci-ios-a', 'platform': 'ios'})
        self.post(f'/api/v2/events/{self.demo.id}/leave/', {'device_id': 'ci-ios-a'})
        self.assertEqual(UsageCount.objects.filter(metric__in=('install', 'join', 'leave')).count(), before)
        Device.objects.filter(id='ci-ios-a').update(event=self.demo, push_token='ci-token')
        self.assertFalse(Device.objects.real().filter(id='ci-ios-a').exists())
        from events.push import _recipients
        (ios_app, ios_clip), android = _recipients(self.demo, silent=True)
        self.assertNotIn('ci-token', ios_app + ios_clip + android)

    # -- refused ---------------------------------------------------------------------------------

    def test_other_events_are_404(self):
        for path in ('bundle/',):
            self.assertEqual(self.get(f'/api/v2/events/{self.other.id}/{path}').status_code, 404)
        for path in ('join/', 'leave/', 'usage/'):
            resp = self.post(f'/api/v2/events/{self.other.id}/{path}', {'device_id': 'ci-ios-1', 'events': []})
            self.assertEqual(resp.status_code, 404, path)
        self.assertFalse(Device.objects.filter(id='ci-ios-1').exists())
        self.assertEqual(self.get('/api/v2/events/999999/bundle/').status_code, 404)

    def test_everything_else_is_401(self):
        for path in ('/api/v1/events/lookup/?q=DEMO', f'/api/v1/events/{self.demo.id}/',
                     f'/api/v1/events/{self.demo.id}/locations/', '/api/v1/events/nearby_events/32.75/-97.33/'):
            self.assertEqual(self.get(path).status_code, 401, path)
        resp = self.post(f'/api/v1/events/{self.demo.id}/join/ci-ios-1/', {'platform': 'ios'})
        self.assertEqual(resp.status_code, 401)

    def test_wrong_short_or_missing_key_is_401(self):
        self.assertEqual(self.get('/api/v2/events/lookup/', key='wrong', data={'q': 'DEMO'}).status_code, 401)
        self.assertEqual(self.client.get('/api/v2/events/lookup/', {'q': 'DEMO'}, secure=True).status_code, 401)
        with self.settings(CI_API_KEYS=['short-ci-key']):
            self.assertEqual(self.get('/api/v2/events/lookup/', key='short-ci-key', data={'q': 'DEMO'}).status_code, 401)

    def test_static_key_stays_off_and_ci_key_works_without_it(self):
        self.assertEqual(self.get('/api/v2/events/lookup/', key=STATIC_KEY, data={'q': 'DEMO'}).status_code, 401)
        self.assertEqual(self.get('/api/v2/events/lookup/', data={'q': 'DEMO'}).status_code, 200)

    def test_without_a_demo_event_everything_is_404(self):
        with self.settings(DEMO_EVENT_CODE=''):
            self.assertEqual(self.get('/api/v2/events/lookup/', data={'q': 'DEMO'}).status_code, 404)
            self.assertEqual(self.get(f'/api/v2/events/{self.demo.id}/bundle/').status_code, 404)
        Event.objects.filter(pk=self.demo.pk).update(status=Event.STATUS_DRAFT)
        self.assertEqual(self.get(f'/api/v2/events/{self.demo.id}/bundle/').status_code, 404)

    def test_static_key_unaffected(self):
        """A full key still reaches every event (and its usage is recorded)."""
        with self.settings(STATIC_API_KEY_ENABLED=True):
            self.assertEqual(self.get(f'/api/v2/events/{self.other.id}/bundle/', key=STATIC_KEY).status_code, 200)
            self.assertEqual(self.post('/api/v2/devices/', {'device_id': 'real-uuid', 'platform': 'ios'},
                                       key=STATIC_KEY).status_code, 200)


class CiDeviceAudienceTests(TestCase):
    def test_console_audience_skips_ci_devices(self):
        from django.contrib.auth.models import User
        user = User.objects.create_user('o', 'o@example.com', 'pw')
        event = Event.objects.create(name='Fair', event_code='FAIR', start_time=timezone.now() + timedelta(hours=1),
                                     user=user)
        Device.objects.create(id='phone-1', event=event, platform='ios', last_seen=timezone.now())
        Device.objects.create(id='ci-ios-1', event=event, platform='ios', last_seen=timezone.now())
        self.client.force_login(user)
        resp = self.client.get(f'/console/events/{event.pk}/', secure=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['audience']['joined'], 1)
        self.assertEqual(resp.context['audience']['ios'], 1)
        dash = self.client.get('/console/', secure=True)
        self.assertEqual(next(e for e in dash.context['upcoming'] if e.pk == event.pk).n_devices, 1)
