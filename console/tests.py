from django.contrib.auth.models import User
from django.test import TestCase, RequestFactory
from django.urls import reverse

from console.views import NotificationDeleteView


class NotificationTestCase(TestCase):
    fixtures = ['dump.json']

    def setUp(self) -> None:
        self.user = User.objects.filter(username='andrew').get()

    def test_delete_notification(self):
        event_id = 3
        notification_id = 2

        request = RequestFactory().post(reverse('notification_delete', args=[event_id, notification_id]))
        request.user = self.user
        response = NotificationDeleteView.as_view()(request, event_id, notification_id)
        self.assertEqual(response.status_code, 302)