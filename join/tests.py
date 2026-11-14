import json
from datetime import datetime, timezone as dt_timezone

from django.contrib.auth.models import User
from django.test import TestCase, Client, override_settings
from django.urls import reverse

_TEST_STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}

from events.models import Event

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
        for ua in (IOS_UA, ANDROID_UA, ANDROID_UA, DESKTOP_UA, 'Slackbot-LinkExpanding 1.0', 'facebookexternalhit/1.1'):
            self.client.get('/join/testevt/', HTTP_USER_AGENT=ua)
        counts = {u.detail: u.count for u in UsageCount.objects.filter(event=self.event, metric='link_open')}
        self.assertEqual(counts, {'ios': 1, 'android': 2, 'desktop': 1})


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

    def test_aasa_only_claims_join_links(self):
        data = self.client.get('/.well-known/apple-app-site-association').json()
        paths = [c['/'] for c in data['applinks']['details'][0]['components']]
        self.assertNotIn('/*', paths)
        self.assertIn('/join/*', paths)

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
