from django.test import TestCase, RequestFactory
from django.urls import reverse

from events.apis import NearbyEventsView, EventByCodeOrNameView


class EventsTestCase(TestCase):
    fixtures = ['dump.json']

    def test_get_nearby_events(self):
        user_latitude = 32.668224
        user_longitude = -97.419591

        request = RequestFactory().get(reverse('get_nearby_events', args=[user_latitude, user_longitude]))
        response = NearbyEventsView.as_view()(request, user_latitude, user_longitude)
        self.assertEqual(response.status_code, 200)
        print(response.content)

    def test_event_by_code_or_name(self):
        data = 'my event'

        request = RequestFactory().get(reverse('get_event_by_code_or_name'))
        request.GET = {'data': data}
        response = EventByCodeOrNameView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        print(response.content)
