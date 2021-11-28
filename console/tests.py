from django.contrib.auth.models import User
from django.test import TestCase, RequestFactory
from django.urls import reverse

from console.views import NotificationDeleteView, ValidatePayPalTransactionView


class NotificationTestCase(TestCase):
    fixtures = ['dump.json']

    def setUp(self) -> None:
        self.user = User.objects.filter(username='andrew').get()

    def test_delete_notification(self):
        event_id = 1
        notification_id = 1

        request = RequestFactory().get(reverse('console:notification_delete', args=[event_id, notification_id]))
        request.user = self.user
        response = NotificationDeleteView.as_view()(request, event_id, notification_id)
        self.assertEqual(response.status_code, 302)

class ValidatePayPalTransactionTestCase(TestCase):

    def test_get_access_token(self):
        view = ValidatePayPalTransactionView()

        access_token = view.get_access_token()
        print(access_token)