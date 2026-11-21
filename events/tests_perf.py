"""Performance regressions: query counts of the apps' API and the key pages, the bundle cache, 304s without
building the bundle, gzip that keeps ETags usable, and phone-sized Cloudinary images."""

import gzip
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import caches
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from events import content
from events.models import Device, Event, EventMember, Location, Notification, ScheduledItem, events_for_user
from join.views import encode_share_payload

AUTH = {'HTTP_AUTHORIZATION': 'Bearer test-key'}
CLOUD = 'https://res.cloudinary.com/demo/image/upload/'


@override_settings(API_KEY='test-key')
class PerfBase(TestCase):
    def setUp(self):
        caches['default'].clear()
        caches['local'].clear()
        self.user = User.objects.create_user('o', 'o@example.com', 'pw')
        start = timezone.now() + timedelta(hours=2)
        self.event = Event.objects.create(
            name='Fair', event_code='FAIR2026', start_time=start, latitude=32.75, longitude=-97.33,
            user=self.user, public=True, paid=True, timezone='America/Chicago')
        # DEMO-sized content, so a per-row query (N+1) would show up in the counts.
        self.locations = [Location.objects.create(event=self.event, user=self.user, name=f'Spot {i}', category='Food',
                                                  latitude=32.75 + i / 1000, longitude=-97.33, tags=['food'])
                          for i in range(18)]
        for i in range(51):
            ScheduledItem.objects.create(event=self.event, user=self.user, location=self.locations[i % 18],
                                         name=f'Set {i}', start_time=start + timedelta(minutes=30 * i), tags=['music'])
        for i in range(4):
            Notification.objects.create(event=self.event, user=self.user, title=f'Update {i}', body='…',
                                        send_time=timezone.now(), sent=True)
        for i in range(30):
            Device.objects.create(id=f'device-{i}', event=self.event, platform='ios' if i % 2 else 'android',
                                  subscribed=True, push_token='t', last_seen=timezone.now())
        self.event.refresh_from_db()
        self.client = Client()
        self.url = f'/api/v2/events/{self.event.id}/bundle/'
        from events import drafts
        drafts.publish(self.event, push=False)  # §4: what attendees get (built once, here)

    def get(self, path, **extra):
        return self.client.get(path, secure=True, **AUTH, **extra)

    def assertMaxQueries(self, most):
        """Like assertNumQueries, but an upper bound (features that are switched off may need fewer)."""
        test = self

        class _Max(CaptureQueriesContext):
            def __exit__(self, *exc):
                super().__exit__(*exc)
                if exc[0] is None:
                    test.assertLessEqual(len(self.captured_queries), most,
                                         '\n'.join(q['sql'][:160] for q in self.captured_queries))
        return _Max(connection)


class BundlePerfTests(PerfBase):
    def test_first_fetch_query_count(self):
        # event, locations, schedule, notifications, item count, device touch
        with self.assertMaxQueries(7):
            resp = self.get(self.url, HTTP_X_DEVICE_ID='device-1')
        data = resp.json()
        self.assertEqual((len(data['locations']), len(data['schedule']), len(data['notifications'])), (18, 51, 4))

    def test_second_fetch_comes_from_the_cache(self):
        first = self.get(self.url)
        with self.assertNumQueries(1), patch('events.content.bundle', side_effect=AssertionError('rebuilt')):
            second = self.get(self.url)
        self.assertEqual(second.content, first.content)
        self.assertEqual(second['ETag'], first['ETag'])
        self.assertEqual(second['Content-Type'], 'application/json')

    def test_cache_follows_the_content_version(self):
        first = self.get(self.url).json()
        loc = self.locations[0]
        loc.name = 'Renamed'
        loc.save()  # bumps content_version
        from events import drafts
        drafts.publish(self.event)  # §4: attendees get it after Publish
        second = self.get(self.url).json()
        self.assertGreater(second['content_version'], first['content_version'])
        self.assertIn('Renamed', [loc['name'] for loc in second['locations']])

    def test_304_without_building_the_bundle(self):
        etag = self.get(self.url)['ETag']
        caches['default'].clear()
        caches['local'].clear()
        with self.assertNumQueries(1), patch('events.content.bundle', side_effect=AssertionError('built')):
            resp = self.get(self.url, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(resp.status_code, 304)
        self.assertEqual(resp['ETag'], etag)
        self.assertEqual(resp.content, b'')

    def test_304_and_404_for_drafts(self):
        etag = self.get(self.url)['ETag']
        Event.objects.filter(pk=self.event.pk).update(status=Event.STATUS_DRAFT)
        self.assertEqual(self.get(self.url, HTTP_IF_NONE_MATCH=etag).status_code, 404)
        self.assertEqual(self.get(self.url).status_code, 404)

    def test_last_seen_written_at_most_every_ten_minutes(self):
        old = timezone.now() - timedelta(hours=1)
        Device.objects.filter(pk='device-1').update(last_seen=old, api_version=1)
        self.get(self.url, HTTP_X_DEVICE_ID='device-1')
        device = Device.objects.get(pk='device-1')
        self.assertGreater(device.last_seen, old)
        self.assertEqual(device.api_version, 2)
        seen = device.last_seen
        etag = self.get(self.url)['ETag']
        self.get(self.url, HTTP_X_DEVICE_ID='device-1', HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(Device.objects.get(pk='device-1').last_seen, seen)  # recent: not rewritten

    def test_gzip_keeps_the_etag_usable(self):
        resp = self.get(self.url, HTTP_ACCEPT_ENCODING='gzip, deflate')
        self.assertEqual(resp['Content-Encoding'], 'gzip')
        self.assertIn('Accept-Encoding', resp['Vary'])
        body = gzip.decompress(resp.content)
        self.assertLess(len(resp.content), len(body) / 3)
        self.assertEqual(json.loads(body)['content_version'], self.event.content_version)
        self.assertFalse(resp['ETag'].startswith('W/'))  # the apps send it back as is
        again = self.get(self.url, HTTP_ACCEPT_ENCODING='gzip', HTTP_IF_NONE_MATCH=resp['ETag'])
        self.assertEqual(again.status_code, 304)

    def test_no_gzip_without_accept_encoding(self):
        resp = self.get(self.url)
        self.assertNotIn('Content-Encoding', resp)
        json.loads(resp.content)

    def test_other_api_calls(self):
        with self.assertMaxQueries(2):  # lookup
            self.assertEqual(self.get('/api/v2/events/lookup/?q=fair2026').status_code, 200)
        with self.assertMaxQueries(2):
            self.assertEqual(self.get('/api/v2/events/nearby/?lat=32.75&lng=-97.33').status_code, 200)
        body = json.dumps({'device_id': 'device-1', 'platform': 'ios', 'push_token': 'abc'})
        with self.assertNumQueries(2):
            self.client.post('/api/v2/devices/', body, content_type='application/json', secure=True, **AUTH)
        body = json.dumps({'platform': 'ios', 'events': [{'name': 'app_open', 'count': 1},
                                                         {'name': 'tab_view', 'detail': 'map', 'count': 2},
                                                         {'name': 'search', 'count': 3}]})
        with self.assertNumQueries(2):  # event + one upsert for the whole batch
            self.client.post(f'/api/v2/events/{self.event.id}/usage/', body, content_type='application/json',
                             secure=True, **AUTH)


class AppImageTests(TestCase):
    def test_cloudinary_images_are_resized_for_phones(self):
        self.assertEqual(content.app_image_url(CLOUD + 'v17/media/stage.jpg', 'location'),
                         CLOUD + 'f_auto,q_auto,c_limit,w_1200/v17/media/stage.jpg')
        self.assertEqual(content.app_image_url(CLOUD + 'media/logo.PNG', 'logo'),
                         CLOUD + 'f_auto,q_auto,c_limit,w_512/media/logo.PNG')

    def test_stable_and_idempotent(self):
        once = content.app_image_url(CLOUD + 'v1/a.webp', 'flyer')
        self.assertEqual(content.app_image_url(CLOUD + 'v1/a.webp', 'flyer'), once)
        self.assertEqual(content.app_image_url(once, 'flyer'), once)

    def test_everything_else_unchanged(self):
        for url in ['https://example.com/a.jpg', CLOUD + 'v1/flyer.pdf', CLOUD + 'v1/anim.gif',
                    'https://res.cloudinary.com/demo/raw/upload/v1/x.jpg', CLOUD + 'w_300/v1/a.jpg', '', None]:
            self.assertEqual(content.app_image_url(url, 'location'), url)

    def test_bundle_uses_them(self):
        user = User.objects.create_user('p', 'p@example.com', 'pw')
        event = Event.objects.create(name='E', event_code='IMG1', start_time=timezone.now(), user=user,
                                     logo_url=CLOUD + 'v1/logo.png')
        Location.objects.create(event=event, user=user, name='A', image_url=CLOUD + 'v1/a.jpg')
        Location.objects.create(event=event, user=user, name='B', image_url='https://maps.example.com/b.jpg')
        data = content.bundle(event)
        self.assertEqual(data['event']['logo_url'], CLOUD + 'f_auto,q_auto,c_limit,w_512/v1/logo.png')
        images = {loc['name']: loc['image_url'] for loc in data['locations']}
        self.assertEqual(images, {'A': CLOUD + 'f_auto,q_auto,c_limit,w_1200/v1/a.jpg',
                                  'B': 'https://maps.example.com/b.jpg'})


class PagePerfTests(PerfBase):
    def test_public_pages(self):
        anon = Client()
        from django.core.cache import caches
        caches['local'].clear()
        with self.assertNumQueries(4):  # the gallery (showcase, DEMO, near you, region), then cached a while
            self.assertEqual(anon.get('/home/', secure=True).status_code, 200)
        with self.assertNumQueries(0):
            self.assertEqual(anon.get('/', secure=True).status_code, 200)
        with self.assertNumQueries(0):
            self.assertEqual(anon.get('/accounts/login/', secure=True).status_code, 200)
        with self.assertNumQueries(2):  # event + the link_open count
            self.assertEqual(anon.get('/join/FAIR2026/', secure=True).status_code, 200)
        ids = list(self.event.scheduleditem_set.values_list('id', flat=True)[:5])
        with self.assertNumQueries(3):
            self.assertEqual(anon.get(f'/p/FAIR2026/{encode_share_payload(ids)}/', secure=True).status_code, 200)

    def test_html_is_gzipped(self):
        resp = Client().get('/', secure=True, HTTP_ACCEPT_ENCODING='gzip')
        self.assertEqual(resp['Content-Encoding'], 'gzip')
        self.assertIn(b'<html', gzip.decompress(resp.content))

    def test_console_pages(self):
        from console.testing import TwoFactorClient
        org = TwoFactorClient()
        org.force_login(self.user)
        # More events and devices must not add queries (the dashboard used to join every relation per event).
        for i in range(3):
            Event.objects.create(name=f'Other {i}', event_code=f'OTHER{i}', start_time=timezone.now(), user=self.user)
        bounds = {'/console/': 8,
                  f'/console/events/{self.event.id}/': 18,  # +1: the QR card's phones line (API-026)
                  f'/console/events/{self.event.id}/locations/': 11,
                  f'/console/events/{self.event.id}/schedule/': 12,
                  f'/console/events/{self.event.id}/usage/': 21}
        for url, most in bounds.items():
            with self.subTest(url=url):
                with self.assertMaxQueries(most):
                    self.assertEqual(org.get(url, secure=True).status_code, 200)

    def test_dashboard_counts(self):
        from console.testing import TwoFactorClient
        Device.objects.create(id='ci-test-1', event=self.event)  # CI devices aren't counted
        member_user = User.objects.create_user('m', 'm@example.com', 'pw')
        EventMember.objects.create(event=self.event, user=member_user, role=EventMember.ROLE_CONTRIBUTOR)
        self.assertEqual(list(events_for_user(member_user)), [self.event])
        org = TwoFactorClient()
        org.force_login(member_user)
        resp = org.get('/console/', secure=True)
        event = next(e for e in [e for s in resp.context['sections'] for e in s['events']] if e.pk == self.event.pk)
        self.assertEqual((event.n_locations, event.n_items, event.n_devices, event.n_pending), (18, 51, 30, 0))
