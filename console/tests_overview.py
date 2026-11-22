"""Batch 7 Track B: the Overview in order of importance, the free limit at the very top."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event


class OverviewOrderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('o', 'o@example.com', 'pw')
        start = timezone.now() + timedelta(days=3)
        self.event = Event.objects.create(name='Order Fest', event_code='ORDERF', user=self.user, start_time=start,
                                          end_time=start + timedelta(hours=4), timezone='America/Chicago')
        self.client.force_login(self.user)

    def page(self):
        response = self.client.get(reverse('console:event', args=[self.event.pk]) + '?page=1', secure=True)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_free_limit_first_then_the_phone_then_the_rest(self):
        body = self.page()
        limit = body.index('free phones')
        self.assertIn('Remove the limit ($', body)
        self.assertLess(limit, body.index('component--live_on_phone'))
        self.assertLess(body.index('component--live_on_phone'), body.index('component--event_map'))
        self.assertLess(body.index('component--event_map'), body.index('Settings and recent activity'))
        self.assertIn('Next: No locations yet', body)

    def test_paid_event_has_no_limit_line(self):
        Event.objects.filter(pk=self.event.pk).update(paid=True)
        self.assertNotIn('free phones', self.page())
