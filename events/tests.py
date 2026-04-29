import io
import json
from datetime import datetime, timezone as dt_timezone

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client, RequestFactory, override_settings
from django.urls import reverse
from PIL import Image

from events.apis import (
    NearbyEventsView, EventByCodeOrNameView, AllLocationsView,
    PushNotificationsUnSubscribe, PushNotificationsSubscribe,
    JoinEventView, LeaveEventView, EventLookupView,
    AllScheduledItemsView, SingleScheduledItemView,
    SingleLocationView,
)
from events.models import Device, Event, Location, ScheduledItem

TEST_API_KEY = 'test-key'
AUTH_HEADER = {'HTTP_AUTHORIZATION': f'Bearer {TEST_API_KEY}'}


def make_image():
    """Return a 1×1 JPEG as a SimpleUploadedFile."""
    buf = io.BytesIO()
    Image.new('RGB', (1, 1)).save(buf, format='JPEG')
    buf.seek(0)
    return SimpleUploadedFile('img.jpg', buf.read(), content_type='image/jpeg')


# ---------------------------------------------------------------------------
# API view tests
# ---------------------------------------------------------------------------

@override_settings(API_KEY=TEST_API_KEY)
class EventsTestCase(TestCase):
    fixtures = ['dump.json']

    def test_all_locations(self):
        event_id = 3

        request = RequestFactory().get(reverse('events:get_all_locations', args=[event_id]), **AUTH_HEADER)
        response = AllLocationsView.as_view()(request, event_id)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertIn('results', data)
        self.assertIn('count', data)

    def test_get_nearby_events(self):
        user_latitude = 32.668224
        user_longitude = -97.419591

        request = RequestFactory().get(reverse('events:get_nearby_events', args=[user_latitude, user_longitude]), **AUTH_HEADER)
        response = NearbyEventsView.as_view()(request, user_latitude, user_longitude)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertIn('results', data)
        self.assertIn('count', data)

    def test_event_by_code_or_name(self):
        data = 'my event'

        request = RequestFactory().get(reverse('events:get_event_by_code_or_name'), **AUTH_HEADER)
        request.GET = {'data': data}
        response = EventByCodeOrNameView.as_view()(request)
        self.assertEqual(response.status_code, 200)


@override_settings(API_KEY=TEST_API_KEY)
class EventLookupTestCase(TestCase):
    fixtures = ['dump.json']

    def test_lookup_by_exact_name(self):
        request = RequestFactory().get(reverse('events:event_lookup'), **AUTH_HEADER)
        request.GET = {'q': 'event 1'}
        response = EventLookupView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['name'], 'event 1')

    def test_lookup_by_exact_code(self):
        request = RequestFactory().get(reverse('events:event_lookup'), **AUTH_HEADER)
        request.GET = {'q': 'event1code'}
        response = EventLookupView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['event_code'], 'event1code')

    def test_lookup_case_insensitive(self):
        request = RequestFactory().get(reverse('events:event_lookup'), **AUTH_HEADER)
        request.GET = {'q': 'EVENT1CODE'}
        response = EventLookupView.as_view()(request)
        self.assertEqual(response.status_code, 200)

    def test_lookup_missing_q(self):
        request = RequestFactory().get(reverse('events:event_lookup'), **AUTH_HEADER)
        request.GET = {}
        response = EventLookupView.as_view()(request)
        self.assertEqual(response.status_code, 400)

    def test_lookup_not_found(self):
        request = RequestFactory().get(reverse('events:event_lookup'), **AUTH_HEADER)
        request.GET = {'q': 'does-not-exist'}
        response = EventLookupView.as_view()(request)
        self.assertEqual(response.status_code, 404)

    def test_response_includes_flyer_url_key(self):
        request = RequestFactory().get(reverse('events:event_lookup'), **AUTH_HEADER)
        request.GET = {'q': 'event 1'}
        response = EventLookupView.as_view()(request)
        data = json.loads(response.content)
        self.assertIn('flyer_url', data)
        self.assertNotIn('flyer', data)

    def test_response_includes_created_at(self):
        request = RequestFactory().get(reverse('events:event_lookup'), **AUTH_HEADER)
        request.GET = {'q': 'event 1'}
        response = EventLookupView.as_view()(request)
        data = json.loads(response.content)
        self.assertIn('created_at', data)
        self.assertIsInstance(data['created_at'], str)
        self.assertGreater(len(data['created_at']), 0)


@override_settings(API_KEY=TEST_API_KEY)
class JoinEventTestCase(TestCase):
    fixtures = ['dump.json']

    def setUp(self) -> None:
        self.device_id = 'mock_device_id'
        self.event_id = 1

    def test_join_new_device(self):
        request = RequestFactory().post(reverse('events:join_event', args=[self.event_id, self.device_id]), **AUTH_HEADER)
        response = JoinEventView.as_view()(request, self.event_id, self.device_id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Device.objects.all().count(), 1)
        event = Event.objects.filter(id=self.event_id).get()
        device = Device.objects.filter(id=self.device_id).get()
        self.assertEqual(device.event, event)

    def test_join_existing_device(self):
        Device.objects.create(id=self.device_id)

        request = RequestFactory().post(reverse('events:join_event', args=[self.event_id, self.device_id]), **AUTH_HEADER)
        response = JoinEventView.as_view()(request, self.event_id, self.device_id)

        self.assertEqual(response.status_code, 200)
        event = Event.objects.filter(id=self.event_id).get()
        device = Device.objects.filter(id=self.device_id).get()
        self.assertEqual(device.event, event)

    def test_join_sets_platform_from_json_body(self):
        body = json.dumps({'platform': 'ios'}).encode()
        request = RequestFactory().post(
            reverse('events:join_event', args=[self.event_id, self.device_id]),
            data=body,
            content_type='application/json',
            **AUTH_HEADER,
        )
        response = JoinEventView.as_view()(request, self.event_id, self.device_id)
        self.assertEqual(response.status_code, 200)
        device = Device.objects.get(id=self.device_id)
        self.assertEqual(device.platform, 'ios')


@override_settings(API_KEY=TEST_API_KEY)
class LeaveEventTestCase(TestCase):
    fixtures = ['dump.json']

    def setUp(self) -> None:
        self.device_id = 'mock_device_id'
        self.event_id = 1
        Event.objects.filter(id=self.event_id).get()
        Device.objects.create(id=self.device_id)

    def test_leave_event(self):
        request = RequestFactory().post(reverse('events:leave_event', args=[self.event_id, self.device_id]), **AUTH_HEADER)
        response = LeaveEventView.as_view()(request, self.event_id, self.device_id)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(Device.objects.filter(id=self.device_id).get().event)


@override_settings(API_KEY=TEST_API_KEY)
class PushNotificationsTestCase(TestCase):
    fixtures = ['dump.json']

    def setUp(self) -> None:
        self.device_id = 'mock_device_id'
        self.event_id = 1
        self.event = Event.objects.filter(id=self.event_id).get()
        Device.objects.create(id=self.device_id, platform='android', event=self.event, subscribed=False)

    def test_subscribe(self):
        request = RequestFactory().post(reverse('events:push_notifications_subscribe', args=[self.device_id]), **AUTH_HEADER)
        response = PushNotificationsSubscribe.as_view()(request, self.device_id)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Device.objects.filter(id=self.device_id).get().subscribed)

    def test_unsubscribe(self):
        request = RequestFactory().post(reverse('events:push_notifications_unsubscribe', args=[self.device_id]), **AUTH_HEADER)
        response = PushNotificationsUnSubscribe.as_view()(request, self.device_id)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Device.objects.filter(id=self.device_id).get().subscribed)


# ---------------------------------------------------------------------------
# Multi-tenant Django admin tests
# ---------------------------------------------------------------------------

_EVENT_BASE = dict(
    description='A description',
    latitude='32.75',
    longitude='-97.35',
    start_time=datetime(2025, 6, 1, 12, 0, tzinfo=dt_timezone.utc),
    flyer='placeholder.pdf',
    address='123 Main St',
    order_id='TEST-ORDER',
)

# Store files in memory so tests never touch the filesystem.
_IN_MEMORY_STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}


@override_settings(STORAGES=_IN_MEMORY_STORAGES, SECURE_SSL_REDIRECT=False)
class MultiTenantAdminTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser('admin', 'admin@test.com', 'adminpass')
        cls.customer1 = User.objects.create_user('customer1', 'c1@test.com', 'pass', is_staff=True)
        cls.customer2 = User.objects.create_user('customer2', 'c2@test.com', 'pass', is_staff=True)

        cls.event1 = Event.objects.create(name='Customer1 Event', event_code='c1evt', user=cls.customer1, **_EVENT_BASE)
        cls.event2 = Event.objects.create(name='Customer2 Event', event_code='c2evt', user=cls.customer2, **_EVENT_BASE)

    def setUp(self):
        self.client = Client()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _event_post_data(self, **overrides):
        """Minimal valid POST data for the Event admin add form.
        event_code is no longer a form field — it's auto-generated on save."""
        data = {
            'name': 'New Event',
            'description': 'A description',
            'latitude': '32.75',
            'longitude': '-97.35',
            'start_time_0': '2025-06-01',
            'start_time_1': '12:00:00',
            'address': '123 Main St',
            'order_id': 'TEST-ORDER',
            'flyer': SimpleUploadedFile('test.pdf', b'%PDF-1.4'),
            # Empty inline management forms (required by the formset machinery)
            'location_set-TOTAL_FORMS': '0',
            'location_set-INITIAL_FORMS': '0',
            'location_set-MIN_NUM_FORMS': '0',
            'location_set-MAX_NUM_FORMS': '1000',
            'notification_set-TOTAL_FORMS': '0',
            'notification_set-INITIAL_FORMS': '0',
            'notification_set-MIN_NUM_FORMS': '0',
            'notification_set-MAX_NUM_FORMS': '1000',
        }
        data.update(overrides)
        return data

    def _location_post_data(self, event_pk, **overrides):
        """Minimal valid POST data for the Location admin add form."""
        data = {
            'name': 'Test Location',
            'snippet': 'A snippet',
            'description': 'A description',
            'website': '',
            'image': make_image(),
            'latitude': '32.75',
            'longitude': '-97.35',
            'address': '123 Main St',
            'event': event_pk,
        }
        data.update(overrides)
        return data

    # ------------------------------------------------------------------
    # Admin login
    # ------------------------------------------------------------------

    def test_customer1_can_access_admin(self):
        self.client.force_login(self.customer1)
        response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 200)

    # ------------------------------------------------------------------
    # Sidebar visibility — has_module_perms
    # ------------------------------------------------------------------

    def test_customer1_can_see_events_section_in_sidebar(self):
        self.client.force_login(self.customer1)
        response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # The Events app section should be visible (has_module_permission returns True)
        self.assertIn('/admin/events/event/', content)
        self.assertIn('/admin/events/location/', content)
        self.assertIn('/admin/events/notification/', content)

    def test_customer1_cannot_see_devices_or_users_in_sidebar(self):
        self.client.force_login(self.customer1)
        response = self.client.get('/admin/')
        content = response.content.decode()
        self.assertNotIn('/admin/events/device/', content)
        self.assertNotIn('/admin/auth/user/', content)

    # ------------------------------------------------------------------
    # Event changelist visibility
    # ------------------------------------------------------------------

    def test_customer1_changelist_shows_own_event(self):
        self.client.force_login(self.customer1)
        response = self.client.get(reverse('admin:events_event_changelist'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Customer1 Event')

    def test_customer1_changelist_excludes_customer2_event(self):
        self.client.force_login(self.customer1)
        response = self.client.get(reverse('admin:events_event_changelist'))
        self.assertNotContains(response, 'Customer2 Event')

    def test_superuser_changelist_shows_all_events(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('admin:events_event_changelist'))
        self.assertContains(response, 'Customer1 Event')
        self.assertContains(response, 'Customer2 Event')

    # ------------------------------------------------------------------
    # Adding events
    # ------------------------------------------------------------------

    def test_customer1_can_add_event_auto_assigned_to_them(self):
        self.client.force_login(self.customer1)
        response = self.client.post(
            reverse('admin:events_event_add'),
            self._event_post_data(name='C1 New Event'),
        )
        self.assertEqual(response.status_code, 302)
        event = Event.objects.get(name='C1 New Event')
        self.assertEqual(event.user, self.customer1)

    def test_event_code_auto_generated_on_admin_add(self):
        self.client.force_login(self.customer1)
        self.client.post(
            reverse('admin:events_event_add'),
            self._event_post_data(name='Auto Code Event'),
        )
        event = Event.objects.get(name='Auto Code Event')
        self.assertTrue(event.event_code, 'event_code should not be empty')
        self.assertEqual(len(event.event_code), 8)

    def test_two_events_can_share_the_same_name(self):
        self.client.force_login(self.customer1)
        self.client.post(
            reverse('admin:events_event_add'),
            self._event_post_data(name='Duplicate Name Event'),
        )
        self.client.post(
            reverse('admin:events_event_add'),
            self._event_post_data(name='Duplicate Name Event'),
        )
        # Both should be created (name uniqueness is gone)
        self.assertEqual(Event.objects.filter(name='Duplicate Name Event').count(), 2)

    # ------------------------------------------------------------------
    # Adding locations
    # ------------------------------------------------------------------

    def test_customer1_can_add_location_for_own_event(self):
        self.client.force_login(self.customer1)
        response = self.client.post(
            reverse('admin:events_location_add'),
            self._location_post_data(self.event1.pk, name='C1 Location'),
        )
        self.assertEqual(response.status_code, 302)
        loc = Location.objects.get(name='C1 Location')
        self.assertEqual(loc.user, self.customer1)
        self.assertEqual(loc.event, self.event1)

    # ------------------------------------------------------------------
    # Editing events
    # ------------------------------------------------------------------

    def test_customer1_can_open_own_event_change_form(self):
        self.client.force_login(self.customer1)
        response = self.client.get(reverse('admin:events_event_change', args=[self.event1.pk]))
        self.assertEqual(response.status_code, 200)

    def test_customer1_blocked_from_customer2_event_change_form(self):
        self.client.force_login(self.customer1)
        response = self.client.get(reverse('admin:events_event_change', args=[self.event2.pk]))
        self.assertIn(response.status_code, [302, 403])

    def test_superuser_can_open_customer2_event_change_form(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('admin:events_event_change', args=[self.event2.pk]))
        self.assertEqual(response.status_code, 200)

    # ------------------------------------------------------------------
    # Deleting events
    # ------------------------------------------------------------------

    def test_customer1_can_delete_own_event(self):
        event = Event.objects.create(name='C1 Delete Me', event_code='c1del', user=self.customer1, **_EVENT_BASE)
        self.client.force_login(self.customer1)
        response = self.client.post(
            reverse('admin:events_event_delete', args=[event.pk]),
            {'post': 'yes'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Event.objects.filter(pk=event.pk).exists())

    def test_customer1_blocked_from_deleting_customer2_event(self):
        self.client.force_login(self.customer1)
        response = self.client.post(
            reverse('admin:events_event_delete', args=[self.event2.pk]),
            {'post': 'yes'},
        )
        self.assertIn(response.status_code, [302, 403])
        self.assertTrue(Event.objects.filter(pk=self.event2.pk).exists())

    def test_superuser_can_delete_customer1_event(self):
        event = Event.objects.create(name='Sup Delete Me', event_code='supdel', user=self.customer1, **_EVENT_BASE)
        self.client.force_login(self.superuser)
        response = self.client.post(
            reverse('admin:events_event_delete', args=[event.pk]),
            {'post': 'yes'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Event.objects.filter(pk=event.pk).exists())

    # ------------------------------------------------------------------
    # FK dropdown scoping on Location add form
    # ------------------------------------------------------------------

    def test_customer1_location_event_dropdown_excludes_customer2_events(self):
        self.client.force_login(self.customer1)
        response = self.client.get(reverse('admin:events_location_add'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('Customer1 Event', content)
        self.assertNotIn('Customer2 Event', content)


# ---------------------------------------------------------------------------
# ScheduledItem model tests
# ---------------------------------------------------------------------------

class ScheduledItemModelTests(TestCase):
    fixtures = ['dump.json']

    def test_str(self):
        item = ScheduledItem.objects.get(pk=1)
        self.assertEqual(str(item), 'Opening Act @ Pizza Place')

    def test_default_ordering_by_start_time(self):
        items = list(ScheduledItem.objects.filter(event_id=1))
        self.assertEqual(items[0].pk, 1)   # 18:00
        self.assertEqual(items[1].pk, 2)   # 20:30

    def test_cascade_delete_with_location(self):
        """Deleting a location removes its scheduled items."""
        loc = Location.objects.get(pk=1)
        self.assertEqual(ScheduledItem.objects.filter(location=loc).count(), 2)
        loc.delete()
        self.assertEqual(ScheduledItem.objects.filter(location_id=1).count(), 0)

    def test_cascade_delete_with_event(self):
        """Deleting an event removes its scheduled items."""
        event = Event.objects.get(pk=1)
        self.assertEqual(ScheduledItem.objects.filter(event=event).count(), 2)
        event.delete()
        self.assertEqual(ScheduledItem.objects.filter(event_id=1).count(), 0)


# ---------------------------------------------------------------------------
# AllScheduledItemsView tests
# ---------------------------------------------------------------------------

@override_settings(API_KEY=TEST_API_KEY)
class AllScheduledItemsViewTests(TestCase):
    fixtures = ['dump.json']

    def test_returns_200_with_results_and_count(self):
        request = RequestFactory().get(
            reverse('events:get_all_scheduled_items', args=[1]),
            **AUTH_HEADER,
        )
        response = AllScheduledItemsView.as_view()(request, event_id=1)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertIn('results', data)
        self.assertIn('count', data)
        self.assertEqual(data['count'], 2)

    def test_items_sorted_by_start_time(self):
        request = RequestFactory().get(
            reverse('events:get_all_scheduled_items', args=[1]),
            **AUTH_HEADER,
        )
        response = AllScheduledItemsView.as_view()(request, event_id=1)
        data = json.loads(response.content)
        times = [r['start_time'] for r in data['results']]
        self.assertEqual(times, sorted(times))

    def test_result_fields(self):
        request = RequestFactory().get(
            reverse('events:get_all_scheduled_items', args=[1]),
            **AUTH_HEADER,
        )
        response = AllScheduledItemsView.as_view()(request, event_id=1)
        item = json.loads(response.content)['results'][0]
        for field in ('id', 'name', 'description', 'start_time', 'location_id', 'location_name'):
            self.assertIn(field, item)

    def test_returns_401_without_auth(self):
        request = RequestFactory().get(
            reverse('events:get_all_scheduled_items', args=[1]),
        )
        response = AllScheduledItemsView.as_view()(request, event_id=1)
        self.assertEqual(response.status_code, 401)

    def test_returns_empty_list_for_event_with_no_items(self):
        # event_id 999 doesn't exist — should still return an empty list, not 404
        request = RequestFactory().get(
            reverse('events:get_all_scheduled_items', args=[999]),
            **AUTH_HEADER,
        )
        response = AllScheduledItemsView.as_view()(request, event_id=999)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['count'], 0)
        self.assertEqual(data['results'], [])

    def test_filters_to_correct_event(self):
        """Items from event 1 should not appear under a different event_id."""
        request = RequestFactory().get(
            reverse('events:get_all_scheduled_items', args=[2]),
            **AUTH_HEADER,
        )
        response = AllScheduledItemsView.as_view()(request, event_id=2)
        data = json.loads(response.content)
        self.assertEqual(data['count'], 0)


# ---------------------------------------------------------------------------
# SingleScheduledItemView tests
# ---------------------------------------------------------------------------

@override_settings(API_KEY=TEST_API_KEY)
class SingleScheduledItemViewTests(TestCase):
    fixtures = ['dump.json']

    def test_returns_200_with_correct_item(self):
        request = RequestFactory().get(
            reverse('events:get_single_scheduled_item', args=[1, 1]),
            **AUTH_HEADER,
        )
        response = SingleScheduledItemView.as_view()(request, event_id=1, item_id=1)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['name'], 'Opening Act')
        self.assertEqual(data['location_name'], 'Pizza Place')

    def test_returns_404_for_missing_item(self):
        request = RequestFactory().get(
            reverse('events:get_single_scheduled_item', args=[1, 999]),
            **AUTH_HEADER,
        )
        response = SingleScheduledItemView.as_view()(request, event_id=1, item_id=999)
        self.assertEqual(response.status_code, 404)

    def test_returns_404_when_item_belongs_to_different_event(self):
        """item_id=1 exists but under event_id=1; looking it up under event_id=2 should 404."""
        request = RequestFactory().get(
            reverse('events:get_single_scheduled_item', args=[2, 1]),
            **AUTH_HEADER,
        )
        response = SingleScheduledItemView.as_view()(request, event_id=2, item_id=1)
        self.assertEqual(response.status_code, 404)

    def test_returns_401_without_auth(self):
        request = RequestFactory().get(
            reverse('events:get_single_scheduled_item', args=[1, 1]),
        )
        response = SingleScheduledItemView.as_view()(request, event_id=1, item_id=1)
        self.assertEqual(response.status_code, 401)


# ---------------------------------------------------------------------------
# Location list includes scheduled_items
# ---------------------------------------------------------------------------

@override_settings(API_KEY=TEST_API_KEY)
class LocationScheduledItemsEmbedTests(TestCase):
    fixtures = ['dump.json']

    def test_location_response_includes_scheduled_items(self):
        request = RequestFactory().get(
            reverse('events:get_all_locations', args=[1]),
            **AUTH_HEADER,
        )
        response = AllLocationsView.as_view()(request, event_id=1)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        location = data['results'][0]
        self.assertIn('scheduled_items', location)
        self.assertEqual(len(location['scheduled_items']), 2)

    def test_embedded_items_sorted_by_start_time(self):
        request = RequestFactory().get(
            reverse('events:get_all_locations', args=[1]),
            **AUTH_HEADER,
        )
        response = AllLocationsView.as_view()(request, event_id=1)
        location = json.loads(response.content)['results'][0]
        times = [i['start_time'] for i in location['scheduled_items']]
        self.assertEqual(times, sorted(times))

    def test_embedded_item_fields(self):
        request = RequestFactory().get(
            reverse('events:get_all_locations', args=[1]),
            **AUTH_HEADER,
        )
        response = AllLocationsView.as_view()(request, event_id=1)
        item = json.loads(response.content)['results'][0]['scheduled_items'][0]
        for field in ('id', 'name', 'description', 'start_time', 'location_id', 'location_name'):
            self.assertIn(field, item)


# ---------------------------------------------------------------------------
# SingleLocationView unit tests
# ---------------------------------------------------------------------------

@override_settings(API_KEY=TEST_API_KEY)
class SingleLocationViewTests(TestCase):
    fixtures = ['dump.json']

    def test_returns_200_with_correct_location(self):
        request = RequestFactory().get(
            reverse('events:get_single_location', args=[1, 1]),
            **AUTH_HEADER,
        )
        response = SingleLocationView.as_view()(request, event_id=1, location_id=1)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['name'], 'Pizza Place')

    def test_returns_all_expected_fields(self):
        request = RequestFactory().get(
            reverse('events:get_single_location', args=[1, 1]),
            **AUTH_HEADER,
        )
        response = SingleLocationView.as_view()(request, event_id=1, location_id=1)
        data = json.loads(response.content)
        for field in ('id', 'name', 'snippet', 'description', 'website',
                      'image_url', 'latitude', 'longitude', 'address', 'scheduled_items'):
            self.assertIn(field, data)

    def test_scheduled_items_embedded_in_single_location(self):
        request = RequestFactory().get(
            reverse('events:get_single_location', args=[1, 1]),
            **AUTH_HEADER,
        )
        response = SingleLocationView.as_view()(request, event_id=1, location_id=1)
        data = json.loads(response.content)
        self.assertIsInstance(data['scheduled_items'], list)
        self.assertEqual(len(data['scheduled_items']), 2)

    def test_returns_404_for_missing_location(self):
        request = RequestFactory().get(
            reverse('events:get_single_location', args=[1, 999]),
            **AUTH_HEADER,
        )
        response = SingleLocationView.as_view()(request, event_id=1, location_id=999)
        self.assertEqual(response.status_code, 404)

    def test_returns_404_when_location_belongs_to_different_event(self):
        request = RequestFactory().get(
            reverse('events:get_single_location', args=[2, 1]),
            **AUTH_HEADER,
        )
        response = SingleLocationView.as_view()(request, event_id=2, location_id=1)
        self.assertEqual(response.status_code, 404)

    def test_returns_401_without_auth(self):
        request = RequestFactory().get(
            reverse('events:get_single_location', args=[1, 1]),
        )
        response = SingleLocationView.as_view()(request, event_id=1, location_id=1)
        self.assertEqual(response.status_code, 401)


# ---------------------------------------------------------------------------
# Admin QR code download + ScheduledItemAdmin scoping
# ---------------------------------------------------------------------------

@override_settings(STORAGES=_IN_MEMORY_STORAGES, SECURE_SSL_REDIRECT=False)
class AdminQrDownloadTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser('qradmin', 'qr@test.com', 'qrpass')
        cls.customer = User.objects.create_user('qrcustomer', 'qrc@test.com', 'pass', is_staff=True)
        cls.event = Event.objects.create(
            name='QR Test Event', event_code='qrtestevt', user=cls.customer, **_EVENT_BASE
        )

    def setUp(self):
        self.client = Client()

    def test_superuser_can_download_qr_png(self):
        self.client.force_login(self.superuser)
        url = f'/admin/events/event/{self.event.pk}/qr.png/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/png')

    def test_qr_download_content_disposition_filename(self):
        self.client.force_login(self.superuser)
        url = f'/admin/events/event/{self.event.pk}/qr.png/'
        response = self.client.get(url)
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('QR_Test_Event.png', response['Content-Disposition'])

    def test_customer_can_download_own_event_qr(self):
        self.client.force_login(self.customer)
        url = f'/admin/events/event/{self.event.pk}/qr.png/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)


@override_settings(STORAGES=_IN_MEMORY_STORAGES, SECURE_SSL_REDIRECT=False)
class ScheduledItemAdminTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser('siadmin', 'si@test.com', 'pass')
        cls.customer1 = User.objects.create_user('sicustomer1', 'sic1@test.com', 'pass', is_staff=True)
        cls.customer2 = User.objects.create_user('sicustomer2', 'sic2@test.com', 'pass', is_staff=True)
        cls.event1 = Event.objects.create(
            name='SI Event 1', event_code='sievt1', user=cls.customer1, **_EVENT_BASE
        )
        cls.event2 = Event.objects.create(
            name='SI Event 2', event_code='sievt2', user=cls.customer2, **_EVENT_BASE
        )
        cls.location1 = Location.objects.create(
            name='SI Location 1', snippet='s', description='d',
            latitude=32.75, longitude=-97.35, address='123 Main St',
            user=cls.customer1, event=cls.event1,
        )
        cls.location2 = Location.objects.create(
            name='SI Location 2', snippet='s', description='d',
            latitude=32.75, longitude=-97.35, address='456 Other St',
            user=cls.customer2, event=cls.event2,
        )
        from datetime import datetime, timezone as dt_tz
        start = datetime(2025, 6, 1, 18, 0, tzinfo=dt_tz.utc)
        cls.item1 = ScheduledItem.objects.create(
            name='C1 Act', description='desc', start_time=start,
            location=cls.location1, event=cls.event1, user=cls.customer1,
        )
        cls.item2 = ScheduledItem.objects.create(
            name='C2 Act', description='desc', start_time=start,
            location=cls.location2, event=cls.event2, user=cls.customer2,
        )

    def setUp(self):
        self.client = Client()

    def test_customer1_changelist_shows_own_item(self):
        self.client.force_login(self.customer1)
        response = self.client.get(reverse('admin:events_scheduleditem_changelist'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'C1 Act')

    def test_customer1_changelist_excludes_customer2_item(self):
        self.client.force_login(self.customer1)
        response = self.client.get(reverse('admin:events_scheduleditem_changelist'))
        self.assertNotContains(response, 'C2 Act')

    def test_superuser_changelist_shows_all_items(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('admin:events_scheduleditem_changelist'))
        self.assertContains(response, 'C1 Act')
        self.assertContains(response, 'C2 Act')

    def test_scheduled_item_appears_in_location_admin_sidebar(self):
        """ScheduledItemAdmin should be visible in admin sidebar for staff."""
        self.client.force_login(self.customer1)
        response = self.client.get('/admin/')
        self.assertIn('/admin/events/scheduleditem/', response.content.decode())
