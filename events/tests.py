from django.test import TestCase
from django.test.client import Client
from django.urls import reverse


class EventsTestCase(TestCase):

    def setUp(self) -> None:
        self.client = Client()

    def test_get_nearby_events(self):
        user_latitude = 32.668224
        user_longitude = -97.419591

        url = reverse('get_nearby_events', args=[user_latitude, user_longitude])

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        print(response)
