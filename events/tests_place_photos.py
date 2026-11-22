"""API-031: live Google Places photos for places with no free photo (never stored; fetched per view)."""
import json
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from attnd import google
from events import content, place_images
from events.models import Event, Location

DETAILS = {'photos': [{'name': 'places/ChIJabc/photos/P1', 'authorAttributions': [
    {'displayName': 'Ana Ruiz', 'uri': 'https://maps.google.com/maps/contrib/1'}]}]}
MEDIA = {'photoUri': 'https://lh3.googleusercontent.com/place-photos/abc=s1200'}


def _resp(body, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    return r


def google_answers(*bodies):
    return patch('httpx.request', side_effect=[_resp(b) for b in bodies])


@override_settings(GOOGLE_PLACES_API_KEY='server-key', GOOGLE_PLACE_PHOTOS=True, GOOGLE_PHOTOS_DAILY_MAX=5)
class PlacePhotoTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user('o', 'o@example.com', 'pw')
        self.event = Event.objects.create(name='Fair', event_code='FAIR9', start_time=timezone.now(), user=self.owner,
                                          status=Event.STATUS_PUBLISHED, address='Fort Worth')
        self.place = Location.objects.create(event=self.event, name='Main Stage', google_place_id='ChIJabc',
                                             google_place_key=place_images._place_key(Location(name='Main Stage')),
                                             google_photo=True)
        self.url = f'/img/places/{self.place.pk}/'

    def test_redirects_to_a_fresh_photo_every_time(self):
        with google_answers(DETAILS, MEDIA, DETAILS, MEDIA) as req:
            first = Client().get(self.url, secure=True)
            second = Client().get(self.url, secure=True)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(first['Location'], MEDIA['photoUri'])
        self.assertEqual(first['Cache-Control'], 'public, max-age=3600')
        self.assertEqual(req.call_count, 4)  # nothing kept on the server
        self.assertEqual(req.call_args_list[0].kwargs['headers']['X-Goog-FieldMask'], 'photos')
        self.assertEqual(req.call_args_list[1].kwargs['params']['skipHttpRedirect'], 'true')
        self.assertEqual(second.status_code, 302)

    def test_attribution(self):
        with google_answers(DETAILS, MEDIA):
            data = Client().get(self.url + '?format=json', secure=True).json()
        self.assertEqual(data['credit'], 'Photo: Ana Ruiz · Google Maps')
        self.assertEqual(data['attributions'][0]['uri'], 'https://maps.google.com/maps/contrib/1')

    def test_only_flagged_places_of_visible_events(self):
        with patch('httpx.request') as req:
            self.place.image_url = 'https://example.com/own.jpg'
            self.place.save()
            self.assertEqual(Client().get(self.url, secure=True).status_code, 404)  # its own image wins
            self.place.image_url = None
            self.place.save()
            Event.objects.filter(pk=self.event.pk).update(status=Event.STATUS_DRAFT)
            self.assertEqual(Client().get(self.url, secure=True).status_code, 404)
            Location.objects.filter(pk=self.place.pk).update(google_photo=False)
            Event.objects.filter(pk=self.event.pk).update(status=Event.STATUS_PUBLISHED)
            self.assertEqual(Client().get(self.url, secure=True).status_code, 404)
        req.assert_not_called()

    def test_daily_cap(self):
        with google_answers(*[DETAILS, MEDIA] * 5) as req:
            for _ in range(5):
                Client().get(self.url, secure=True)
            self.assertEqual(Client().get(self.url, secure=True).status_code, 404)
        self.assertEqual(req.call_count, 10)
        self.assertEqual(google.photo_loads_left(), 0)

    def test_bundle_points_at_it_with_a_credit(self):
        data = content.location_v2(self.place)
        self.assertTrue(data['image_url'].endswith(self.url))
        self.assertEqual(data['image_credit'], 'Photo: Google Maps')
        self.assertEqual(data['image_attribution_url'], data['image_url'] + '?format=json')
        self.place.google_photo = False
        self.assertIsNone(content.location_v2(self.place)['image_attribution_url'])

    def test_set_only_after_the_free_sources_fail(self):
        place = Location.objects.create(event=self.event, name='Taco Row', google_place_id='ChIJt',
                                        google_place_key=place_images._place_key(Location(name='Taco Row')))
        with patch.object(place_images, 'enabled', return_value=True), \
                patch.object(place_images, 'from_event_pages', return_value=[]), \
                patch.object(place_images, 'from_venue', return_value=[]), \
                patch.object(place_images, 'from_wikimedia', return_value=[]), \
                patch.object(place_images, 'budget_left', return_value=1), \
                patch.object(place_images, 'venue_website', return_value=''), patch('httpx.request') as req:
            self.assertEqual(place_images._find_for(place, pages={}), 'none')
        req.assert_not_called()
        place.refresh_from_db()
        self.assertTrue(place.google_photo)
        self.assertFalse(place.image)  # nothing of Google's stored
        with override_settings(GOOGLE_PLACE_PHOTOS=False):
            other = Location.objects.create(event=self.event, name='Gate', google_place_id='ChIJg')
            self.assertFalse(place_images.use_google_photo(other))
