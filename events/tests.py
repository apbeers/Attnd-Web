from django.test import TestCase, RequestFactory, override_settings
from django.urls import reverse

from events.apis import NearbyEventsView, EventByCodeOrNameView, AllLocationsView, PushNotificationsUnSubscribe, PushNotificationsSubscribe, JoinEventView, LeaveEventView
from events.models import Device, Event

TEST_API_KEY = 'test-key'
AUTH_HEADER = {'HTTP_AUTHORIZATION': f'Bearer {TEST_API_KEY}'}


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
