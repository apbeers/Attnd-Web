import io
from datetime import datetime, timezone as dt_timezone

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client, RequestFactory, override_settings
from django.urls import reverse
from PIL import Image

from events.apis import (
    NearbyEventsView, EventByCodeOrNameView, AllLocationsView,
    PushNotificationsUnSubscribe, PushNotificationsSubscribe,
    JoinEventView, LeaveEventView,
)
from events.models import Device, Event, Location

TEST_API_KEY = 'test-key'
AUTH_HEADER = {'HTTP_AUTHORIZATION': f'Bearer {TEST_API_KEY}'}


def make_image():
    """Return a 1×1 JPEG as a SimpleUploadedFile."""
    buf = io.BytesIO()
    Image.new('RGB', (1, 1)).save(buf, format='JPEG')
    buf.seek(0)
    return SimpleUploadedFile('img.jpg', buf.read(), content_type='image/jpeg')


# ---------------------------------------------------------------------------
# Existing API-view tests
# ---------------------------------------------------------------------------

@override_settings(API_KEY=TEST_API_KEY)
class EventsTestCase(TestCase):
    fixtures = ['dump.json']

    def test_all_locations(self):
        event_id = 3

        request = RequestFactory().get(reverse('events:get_all_locations', args=[event_id]), **AUTH_HEADER)
        response = AllLocationsView.as_view()(request, event_id)
        self.assertEqual(response.status_code, 200)
        print(response.content)

    def test_get_nearby_events(self):
        user_latitude = 32.668224
        user_longitude = -97.419591

        request = RequestFactory().get(reverse('events:get_nearby_events', args=[user_latitude, user_longitude]), **AUTH_HEADER)
        response = NearbyEventsView.as_view()(request, user_latitude, user_longitude)
        self.assertEqual(response.status_code, 200)
        print(response.content)

    def test_event_by_code_or_name(self):
        data = 'my event'

        request = RequestFactory().get(reverse('events:get_event_by_code_or_name'), **AUTH_HEADER)
        request.GET = {'data': data}
        response = EventByCodeOrNameView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        print(response.content)


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


@override_settings(STORAGES=_IN_MEMORY_STORAGES)
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
        """Minimal valid POST data for the Event admin add/change form."""
        data = {
            'name': 'New Event',
            'event_code': 'newevt',
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
            self._event_post_data(name='C1 New Event', event_code='c1new'),
        )
        self.assertEqual(response.status_code, 302)
        event = Event.objects.get(name='C1 New Event')
        self.assertEqual(event.user, self.customer1)

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
        # Admin redirects (302) when the object is absent from the filtered queryset
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
        # event2 must still exist
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
