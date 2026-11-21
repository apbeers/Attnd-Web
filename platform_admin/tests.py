"""D1 (Studio for every organizer, publishing by plan) and D2 (the Platform admin, superusers only)."""
from decimal import Decimal

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from console.testing import TwoFactorClient
from events import free_limit
from events.models import Event
from platform_admin import plans
from platform_admin.models import EventOverride, PaymentRecord, PlatformSettings, UpgradeRequest


@override_settings(ADMINS=[('Owner', 'owner@attnd.example')])
class PlanTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('o', 'o@example.com', 'pw')
        self.event = Event.objects.create(name='Fair', event_code='FAIR1', user=self.owner, start_time=timezone.now())
        self.client.force_login(self.owner)

    def test_plans_and_prices(self):
        self.assertEqual(plans.plan_of(self.event), plans.FREE)
        self.assertFalse(plans.can_go_live(self.event))
        self.assertEqual((plans.price_label('standard'), plans.price_label('studio')), ('$299', '$499'))
        self.event.paid = True
        self.assertEqual(plans.plan_of(self.event), plans.STANDARD)
        self.event.plan = Event.PLAN_STUDIO
        self.assertEqual(plans.plan_of(self.event), plans.STUDIO)
        self.assertTrue(plans.can_go_live(self.event))

    def test_upgrade_request_emails_the_admins_once(self):
        response = self.client.post(reverse('console:event_upgrade_request', args=[self.event.pk]),
                                    {'kind': 'studio', 'message': 'Our site please'})
        self.assertIn(response.status_code, (200, 302))
        req = UpgradeRequest.objects.get()
        self.assertEqual((req.kind, req.price_usd, req.status), ('studio', Decimal('499'), 'pending'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('$499', mail.outbox[0].body)
        self.client.post(reverse('console:event_upgrade_request', args=[self.event.pk]), {'kind': 'studio'})
        self.assertEqual(UpgradeRequest.objects.count(), 1)  # one pending per event and kind
        self.assertEqual(len(mail.outbox), 1)

    def test_remove_the_limit_is_a_standard_request(self):
        self.client.post(reverse('console:event_limit_request', args=[self.event.pk]), {'note': 'a fair of 400'})
        self.assertEqual(UpgradeRequest.objects.get().kind, 'standard')

    def test_overrides_apply(self):
        self.assertEqual(free_limit.limit(self.event), 20)
        override = EventOverride.objects.create(event=self.event, free_device_limit=50, allow_publishing=True,
                                                studio_daily_cap_usd=Decimal('3'))
        event = Event.objects.get(pk=self.event.pk)
        self.assertEqual(free_limit.limit(event), 50)
        self.assertTrue(plans.can_go_live(event))
        from studio.ai import daily_cap
        self.assertEqual(daily_cap(event), Decimal('3'))
        override.no_free_limit = True
        override.save()
        self.assertTrue(free_limit.exempt(Event.objects.get(pk=self.event.pk)))

    def test_settings_change_the_free_limit(self):
        PlatformSettings.objects.create(pk=1, free_device_limit=7)
        self.assertEqual(free_limit.limit(self.event), 7)


@override_settings(ADMINS=[])
class AdminPageTests(TestCase):
    def setUp(self):
        self.su = User.objects.create_user('su', 'su@example.com', 'pw', is_superuser=True, is_staff=True)
        self.owner = User.objects.create_user('o', 'o@example.com', 'pw')
        self.event = Event.objects.create(name='Fair', event_code='FAIR1', user=self.owner, start_time=timezone.now())
        self.client = TwoFactorClient()
        self.client.force_login(self.su)

    def test_superusers_only(self):
        staff = User.objects.create_user('st', 'st@example.com', 'pw', is_staff=True)
        other = TwoFactorClient()
        other.force_login(staff)
        self.assertEqual(other.get(reverse('platform:home')).status_code, 404)
        for tab in ('overview', 'pricing', 'customers', 'requests', 'events', 'ai'):
            self.assertEqual(self.client.get(reverse('platform:home') + f'?tab={tab}').status_code, 200, tab)
        self.assertEqual(self.client.get(reverse('platform:customer', args=[self.owner.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('platform:event', args=[self.event.pk])).status_code, 200)
        csv = self.client.get(reverse('platform:customers_csv'))
        self.assertIn(b'o@example.com', csv.content)

    def test_pricing_save_is_logged(self):
        self.client.post(reverse('platform:pricing'), {'standard_price_usd': '349', 'studio_price_usd': '599',
                                                       'free_device_limit': '25', 'new_account_daily_imports': '3'})
        conf = PlatformSettings.objects.get(pk=1)
        self.assertEqual((conf.standard_price_usd, conf.free_device_limit), (Decimal('349'), 25))
        self.assertTrue(LogEntry.objects.filter(user=self.su).exists())
        self.assertEqual(plans.price_label('studio'), '$599')

    def test_approve_sets_plan_paid_and_payment(self):
        req, _ = plans.request_upgrade(self.event, self.owner, 'studio')
        self.client.post(reverse('platform:approve'), {'request_id': req.pk})
        self.event.refresh_from_db()
        self.assertEqual((self.event.plan, self.event.paid), (Event.PLAN_STUDIO, True))
        self.assertEqual(PaymentRecord.objects.get().amount_usd, Decimal('499'))
        req.refresh_from_db()
        self.assertEqual(req.status, 'approved')

    def test_decline(self):
        req, _ = plans.request_upgrade(self.event, self.owner, 'standard')
        self.client.post(reverse('platform:decline'), {'request_id': req.pk})
        req.refresh_from_db()
        self.event.refresh_from_db()
        self.assertEqual((req.status, self.event.paid), ('declined', False))

    def test_event_overrides_save(self):
        self.client.post(reverse('platform:event_save', args=[self.event.pk]), {
            'plan': 'standard', 'free_device_limit': '', 'allow_publishing': 'on', 'listed': 'on', 'notes': 'friend',
            'owner_ai_monthly_usd': '60'})
        self.event.refresh_from_db()
        self.assertEqual((self.event.paid, self.event.public), (True, True))
        override = EventOverride.objects.get(event=self.event)
        self.assertTrue(override.allow_publishing)
        from credit.models import AiLimitOverride
        self.assertEqual(AiLimitOverride.objects.get(user=self.owner).monthly_usd, Decimal('60'))

    def test_agent_tools_are_superuser_only(self):
        from agent import registry
        self.assertIn('save_platform_pricing', registry.writes_for(self.su))
        self.assertNotIn('save_platform_pricing', registry.writes_for(self.owner))
        self.assertTrue(registry.registry()['writes']['approve_upgrade_request'].confirm)
