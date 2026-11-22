"""Batch 7 billing: invoices, manual payments, approved upgrades, PDF, receipts, CSV, Stripe off / on (mocked)."""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from console.testing import TwoFactorClient
from events.models import Event, EventMember
from platform_admin import billing, plans
from platform_admin.models import Invoice, PaymentRecord, UpgradeRequest


class BillingTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('own', 'own@example.com', 'pw')
        start = timezone.now() + timedelta(days=5)
        self.event = Event.objects.create(name='Bill Fest', event_code='BILLF', user=self.owner, start_time=start,
                                          timezone='America/Chicago')
        self.client.force_login(self.owner)

    def test_numbers_are_sequential(self):
        a = billing.issue(self.event, 'standard')
        b = billing.issue(self.event, 'studio')
        year = timezone.now().year
        self.assertEqual((a.number, b.number), (f'ATT-{year}-0001', f'ATT-{year}-0002'))
        self.assertEqual(a.amount_usd, plans.prices()['standard'])
        self.assertEqual(a.billed_to, 'own@example.com')

    def test_manual_payment_applies_the_plan_once(self):
        invoice = billing.record_manual(self.event, 'studio', Decimal('450'), None, note='cheque')
        self.event.refresh_from_db()
        self.assertTrue(self.event.paid)
        self.assertEqual(self.event.plan, Event.PLAN_STUDIO)
        self.assertEqual(invoice.status, Invoice.PAID)
        self.assertEqual(PaymentRecord.objects.get().amount_usd, Decimal('450'))
        billing.mark_paid(invoice, Invoice.MANUAL)  # twice (a retried webhook): nothing doubles
        self.assertEqual(PaymentRecord.objects.count(), 1)

    def test_approved_upgrade_makes_a_paid_invoice(self):
        req = UpgradeRequest.objects.create(event=self.event, requested_by=self.owner, kind='standard',
                                            price_usd=Decimal('299'))
        plans.approve(req, None)
        self.assertEqual(Invoice.objects.get().status, Invoice.PAID)
        self.assertEqual(PaymentRecord.objects.count(), 1)

    def test_pdf_csv_and_page(self):
        invoice = billing.record_manual(self.event, 'standard', Decimal('299'), None)
        data = billing.pdf(invoice)
        self.assertTrue(data.startswith(b'%PDF-1.4') and data.rstrip().endswith(b'%%EOF'))
        self.assertIn(invoice.number.encode(), data)
        page = self.client.get(reverse('console:event_billing', args=[self.event.pk]), secure=True)
        self.assertContains(page, invoice.number)
        self.assertContains(page, "Payments aren't set up yet")
        pdf = self.client.get(reverse('console:event_invoice_pdf', args=[self.event.pk, invoice.number]), secure=True)
        self.assertEqual(pdf['Content-Type'], 'application/pdf')
        csv = self.client.get(reverse('console:event_invoices_csv', args=[self.event.pk]), secure=True)
        self.assertIn(invoice.number, csv.content.decode())

    def test_send_receipt(self):
        invoice = billing.record_manual(self.event, 'standard', Decimal('299'), None)
        self.client.post(reverse('console:event_send_receipt', args=[self.event.pk]),
                         {'invoice': invoice.number, 'email': 'books@example.com'}, secure=True)
        message = [m for m in mail.outbox if invoice.number in m.subject][0]
        self.assertEqual(message.to, ['books@example.com'])
        self.assertEqual(message.attachments[0][2], 'application/pdf')

    def test_contributors_and_strangers_cannot_see_billing(self):
        helper = User.objects.create_user('c', 'c@example.com', 'pw')
        EventMember.objects.create(event=self.event, user=helper, role=EventMember.ROLE_CONTRIBUTOR)
        other = TwoFactorClient()
        other.force_login(helper)
        self.assertEqual(other.get(reverse('console:event_billing', args=[self.event.pk]), secure=True).status_code, 404)

    def test_pay_now_without_stripe_says_so(self):
        response = self.client.post(reverse('console:event_pay', args=[self.event.pk]), {'plan': 'standard'},
                                    secure=True, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.post(reverse('console:stripe_webhook'), secure=True).status_code, 404)

    @override_settings(STRIPE_SECRET_KEY='sk_test_x', STRIPE_WEBHOOK_SECRET='whsec_x')
    def test_stripe_checkout_and_webhook(self):
        session = mock.Mock(id='cs_1', url='https://checkout.stripe.com/c/cs_1')
        with mock.patch('stripe.checkout.Session.create', return_value=session) as create:
            response = self.client.post(reverse('console:event_pay', args=[self.event.pk]), {'plan': 'standard'},
                                        secure=True, HTTP_ACCEPT='application/json')
        self.assertEqual(response.json()['url'], session.url)
        invoice = Invoice.objects.get()
        self.assertEqual(create.call_args.kwargs['line_items'][0]['price_data']['unit_amount'], 29900)
        event = {'type': 'checkout.session.completed', 'data': {'object': {
            'id': 'cs_1', 'client_reference_id': invoice.number, 'metadata': {'invoice': invoice.number},
            'payment_status': 'paid', 'customer': 'cus_1'}}}
        with mock.patch('stripe.Webhook.construct_event', return_value=event):
            ok = self.client.post(reverse('console:stripe_webhook'), data=b'{}', content_type='application/json',
                                  HTTP_STRIPE_SIGNATURE='t=1,v1=x', secure=True)
        self.assertEqual(ok.status_code, 200)
        invoice.refresh_from_db()
        self.assertEqual((invoice.status, invoice.method, invoice.stripe_customer), ('paid', 'stripe', 'cus_1'))
        with mock.patch('stripe.Webhook.construct_event', side_effect=ValueError('bad')):
            bad = self.client.post(reverse('console:stripe_webhook'), data=b'{}', content_type='application/json',
                                   secure=True)
        self.assertEqual(bad.status_code, 400)
