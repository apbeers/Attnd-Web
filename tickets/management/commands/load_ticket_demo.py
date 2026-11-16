"""The ticketed demo event for walkthroughs and the live suites (spec: Testing → walkthrough kit).

    python manage.py load_ticket_demo [--owner you@example.com] [--stripe-account acct_…]
    heroku run -a attnd-dev python manage.py load_ticket_demo --owner you@example.com --stripe-account acct_…

Creates (or resets, keeping the same event id) event TIXDEMO on the Studio + tickets plan, a week from now, with every
ticket kind, a promo code, a door team, a test organizer's payout account and some orders. Buyers use the test
domain (settings.TICKETS_TEST_EMAIL_DOMAIN), so nobody is emailed. DEMO facts (tickets/tests_demo_facts.py, and the
API-017 entry in API_CHANGES.md):
    7 ticket types (sort order): General Admission $25 ×200, VIP $75 ×20 (pool "Venue" 250), Kids under 12 free ×50,
    Support the artists pay-what-you-want (min $5), Parking add-on $10 ×40, Backstage hidden (unlock BACKSTAGE) $150
    ×10, Early Bird $15 ×10 (sold out, waitlist); promo SAVE10 = 10% off; door staff door@<test domain>;
    paid orders: buyer1 (2 × General Admission, one checked in at "Gate A"), buyer2 (VIP + Parking), buyer3 (10 ×
    Early Bird); buyer4's General Admission order is refunded.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from events.models import Event
from tickets import money, payments, signing
from tickets.models import (CapacityPool, CheckIn, DoorStaff, Order, OrderItem, PayoutAccount, PromoCode, Ticket,
                            TicketSettings, TicketType, WaitlistEntry)

CENTER = (32.7555, -97.3308)

TYPES = [
    # name, kind, price, quantity, extra
    ('General Admission', 'paid', 2500, 200, {'description': 'Standing room on the lawn. Doors at 6 pm.'}),
    ('VIP', 'paid', 7500, 20, {'description': 'Front section, a drink ticket and the VIP bar.', 'pool': True}),
    ('Kids under 12', 'free', 0, 50, {'description': 'Free with an adult ticket. Bring ear protection.'}),
    ('Support the artists', 'donation', 500, None, {'description': 'Pay what you want (from $5). All of it goes to '
                                                                  'the performers.'}),
    ('Parking', 'paid', 1000, 40, {'description': 'One car in Lot B.', 'add_on': True, 'reentry': True}),
    ('Backstage', 'paid', 15000, 10, {'description': 'Meet the band after the show.', 'hidden': True,
                                      'unlock_code': 'BACKSTAGE'}),
    ('Early Bird', 'paid', 1500, 10, {'description': 'The first ten tickets.', 'waitlist': True}),
]


class Command(BaseCommand):
    help = 'Create or reset the ticketed demo event (TIXDEMO).'

    def add_arguments(self, parser):
        parser.add_argument('--code', default=settings.TICKETS_DEMO_EVENT_CODE)
        parser.add_argument('--owner', default='')
        parser.add_argument('--stripe-account', default='', help='A completed Stripe test-mode Express account id')

    @transaction.atomic
    def handle(self, *args, code, owner, stripe_account, **options):
        domain = settings.TICKETS_TEST_EMAIL_DOMAIN
        owner_user = User.objects.filter(email__iexact=owner).first() if owner else None
        if owner_user is None:
            owner_user, _ = User.objects.get_or_create(username='tixdemo-organizer',
                                                       defaults={'email': f'organizer@{domain}'})
            if not owner_user.has_usable_password():
                owner_user.set_unusable_password()
                owner_user.save()
        now = timezone.now()
        start = (now + timedelta(days=7)).replace(hour=1, minute=0, second=0, microsecond=0)  # 7 pm Chicago
        fields = dict(name='Demo: Prairie Lights Concert Night', user=owner_user, plan=Event.PLAN_STUDIO,
                      description='An evening of live music on the lawn. A demo of Attnd tickets.',
                      address='Panther Island Pavilion, Fort Worth, TX 76102', latitude=CENTER[0],
                      longitude=CENTER[1], start_time=start, end_time=start + timedelta(hours=5),
                      timezone='America/Chicago', status=Event.STATUS_PUBLISHED, paid=True, public=False,
                      refresh_schedule=Event.REFRESH_OFF)
        event = Event.objects.filter(event_code=code.upper()).first()
        if event is None:
            event = Event.objects.create(event_code=code.upper(), **fields)
        else:
            self.stdout.write(f'Resetting {event.event_code} (id {event.id}).')
            CheckIn.objects.filter(event=event).delete()
            Ticket.objects.filter(event=event).delete()
            Order.objects.filter(event=event).delete()
            WaitlistEntry.objects.filter(event=event).delete()
            PromoCode.objects.filter(event=event).delete()
            TicketType.objects.filter(event=event).delete()
            CapacityPool.objects.filter(event=event).delete()
            DoorStaff.objects.filter(event=event).delete()
            Event.objects.filter(pk=event.pk).update(**fields)
            event.refresh_from_db()

        account_id = stripe_account or f'acct_demo_{event.event_code.lower()}'
        account, _ = PayoutAccount.objects.update_or_create(stripe_account_id=account_id, defaults={
            'user': owner_user, 'email': owner_user.email, 'charges_enabled': True, 'payouts_enabled': True,
            'details_submitted': True, 'livemode': False})
        TicketSettings.objects.update_or_create(event=event, defaults={
            'enabled': True, 'on_sale': True, 'payout_account': account, 'cancelled_at': None, 'cancel_reason': '',
            'sold_out': False, 'refund_policy': TicketSettings.REFUND_UNTIL, 'refund_days_before': 3,
            'page_text': 'Lawn chairs welcome. Rain or shine.',
            'refund_policy_text': 'Full refund of the ticket price until 3 days before the show.',
            'faq': 'Doors open at 6 pm. Re-entry with a Parking pass only. The venue is step-free.',
            'questions': [{'id': 't_shirt_size', 'label': 'T-shirt size', 'kind': 'select',
                           'options': ['S', 'M', 'L', 'XL'], 'required': False, 'per_ticket': True}]})
        pool = CapacityPool.objects.create(event=event, name='Venue', capacity=250)
        types = {}
        for i, (name, kind, price, quantity, extra) in enumerate(TYPES):
            extra = dict(extra)
            types[name] = TicketType.objects.create(event=event, name=name, kind=kind, price_cents=price,
                                                    quantity=quantity, sort_order=i,
                                                    pool=pool if extra.pop('pool', False) else None, **extra)
        promo = PromoCode.objects.create(event=event, code='SAVE10', percent_off=10)
        promo.ticket_types.add(types['General Admission'], types['VIP'])
        DoorStaff.objects.create(event=event, email=f'door@{domain}', name='Door demo')
        signing.event_key(event)

        def paid_order(email, lines, status=Order.PAID):
            order = Order.objects.create(event=event, email=email, name=email.split('@')[0].title(), status=Order.PAID,
                                         paid_at=now, stripe_payment_intent_id=f'pi_demo_{email.split("@")[0]}',
                                         stripe_account_id=account_id)
            subtotal = 0
            for type_name, qty in lines:
                t = types[type_name]
                OrderItem.objects.create(order=order, ticket_type=t, quantity=qty, unit_price_cents=t.price_cents)
                subtotal += t.price_cents * qty
            order.subtotal_cents, order.fee_cents = subtotal, money.fee_for(subtotal)
            order.total_cents = subtotal + order.fee_cents
            order.save()
            payments._issue(order)
            return order

        first = paid_order(f'buyer1@{domain}', [('General Admission', 2)])
        paid_order(f'buyer2@{domain}', [('VIP', 1), ('Parking', 1)])
        paid_order(f'buyer3@{domain}', [('Early Bird', 10)])
        refunded = paid_order(f'buyer4@{domain}', [('General Admission', 1)])
        for t in refunded.tickets.all():
            t.refunded_cents = t.price_cents
            payments.void(t, Ticket.REFUNDED, 'refund')
        Order.objects.filter(pk=refunded.pk).update(status=Order.REFUNDED,
                                                    refunded_cents=refunded.subtotal_cents)
        ticket = first.tickets.order_by('id').first()
        CheckIn.objects.create(event=event, ticket=ticket, client_id='demo-checkin-1', device_id='demo-door',
                               gate='Gate A', at=now, result=CheckIn.OK, staff_email=f'door@{domain}')
        Ticket.objects.filter(pk=ticket.pk).update(checked_in_at=now, checked_in_gate='Gate A', inside=True)
        WaitlistEntry.objects.create(event=event, ticket_type=types['Early Bird'], email=f'waiting@{domain}')
        TicketSettings.objects.filter(event=event).update(sold_out=False)
        event.bump_version()
        self.stdout.write(self.style.SUCCESS(
            f'{event.event_code} (id {event.id}): {len(TYPES)} ticket types, {Ticket.objects.filter(event=event).count()} '
            f'tickets. Ticket page: {settings.SITE_URL}/t/{event.event_code}/'))
