"""AI credit (batch 6 §2): the ledger, counting rules, limits and overrides, warnings, enforcement, pages."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from attnd import claude
from attnd.testing import fake_claude, reply
from console.testing import TwoFactorClient
from credit import ledger
from credit.models import AiCreditNotice, AiLimitOverride, AiLimits, AiSpend
from events.models import Event

User = get_user_model()
SCHEMA = {'type': 'object', 'properties': {'name': {'type': 'string'}}, 'required': ['name']}


def spend(user, cost, **kw):
    return AiSpend.objects.create(user=user, cost_usd=Decimal(str(cost)), feature=kw.pop('feature', 'ask_attnd'), **kw)


class CreditTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('u1', 'org@example.com', 'pw-long-enough-1')
        now = timezone.now()
        self.event = Event.objects.create(name='Prairie Fest', event_code='PRAIRIE', user=self.user,
                                          start_time=now, end_time=now + timedelta(hours=8), timezone='America/Chicago')
        self.staff = User.objects.create_user('s1', 'staff@example.com', 'pw-long-enough-1', is_staff=True)


class LedgerTests(CreditTestCase):
    def test_every_claude_call_is_recorded_for_the_person_in_context(self):
        with ledger.spend_context(user=self.user, event=self.event, feature='theme'), fake_claude(reply({'name': 'A'})):
            _, cost = claude.structured(system='s', content='c', schema=SCHEMA)
        row = AiSpend.objects.get()
        self.assertEqual((row.user, row.event, row.feature, row.provider, row.counted), (self.user, self.event, 'theme', 'anthropic', True))
        self.assertEqual(row.cost_usd, Decimal(str(cost)).quantize(Decimal('0.00001')))

    def test_background_work_counts_against_the_events_owner(self):
        with ledger.spend_context(event=self.event, feature='place_images', background=True):
            ledger.record(cost=0.01, model='m')
        row = AiSpend.objects.get()
        self.assertEqual((row.user, row.background, row.counted), (self.user, True, True))

    def test_exemptions(self):
        with ledger.spend_context(user=self.staff, feature='prospects'):
            ledger.record(cost=1)
        self.event.showcase = True
        self.event.save()
        with ledger.spend_context(user=self.user, event=self.event):
            ledger.record(cost=1)
        self.assertFalse(AiSpend.objects.filter(counted=True).exists())
        self.assertEqual(ledger.used_this_month(self.user), 0)

    def test_last_month_does_not_count(self):
        spend(self.user, 29, created_at=ledger.month_start() - timedelta(minutes=1))
        spend(self.user, 2)
        self.assertEqual(ledger.used_this_month(self.user), Decimal('2'))


class LimitTests(CreditTestCase):
    def test_default_override_and_staff(self):
        self.assertEqual(ledger.monthly_limit(self.user), Decimal('30'))
        AiLimits.objects.create(pk=1, monthly_default_usd=12)
        self.assertEqual(ledger.monthly_limit(self.user), Decimal('12'))
        AiLimitOverride.objects.create(user=self.user, monthly_usd=50)
        self.assertEqual(ledger.monthly_limit(self.user), Decimal('50'))
        AiLimitOverride.objects.filter(user=self.user).update(unlimited=True)
        self.assertIsNone(ledger.monthly_limit(self.user))
        self.assertIsNone(ledger.monthly_limit(self.staff))

    @override_settings(AI_MONTHLY_CREDIT_USD=10)
    def test_limit_reached_message_and_exempt_events(self):
        spend(self.user, 10)
        self.assertFalse(ledger.allowed(self.user))
        message = ledger.limit_message(self.user)
        self.assertIn("You've used this month's AI credit ($10). It renews on", message)
        self.assertIn(f'{ledger.next_month():%b} 1', message)
        self.event.showcase = True
        self.assertTrue(ledger.allowed(self.user, event=self.event))

    @override_settings(AI_MONTHLY_CREDIT_USD=10, AI_PLATFORM_ALERT_USD=5, ADMINS=[('Owner', 'owner@example.com')])
    def test_warnings_once_at_80_and_100_and_the_platform_alert(self):
        with ledger.spend_context(user=self.user):
            ledger.record(cost=8.5)
            ledger.record(cost=0.1)
        subjects = [m.subject for m in mail.outbox]
        self.assertEqual(subjects.count("You've used 80% of this month's AI credit"), 1)
        self.assertEqual(subjects.count('Attnd AI spend passed $5 this month'), 1)
        with ledger.spend_context(user=self.user):
            ledger.record(cost=2)
            ledger.record(cost=1)
        self.assertEqual([m.subject for m in mail.outbox].count("You've used this month's AI credit"), 1)
        self.assertEqual(AiCreditNotice.objects.count(), 3)

    def test_visitor_spend_moves_into_the_account_at_sign_in(self):
        session = self.client.session
        session['ai_visitor'] = 'v' * 40
        session.save()
        AiSpend.objects.create(visitor='v' * 40, cost_usd=Decimal('0.5'), feature='import')
        self.client.login(username='org@example.com', password='pw-long-enough-1')
        row = AiSpend.objects.get()
        self.assertEqual((row.user, row.visitor), (self.user, ''))


@override_settings(AI_MONTHLY_CREDIT_USD=1)
class EnforcementTests(CreditTestCase):
    def setUp(self):
        super().setUp()
        spend(self.user, 1)
        self.client.force_login(self.user)

    def test_ask_attnd_refuses_with_the_friendly_message(self):
        response = self.client.post(reverse('agent:start'), {'goal': 'hello'}, secure=True)
        self.assertEqual(response.status_code, 429)
        self.assertIn("this month's AI credit", response.json()['error'])

    def test_ai_features_refuse_but_editing_works(self):
        response = self.client.post(reverse('console:event_refresh', args=[self.event.pk]), secure=True,
                                    HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 429)
        response = self.client.post(reverse('console:event_theme_generate', args=[self.event.pk]), secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(reverse('console:event', args=[self.event.pk]), secure=True).status_code, 200)

    def test_account_meter_and_chat_note(self):
        page = self.client.get(reverse('console:account'), secure=True)
        self.assertContains(page, 'of $1 used this month')
        from agent.views import credit_note
        self.assertIn("You've used this month's AI credit", credit_note(self.user))
        AiLimitOverride.objects.create(user=self.user, unlimited=True)
        self.assertEqual(credit_note(self.user), '')


class StaffPageTests(CreditTestCase):
    def setUp(self):
        super().setUp()
        spend(self.user, 2, event=self.event)
        self.client = TwoFactorClient()
        self.client.force_login(self.staff)

    def test_staff_only(self):
        other = self.client_class()
        other.force_login(self.user)
        self.assertEqual(other.get(reverse('credit:spend'), secure=True).status_code, 404)
        page = self.client.get(reverse('credit:spend'), secure=True)
        self.assertContains(page, 'AI spend')
        self.assertContains(page, 'org@example.com')
        self.assertContains(page, 'data-confirm="Change the AI limits for everyone?')

    def test_save_limits_logs_history(self):
        data = {f: getattr(AiLimits.load(), f) for f in ('monthly_default_usd', 'visitor_allowance_usd',
                'platform_alert_usd', 'agent_daily_usd', 'studio_daily_usd', 'place_images_daily_usd',
                'covers_daily_usd', 'prospects_daily_usd', 'new_account_daily_imports')}
        data['monthly_default_usd'] = '45'
        response = self.client.post(reverse('credit:limits'), data, secure=True)
        self.assertRedirects(response, reverse('credit:spend'), fetch_redirect_response=False)
        self.assertEqual(AiLimits.load().monthly_default_usd, Decimal('45'))
        self.assertEqual(ledger.monthly_limit(self.user), Decimal('45'))
        entry = LogEntry.objects.get(object_repr='AI limits')
        self.assertIn('Monthly AI credit per person', entry.change_message)

    def test_overrides(self):
        self.client.post(reverse('credit:override'), {'email': 'ORG@example.com', 'monthly_usd': '80', 'note': 'beta'}, secure=True)
        override = AiLimitOverride.objects.get(user=self.user)
        self.assertEqual(override.monthly_usd, Decimal('80'))
        self.client.post(reverse('credit:override_remove'), {'override': override.pk}, secure=True)
        self.assertFalse(AiLimitOverride.objects.exists())
        self.assertEqual(LogEntry.objects.filter(object_repr__startswith='org@example.com').count(), 2)

    def test_csv(self):
        response = self.client.get(reverse('credit:export'), secure=True)
        body = response.content.decode()
        self.assertIn('org@example.com', body)
        self.assertIn('PRAIRIE', body)

    def test_changes_always_ask_in_ask_attnd(self):
        from agent import registry
        writes = registry.registry()['writes']
        for name in ('save_ai_limits', 'set_ai_limit_override', 'remove_ai_limit_override'):
            self.assertTrue(writes[name].confirm, name)
            self.assertEqual(writes[name].audience, 'staff', name)
