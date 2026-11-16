"""Unit tests: fees and all-in prices, QR token signing, inventory holds, promo/unlock rules (spec: Testing → Unit)."""

import threading
from datetime import timedelta

from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from tickets import inventory, money, signing
from tickets.fakes import STRIPE_SETTINGS, ticketed_event
from tickets.models import CapacityPool, Order, PromoCode, TicketKey, TicketType


class MoneyTests(TestCase):
    def test_gross_up(self):
        # (2500 + 30) / 0.971 = 2605.56 → 2606: the organizer keeps $25.00, the fee pays Stripe's 2.9% + 30¢.
        self.assertEqual(money.fee_for(2500), 106)
        self.assertEqual(money.all_in(2500), 2606)
        self.assertEqual(money.fee_for(0), 0)
        self.assertEqual(money.total_for(0), 0)
        for subtotal in (50, 999, 2500, 7500, 123456):
            total = money.total_for(subtotal)
            stripe_fee = total * 0.029 + 30
            self.assertGreaterEqual(total - subtotal, stripe_fee - 0.5)  # covers Stripe's fee (it rounds)
            self.assertLess(total - subtotal - stripe_fee, 1.5)  # and not more than a cent or so over

    def test_order_total_never_more_than_sum_of_all_in_prices(self):
        for prices in ([2500, 2500], [2500, 7500, 1000], [500] * 10):
            self.assertLessEqual(money.total_for(sum(prices)), sum(money.all_in(p) for p in prices))

    @override_settings(TICKETS_FEE_PERCENT='3.5', TICKETS_FEE_FIXED_CENTS=0)
    def test_rate_from_settings(self):
        self.assertEqual(money.fee_for(1000), 37)  # 1000 / 0.965 = 1036.27 → 1037

    def test_split_fee_adds_up(self):
        for fee, amounts in ((106, [2500]), (368, [2500, 2500]), (7, [1, 1, 1]), (100, [0, 0]), (10, [3000, 1000])):
            shares = money.split_fee(fee, amounts)
            self.assertEqual(sum(shares), fee if sum(amounts) else 0)
        self.assertEqual(money.split_fee(10, [3000, 1000]), [8, 2])

    def test_format_and_parse(self):
        self.assertEqual(money.fmt(2606), '$26.06')
        self.assertEqual(money.fmt(123456789), '$1,234,567.89')
        self.assertEqual(money.fmt(0), '$0.00')
        self.assertEqual(money.parse_dollars('$1,025.50'), 102550)
        self.assertEqual(money.parse_dollars('25'), 2500)
        for bad in ('', '-1', '2.555', 'abc'):
            with self.assertRaises(Exception):
                money.parse_dollars(bad)


class SigningTests(TestCase):
    def setUp(self):
        self.event, self.ga, _ = ticketed_event()
        from tickets.models import Ticket
        order = Order.objects.create(event=self.event, email='a@example.com', status=Order.PAID)
        self.ticket = Ticket.objects.create(order=order, event=self.event, ticket_type=self.ga)

    def test_token_roundtrip_offline(self):
        token = signing.token_for(self.ticket)
        self.assertTrue(token.startswith('ATK1.'))
        self.assertLess(len(token), 200)  # small QR
        keys = {k['kid']: k['public_key'] for k in signing.public_keys(self.event)}
        payload = signing.verify(token, keys)  # what a door device does, no database
        self.assertEqual(payload, {'e': self.event.id, 'k': 1, 'n': 1, 't': self.ticket.id, 'y': self.ga.id})

    def test_tampering_and_wrong_keys_fail(self):
        token = signing.token_for(self.ticket)
        keys = {k['kid']: k['public_key'] for k in signing.public_keys(self.event)}
        head, body, sig = token.split('.')
        forged = signing.b64url(signing.unb64url(body).replace(b'"n":1', b'"n":9'))
        self.assertIsNone(signing.verify(f'{head}.{forged}.{sig}', keys))
        self.assertIsNone(signing.verify(token[:-3] + 'AAA', keys))
        self.assertIsNone(signing.verify(token, {}))
        self.assertIsNone(signing.verify('nonsense', keys))
        other, _, _ = ticketed_event(owner=self.event.user, event_code='OTHER')
        self.assertIsNone(signing.verify_for_event(token, other))  # another event's key

    def test_private_key_encrypted_at_rest(self):
        signing.token_for(self.ticket)
        key = TicketKey.objects.get(event=self.event)
        self.assertNotIn(key.public_key, key.private_key)
        self.assertTrue(key.private_key.startswith('gAAAA'))  # Fernet
        with override_settings(TICKETS_KEY_SECRET='another'):
            with self.assertRaises(Exception):
                signing.token_for(self.ticket)

    def test_version_bump_changes_token(self):
        first = signing.token_for(self.ticket)
        self.ticket.version = 2
        self.ticket.save()
        second = signing.token_for(self.ticket)
        self.assertNotEqual(first, second)


@override_settings(**STRIPE_SETTINGS)
class InventoryTests(TestCase):
    def setUp(self):
        self.event, self.ga, self.free = ticketed_event()

    def hold(self, lines, **kw):
        return inventory.create_hold(self.event, lines, **kw)

    def test_hold_totals_and_expiry(self):
        order = self.hold([(self.ga.id, 2, None)])
        self.assertEqual((order.subtotal_cents, order.fee_cents, order.total_cents), (5000, 181, 5181))
        self.assertEqual(order.status, Order.HELD)
        self.assertAlmostEqual((order.expires_at - timezone.now()).total_seconds(), 600, delta=5)
        self.assertEqual(inventory.availability(self.event)[self.ga.id], 98)
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(inventory.availability(self.event)[self.ga.id], 100)  # lapsed holds count for nothing
        self.assertEqual(inventory.expire_holds(), 1)
        self.assertEqual(Order.objects.get(pk=order.pk).status, Order.EXPIRED)

    def test_limits(self):
        with self.assertRaisesMessage(inventory.CartError, 'Pick at least one'):
            self.hold([(self.ga.id, 0, None)])
        with self.assertRaisesMessage(inventory.CartError, 'up to 10'):
            self.hold([(self.ga.id, 11, None)])
        self.ga.quantity = 3
        self.ga.save()
        with self.assertRaisesMessage(inventory.CartError, 'Only 3'):
            self.hold([(self.ga.id, 4, None)])
        self.ga.sales_start = timezone.now() + timedelta(days=1)
        self.ga.save()
        with self.assertRaisesMessage(inventory.CartError, 'isn’t on sale yet'):
            self.hold([(self.ga.id, 1, None)])

    def test_sales_state(self):
        ts = inventory.ticketing(self.event)
        ts.on_sale = False
        ts.save()
        with self.assertRaisesMessage(inventory.CartError, 'aren’t on sale'):
            self.hold([(self.free.id, 1, None)])
        self.event.plan = 'standard'
        self.event.save()
        self.assertEqual(inventory.sales_state(self.event)[0], 'off')

    def test_paid_needs_ready_payouts_free_does_not(self):
        account = inventory.ticketing(self.event).payout_account
        account.charges_enabled = False
        account.save()
        with self.assertRaisesMessage(inventory.CartError, 'Paid tickets aren’t available'):
            self.hold([(self.ga.id, 1, None)])
        order = self.hold([(self.free.id, 2, None)])
        self.assertEqual(order.total_cents, 0)
        self.assertEqual(order.fee_cents, 0)

    def test_hidden_unlock_and_add_on(self):
        vip = TicketType.objects.create(event=self.event, name='Backstage', price_cents=9000, hidden=True,
                                        unlock_code='SECRET')
        parking = TicketType.objects.create(event=self.event, name='Parking', price_cents=1000, add_on=True)
        self.assertNotIn(vip.id, [r['type'].id for r in inventory.listing(self.event)])
        self.assertIn(vip.id, [r['type'].id for r in inventory.listing(self.event, ['secret'])])
        with self.assertRaises(inventory.CartError):
            self.hold([(vip.id, 1, None)])
        self.hold([(vip.id, 1, None)], unlock_codes=['secret'])
        with self.assertRaisesMessage(inventory.CartError, 'Add-ons'):
            self.hold([(parking.id, 1, None)])
        self.hold([(parking.id, 1, None), (self.ga.id, 1, None)])

    def test_donation_minimum(self):
        tip = TicketType.objects.create(event=self.event, name='Tip', kind=TicketType.KIND_DONATION, price_cents=500)
        with self.assertRaisesMessage(inventory.CartError, 'minimum is $5.00'):
            self.hold([(tip.id, 1, '4.99')])
        order = self.hold([(tip.id, 1, '12.50')])
        self.assertEqual(order.subtotal_cents, 1250)

    def test_promo_codes(self):
        promo = PromoCode.objects.create(event=self.event, code='save10', percent_off=10, max_uses=1)
        order = self.hold([(self.ga.id, 2, None)], promo_code='SAVE10')
        self.assertEqual(order.discount_cents, 500)
        self.assertEqual(order.fee_cents, money.fee_for(4500))
        with self.assertRaisesMessage(inventory.CartError, 'used up'):
            self.hold([(self.ga.id, 1, None)], promo_code='save10')
        with self.assertRaisesMessage(inventory.CartError, 'isn’t valid'):
            self.hold([(self.ga.id, 1, None)], promo_code='NOPE')
        flat = PromoCode.objects.create(event=self.event, code='FLAT', amount_off_cents=5000)
        flat.ticket_types.add(self.ga)
        order = self.hold([(self.ga.id, 1, None)], promo_code='flat')
        self.assertEqual(order.total_cents, 0)  # never below zero; a free order needs no Stripe
        promo.refresh_from_db()

    def test_shared_pool(self):
        pool = CapacityPool.objects.create(event=self.event, name='Venue', capacity=5)
        vip = TicketType.objects.create(event=self.event, name='VIP', price_cents=7500, pool=pool)
        self.ga.pool = pool
        self.ga.save()
        self.hold([(self.ga.id, 3, None)])
        with self.assertRaisesMessage(inventory.CartError, 'Only 2'):
            self.hold([(vip.id, 3, None)])
        with self.assertRaisesMessage(inventory.CartError, 'combination'):
            self.hold([(vip.id, 2, None), (self.ga.id, 1, None)])

    def test_sold_out_flag_bumps_version_only_when_it_flips(self):
        self.free.quantity = 1
        self.free.save()
        self.ga.quantity = 1
        self.ga.save()
        self.hold([(self.ga.id, 1, None)])
        self.hold([(self.free.id, 1, None)])
        version = self.event.content_version
        self.assertTrue(inventory.refresh_sold_out(self.event))
        self.assertEqual(self.event.content_version, version + 1)
        self.assertFalse(inventory.refresh_sold_out(self.event))


@override_settings(**STRIPE_SETTINGS)
class OversellRaceTests(TransactionTestCase):
    """Parallel transactions on Postgres: SELECT … FOR UPDATE keeps the last tickets to one buyer each."""

    def test_concurrent_holds_never_oversell(self):
        event, ga, _ = ticketed_event()
        ga.quantity = 5
        ga.save()
        results, barrier = [], threading.Barrier(8)

        def buy():
            try:
                barrier.wait()
                inventory.create_hold(event, [(ga.id, 1, None)])
                results.append('ok')
            except inventory.CartError:
                results.append('sold out')
            finally:
                connection.close()

        threads = [threading.Thread(target=buy) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count('ok'), 5)
        self.assertEqual(results.count('sold out'), 3)
        self.assertEqual(inventory.availability(event)[ga.id], 0)
