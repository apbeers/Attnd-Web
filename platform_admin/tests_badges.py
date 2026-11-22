"""Batch 7: the event header shows the plan (Free / Standard / Studio) and an "Override" badge when the Platform admin
changed something for the event; only staff see what it changes."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from console.components import catalog
from console.testing import TwoFactorClient
from events.models import Event
from platform_admin.models import EventOverride


class PlanBadgeTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('o', 'o@example.com', 'pw')
        start = timezone.now() + timedelta(days=3)
        self.event = Event.objects.create(name='Fest', event_code='PBFEST', user=self.owner, start_time=start,
                                          end_time=start + timedelta(hours=4))

    def kinds(self, user):
        return {b['kind']: b for b in catalog.event_header(user, event=self.event)['plan_badges']}

    def test_plan_badges(self):
        self.assertEqual(self.kinds(self.owner)['plan']['label'], 'Free')
        self.event.paid = True
        self.assertEqual(self.kinds(self.owner)['plan']['label'], 'Standard')
        self.event.plan = Event.PLAN_STUDIO
        self.assertEqual(self.kinds(self.owner)['plan']['label'], 'Studio')
        self.assertNotIn('Paid', [b['label'] for b in catalog.event_header(self.owner, event=self.event)['badges']])

    def test_override_badge_details_for_staff_only(self):
        EventOverride.objects.create(event=self.event, notes='just a note')
        self.assertNotIn('override', self.kinds(self.owner))  # notes alone change nothing
        EventOverride.objects.filter(event=self.event).update(free_device_limit=50, allow_publishing=True)
        self.event = Event.objects.get(pk=self.event.pk)
        badge = self.kinds(self.owner)['override']
        self.assertEqual((badge['details'], badge['url']), ([], ''))
        staff = User.objects.create_user('s', 's@example.com', 'pw', is_staff=True)
        self.event = Event.objects.get(pk=self.event.pk)
        badge = self.kinds(staff)['override']
        self.assertIn('Free phones: 50', badge['details'])
        client = TwoFactorClient()
        client.force_login(self.owner)
        page = client.get(reverse('console:locations', args=[self.event.pk]), secure=True)
        self.assertContains(page, 'data-plan-badge="plan"')
        self.assertContains(page, 'data-plan-badge="override"')
        self.assertNotContains(page, 'Free phones: 50')
