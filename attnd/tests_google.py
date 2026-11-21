"""attnd/google.py: every server-side Google call cached per the Maps Platform terms (coordinates and place ids 30
days, other content a day), negative caching, quota handling, counters; venue websites looked up once per place."""
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from attnd import google
from events.models import Event, Location


def _resp(status=200, body=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else {}
    return r


PLACE = {'places': [{'id': 'ChIJabc', 'displayName': {'text': 'Main Stage'}, 'formattedAddress': '1 Main St',
                     'location': {'latitude': 32.75, 'longitude': -97.33}, 'websiteUri': 'https://stage.example/'}]}


@override_settings(GOOGLE_PLACES_API_KEY='server-key')
class GoogleTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_text_search_is_cached_and_counted(self):
        with patch('httpx.request', return_value=_resp(body=PLACE)) as req:
            a = google.text_search('Main  Stage', near=(32.751, -97.331))
            b = google.text_search('main stage', near=(32.7512, -97.3309))  # same query, same ~1 km area
        self.assertEqual(req.call_count, 1)
        self.assertEqual(a, b)
        self.assertEqual(a[0]['place_id'], 'ChIJabc')
        sent = req.call_args.kwargs
        self.assertEqual(sent['headers']['X-Goog-FieldMask'],
                         'places.id,places.displayName,places.formattedAddress,places.location')
        self.assertEqual(sent['timeout'], google.TIMEOUT)
        n = google.usage()['places_text']
        self.assertEqual((n['calls'], n['hits']), (1, 1))

    def test_coordinates_outlive_labels(self):
        """Labels and addresses are kept a day, coordinates and place ids 30 days."""
        with patch('httpx.request', return_value=_resp(body=PLACE)), patch.object(cache, 'set', wraps=cache.set) as s:
            google.text_search('Main Stage')
        ttls = {c.args[0].split(':')[1]: c.args[2] for c in s.call_args_list if c.args[0].startswith('google:t')}
        self.assertEqual(ttls, {'tg': google.COORDS_TTL, 'tc': google.CONTENT_TTL})
        self.assertLessEqual(google.COORDS_TTL, 30 * 24 * 3600)

    def test_nothing_found_is_cached_too(self):
        with patch('httpx.request', return_value=_resp(body={})) as req:
            self.assertEqual(google.text_search('Nowhere at all'), [])
            self.assertEqual(google.text_search('nowhere at all'), [])
        self.assertEqual(req.call_count, 1)
        with patch('httpx.request', return_value=_resp(body={'status': 'ZERO_RESULTS', 'results': []})) as req:
            self.assertIsNone(google.geocode('No such place'))
            self.assertIsNone(google.geocode('no  such place'))
        self.assertEqual(req.call_count, 1)

    def test_geocode(self):
        body = {'status': 'OK', 'results': [{'place_id': 'g1', 'formatted_address': '1 Main St, Fort Worth, TX',
                                             'geometry': {'location': {'lat': 32.7, 'lng': -97.3}}}]}
        with patch('httpx.request', return_value=_resp(body=body)) as req:
            a = google.geocode('1 Main St Fort Worth')
            b = google.geocode('1 main st fort worth')
        self.assertEqual(req.call_count, 1)
        self.assertEqual(a, b)
        self.assertEqual(a['lat'], 32.7)

    def test_quota_refusal_answers_nothing_until_midnight_without_retries(self):
        quota = _resp(429, {'error': {'status': 'RESOURCE_EXHAUSTED'}})
        with patch('httpx.request', return_value=quota) as req, self.assertLogs('attnd.google', 'WARNING') as logs:
            self.assertEqual(google.text_search('A'), [])
            self.assertEqual(google.text_search('B'), [])
            self.assertEqual(google.text_search('C'), [])
        self.assertEqual(req.call_count, 1)
        self.assertEqual(len([m for m in logs.output if 'quota' in m]), 1)
        self.assertTrue(google.over_quota('places_text'))
        self.assertFalse(google.over_quota('geocode'))
        self.assertEqual(google.usage()['places_text']['quota'], 3)
        # Geocoding's own quota word.
        with patch('httpx.request', return_value=_resp(200, {'status': 'OVER_QUERY_LIMIT', 'results': []})):
            self.assertIsNone(google.geocode('x'))
        self.assertTrue(google.over_quota('geocode'))

    def test_no_key_no_call(self):
        with override_settings(GOOGLE_PLACES_API_KEY=''), patch('httpx.request') as req:
            self.assertEqual(google.text_search('x'), [])
            self.assertIsNone(google.geocode('x'))
        req.assert_not_called()

    def test_console_and_import_use_the_helper(self):
        from console.places import search
        from scrape.service import _geocode, _places_text_search
        with patch('httpx.request', return_value=_resp(body=PLACE)) as req:
            self.assertEqual(search('Main Stage', near=(32.75, -97.33), limit=1)[0]['label'], 'Main Stage')
            self.assertEqual(_places_text_search('Main Stage', '', 32.75, -97.33)[:2], (32.75, -97.33))
        self.assertEqual(req.call_count, 1)  # the same query and area: one call between them
        with patch('httpx.request', return_value=_resp(body={'status': 'ZERO_RESULTS', 'results': []})):
            self.assertIsNone(_geocode('nowhere'))


@override_settings(GOOGLE_PLACES_API_KEY='server-key')
class VenueWebsiteTests(TestCase):
    def setUp(self):
        cache.clear()
        user = User.objects.create_user('o', 'o@example.com', 'pw')
        self.event = Event.objects.create(name='E', event_code='GOO1', user=user, timezone='UTC',
                                          start_time=timezone.now())
        self.loc = Location.objects.create(event=self.event, name='Main Stage', address='1 Main St',
                                           latitude=32.75, longitude=-97.33)

    def test_place_id_kept_and_reused_until_name_or_address_change(self):
        from events.place_images import venue_website
        with patch('httpx.request', return_value=_resp(body=PLACE)) as req:
            self.assertEqual(venue_website(self.loc), 'https://stage.example/')
        self.assertEqual(req.call_count, 1)
        self.loc.refresh_from_db()
        self.assertEqual(self.loc.google_place_id, 'ChIJabc')
        version = Event.objects.get(pk=self.event.pk).content_version
        cache.clear()  # even after the day's website cache is gone: Place Details for the id, never a new search
        with patch('httpx.request', return_value=_resp(body={'websiteUri': 'https://stage.example/'})) as req:
            self.assertEqual(venue_website(self.loc), 'https://stage.example/')
        self.assertIn('/v1/places/ChIJabc', req.call_args.args[1])
        self.assertEqual(Event.objects.get(pk=self.event.pk).content_version, version)  # no app bump
        self.loc.name = 'River Stage'
        self.loc.save()
        with patch('httpx.request', return_value=_resp(body={})) as req:
            self.assertEqual(venue_website(self.loc), '')
        self.assertIn('searchText', req.call_args.args[1])
        self.loc.refresh_from_db()
        self.assertEqual(self.loc.google_place_id, '')  # looked up: nothing found
        with patch('httpx.request') as req:
            self.assertEqual(venue_website(self.loc), '')
        req.assert_not_called()

    def test_quota_does_not_store_nothing_found(self):
        from events.place_images import venue_website
        with patch('httpx.request', return_value=_resp(429, {'error': {'status': 'RESOURCE_EXHAUSTED'}})):
            self.assertEqual(venue_website(self.loc), '')
        self.loc.refresh_from_db()
        self.assertEqual(self.loc.google_place_key, '')

    def test_backfills_skip_locations_already_looked_at(self):
        from events import place_images
        from events.models import PlaceImageAttempt
        PlaceImageAttempt.objects.create(location=self.loc, event=self.event, result='none')
        with patch('events.place_images.enabled', return_value=True), \
                patch('events.place_images.from_venue') as venue:
            self.assertEqual(place_images.find_for(self.loc), 'skipped')
        venue.assert_not_called()


class DiagnosticsCounterTests(TestCase):
    def test_counters_on_the_endpoints_page(self):
        from console.testing import TwoFactorClient
        cache.clear()
        google._count('geocode', 'calls', 4)
        staff = User.objects.create_user('s', 's@example.com', 'pw', is_staff=True)
        client = TwoFactorClient()
        client.force_login(staff)
        html = client.get('/console/diagnostics/endpoints/', secure=True).content.decode()
        self.assertIn('Google APIs', html)
        self.assertRegex(html, r'geocode</td>\s*<td class="num">4</td>')
