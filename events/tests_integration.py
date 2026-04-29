"""
Integration tests for all API endpoints using the sample_data fixture.
All requests go through the full Django test client (no RequestFactory shortcuts).
timezone.now is frozen to 2026-04-26 12:00 UTC so fixture event dates fall
inside the NearbyEventsView time window.
"""
import json
from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch

from django.test import TestCase, Client, override_settings
from django.urls import reverse

from events.models import Device

TEST_API_KEY = 'test-key'
_AUTH = {'HTTP_AUTHORIZATION': f'Bearer {TEST_API_KEY}'}
_FROZEN_NOW = datetime(2026, 4, 26, 12, 0, 0, tzinfo=dt_timezone.utc)
_SETTINGS = dict(API_KEY=TEST_API_KEY, SECURE_SSL_REDIRECT=False)

# Fort Worth TX coordinates matching event pk=1 exactly
_FW_LAT = '32.7555'
_FW_LON = '-97.3308'

# Coordinates in the middle of the Atlantic Ocean
_OCEAN_LAT = '0.0'
_OCEAN_LON = '0.0'


@override_settings(**_SETTINGS)
class NearbyEventsTests(TestCase):
    fixtures = ['sample_data']

    def setUp(self):
        self.client = Client()

    def _get(self, url, **extra):
        return self.client.get(url, **{**_AUTH, **extra})

    @patch('events.apis.timezone.now', return_value=_FROZEN_NOW)
    def test_nearby_fort_worth_returns_results(self, _):
        url = reverse('events:get_nearby_events', args=[_FW_LAT, _FW_LON])
        resp = self._get(url)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertGreater(data['count'], 0)
        self.assertGreater(len(data['results']), 0)

    @patch('events.apis.timezone.now', return_value=_FROZEN_NOW)
    def test_nearby_fort_worth_result_shape(self, _):
        url = reverse('events:get_nearby_events', args=[_FW_LAT, _FW_LON])
        resp = self._get(url)
        data = json.loads(resp.content)
        first = data['results'][0]
        self.assertIn('event', first)
        self.assertIn('distance_miles', first)
        self.assertIn('starts_in_hours', first)

    @patch('events.apis.timezone.now', return_value=_FROZEN_NOW)
    def test_nearby_ocean_returns_empty(self, _):
        url = reverse('events:get_nearby_events', args=[_OCEAN_LAT, _OCEAN_LON])
        resp = self._get(url)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data['count'], 0)
        self.assertEqual(data['results'], [])

    def test_nearby_requires_auth(self):
        url = reverse('events:get_nearby_events', args=[_FW_LAT, _FW_LON])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 401)

    def test_nearby_returns_json_content_type(self):
        url = reverse('events:get_nearby_events', args=[_FW_LAT, _FW_LON])
        resp = self._get(url)
        self.assertIn('application/json', resp['Content-Type'])


@override_settings(**_SETTINGS)
class EventDetailTests(TestCase):
    fixtures = ['sample_data']

    def setUp(self):
        self.client = Client()

    def _get(self, url):
        return self.client.get(url, **_AUTH)

    def test_event_detail_returns_correct_fields(self):
        url = reverse('events:get_single_event', args=[1])
        resp = self._get(url)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data['id'], 1)
        self.assertEqual(data['name'], 'Fort Worth Food & Music Festival')
        self.assertEqual(data['event_code'], 'FWFM2026')
        self.assertIn('description', data)
        self.assertIn('latitude', data)
        self.assertIn('longitude', data)
        self.assertIn('start_time', data)
        self.assertIn('paid', data)
        self.assertIn('address', data)

    def test_event_detail_includes_flyer_url(self):
        url = reverse('events:get_single_event', args=[1])
        resp = self._get(url)
        data = json.loads(resp.content)
        self.assertIn('flyer_url', data)
        self.assertNotIn('flyer', data)

    def test_event_detail_flyer_url_is_null_when_no_flyer(self):
        url = reverse('events:get_single_event', args=[1])
        resp = self._get(url)
        data = json.loads(resp.content)
        self.assertIsNone(data['flyer_url'])

    def test_event_detail_not_found(self):
        url = reverse('events:get_single_event', args=[9999])
        resp = self._get(url)
        self.assertEqual(resp.status_code, 404)

    def test_event_detail_requires_auth(self):
        url = reverse('events:get_single_event', args=[1])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 401)

    def test_event_detail_returns_json_content_type(self):
        url = reverse('events:get_single_event', args=[1])
        resp = self._get(url)
        self.assertIn('application/json', resp['Content-Type'])


@override_settings(**_SETTINGS)
class LocationTests(TestCase):
    fixtures = ['sample_data']

    def setUp(self):
        self.client = Client()

    def _get(self, url):
        return self.client.get(url, **_AUTH)

    def test_locations_list_returns_envelope(self):
        url = reverse('events:get_all_locations', args=[1])
        resp = self._get(url)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertIn('count', data)
        self.assertIn('results', data)

    def test_locations_list_returns_exactly_10(self):
        for event_id in [1, 2, 3]:
            with self.subTest(event_id=event_id):
                url = reverse('events:get_all_locations', args=[event_id])
                resp = self._get(url)
                data = json.loads(resp.content)
                self.assertEqual(data['count'], 10)
                self.assertEqual(len(data['results']), 10)

    def test_single_location_correct_fields(self):
        url = reverse('events:get_single_location', args=[1, 1])
        resp = self._get(url)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertIn('id', data)
        self.assertIn('name', data)
        self.assertIn('snippet', data)
        self.assertIn('description', data)
        self.assertIn('website', data)
        self.assertIn('latitude', data)
        self.assertIn('longitude', data)
        self.assertIn('address', data)

    def test_single_location_image_url_is_null(self):
        url = reverse('events:get_single_location', args=[1, 1])
        resp = self._get(url)
        data = json.loads(resp.content)
        self.assertIn('image_url', data)
        self.assertIsNone(data['image_url'])

    def test_single_location_not_found(self):
        url = reverse('events:get_single_location', args=[1, 9999])
        resp = self._get(url)
        self.assertEqual(resp.status_code, 404)

    def test_locations_list_requires_auth(self):
        url = reverse('events:get_all_locations', args=[1])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 401)

    def test_single_location_requires_auth(self):
        url = reverse('events:get_single_location', args=[1, 1])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 401)

    def test_locations_list_returns_json_content_type(self):
        url = reverse('events:get_all_locations', args=[1])
        resp = self._get(url)
        self.assertIn('application/json', resp['Content-Type'])

    def test_single_location_returns_json_content_type(self):
        url = reverse('events:get_single_location', args=[1, 1])
        resp = self._get(url)
        self.assertIn('application/json', resp['Content-Type'])


@override_settings(**_SETTINGS)
class NotificationTests(TestCase):
    fixtures = ['sample_data']

    def setUp(self):
        self.client = Client()

    def _get(self, url):
        return self.client.get(url, **_AUTH)

    def test_notifications_list_returns_envelope(self):
        url = reverse('events:get_all_notifications', args=[1])
        resp = self._get(url)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertIn('count', data)
        self.assertIn('results', data)

    def test_notifications_list_only_returns_sent(self):
        url = reverse('events:get_all_notifications', args=[1])
        resp = self._get(url)
        data = json.loads(resp.content)
        for notification in data['results']:
            self.assertTrue(notification['sent'])

    def test_notifications_list_count_matches_sent_only(self):
        # Event 1 has 2 sent notifications (pk=1 and pk=2)
        url = reverse('events:get_all_notifications', args=[1])
        resp = self._get(url)
        data = json.loads(resp.content)
        self.assertEqual(data['count'], 2)

    def test_notifications_list_ordered_newest_first(self):
        url = reverse('events:get_all_notifications', args=[1])
        resp = self._get(url)
        data = json.loads(resp.content)
        send_times = [r['send_time'] for r in data['results']]
        self.assertEqual(send_times, sorted(send_times, reverse=True))

    def test_single_notification_correct_fields(self):
        url = reverse('events:get_single_notification', args=[1, 1])
        resp = self._get(url)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertIn('id', data)
        self.assertIn('title', data)
        self.assertIn('body', data)
        self.assertIn('send_time', data)
        self.assertIn('sent', data)

    def test_single_notification_returns_correct_event(self):
        # pk=1 belongs to event 1 — should be found
        url = reverse('events:get_single_notification', args=[1, 1])
        resp = self._get(url)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data['id'], 1)

    def test_single_notification_wrong_event_returns_404(self):
        # pk=1 belongs to event 1 — looking it up under event 2 should 404
        url = reverse('events:get_single_notification', args=[2, 1])
        resp = self._get(url)
        self.assertEqual(resp.status_code, 404)

    def test_notifications_list_requires_auth(self):
        url = reverse('events:get_all_notifications', args=[1])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 401)

    def test_single_notification_requires_auth(self):
        url = reverse('events:get_single_notification', args=[1, 1])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 401)

    def test_notifications_list_returns_json_content_type(self):
        url = reverse('events:get_all_notifications', args=[1])
        resp = self._get(url)
        self.assertIn('application/json', resp['Content-Type'])


@override_settings(**_SETTINGS)
class EventLookupTests(TestCase):
    fixtures = ['sample_data']

    def setUp(self):
        self.client = Client()

    def _get(self, url, params=None):
        return self.client.get(url, params or {}, **_AUTH)

    def test_lookup_by_exact_name(self):
        url = reverse('events:event_lookup')
        resp = self._get(url, {'q': 'Fort Worth Food & Music Festival'})
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data['name'], 'Fort Worth Food & Music Festival')

    def test_lookup_by_exact_name_case_insensitive(self):
        url = reverse('events:event_lookup')
        resp = self._get(url, {'q': 'fort worth food & music festival'})
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data['name'], 'Fort Worth Food & Music Festival')

    def test_lookup_by_exact_code(self):
        url = reverse('events:event_lookup')
        resp = self._get(url, {'q': 'FWFM2026'})
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data['event_code'], 'FWFM2026')

    def test_lookup_by_exact_code_case_insensitive(self):
        url = reverse('events:event_lookup')
        resp = self._get(url, {'q': 'fwfm2026'})
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data['event_code'], 'FWFM2026')

    def test_lookup_missing_q_returns_400(self):
        url = reverse('events:event_lookup')
        resp = self._get(url)
        self.assertEqual(resp.status_code, 400)

    def test_lookup_not_found_returns_404(self):
        url = reverse('events:event_lookup')
        resp = self._get(url, {'q': 'does-not-exist-xyz'})
        self.assertEqual(resp.status_code, 404)

    def test_lookup_requires_auth(self):
        url = reverse('events:event_lookup')
        resp = self.client.get(url, {'q': 'FWFM2026'})
        self.assertEqual(resp.status_code, 401)

    def test_lookup_returns_json_content_type(self):
        url = reverse('events:event_lookup')
        resp = self._get(url, {'q': 'FWFM2026'})
        self.assertIn('application/json', resp['Content-Type'])


@override_settings(**_SETTINGS)
class FuzzySearchTests(TestCase):
    fixtures = ['sample_data']

    def setUp(self):
        self.client = Client()

    def _get(self, url, params=None):
        return self.client.get(url, params or {}, **_AUTH)

    def test_fuzzy_search_returns_public_event(self):
        url = reverse('events:get_event_by_code_or_name')
        resp = self._get(url, {'data': 'Fort Worth Food Music Festival'})
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data['event_code'], 'FWFM2026')

    def test_fuzzy_search_by_code(self):
        url = reverse('events:get_event_by_code_or_name')
        resp = self._get(url, {'data': 'FWFM2026'})
        self.assertEqual(resp.status_code, 200)

    def test_fuzzy_search_no_match_returns_404(self):
        url = reverse('events:get_event_by_code_or_name')
        resp = self._get(url, {'data': 'xyzxyzxyz'})
        self.assertEqual(resp.status_code, 404)

    def test_fuzzy_search_requires_auth(self):
        url = reverse('events:get_event_by_code_or_name')
        resp = self.client.get(url, {'data': 'FWFM2026'})
        self.assertEqual(resp.status_code, 401)

    def test_fuzzy_search_returns_json_content_type(self):
        url = reverse('events:get_event_by_code_or_name')
        resp = self._get(url, {'data': 'FWFM2026'})
        self.assertIn('application/json', resp['Content-Type'])


@override_settings(**_SETTINGS)
class JoinLeaveTests(TestCase):
    fixtures = ['sample_data']

    def setUp(self):
        self.client = Client()
        self.device_id = 'test-device-abc123'

    def _post(self, url, body=None):
        return self.client.post(
            url,
            data=json.dumps(body or {}).encode(),
            content_type='application/json',
            **_AUTH,
        )

    def test_join_returns_ok(self):
        url = reverse('events:join_event', args=[1, self.device_id])
        resp = self._post(url, {'platform': 'ios'})
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data['status'], 'ok')

    def test_join_creates_device_record(self):
        url = reverse('events:join_event', args=[1, self.device_id])
        self._post(url, {'platform': 'ios'})
        self.assertTrue(Device.objects.filter(id=self.device_id).exists())

    def test_join_associates_device_with_event(self):
        url = reverse('events:join_event', args=[1, self.device_id])
        self._post(url, {'platform': 'android'})
        device = Device.objects.get(id=self.device_id)
        self.assertEqual(device.event_id, 1)
        self.assertEqual(device.platform, 'android')

    def test_leave_returns_ok(self):
        Device.objects.create(id=self.device_id, platform='ios')
        url = reverse('events:leave_event', args=[1, self.device_id])
        resp = self._post(url)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data['status'], 'ok')

    def test_leave_clears_device_event(self):
        from events.models import Event
        event = Event.objects.get(pk=1)
        Device.objects.create(id=self.device_id, platform='ios', event=event)
        url = reverse('events:leave_event', args=[1, self.device_id])
        self._post(url)
        device = Device.objects.get(id=self.device_id)
        self.assertIsNone(device.event)

    def test_join_requires_auth(self):
        url = reverse('events:join_event', args=[1, self.device_id])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 401)

    def test_leave_requires_auth(self):
        url = reverse('events:leave_event', args=[1, self.device_id])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 401)

    def test_join_returns_json_content_type(self):
        url = reverse('events:join_event', args=[1, self.device_id])
        resp = self._post(url)
        self.assertIn('application/json', resp['Content-Type'])

    def test_leave_returns_json_content_type(self):
        Device.objects.create(id=self.device_id, platform='ios')
        url = reverse('events:leave_event', args=[1, self.device_id])
        resp = self._post(url)
        self.assertIn('application/json', resp['Content-Type'])


@override_settings(**_SETTINGS)
class ScheduleIntegrationTests(TestCase):
    """Integration tests for the schedule endpoints via the full Django test client."""
    fixtures = ['sample_data']

    def setUp(self):
        self.client = Client()

    def _get(self, url):
        return self.client.get(url, **_AUTH)

    def test_all_scheduled_items_returns_envelope(self):
        url = reverse('events:get_all_scheduled_items', args=[1])
        resp = self._get(url)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertIn('count', data)
        self.assertIn('results', data)

    def test_all_scheduled_items_count_matches_fixture(self):
        # Event 1 has 2 scheduled items in sample_data fixture
        url = reverse('events:get_all_scheduled_items', args=[1])
        resp = self._get(url)
        data = json.loads(resp.content)
        self.assertEqual(data['count'], 2)

    def test_all_scheduled_items_sorted_by_start_time(self):
        url = reverse('events:get_all_scheduled_items', args=[1])
        resp = self._get(url)
        data = json.loads(resp.content)
        times = [r['start_time'] for r in data['results']]
        self.assertEqual(times, sorted(times))

    def test_all_scheduled_items_correct_fields(self):
        url = reverse('events:get_all_scheduled_items', args=[1])
        resp = self._get(url)
        item = json.loads(resp.content)['results'][0]
        for field in ('id', 'name', 'description', 'start_time', 'location_id', 'location_name'):
            self.assertIn(field, item)

    def test_all_scheduled_items_filters_by_event(self):
        # Event 2 has 1 item; event 3 has 0
        url_e2 = reverse('events:get_all_scheduled_items', args=[2])
        url_e3 = reverse('events:get_all_scheduled_items', args=[3])
        self.assertEqual(json.loads(self._get(url_e2).content)['count'], 1)
        self.assertEqual(json.loads(self._get(url_e3).content)['count'], 0)

    def test_all_scheduled_items_empty_for_unknown_event(self):
        url = reverse('events:get_all_scheduled_items', args=[9999])
        resp = self._get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.content)['count'], 0)

    def test_all_scheduled_items_requires_auth(self):
        url = reverse('events:get_all_scheduled_items', args=[1])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 401)

    def test_all_scheduled_items_returns_json_content_type(self):
        url = reverse('events:get_all_scheduled_items', args=[1])
        resp = self._get(url)
        self.assertIn('application/json', resp['Content-Type'])

    def test_single_scheduled_item_returns_200(self):
        url = reverse('events:get_single_scheduled_item', args=[1, 1])
        resp = self._get(url)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data['id'], 1)
        self.assertEqual(data['name'], 'Opening Ceremony')

    def test_single_scheduled_item_not_found(self):
        url = reverse('events:get_single_scheduled_item', args=[1, 9999])
        resp = self._get(url)
        self.assertEqual(resp.status_code, 404)

    def test_single_scheduled_item_wrong_event_returns_404(self):
        # item pk=1 belongs to event 1; looking it up under event 2 should 404
        url = reverse('events:get_single_scheduled_item', args=[2, 1])
        resp = self._get(url)
        self.assertEqual(resp.status_code, 404)

    def test_single_scheduled_item_requires_auth(self):
        url = reverse('events:get_single_scheduled_item', args=[1, 1])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 401)


@override_settings(**_SETTINGS)
class LocationScheduledItemsIntegrationTests(TestCase):
    """Verify that location responses embed scheduled_items."""
    fixtures = ['sample_data']

    def setUp(self):
        self.client = Client()

    def _get(self, url):
        return self.client.get(url, **_AUTH)

    def test_location_list_embeds_scheduled_items(self):
        url = reverse('events:get_all_locations', args=[1])
        resp = self._get(url)
        data = json.loads(resp.content)
        # All locations have a scheduled_items key
        for loc in data['results']:
            self.assertIn('scheduled_items', loc)

    def test_location_with_items_has_correct_count(self):
        # Location pk=1 (event 1) has 2 items
        url = reverse('events:get_single_location', args=[1, 1])
        resp = self._get(url)
        data = json.loads(resp.content)
        self.assertIn('scheduled_items', data)
        self.assertEqual(len(data['scheduled_items']), 2)

    def test_location_without_items_returns_empty_list(self):
        # Location pk=2 (event 1, no items) should have empty scheduled_items
        url = reverse('events:get_single_location', args=[1, 2])
        resp = self._get(url)
        data = json.loads(resp.content)
        self.assertEqual(data['scheduled_items'], [])
