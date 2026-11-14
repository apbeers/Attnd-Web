"""Live integration tests against a deployed Attnd server (dev or production).

Standard library only (urllib). Not collected by `manage.py test` (no __init__.py, files named live_*.py).

    ATTND_IT_SERVER=attnd-dev-7ab893f37020.herokuapp.com ATTND_IT_KEY=<key> python3 live_tests/run.py

ATTND_IT_SERVER is a host (or an https:// URL); ATTND_IT_KEY a bearer key the server accepts: the dev static key,
or a production CI key (API-008: DEMO event only). Without both, every test is skipped.

The DEMO facts and edge cases come from the spec shared with the iOS and Android suites; the web repo's own
Django test (events/tests_demo_facts.py) keeps load_demo_event and these facts in step.
"""
import json
import os
import ssl
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

SERVER = os.getenv('ATTND_IT_SERVER', '').strip().rstrip('/')
KEY = os.getenv('ATTND_IT_KEY', '').strip()
BASE = SERVER if SERVER.startswith(('http://', 'https://')) else f'https://{SERVER}'
RUN_ID = os.getenv('GITHUB_RUN_ID') or uuid.uuid4().hex[:12]
# Marks these requests so /join/ link opens aren't counted in the DEMO event's analytics.
USER_AGENT = 'attnd-ci live-tests (web)'

IPHONE_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 attnd-ci'
ANDROID_UA = 'Mozilla/5.0 (Linux; Android 16; Pixel 9) AppleWebKit/537.36 Mobile Safari/537.36 attnd-ci'
DESKTOP_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0) AppleWebKit/605.1.15 Safari/605.1.15 attnd-ci'

DEMO_LOCATIONS = {
    'Main Stage', 'Trinity Stage', 'Panther Island Pavilion', 'Chef Demo Kitchen', 'Taco Row', 'BBQ Pit Lane',
    'Sweet Spot', 'Craft Beer Garden', 'Lemonade Stand', 'Makers Market', 'Kids Zone', 'First Aid', 'Info Booth',
    'Restrooms North', 'Restrooms South', 'Rideshare Pickup', 'Overflow Parking', 'Roaming Mariachi',
}
DEMO_CATEGORIES = {'Stage', 'Food', 'Drinks', 'Vendor', 'Family', 'Services', 'Parking'}
DEMO_NOTIFICATIONS = {'Welcome!', 'Taco Row is open', 'Shade & water', 'Set time change'}
DEMO_SCHEDULED_NOTIFICATIONS = {'Headliner soon', 'Day 2'}
CENTRE = (32.7495, -97.3316)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_CONTEXT = ssl.create_default_context(cafile=os.getenv('SSL_CERT_FILE') or None)
_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=_CONTEXT))


class Response:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    def json(self):
        return json.loads(self.body.decode())

    def header(self, name):
        return self.headers.get(name)

    def header_all(self, name):
        return self.headers.get_all(name) or []


def request(method, path, *, key=KEY, body=None, headers=None, params=None, user_agent=USER_AGENT):
    url = BASE + path
    if params:
        url += ('&' if '?' in url else '?') + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('User-Agent', user_agent)
    req.add_header('Accept', 'application/json')
    if data is not None:
        req.add_header('Content-Type', 'application/json')
    if key:
        req.add_header('Authorization', f'Bearer {key}')
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    try:
        with _OPENER.open(req, timeout=30) as resp:
            return Response(resp.status, resp.headers, resp.read())
    except urllib.error.HTTPError as err:
        return Response(err.code, err.headers, err.read())


def get(path, **kw):
    return request('GET', path, **kw)


def post(path, body, **kw):
    return request('POST', path, body=body, **kw)


def ts(value):
    return datetime.fromisoformat(value)


_DEMO = {}


class HttpAssertions(unittest.TestCase):
    def assertStatus(self, resp, status, msg=''):
        self.assertEqual(resp.status, status, f'{msg} HTTP {resp.status}: {resp.body[:300]!r}')


@unittest.skipUnless(SERVER and KEY, 'ATTND_IT_SERVER and ATTND_IT_KEY are not set')
class LiveBase(HttpAssertions):
    @property
    def demo(self):
        """The DEMO event from lookup, fetched once for the whole run."""
        if 'event' not in _DEMO:
            resp = get('/api/v2/events/lookup/', params={'q': 'DEMO'})
            if resp.status != 200:
                raise AssertionError(f'lookup DEMO on {BASE}: HTTP {resp.status} {resp.body[:200]!r}')
            _DEMO['event'] = resp.json()['event']
        return _DEMO['event']

    @property
    def demo_id(self):
        return self.demo['id']

    def device_id(self, suffix=''):
        return f'ci-web-{RUN_ID}-{suffix or uuid.uuid4().hex[:8]}'


class AppStatusTests(LiveBase):
    """1. app-status (unversioned, no auth)."""

    def test_app_status_for_each_platform(self):
        for platform in ('ios', 'android'):
            resp = get('/api/app-status/', key='', params={'platform': platform, 'version': '2.0.1', 'api': '2'})
            self.assertStatus(resp, 200, platform)
            data = resp.json()
            self.assertIn(data['update'], ('none', 'available', 'required'))
            for k in ('min_version', 'latest_version', 'update_url', 'message'):
                self.assertIn(k, data)
            self.assertTrue(data['update_url'].startswith('https://'))

    def test_retired_api_version_is_410(self):
        """7. /api/v9/… → 410 update_required."""
        resp = get('/api/v9/events/lookup/', params={'q': 'DEMO'})
        self.assertStatus(resp, 410)
        data = resp.json()
        self.assertEqual(data['error'], 'update_required')
        self.assertTrue(data['update_url'].startswith('https://'))


class LookupTests(LiveBase):
    """2. lookup."""

    def test_code_in_any_case(self):
        for q in ('DEMO', 'demo', ' Demo '):
            resp = get('/api/v2/events/lookup/', params={'q': q})
            self.assertStatus(resp, 200, q)
            self.assertEqual(resp.json()['event']['id'], self.demo_id)
            self.assertEqual(resp.json()['event']['event_code'], 'DEMO')

    def test_unknown_code_is_404(self):
        self.assertStatus(get('/api/v2/events/lookup/', params={'q': f'NO-SUCH-EVENT-{RUN_ID}'}), 404)

    def test_missing_q_is_400(self):
        self.assertStatus(get('/api/v2/events/lookup/'), 400)

    def test_missing_or_wrong_key_is_401(self):
        self.assertStatus(get('/api/v2/events/lookup/', key='', params={'q': 'DEMO'}), 401)
        self.assertStatus(get('/api/v2/events/lookup/', key='wrong-key-' + uuid.uuid4().hex, params={'q': 'DEMO'}), 401)

    def test_nearby_lists_demo(self):
        resp = get('/api/v2/events/nearby/', params={'lat': CENTRE[0], 'lng': CENTRE[1]})
        self.assertStatus(resp, 200)
        data = resp.json()
        self.assertEqual(data['count'], len(data['results']))
        self.assertIn(self.demo_id, [r['event']['id'] for r in data['results']])
        self.assertStatus(get('/api/v2/events/nearby/', params={'lat': 'x'}), 400)


class BundleTests(LiveBase):
    """3. bundle, 8. counts, and the shared DEMO facts."""

    def url(self, event_id=None):
        return f'/api/v2/events/{event_id or self.demo_id}/bundle/'

    def test_etag_and_304(self):
        first = get(self.url())
        self.assertStatus(first, 200)
        etag = first.header('ETag')
        self.assertTrue(etag, 'bundle has no ETag')
        again = get(self.url(), headers={'If-None-Match': etag})
        if again.status == 200:  # content changed between the two requests (demo reload): one retry
            etag = again.header('ETag')
            again = get(self.url(), headers={'If-None-Match': etag})
        self.assertStatus(again, 304)
        self.assertEqual(again.body, b'')
        self.assertEqual(again.header('ETag'), etag)

    def test_garbage_if_none_match_is_200(self):
        for value in ('"garbage"', '"e1-v0"', 'W/"nope"'):
            resp = get(self.url(), headers={'If-None-Match': value})
            self.assertStatus(resp, 200, value)
            self.assertIn('event', resp.json())

    def test_unknown_event_is_404(self):
        self.assertStatus(get(self.url(2_000_000_000)), 404)

    def test_bundle_without_key_is_401(self):
        self.assertStatus(get(self.url(), key=''), 401)

    def test_demo_facts(self):
        resp = get(self.url(), headers={'X-Device-Id': self.device_id('bundle')})
        self.assertStatus(resp, 200)
        b = resp.json()
        self.assertEqual(b['schema_version'], 2)
        self.assertIsInstance(b['content_version'], int)
        self.assertGreaterEqual(b['content_version'], 1)
        ev = b['event']
        self.assertEqual(ev['event_code'], 'DEMO')
        self.assertEqual(ev['name'], 'Demo: Fort Worth Food & Music Festival')
        self.assertEqual(ev['timezone'], 'America/Chicago')
        self.assertEqual(ev['status'], 'published')
        self.assertIs(ev['paid'], True)
        self.assertIs(ev['is_demo'], False)
        self.assertEqual(ev['address'], 'Panther Island, Fort Worth, TX 76102')
        self.assertAlmostEqual(ev['latitude'], CENTRE[0], delta=0.001)
        self.assertAlmostEqual(ev['longitude'], CENTRE[1], delta=0.001)
        now = datetime.now(timezone.utc)
        self.assertLessEqual(ts(ev['start_time']), now)
        self.assertGreaterEqual(ts(ev['end_time']), now)

        display = b['display']
        self.assertEqual(display.get('resolved_preset'), 'festival')
        self.assertEqual(display['secondary_pane'], 'now_next')
        self.assertLessEqual({'map', 'locations', 'schedule', 'notifications'}, set(display['tabs']))

        locs = b['locations']
        self.assertEqual(len(locs), 18)
        self.assertEqual({l['name'] for l in locs}, DEMO_LOCATIONS)
        self.assertEqual({l['category'] for l in locs}, DEMO_CATEGORIES)
        self.assertEqual(set(b['categories']), DEMO_CATEGORIES)
        pinned = [l for l in locs if l['latitude'] is not None and l['longitude'] is not None]
        self.assertEqual(len(pinned), 17)
        for l in pinned:
            self.assertAlmostEqual(l['latitude'], CENTRE[0], delta=0.01, msg=l['name'])
            self.assertAlmostEqual(l['longitude'], CENTRE[1], delta=0.01, msg=l['name'])
        mariachi = next(l for l in locs if l['name'] == 'Roaming Mariachi')
        self.assertEqual(mariachi['category'], 'Stage')
        self.assertIsNone(mariachi['latitude'])
        self.assertIsNone(mariachi['longitude'])
        main = next(l for l in locs if l['name'] == 'Main Stage')
        self.assertEqual((main['category'], main['snippet']), ('Stage', 'Headliners every night'))
        self.assertTrue(main['description'].startswith('The big one: headliners each night'))

        items = b['schedule']
        self.assertEqual(len(items), 51)
        general = [i for i in items if i['location_id'] is None]
        self.assertEqual(sorted(i['name'] for i in general), ['Gates open', 'Gates open', 'Opening ceremony'])
        self.assertTrue(all(i['tags'] == ['General'] for i in general))
        stages = {'Main Stage', 'Trinity Stage', 'Panther Island Pavilion'}
        music = [i for i in items if 'Music' in i['tags']]
        self.assertEqual(len(music), 36)
        self.assertTrue(all(i['location_name'] in stages for i in music))
        demos = [i for i in items if {'Food', 'Demo'} & set(i['tags'])]
        self.assertEqual(len(demos), 9)
        self.assertTrue(all(i['location_name'] == 'Chef Demo Kitchen' for i in demos))
        kids = [i for i in items if i['name'] == 'Face painting & games']
        self.assertEqual(len(kids), 3)
        self.assertTrue(all(i['location_name'] == 'Kids Zone' and i['tags'] == ['Family'] for i in kids))
        for i in items:
            self.assertLess(ts(i['start_time']), ts(i['end_time']), i['name'])
        self.assertEqual([i['start_time'] for i in items], sorted((i['start_time'] for i in items), key=ts))
        self.assertTrue(any(ts(i['end_time']) > now for i in items), 'nothing happening now or next')

        # The 4 sent at load time are always there. The 2 scheduled ones ("Headliner soon" 90 min after a reload,
        # "Day 2" a day after) are sent by the scheduler on a live server once due, so 4 to 6, never a future one.
        notes = b['notifications']
        titles = [n['title'] for n in notes]
        self.assertLessEqual(DEMO_NOTIFICATIONS, set(titles))
        self.assertLessEqual(set(titles), DEMO_NOTIFICATIONS | DEMO_SCHEDULED_NOTIFICATIONS)
        self.assertEqual(len(titles), len(set(titles)))
        self.assertTrue(all(ts(n['send_time']) <= now for n in notes), 'a future notification is in the bundle')
        self.assertEqual([n['send_time'] for n in notes], sorted((n['send_time'] for n in notes), key=ts, reverse=True))


class MembershipTests(LiveBase):
    """4. join / leave, 5. devices."""

    def setUp(self):
        self.device = self.device_id()

    def tearDown(self):
        if 'event' in _DEMO:  # always leave
            post(f'/api/v2/events/{self.demo_id}/leave/', {'device_id': self.device})

    def test_join_twice_then_leave_twice(self):
        body = {'device_id': self.device, 'platform': 'ios', 'app_version': '2.0.0'}
        for attempt in (1, 2):
            resp = post(f'/api/v2/events/{self.demo_id}/join/', body)
            self.assertStatus(resp, 200, f'join {attempt}')
            self.assertEqual(resp.json()['status'], 'ok')
            self.assertIsInstance(resp.json()['content_version'], int)
        for attempt in (1, 2):
            resp = post(f'/api/v2/events/{self.demo_id}/leave/', {'device_id': self.device})
            self.assertStatus(resp, 200, f'leave {attempt}')
            self.assertEqual(resp.json(), {'status': 'ok'})

    def test_join_unknown_event_is_404_and_missing_device_400(self):
        self.assertStatus(post('/api/v2/events/2000000000/join/', {'device_id': self.device}), 404)
        self.assertStatus(post(f'/api/v2/events/{self.demo_id}/join/', {'platform': 'ios'}), 400)

    def test_devices(self):
        resp = post('/api/v2/devices/', {'device_id': self.device, 'platform': 'android', 'app_version': '2.0.0',
                                         'push_token': f'ci-{uuid.uuid4()}', 'subscribed': False})
        self.assertStatus(resp, 200)
        self.assertEqual(resp.json(), {'status': 'ok', 'subscribed': False, 'event_id': None})
        self.assertStatus(post(f'/api/v2/events/{self.demo_id}/join/', {'device_id': self.device,
                                                                          'platform': 'android'}), 200)
        # No push_token: accepted, the stored token stays (not observable through the API beyond this).
        resp = post('/api/v2/devices/', {'device_id': self.device, 'platform': 'android', 'subscribed': True})
        self.assertStatus(resp, 200)
        self.assertEqual(resp.json(), {'status': 'ok', 'subscribed': True, 'event_id': self.demo_id})
        self.assertStatus(post('/api/v2/devices/', {'platform': 'android'}), 400)


class UsageTests(LiveBase):
    """6. usage: malformed input is dropped, never an error."""

    def usage(self, body):
        return post(f'/api/v2/events/{self.demo_id}/usage/', body)

    def test_valid_batch(self):
        at = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        at = at.replace(minute=at.minute - at.minute % 5).isoformat().replace('+00:00', 'Z')
        resp = self.usage({'platform': 'ios', 'events': [
            {'name': 'tab_view', 'detail': 'map', 'count': 2, 'at': at}, {'name': 'app_open'}, {'name': 'search'}]})
        self.assertStatus(resp, 200)
        self.assertEqual(resp.json(), {'status': 'ok', 'accepted': 3})

    def test_invalid_entries_are_dropped_with_200(self):
        resp = self.usage({'platform': 'ios', 'events': [
            {'name': 'not_a_metric'}, {'name': 'tab_view', 'detail': 'nope'}, {'name': 'app_open', 'count': 'x'},
            {'name': 'location_view', 'detail': '999999999'}, 'garbage']})
        self.assertStatus(resp, 200)
        self.assertEqual(resp.json(), {'status': 'ok', 'accepted': 0})
        bad_at = self.usage({'platform': 'ios', 'events': [{'name': 'app_open', 'at': 'yesterday'}]})
        self.assertStatus(bad_at, 200)  # an unparsable `at` falls back to now
        self.assertEqual(bad_at.json()['accepted'], 1)

    def test_empty_and_malformed_bodies(self):
        for body in ({'platform': 'ios', 'events': []}, {'platform': 'ios'}, {'events': 'nope'}, []):
            resp = self.usage(body)
            self.assertStatus(resp, 200, repr(body))
            self.assertEqual(resp.json(), {'status': 'ok', 'accepted': 0})

    def test_unknown_event_is_404(self):
        self.assertStatus(post('/api/v2/events/2000000000/usage/', {'events': []}), 404)


@unittest.skipUnless(SERVER, 'ATTND_IT_SERVER is not set')
class WebTests(HttpAssertions):
    """The public site, deep links, well-known files and security headers (no API key used)."""

    def test_health(self):
        resp = get('/health/', key='')
        self.assertStatus(resp, 200)
        self.assertEqual(resp.json(), {'status': 'ok', 'database': 'ok'})

    def test_homepage_and_security_headers(self):
        resp = get('/', key='', headers={'Accept': 'text/html'})
        self.assertStatus(resp, 200)
        self.assertIn(b'Attnd', resp.body)
        hsts = resp.header('Strict-Transport-Security') or ''
        self.assertIn('max-age=31536000', hsts)
        self.assertIn('includeSubDomains', hsts)
        csp = resp.header('Content-Security-Policy') or ''
        self.assertIn("default-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("object-src 'none'", csp)
        self.assertEqual(resp.header('X-Frame-Options'), 'DENY')
        self.assertEqual(resp.header('X-Content-Type-Options'), 'nosniff')

    def test_sign_in_page_cookies_are_secure(self):
        resp = get('/accounts/login/', key='', headers={'Accept': 'text/html'})
        self.assertStatus(resp, 200)
        cookies = resp.header_all('Set-Cookie')
        self.assertTrue(cookies, 'sign-in page sets no cookie (expected csrftoken)')
        for cookie in cookies:
            attrs = [a.strip().lower() for a in cookie.split(';')]
            self.assertIn('secure', attrs, cookie)
            self.assertIn('samesite=lax', attrs, cookie)
            if cookie.lower().startswith('sessionid='):
                self.assertIn('httponly', attrs, cookie)

    def test_admin_is_404(self):
        self.assertStatus(get('/admin/', key='', headers={'Accept': 'text/html'}), 404)

    def test_join_android_goes_to_play_with_referrer(self):
        resp = get('/join/DEMO/', key='', user_agent=ANDROID_UA)
        self.assertIn(resp.status, (301, 302))
        location = resp.header('Location')
        self.assertTrue(location.startswith('https://play.google.com/store/apps/details?id=com.arcu.attnd'), location)
        self.assertIn('referrer=event_code%3DDEMO', location)

    def test_join_iphone_and_desktop_show_the_landing_page(self):
        for ua in (IPHONE_UA, DESKTOP_UA):
            resp = get('/join/demo/', key='', user_agent=ua, headers={'Accept': 'text/html'})
            self.assertStatus(resp, 200, ua)
            self.assertIn(b'Fort Worth Food', resp.body)
            self.assertIn(b'DEMO', resp.body)

    def test_join_unknown_code_is_404(self):
        self.assertStatus(get(f'/join/NOPE{RUN_ID}/', key='', user_agent=DESKTOP_UA), 404)

    def test_apple_app_site_association(self):
        resp = get('/.well-known/apple-app-site-association', key='')
        self.assertStatus(resp, 200)
        self.assertTrue(resp.header('Content-Type').startswith('application/json'))
        data = resp.json()
        clips = data['appclips']['apps']
        self.assertEqual(len(clips), 1)
        self.assertTrue(clips[0].endswith('.clip'))
        details = data['applinks']['details']
        self.assertTrue(details)
        app_ids = details[0]['appIDs']
        self.assertIn(clips[0], app_ids)
        self.assertTrue(any(a.endswith('.com.arcu.Attnd') for a in app_ids), app_ids)
        self.assertNotIn('<TEAM_ID>', json.dumps(data))
        paths = [c.get('/') for c in details[0]['components']]
        self.assertIn('/join/*', paths)

    def test_assetlinks(self):
        resp = get('/.well-known/assetlinks.json', key='')
        self.assertStatus(resp, 200)
        data = resp.json()
        self.assertIsInstance(data, list)
        entry = data[0]
        self.assertIn('delegate_permission/common.handle_all_urls', entry['relation'])
        self.assertEqual(entry['target']['namespace'], 'android_app')
        self.assertEqual(entry['target']['package_name'], 'com.arcu.attnd')
        prints = entry['target']['sha256_cert_fingerprints']
        self.assertTrue(prints)
        for fp in prints:
            self.assertRegex(fp, r'^([0-9A-F]{2}:){31}[0-9A-F]{2}$')
