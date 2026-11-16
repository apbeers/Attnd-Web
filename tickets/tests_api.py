"""App APIs (API-017): My tickets, door mode, offline check-in merge, CI scope (spec: Testing)."""

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from tickets import checkin, holders, payments, signing
from tickets.fakes import STRIPE_SETTINGS, ticketed_event
from tickets.models import CheckIn, DoorStaff, Order, OrderItem, Ticket, TicketType

APP = {'HTTP_AUTHORIZATION': 'Bearer app-key'}


def issue(event, ticket_type, email='buyer@example.com', qty=1, name='Bea'):
    order = Order.objects.create(event=event, email=email, name=name, status=Order.HELD,
                                 expires_at=timezone.now() + timedelta(minutes=5))
    OrderItem.objects.create(order=order, ticket_type=ticket_type, quantity=qty, unit_price_cents=0)
    payments.fulfil(order.public_id)
    return list(Order.objects.get(pk=order.pk).tickets.all())


@override_settings(API_KEY='app-key', **STRIPE_SETTINGS)
class ApiTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.event, self.ga, self.free = ticketed_event()

    def post(self, path, body, **headers):
        return self.client.post(f'/api/v2/{path}', json.dumps(body), content_type='application/json', secure=True,
                                **APP, **headers)

    def get(self, path, **headers):
        return self.client.get(f'/api/v2/{path}', secure=True, **APP, **headers)

    def sign_in(self, email, kind='tickets'):
        with patch('tickets.buyers.secrets.randbelow', return_value=123456):
            self.assertEqual(self.post(f'{kind}/code/', {'email': email}).status_code, 200)
        resp = self.post(f'{kind}/verify/', {'email': email, 'code': '123456', 'device_name': 'Test phone'})
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()


class MyTicketsTests(ApiTestCase):
    def test_code_verify_and_list(self):
        tickets = issue(self.event, self.free, qty=2)
        data = self.sign_in('Buyer@Example.com')
        self.assertEqual(data['email'], 'buyer@example.com')
        self.assertIn('123456', mail.outbox[0].body)
        resp = self.get('tickets/mine/', HTTP_X_ATTND_BUYER=data['buyer_token'])
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body['tickets']), 2)
        first = body['tickets'][0]
        self.assertEqual(first['event']['event_code'], 'TIXFEST')
        self.assertEqual(signing.verify_for_event(first['qr'], self.event)['t'], tickets[0].id)
        self.assertIsNone(first['apple_wallet_url'])  # Wallet not configured in tests
        etag = resp['ETag']
        self.assertEqual(self.get('tickets/mine/', HTTP_X_ATTND_BUYER=data['buyer_token'],
                                  HTTP_IF_NONE_MATCH=etag).status_code, 304)
        holders.transfer(tickets[0], 'friend@example.com', 'Fran')  # gone from my list, the change shows
        body = self.get('tickets/mine/', HTTP_X_ATTND_BUYER=data['buyer_token']).json()
        self.assertEqual(len(body['tickets']), 1)
        self.assertEqual(self.post('tickets/signout/', {}, HTTP_X_ATTND_BUYER=data['buyer_token']).status_code, 200)
        self.assertEqual(self.get('tickets/mine/', HTTP_X_ATTND_BUYER=data['buyer_token']).status_code, 401)

    def test_no_enumeration_and_limits(self):
        a = self.post('tickets/code/', {'email': 'nobody@example.com'})
        cache.clear()
        issue(self.event, self.free, email='has@example.com')
        b = self.post('tickets/code/', {'email': 'has@example.com'})
        self.assertEqual(a.json(), b.json())  # same answer with or without tickets
        for _ in range(2):
            self.post('tickets/code/', {'email': 'has@example.com'})
        self.assertEqual(self.post('tickets/code/', {'email': 'has@example.com'}).status_code, 429)
        self.assertEqual(self.post('tickets/code/', {'email': 'not-an-email'}).status_code, 400)

    def test_wrong_codes(self):
        self.post('tickets/code/', {'email': 'x@example.com'})
        for _ in range(5):
            resp = self.post('tickets/verify/', {'email': 'x@example.com', 'code': '000000'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error'], 'invalid_code')
        for _ in range(4):
            resp = self.post('tickets/verify/', {'email': 'x@example.com', 'code': '000000'})
        self.assertEqual(resp.status_code, 429)

    def test_needs_app_auth(self):
        resp = self.client.post('/api/v2/tickets/code/', '{}', content_type='application/json', secure=True)
        self.assertEqual(resp.status_code, 401)


class DoorTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        DoorStaff.objects.create(event=self.event, email='door@example.com')
        self.tickets = issue(self.event, self.free, qty=3, email='guest@example.com', name='Gus Guest')
        self.token = self.sign_in('door@example.com', 'door')['door_token']
        self.h = {'HTTP_X_ATTND_DOOR': self.token}

    def test_manifest_is_minimal_and_verifies_offline(self):
        data = self.get(f'door/events/{self.event.id}/manifest/', **self.h).json()
        self.assertEqual(len(data['tickets']), 3)
        row = data['tickets'][0]
        self.assertEqual(set(row), {'id', 'code', 'type_id', 'status', 'version', 'name', 'email_hash', 'order_ref',
                                    'checked_in_at', 'checked_in_gate', 'inside'})
        self.assertNotIn('guest@example.com', json.dumps(data))  # no emails, prices or answers on door devices
        self.assertEqual(row['email_hash'], checkin.email_hash(self.event.id, 'GUEST@example.com'))
        keys = {k['kid']: k['public_key'] for k in data['keys']}
        self.assertIsNotNone(signing.verify(signing.token_for(self.tickets[0]), keys))

    def test_checkins_offline_merge(self):
        t0, t1, t2 = self.tickets
        now = timezone.now()
        scans = [
            {'client_id': 'a1', 'token': signing.token_for(t0), 'at': (now - timedelta(minutes=5)).isoformat(),
             'device_result': 'ok'},
            {'client_id': 'b1', 'token': signing.token_for(t0), 'at': (now - timedelta(minutes=9)).isoformat(),
             'gate': 'Gate B', 'device_result': 'ok'},  # another gate, offline, earlier: it wins
            {'client_id': 'c1', 'ticket_id': t1.id, 'at': now.isoformat()},  # manual check-in by search
        ]
        resp = self.post(f'door/events/{self.event.id}/checkins/', {'device_id': 'ipad-1', 'gate': 'Gate A',
                                                                   'checkins': scans}, **self.h)
        self.assertEqual(resp.status_code, 200, resp.content)
        results = {r['client_id']: r for r in resp.json()['results']}
        self.assertEqual(results['a1']['result'], 'ok')
        self.assertEqual(results['b1']['result'], 'ok')
        self.assertEqual(results['c1']['result'], 'ok')
        self.assertEqual(CheckIn.objects.get(client_id='a1').result, 'duplicate')  # re-judged: later scan
        self.assertTrue(CheckIn.objects.get(client_id='a1').flagged)
        t0.refresh_from_db()
        self.assertEqual(t0.checked_in_gate, 'Gate B')
        # Idempotent: the same upload again changes nothing.
        again = self.post(f'door/events/{self.event.id}/checkins/', {'device_id': 'ipad-1', 'checkins': scans},
                          **self.h).json()
        self.assertEqual(CheckIn.objects.count(), 3)
        self.assertEqual({r['client_id'] for r in again['results']}, {'a1', 'b1', 'c1'})
        later = self.post(f'door/events/{self.event.id}/checkins/', {'checkins': [
            {'client_id': 'd1', 'token': signing.token_for(t0), 'at': now.isoformat()}]}, **self.h).json()
        self.assertEqual(later['results'][0]['result'], 'duplicate')
        self.assertEqual(later['results'][0]['first_gate'], 'Gate B')
        self.assertEqual(later['counts']['checked_in'], 2)

    def test_revoked_void_reentry_wrong_day(self):
        t0, t1, t2 = self.tickets
        old = signing.token_for(t0)
        holders.transfer(t0, 'new@example.com')
        payments.void(Ticket.objects.get(pk=t1.pk))
        since = self.get(f'door/events/{self.event.id}/revocations/?since=0', **self.h).json()
        self.assertEqual({r['ticket_id'] for r in since['revocations']}, {t0.id, t1.id})
        empty = self.get(f'door/events/{self.event.id}/revocations/?since={since["cursor"]}', **self.h).json()
        self.assertEqual(empty['revocations'], [])
        res = checkin.sync(self.event, [{'client_id': 'r1', 'token': old}, {'client_id': 'v1', 'ticket_id': t1.id},
                                        {'client_id': 'x1', 'token': 'ATK1.bad.sig'}])
        self.assertEqual([r['result'] for r in res], ['revoked', 'void', 'invalid'])
        self.free.reentry = True
        self.free.save()
        token = signing.token_for(t2)
        now = timezone.now()
        res = checkin.sync(self.event, [
            {'client_id': 'e1', 'token': token, 'at': (now - timedelta(hours=2)).isoformat()},
            {'client_id': 'e2', 'token': token, 'kind': 'out', 'at': (now - timedelta(hours=1)).isoformat()},
            {'client_id': 'e3', 'token': token, 'at': now.isoformat()}])
        self.assertEqual([r['result'] for r in res], ['ok', 'ok', 'ok'])
        day = TicketType.objects.create(event=self.event, name='Sunday', kind='free',
                                        valid_on=(now + timedelta(days=3)).date())
        sunday = issue(self.event, day)[0]
        self.assertEqual(checkin.sync(self.event, [{'client_id': 'w1', 'token': signing.token_for(sunday)}])[0]
                         ['result'], 'wrong_day')

    def test_access_is_rechecked_and_scoped(self):
        events = self.get('door/events/', **self.h).json()['events']
        self.assertEqual([e['id'] for e in events], [self.event.id])
        other, _, _ = ticketed_event(owner=User.objects.create_user('o2', 'o2@example.com', 'pw'), event_code='OTHER')
        self.assertEqual(self.get(f'door/events/{other.id}/manifest/', **self.h).status_code, 404)
        DoorStaff.objects.all().delete()
        self.assertEqual(self.get(f'door/events/{self.event.id}/manifest/', **self.h).status_code, 404)
        self.assertEqual(self.get(f'door/events/{self.event.id}/counts/').status_code, 401)
        buyer = self.sign_in('guest@example.com')['buyer_token']  # a buyer token is not a door token
        self.assertEqual(self.get('door/events/', HTTP_X_ATTND_DOOR=buyer).status_code, 401)

    def test_team_members_can_scan(self):
        owner_token = self.sign_in('org@example.com', 'door')['door_token']
        self.assertEqual(self.get(f'door/events/{self.event.id}/counts/', HTTP_X_ATTND_DOOR=owner_token).json()['total'], 3)


@override_settings(API_KEY='', CI_API_KEYS=['c' * 40], DEMO_EVENT_CODE='DEMO', TICKETS_DEMO_EVENT_CODE='TIXFEST',
                   TICKETS_DEV_TOOLS=True, **STRIPE_SETTINGS)
class CiScopeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.event, self.ga, self.free = ticketed_event()
        self.h = {'HTTP_AUTHORIZATION': 'Bearer ' + 'c' * 40}

    def post(self, path, body):
        return self.client.post(f'/api/v2/{path}', json.dumps(body), content_type='application/json', secure=True,
                                **self.h)

    def test_ci_keys_only_test_addresses_and_demo_events(self):
        self.assertEqual(self.post('tickets/code/', {'email': 'real@example.com'}).status_code, 403)
        self.assertEqual(self.post('tickets/code/', {'email': 'ci1@ci.attnd.test'}).status_code, 200)
        self.assertEqual(len(mail.outbox), 0)  # the test domain is never mailed
        code = self.client.get('/api/v2/tickets/test-inbox/?email=ci1@ci.attnd.test', secure=True, **self.h).json()
        token = self.post('tickets/verify/', {'email': 'ci1@ci.attnd.test', 'code': code['code']}).json()
        order = self.post('tickets/test/order/', {'event_id': self.event.id, 'email': 'ci1@ci.attnd.test',
                                                  'lines': [{'type_id': self.free.id, 'quantity': 1}]}).json()
        self.assertEqual(order['status'], 'paid')
        mine = self.client.get('/api/v2/tickets/mine/', secure=True, HTTP_X_ATTND_BUYER=token['buyer_token'],
                               **self.h).json()
        self.assertEqual(len(mine['tickets']), 1)
        other, _, free2 = ticketed_event(owner=self.event.user, event_code='REAL')
        issue(other, free2, email='ci1@ci.attnd.test')
        mine = self.client.get('/api/v2/tickets/mine/', secure=True, HTTP_X_ATTND_BUYER=token['buyer_token'],
                               **self.h).json()
        self.assertEqual(len(mine['tickets']), 1)  # CI never sees real events
        self.assertEqual(self.post('tickets/test/order/', {'event_id': other.id, 'email': 'ci1@ci.attnd.test',
                                                           'lines': []}).status_code, 404)

    @override_settings(TICKETS_DEV_TOOLS=False)
    def test_test_inbox_off_in_production(self):
        self.post('tickets/code/', {'email': 'ci2@ci.attnd.test'})
        resp = self.client.get('/api/v2/tickets/test-inbox/?email=ci2@ci.attnd.test', secure=True, **self.h)
        self.assertEqual(resp.status_code, 404)

    def test_other_keys_cannot_use_dev_tools(self):
        with override_settings(API_KEY='app-key'):
            resp = self.client.get('/api/v2/tickets/test-inbox/?email=a@ci.attnd.test', secure=True, **APP)
        self.assertEqual(resp.status_code, 404)


@override_settings(API_KEY='app-key', **STRIPE_SETTINGS)
class BundleTests(TestCase):
    def test_bundle_tickets_object(self):
        event, ga, free = ticketed_event()
        data = self.client.get(f'/api/v2/events/{event.id}/bundle/', secure=True, **APP).json()
        self.assertEqual(data['event']['tickets_url'], 'https://attnd.app/t/TIXFEST/')
        t = data['tickets']
        self.assertEqual((t['on_sale'], t['state'], t['min_price_cents'], t['max_price_cents']), (True, 'open', 0, 2606))
        self.assertEqual(t['price_text'], 'From $0.00')
        self.assertTrue(t['prices_include_fees'])
        event.plan = 'standard'
        event.save()
        data = self.client.get(f'/api/v2/events/{event.id}/bundle/', secure=True, **APP).json()
        self.assertIsNone(data['tickets'])
        self.assertIsNone(data['event']['tickets_url'])
