from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from console.testing import TwoFactorClient
from events.models import Event, Location, ScheduledItem


# ---------------------------------------------------------------------------
# ScheduledItem model tests
# ---------------------------------------------------------------------------

class ScheduledItemModelTests(TestCase):
    fixtures = ['test_event.json']

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
# Django admin: superusers (and staff with model permissions) only; organizers use the console
# ---------------------------------------------------------------------------

@override_settings(SECURE_SSL_REDIRECT=False)
class AdminAccessTests(TestCase):
    fixtures = ['test_event.json']

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser('siadmin', 'si@example.com', 'pass')
        cls.staff = User.objects.create_user('sistaff', 'staff@example.com', 'pass', is_staff=True)
        cls.organizer = User.objects.get(pk=1)

    def setUp(self):
        self.client = TwoFactorClient()  # staff: past the two-factor step

    def test_superuser_sees_every_model(self):
        self.client.force_login(self.superuser)
        index = self.client.get(reverse('admin:index')).content.decode()
        for model in ('event', 'location', 'scheduleditem', 'notification', 'device'):
            self.assertIn(f'/{settings.ADMIN_URL_PATH}events/{model}/', index)
        response = self.client.get(reverse('admin:events_scheduleditem_changelist'))
        self.assertContains(response, 'Opening Act')
        self.assertEqual(self.client.get(reverse('admin:events_event_change', args=[1])).status_code, 200)

    def test_staff_without_permissions_sees_no_event_models(self):
        self.client.force_login(self.staff)
        index = self.client.get(reverse('admin:index')).content.decode()
        self.assertNotIn(f'/{settings.ADMIN_URL_PATH}events/', index)
        self.assertEqual(self.client.get(reverse('admin:events_event_changelist')).status_code, 403)

    def test_organizers_are_not_let_in(self):
        self.client.force_login(self.organizer)
        response = self.client.get(reverse('admin:events_event_changelist'))
        self.assertEqual(response.status_code, 302)
