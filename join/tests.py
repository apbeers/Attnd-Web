import json
from datetime import datetime, timezone as dt_timezone

from django.contrib.auth.models import User
from django.test import TestCase, Client, override_settings
from django.urls import reverse

_TEST_STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}

from events.models import Event, Location, ScheduledItem
from join.views import decode_share_payload, encode_share_payload

_EVENT_BASE = dict(
    description='A description',
    latitude='32.75',
    longitude='-97.35',
    start_time=datetime(2025, 6, 1, 12, 0, tzinfo=dt_timezone.utc),
    address='123 Main St',
    order_id='',
    public=True,
)

IOS_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15'
ANDROID_UA = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36'
DESKTOP_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES=_TEST_STORAGES, IOS_APP_STORE_URL="https://apps.apple.com/app/id123")
class JoinEventRedirectTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('organizer', 'org@test.com', 'pass')
        cls.event = Event.objects.create(
            name='Test Event', event_code='testevt', user=cls.user, **_EVENT_BASE
        )

    def setUp(self):
        self.client = Client()

    def test_ios_gets_the_app_clip_banner_not_a_redirect(self):
        response = self.client.get(
            reverse('join:join_event', args=['testevt']),
            HTTP_USER_AGENT=IOS_UA,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="apple-itunes-app" content="app-id=123, app-clip-bundle-id=')
        self.assertContains(response, 'app-clip-display=card')
        self.assertContains(response, 'https://apps.apple.com/app/id123')

    def test_android_redirects_to_play_store(self):
        response = self.client.get(
            reverse('join:join_event', args=['testevt']),
            HTTP_USER_AGENT=ANDROID_UA,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('play.google.com', response['Location'])

    def test_android_redirect_includes_event_code_as_referrer(self):
        response = self.client.get(
            reverse('join:join_event', args=['testevt']),
            HTTP_USER_AGENT=ANDROID_UA,
        )
        self.assertIn('event_code', response['Location'])
        self.assertIn('testevt', response['Location'])

    def test_unknown_ua_shows_landing_page(self):
        response = self.client.get(
            reverse('join:join_event', args=['testevt']),
            HTTP_USER_AGENT=DESKTOP_UA,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test Event')
        self.assertContains(response, 'testevt')

    def test_landing_page_has_both_store_buttons(self):
        response = self.client.get(
            reverse('join:join_event', args=['testevt']),
            HTTP_USER_AGENT=DESKTOP_UA,
        )
        self.assertContains(response, 'App Store')
        self.assertContains(response, 'Google Play')

    def test_invalid_event_code_returns_404(self):
        response = self.client.get(
            reverse('join:join_event', args=['no-such-event']),
            HTTP_USER_AGENT=DESKTOP_UA,
        )
        self.assertEqual(response.status_code, 404)

    def test_redirect_has_no_cache_header(self):
        response = self.client.get(
            reverse('join:join_event', args=['testevt']),
            HTTP_USER_AGENT=IOS_UA,
        )
        self.assertEqual(response['Cache-Control'], 'no-cache')

    def test_codes_are_case_insensitive_and_drafts_are_hidden(self):
        self.assertEqual(self.client.get('/join/TESTEVT/', HTTP_USER_AGENT=DESKTOP_UA).status_code, 200)
        Event.objects.filter(pk=self.event.pk).update(status=Event.STATUS_DRAFT)
        response = self.client.get('/join/testevt/', HTTP_USER_AGENT=DESKTOP_UA)
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "couldn't find that event", status_code=404)

    def test_desktop_page_shows_a_qr_code(self):
        response = self.client.get('/join/testevt/', HTTP_USER_AGENT=DESKTOP_UA)
        self.assertContains(response, '/join/testevt/qr.svg')
        svg = self.client.get('/join/testevt/qr.svg')
        self.assertEqual(svg['Content-Type'], 'image/svg+xml')
        self.assertIn(b'<svg', svg.content)

    def test_opens_are_counted_by_device_and_bots_are_ignored(self):
        from events.models import UsageCount
        for ua in (IOS_UA, ANDROID_UA, ANDROID_UA, DESKTOP_UA, 'Slackbot-LinkExpanding 1.0', 'facebookexternalhit/1.1',
                   IOS_UA + ' attnd-ci', ANDROID_UA + ' attnd-ci'):  # live integration tests aren't counted
            self.client.get('/join/testevt/', HTTP_USER_AGENT=ua)
        counts = {u.detail: u.count for u in UsageCount.objects.filter(event=self.event, metric='link_open')}
        self.assertEqual(counts, {'ios': 1, 'android': 2, 'desktop': 1})


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES=_TEST_STORAGES, IOS_APP_STORE_URL="https://apps.apple.com/app/id123")
class SharedPlanLinkTests(TestCase):
    """API-015: /p/<code>/<payload>/ behaves like /join/<code>/ and lists the shared plan on the web page."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('organizer', 'org@test.com', 'pass')
        cls.event = Event.objects.create(name='Share Fest', event_code='SHARE1', user=cls.user,
                                         timezone='America/Chicago', **_EVENT_BASE)
        cls.other = Event.objects.create(name='Other Fest', event_code='OTHER1', user=cls.user, **_EVENT_BASE)
        stage = Location.objects.create(event=cls.event, name='Main Stage')
        hidden_loc = Location.objects.create(event=cls.event, name='Backstage', hidden=True)
        # 7:30 PM – 8:30 PM Saturday Nov 14 2026 in Chicago (UTC-6)
        cls.late = ScheduledItem.objects.create(
            event=cls.event, location=stage, name='The Prairie Lights',
            start_time=datetime(2026, 11, 15, 1, 30, tzinfo=dt_timezone.utc),
            end_time=datetime(2026, 11, 15, 2, 30, tzinfo=dt_timezone.utc))
        cls.early = ScheduledItem.objects.create(
            event=cls.event, name='Gates open',
            start_time=datetime(2026, 11, 14, 16, 0, tzinfo=dt_timezone.utc))
        cls.hidden = ScheduledItem.objects.create(
            event=cls.event, name='Secret set', hidden=True,
            start_time=datetime(2026, 11, 14, 18, 0, tzinfo=dt_timezone.utc))
        cls.at_hidden_loc = ScheduledItem.objects.create(
            event=cls.event, location=hidden_loc, name='Crew dinner',
            start_time=datetime(2026, 11, 14, 23, 0, tzinfo=dt_timezone.utc))
        cls.foreign = ScheduledItem.objects.create(
            event=cls.other, name='Elsewhere act', start_time=datetime(2026, 11, 14, 20, 0, tzinfo=dt_timezone.utc))

    def payload(self, ids=None, day='2026-11-14'):
        ids = ids if ids is not None else [self.late.pk, self.early.pk]
        return encode_share_payload(ids, day)

    def test_payload_codec_round_trip_and_known_string(self):
        self.assertEqual(encode_share_payload([101, 114], '2026-11-14'),
                         'eyJ2IjoxLCJpZHMiOlsxMDEsMTE0XSwiZCI6IjIwMjYtMTEtMTQifQ')
        self.assertEqual(encode_share_payload([3]), 'eyJ2IjoxLCJpZHMiOlszXX0')
        from datetime import date
        self.assertEqual(decode_share_payload('eyJ2IjoxLCJpZHMiOlsxMDEsMTE0XSwiZCI6IjIwMjYtMTEtMTQifQ'),
                         ([101, 114], date(2026, 11, 14)))
        self.assertEqual(decode_share_payload('eyJ2IjoxLCJpZHMiOlszXX0='), ([3], None))  # padding tolerated

    def test_invalid_payloads(self):
        import base64
        def b64(raw):
            return base64.urlsafe_b64encode(raw.encode()).decode().rstrip('=')
        for bad in ('', 'not base64!', '***', b64('[1,2]'), b64('{"v":2,"ids":[1]}'), b64('{"ids":[1]}'),
                    b64('{"v":1,"ids":"1"}'), b64('not json'), 'A' * 1501):
            self.assertIsNone(decode_share_payload(bad), bad)
        # Lenient inside a v1 payload: junk ids skipped, "s12" accepted, a bad day ignored.
        self.assertEqual(decode_share_payload(b64('{"v":1,"ids":[5,"s12","x",true,-1,5,null],"d":"14/11"}')),
                         ([5, 12], None))

    def test_desktop_lists_the_plan_in_the_event_time_zone(self):
        ids = [self.late.pk, self.early.pk, self.hidden.pk, self.at_hidden_loc.pk, self.foreign.pk, 999999]
        response = self.client.get(f'/p/SHARE1/{self.payload(ids)}/', HTTP_USER_AGENT=DESKTOP_UA)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-cache')
        html = response.content.decode()
        self.assertIn('Shared plan · Saturday, November 14', html)
        self.assertIn('Sat 10:00 AM', html)
        self.assertIn('Sat 7:30 PM – 8:30 PM', html)
        self.assertIn('Main Stage', html)
        self.assertLess(html.index('Gates open'), html.index('The Prairie Lights'))  # time order
        for skipped in ('Secret set', 'Crew dinner', 'Elsewhere act'):
            self.assertNotIn(skipped, html)
        self.assertContains(response, 'App Store')
        self.assertContains(response, 'Google Play')
        self.assertContains(response, f'/p/SHARE1/{self.payload(ids)}/qr.svg')
        self.assertContains(response, 'name="robots" content="noindex"')

    def test_without_trailing_slash_and_lower_case_code(self):
        response = self.client.get(f'/p/share1/{self.payload()}', HTTP_USER_AGENT=DESKTOP_UA)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'The Prairie Lights')

    def test_android_redirects_to_play_with_the_event_code(self):
        response = self.client.get(f'/p/SHARE1/{self.payload()}/', HTTP_USER_AGENT=ANDROID_UA)
        self.assertEqual(response.status_code, 302)
        self.assertIn('play.google.com', response['Location'])
        self.assertIn('referrer=event_code%3DSHARE1', response['Location'])

    def test_iphone_gets_the_app_clip_banner_and_the_items(self):
        response = self.client.get(f'/p/SHARE1/{self.payload()}/', HTTP_USER_AGENT=IOS_UA)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'app-clip-bundle-id=')
        self.assertContains(response, 'https://apps.apple.com/app/id123')
        self.assertContains(response, 'The Prairie Lights')

    def test_invalid_payload_falls_back_to_the_join_page(self):
        response = self.client.get('/p/SHARE1/not-a-plan/', HTTP_USER_AGENT=DESKTOP_UA)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Shared plan')
        self.assertContains(response, '/join/SHARE1/qr.svg')
        self.assertEqual(self.client.get('/p/SHARE1/not-a-plan/', HTTP_USER_AGENT=ANDROID_UA).status_code, 302)

    def test_unknown_or_draft_event_is_the_join_404(self):
        response = self.client.get(f'/p/NOPE/{self.payload()}/', HTTP_USER_AGENT=DESKTOP_UA)
        self.assertContains(response, "couldn't find that event", status_code=404)
        Event.objects.filter(pk=self.event.pk).update(status=Event.STATUS_DRAFT)
        self.assertEqual(self.client.get(f'/p/SHARE1/{self.payload()}/', HTTP_USER_AGENT=DESKTOP_UA).status_code, 404)

    def test_no_known_items_still_shows_the_event(self):
        response = self.client.get(f'/p/SHARE1/{self.payload([999999])}/', HTTP_USER_AGENT=DESKTOP_UA)
        self.assertContains(response, 'no longer on the schedule')
        self.assertContains(response, 'Share Fest')

    def test_qr_code_encodes_the_share_link(self):
        svg = self.client.get(f'/p/SHARE1/{self.payload()}/qr.svg')
        self.assertEqual(svg['Content-Type'], 'image/svg+xml')
        self.assertIn(b'<svg', svg.content)
        self.assertEqual(self.client.get('/p/SHARE1/not-a-plan/qr.svg').status_code, 404)
        long_ids = list(range(100000, 100000 + 150))  # a payload close to the 1,500 character limit
        payload = encode_share_payload(long_ids)
        self.assertLessEqual(len(payload), 1500)
        self.assertEqual(self.client.get(f'/p/SHARE1/{payload}/qr.svg').status_code, 200)

    def test_opens_are_counted_like_join_links(self):
        from events.models import UsageCount
        for ua in (IOS_UA, ANDROID_UA, DESKTOP_UA, 'Slackbot-LinkExpanding 1.0', DESKTOP_UA + ' attnd-ci'):
            self.client.get(f'/p/SHARE1/{self.payload()}/', HTTP_USER_AGENT=ua)
        counts = {u.detail: u.count for u in UsageCount.objects.filter(event=self.event, metric='link_open')}
        self.assertEqual(counts, {'ios': 1, 'android': 1, 'desktop': 1})


@override_settings(SECURE_SSL_REDIRECT=False, IOS_APP_STORE_URL="https://apps.apple.com/app/id123")
class WellKnownTests(TestCase):

    def setUp(self):
        self.client = Client()

    def test_aasa_returns_200(self):
        response = self.client.get('/.well-known/apple-app-site-association')
        self.assertEqual(response.status_code, 200)

    def test_aasa_content_type_is_json(self):
        response = self.client.get('/.well-known/apple-app-site-association')
        self.assertEqual(response['Content-Type'], 'application/json')

    def test_aasa_has_no_cache_header(self):
        response = self.client.get('/.well-known/apple-app-site-association')
        self.assertEqual(response['Cache-Control'], 'no-cache')

    def test_aasa_only_claims_join_and_shared_plan_links(self):
        data = self.client.get('/.well-known/apple-app-site-association').json()
        detail = data['applinks']['details'][0]
        components = detail['components']
        paths = [c['/'] for c in components]
        self.assertNotIn('/*', paths)
        self.assertIn('/join/*', paths)
        self.assertIn('/p/*', paths)  # API-015
        # The QR images stay on the web: their exclusions come before the wildcards that would match them.
        self.assertLess(paths.index('/p/*/*/qr.svg'), paths.index('/p/*'))
        self.assertTrue(components[paths.index('/p/*/*/qr.svg')].get('exclude'))
        self.assertTrue(any(a.endswith('.clip') for a in detail['appIDs']))  # the App Clip opens them too

    def test_aasa_structure(self):
        response = self.client.get('/.well-known/apple-app-site-association')
        data = json.loads(response.content)
        self.assertIn('appclips', data)
        self.assertIn('applinks', data)
        detail = data['applinks']['details'][0]
        self.assertIn('appIDs', detail)
        # Modern format uses 'components' (list of path dicts) not 'paths'
        components = detail.get('components', [])
        paths = [c.get('/') for c in components]
        self.assertIn('/join/*', paths)

    def test_assetlinks_returns_200(self):
        response = self.client.get('/.well-known/assetlinks.json')
        self.assertEqual(response.status_code, 200)

    def test_assetlinks_is_valid_json_list(self):
        response = self.client.get('/.well-known/assetlinks.json')
        data = json.loads(response.content)
        self.assertIsInstance(data, list)
        self.assertEqual(data[0]['relation'], ['delegate_permission/common.handle_all_urls'])
        self.assertIn('android_app', data[0]['target']['namespace'])
