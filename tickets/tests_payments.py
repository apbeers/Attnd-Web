"""Server integration with Stripe mocked at the HTTP layer (fixtures/stripe/): checkout → webhook → tickets →
refund / dispute / cancel → payouts; webhook idempotency and replay (spec: Testing → Server integration)."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from tickets import inventory, money, payments
from tickets.fakes import STRIPE_SETTINGS, FakeStripe, completed_event, fixture, ticketed_event, webhook
from tickets.models import (Order, Payout, PayoutAccount, Revocation, StripeEvent, Ticket, TicketSettings,
                            TicketType)


@override_settings(**STRIPE_SETTINGS, SITE_URL='https://attnd.app')
class PaymentFlowTests(TestCase):
    def setUp(self):
        self.stripe = FakeStripe().install(self)
        self.event, self.ga, self.free = ticketed_event()

    def held(self, qty=2, **kw):
        order = inventory.create_hold(self.event, [(self.ga.id, qty, None)], **kw)
        order.email, order.name = 'buyer@example.com', 'Bea Buyer'
        order.save()
        return order

    def paid(self, qty=2):
        order = self.held(qty)
        payments.start_checkout(order)
        order.refresh_from_db()
        self.stripe.amount_received = order.total_cents
        with self.captureOnCommitCallbacks(execute=True):
            response = webhook(self.client, completed_event(order, f'evt_paid_{order.pk}'))
        self.assertEqual(response.status_code, 200, response.content)
        order.refresh_from_db()
        return order

    def test_checkout_session_is_a_destination_charge_with_the_fee_as_application_fee(self):
        order = self.held(2)
        url = payments.start_checkout(order)
        self.assertTrue(url.startswith('https://checkout.stripe.com/'))
        call = self.stripe.find('POST', '/v1/checkout/sessions')[0]
        p = call.params
        self.assertEqual(p['mode'], 'payment')
        self.assertEqual(p['payment_intent_data']['application_fee_amount'], str(order.fee_cents))
        self.assertEqual(p['payment_intent_data']['transfer_data']['destination'], 'acct_test_tixfest')
        self.assertEqual(p['payment_intent_data']['on_behalf_of'], 'acct_test_tixfest')
        self.assertEqual(p['metadata']['order_id'], str(order.public_id))
        lines = p['line_items']
        self.assertEqual(sum(int(lines[k]['price_data']['unit_amount']) for k in lines), order.total_cents)
        self.assertEqual(lines['1']['price_data']['product_data']['name'], 'Processing fee')  # itemised
        self.assertIn('Idempotency-Key', call.headers)
        self.assertEqual(call.headers['Stripe-Version'], '2025-03-31.basil')
        order.refresh_from_db()
        self.assertEqual(order.status, Order.PENDING)
        self.assertGreaterEqual((order.expires_at - timezone.now()).total_seconds(), 29 * 60)  # Stripe's minimum

    def test_webhook_issues_tickets_once_and_emails(self):
        order = self.paid(2)
        self.assertEqual(order.status, Order.PAID)
        self.assertEqual(order.stripe_charge_id, 'ch_3QTestCharge')
        self.assertEqual(order.stripe_transfer_id, 'tr_3QTestTransfer')
        tickets = list(order.tickets.all())
        self.assertEqual(len(tickets), 2)
        self.assertEqual(sum(t.fee_cents for t in tickets), order.fee_cents)
        self.assertEqual(sum(t.price_cents for t in tickets), 5000)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Your tickets for Tix Fest', mail.outbox[0].subject)
        self.assertIn('/tickets/ticket/', mail.outbox[0].body)
        self.assertTrue(Payout.objects.filter(event=self.event, status=Payout.SCHEDULED).exists())
        # Replay: the same event again, and a different event id for the same payment.
        self.assertEqual(webhook(self.client, completed_event(order, f'evt_paid_{order.pk}')).content, b'duplicate')
        webhook(self.client, completed_event(order, 'evt_other'))
        pi = fixture('event_payment_intent_succeeded')
        pi['data']['object']['metadata'] = {'order_id': str(order.public_id)}
        webhook(self.client, pi)
        self.assertEqual(order.tickets.count(), 2)
        self.assertEqual(StripeEvent.objects.filter(processed_at__isnull=False).count(), 3)

    def test_webhook_signature_and_livemode(self):
        order = self.held()
        self.assertEqual(webhook(self.client, completed_event(order), secret='whsec_wrong').status_code, 400)
        live = completed_event(order, 'evt_live')
        live['livemode'] = True  # a live event never reaches a test-mode server (and the other way round)
        self.assertEqual(webhook(self.client, live).status_code, 400)
        self.assertFalse(order.tickets.exists())
        stale = self.client.post('/tickets/stripe/webhook/', b'{}', content_type='application/json',
                                 HTTP_STRIPE_SIGNATURE='t=1,v1=abc', secure=True)
        self.assertEqual(stale.status_code, 400)

    def test_handler_error_returns_500_so_stripe_retries(self):
        order = self.held()
        with patch('tickets.payments.fulfil', side_effect=RuntimeError('db down')), \
                self.assertLogs('tickets.payments', 'ERROR'), self.assertLogs('django.request', 'ERROR'):
            self.assertEqual(webhook(self.client, completed_event(order)).status_code, 500)
        self.assertIsNone(StripeEvent.objects.get(id='evt_test_completed').processed_at)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(webhook(self.client, completed_event(order)).status_code, 200)  # the retry works
        self.assertEqual(order.tickets.count(), 2)

    def test_underpaid_session_issues_nothing(self):
        order = self.held()
        evt = completed_event(order)
        evt['data']['object']['amount_total'] = 100
        webhook(self.client, evt)
        self.assertFalse(order.tickets.exists())

    def test_late_payment_after_hold_sold_out_is_refunded(self):
        self.ga.quantity = 2
        self.ga.save()
        order = self.held(2)
        payments.start_checkout(order)
        Order.objects.filter(pk=order.pk).update(status=Order.EXPIRED, expires_at=timezone.now())
        inventory.create_hold(self.event, [(self.ga.id, 2, None)])  # someone else took them
        order.refresh_from_db()
        webhook(self.client, completed_event(order))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.REFUNDED)
        self.assertFalse(order.tickets.exists())
        self.assertTrue(self.stripe.find('POST', '/v1/refunds'))
        self.assertIn('was refunded', mail.outbox[-1].subject)

    def test_refund_returns_ticket_price_not_fee(self):
        order = self.paid(2)
        ticket = order.tickets.first()
        with self.captureOnCommitCallbacks(execute=True):
            payments.refund(order, tickets=[ticket])
        refund_call = self.stripe.find('POST', '/v1/refunds')[0]
        self.assertEqual(refund_call.params['amount'], '2500')  # the ticket price, no fee
        reversal = self.stripe.find('POST', r'/v1/transfers/tr_3QTestTransfer/reversals')[0]
        self.assertEqual(reversal.params['amount'], '2500')  # exactly that much back from the organizer
        ticket.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.REFUNDED)
        self.assertEqual(ticket.version, 2)
        self.assertTrue(Revocation.objects.filter(ticket=ticket, version=1).exists())
        self.assertEqual((order.status, order.refunded_cents, order.fee_refunded_cents), (Order.PAID, 2500, 0))
        self.assertIn('Refund for your', mail.outbox[-1].subject)
        with self.assertRaises(payments.RefundError):
            payments.refund(order, tickets=[ticket])
        payments.refund(order)  # the rest
        order.refresh_from_db()
        self.assertEqual(order.status, Order.REFUNDED)

    def test_partial_refund_keeps_tickets(self):
        order = self.paid(2)
        payments.refund(order, amount_cents=1000)
        order.refresh_from_db()
        self.assertEqual(order.refunded_cents, 1000)
        self.assertEqual(order.tickets.filter(status=Ticket.VALID).count(), 2)

    def test_stripe_refund_error_is_reported_and_nothing_changes(self):
        order = self.paid(1)
        self.stripe.responses[('POST', '/v1/refunds')] = (400, fixture('error_card'))
        with self.assertRaises(payments.RefundError):
            payments.refund(order)
        order.refresh_from_db()
        self.assertEqual(order.refunded_cents, 0)
        self.assertEqual(order.tickets.filter(status=Ticket.VALID).count(), 1)

    def test_cancel_event_refunds_everything_including_fees(self):
        first, second = self.paid(2), self.paid(1)
        with self.captureOnCommitCallbacks(execute=True):
            done, failed = payments.cancel_event(self.event, 'Storm warning')
        self.assertEqual((done, failed), (2, 0))
        amounts = sorted(int(c.params['amount']) for c in self.stripe.find('POST', '/v1/refunds'))
        self.assertEqual(amounts, sorted([first.total_cents, second.total_cents]))
        reversals = sorted(int(c.params['amount']) for c in self.stripe.find('POST', r'/v1/transfers/.*/reversals'))
        self.assertEqual(reversals, [2500, 5000])  # the organizer returns the ticket money; Attnd absorbs the fee
        self.assertFalse(Ticket.objects.filter(event=self.event, status=Ticket.VALID).exists())
        ts = TicketSettings.objects.get(event=self.event)
        self.assertTrue(ts.cancelled and not ts.on_sale)
        self.assertTrue(any('is cancelled' in m.subject for m in mail.outbox))
        self.assertEqual(inventory.sales_state(self.event)[0], 'cancelled')
        self.assertEqual(payments.refund_policy(Order.objects.get(pk=first.pk))[0], False)

    def test_refund_policy_rules(self):
        order = self.paid(1)
        ts = inventory.ticketing(self.event)
        ts.refund_policy = TicketSettings.REFUND_UNTIL
        ts.refund_days_before = 7
        ts.save()
        self.assertEqual(payments.refund_policy(order)[:2], (True, True))  # 10 days out
        ts.refund_days_before = 30
        ts.save()
        self.assertEqual(payments.refund_policy(order)[:2], (False, False))
        ts.refund_policy = TicketSettings.REFUND_REQUESTS
        ts.save()
        self.assertEqual(payments.refund_policy(order)[:2], (True, False))
        ts.refund_policy = TicketSettings.REFUND_NONE
        ts.save()
        self.assertFalse(payments.refund_policy(order)[0])

    def test_dispute_flags_then_lost_voids(self):
        order = self.paid(1)
        webhook(self.client, fixture('event_dispute_created'))
        order.refresh_from_db()
        self.assertEqual(order.dispute_status, 'needs_response')
        self.assertTrue(any('disputed' in m.subject for m in mail.outbox))
        webhook(self.client, fixture('event_dispute_lost'))
        self.assertEqual(order.tickets.get().status, Ticket.VOID)

    def test_dashboard_refund_is_reconciled(self):
        order = self.paid(1)
        evt = fixture('event_charge_refunded')
        evt['data']['object']['amount_refunded'] = order.total_cents
        webhook(self.client, evt)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.REFUNDED)
        self.assertEqual(order.refunded_cents + order.fee_refunded_cents, order.total_cents)

    def test_payouts_held_until_after_the_event(self):
        order = self.paid(2)
        self.assertEqual(payments.release_due(), 0)  # the event hasn't happened
        payout = Payout.objects.get(event=self.event)
        self.assertEqual(payout.release_at, self.event.effective_end + timedelta(days=3))
        later = payout.release_at + timedelta(minutes=1)
        from events.models import Event
        Event.objects.filter(pk=self.event.pk).update(start_time=timezone.now() - timedelta(days=5),
                                                      end_time=timezone.now() - timedelta(days=4))
        self.assertEqual(payments.release_due(later), 1)
        call = self.stripe.find('POST', '/v1/payouts')[0]
        self.assertEqual(call.headers['Stripe-Account'], 'acct_test_tixfest')
        self.assertEqual(call.params['amount'], str(order.subtotal_cents))  # ticket money only
        payout.refresh_from_db()
        self.assertEqual(payout.status, Payout.RELEASED)
        webhook(self.client, dict(fixture('event_payout_paid'), data={'object': dict(
            fixture('payout'), id=payout.stripe_payout_id, status='paid')}))
        payout.refresh_from_db()
        self.assertEqual(payout.stripe_status, 'paid')

    def test_payout_waits_while_funds_settle(self):
        self.paid(1)
        from events.models import Event
        Event.objects.filter(pk=self.event.pk).update(start_time=timezone.now() - timedelta(days=9),
                                                      end_time=timezone.now() - timedelta(days=8))
        Payout.objects.update(release_at=timezone.now() - timedelta(hours=1))
        self.stripe.responses[('POST', '/v1/payouts')] = (400, fixture('error_balance'))
        self.assertEqual(payments.release_due(), 0)
        payout = Payout.objects.get()
        self.assertEqual(payout.status, Payout.SCHEDULED)
        self.assertIn('insufficient', payout.error)

    def test_free_order_skips_stripe(self):
        order = inventory.create_hold(self.event, [(self.free.id, 2, None)])
        order.email = 'free@example.com'
        order.save()
        with self.captureOnCommitCallbacks(execute=True):
            payments.complete_free(order)
        self.assertEqual(order.tickets.count(), 2)
        self.assertEqual(self.stripe.calls, [])
        self.assertEqual(len(mail.outbox), 1)

    def test_expired_session_webhook_releases_the_hold(self):
        order = self.held()
        payments.start_checkout(order)
        evt = fixture('event_session_expired')
        evt['data']['object']['metadata'] = {'order_id': str(order.public_id)}
        webhook(self.client, evt)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.EXPIRED)


@override_settings(**STRIPE_SETTINGS, SITE_URL='https://attnd.app')
class ConnectTests(TestCase):
    def setUp(self):
        self.stripe = FakeStripe().install(self)

    def test_express_account_with_manual_payouts(self):
        user = User.objects.create_user('o', 'o@example.com', 'pw')
        account = payments.connect_account(user)
        p = self.stripe.find('POST', '/v1/accounts')[0].params
        self.assertEqual(p['type'], 'express')
        self.assertEqual(p['settings']['payouts']['schedule']['interval'], 'manual')
        self.assertFalse(account.ready)
        self.assertEqual(payments.connect_account(user), account)  # one per organizer
        event, _, _ = ticketed_event(owner=user, event_code='CONN')
        self.assertTrue(payments.onboarding_url(account, event).startswith('https://connect.stripe.com/'))
        webhook(self.client, fixture('event_account_updated'))
        account = PayoutAccount.objects.get(stripe_account_id='acct_1QTestOrganizer')
        self.assertTrue(account.ready and account.payouts_enabled)

    @override_settings(STRIPE_SECRET_KEY='')
    def test_disabled_without_keys(self):
        from tickets import stripe_api
        with self.assertRaises(stripe_api.NotConfigured):
            stripe_api.get('/v1/balance')
        event, ga, free = ticketed_event()
        self.assertFalse(inventory.paid_ready(event))
        with self.assertRaises(inventory.CartError):
            inventory.create_hold(event, [(ga.id, 1, None)])
        self.assertEqual(inventory.create_hold(event, [(free.id, 1, None)]).total_cents, 0)

    def test_form_encoding(self):
        from tickets.stripe_api import encode
        self.assertEqual(encode({'a': {'b': [{'c': 1}], 'd': True}, 'e': None}),
                         [('a[b][0][c]', '1'), ('a[d]', 'true')])

    def test_signature_scheme(self):
        from tickets import stripe_api
        body = b'{"id": "evt_1"}'
        header = stripe_api.sign_payload(body, 'whsec_x', timestamp=1000)
        self.assertEqual(stripe_api.verify_webhook(body, header, ['whsec_x'], now=1100)['id'], 'evt_1')
        with self.assertRaises(stripe_api.SignatureError):
            stripe_api.verify_webhook(body, header, ['whsec_x'], now=2000)  # too old: replay
        with self.assertRaises(stripe_api.SignatureError):
            stripe_api.verify_webhook(body + b' ', header, ['whsec_x'], now=1100)


class TicketTypeMoneyTests(TestCase):
    def test_all_in_shown_for_types(self):
        event, ga, free = ticketed_event()
        rows = {r['type'].name: r for r in inventory.listing(event)}
        self.assertEqual(rows['General Admission']['all_in_cents'], money.all_in(2500))
        self.assertEqual(rows['RSVP']['all_in_cents'], 0)
        self.assertEqual(TicketType.MONEY_FIELDS, ['kind', 'price_cents', 'quantity'])
