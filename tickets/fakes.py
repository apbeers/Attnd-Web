"""Test helpers: Stripe mocked at the HTTP layer (tickets.stripe_api._send) with the payloads in fixtures/stripe/."""

import copy
import itertools
import json
import re
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qsl, urlsplit

from django.contrib.auth.models import User
from django.utils import timezone

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'stripe'
STRIPE_SETTINGS = dict(STRIPE_SECRET_KEY='sk_test_unit', STRIPE_WEBHOOK_SECRET='whsec_unit',
                       STRIPE_CONNECT_WEBHOOK_SECRET='')


def fixture(name):
    return json.loads((FIXTURES / f'{name}.json').read_text())


def unflatten(pairs):
    """a[b][0][c]=v → nested dicts (lists stay dicts keyed '0', '1', …): enough to assert on."""
    out = {}
    for key, value in pairs:
        parts = re.findall(r'[^\[\]]+', key)
        node = out
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value
    return out


class Call:
    def __init__(self, method, path, params, headers):
        self.method, self.path, self.params, self.headers = method, path, params, headers

    def __repr__(self):
        return f'{self.method} {self.path}'


class FakeStripe:
    """Answers like Stripe test mode. `responses[(method, path regex)] = (status, body)` overrides a route."""

    def __init__(self):
        self.calls = []
        self.responses = {}
        self.amount_received = None
        self._ids = itertools.count(1)

    def install(self, test):
        patcher = patch('tickets.stripe_api._send', self)
        patcher.start()
        test.addCleanup(patcher.stop)
        return self

    def find(self, method, path_re):
        return [c for c in self.calls if c.method == method and re.fullmatch(path_re, c.path)]

    def __call__(self, method, url, data, headers, timeout):
        parts = urlsplit(url)
        raw = data.decode() if data else parts.query
        params = unflatten(parse_qsl(raw, keep_blank_values=True))
        path = parts.path
        self.calls.append(Call(method, path, params, headers))
        for (m, pattern), (status, body) in self.responses.items():
            if m == method and re.fullmatch(pattern, path):
                return status, json.dumps(body).encode()
        return 200, json.dumps(self.route(method, path, params)).encode()

    def route(self, method, path, params):
        n = next(self._ids)
        if method == 'POST' and path == '/v1/accounts':
            return fixture('account')
        if method == 'GET' and path.startswith('/v1/accounts/'):
            return dict(fixture('account_ready'), id=path.rsplit('/', 1)[1])
        if path == '/v1/account_links':
            return fixture('account_link')
        if path.endswith('/login_links'):
            return fixture('login_link')
        if method == 'POST' and path == '/v1/checkout/sessions':
            body = fixture('checkout_session')
            body.update(id=f'cs_test_{n}', metadata=params.get('metadata', {}),
                        client_reference_id=params.get('client_reference_id'),
                        url=f'https://checkout.stripe.com/c/pay/cs_test_{n}')
            return body
        if path.startswith('/v1/checkout/sessions/') and path.endswith('/expire'):
            return dict(fixture('session_expired'), id=path.split('/')[4])
        if method == 'GET' and path.startswith('/v1/checkout/sessions/'):
            return dict(fixture('checkout_session'), id=path.rsplit('/', 1)[1])
        if path.startswith('/v1/payment_intents'):
            body = fixture('payment_intent')
            if self.amount_received is not None:
                body['amount'] = body['amount_received'] = self.amount_received
            if method == 'POST':
                body.update(id=f'pi_test_{n}', metadata=params.get('metadata', {}), status='succeeded')
            return body
        if path == '/v1/refunds':
            return dict(fixture('refund'), id=f're_test_{n}', amount=int(params.get('amount', 0) or 0))
        if path.endswith('/reversals'):
            return dict(fixture('transfer_reversal'), id=f'trr_test_{n}', amount=int(params.get('amount', 0)))
        if path == '/v1/payouts':
            return dict(fixture('payout'), id=f'po_test_{n}', amount=int(params.get('amount', 0)))
        raise AssertionError(f'Unexpected Stripe call {method} {path}')


def webhook(client, payload, secret='whsec_unit'):
    """POST a signed webhook event like Stripe does."""
    from tickets import stripe_api
    body = json.dumps(payload).encode()
    return client.post('/tickets/stripe/webhook/', body, content_type='application/json',
                       HTTP_STRIPE_SIGNATURE=stripe_api.sign_payload(body, secret), secure=True)


def completed_event(order, evt_id='evt_test_completed'):
    evt = copy.deepcopy(fixture('event_checkout_completed'))
    evt['id'] = evt_id
    obj = evt['data']['object']
    obj.update(id=order.stripe_session_id or 'cs_test_x', client_reference_id=str(order.public_id),
               metadata={'order_id': str(order.public_id)}, amount_total=order.total_cents)
    return evt


def ticketed_event(owner=None, ready=True, **event_fields):
    """A published Studio event selling tickets, with GA $25 ×100 and a free RSVP type."""
    from events.models import Event
    from tickets.models import PayoutAccount, TicketSettings, TicketType
    owner = owner or User.objects.create_user('organizer', 'org@example.com', 'pw')
    start = timezone.now() + timedelta(days=10)
    fields = dict(name='Tix Fest', event_code='TIXFEST', start_time=start, end_time=start + timedelta(hours=6),
                  timezone='America/Chicago', user=owner, plan=Event.PLAN_STUDIO, paid=True,
                  latitude=32.75, longitude=-97.33, address='1 Main St')
    fields.update(event_fields)
    event = Event.objects.create(**fields)
    account = PayoutAccount.objects.create(user=owner, stripe_account_id=f'acct_test_{fields["event_code"].lower()}',
                                           charges_enabled=ready, payouts_enabled=ready, details_submitted=ready)
    TicketSettings.objects.create(event=event, enabled=True, on_sale=True, payout_account=account)
    ga = TicketType.objects.create(event=event, name='General Admission', price_cents=2500, quantity=100, sort_order=1)
    free = TicketType.objects.create(event=event, name='RSVP', kind=TicketType.KIND_FREE, quantity=50, sort_order=2)
    return event, ga, free
