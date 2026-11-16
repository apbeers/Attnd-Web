"""Live tickets suite (API-017) against a deployed server, through the same endpoints the apps use.

Read-only checks run everywhere (production included). The end-to-end flow (hold → Stripe test-mode payment → webhook
→ My tickets → offline QR check → door check-in) runs only where the server has dev tools on and a CI key: the
TIXDEMO event from `manage.py load_ticket_demo`, test addresses @ci.attnd.test (never mailed; codes come from the
dev test inbox). LEAN CI: this file runs with the other live tests, which only run with the CI_FULL variable.
"""
import base64
import json
import time
import unittest
import uuid

from live_api import BASE, RUN_ID, LiveBase, get, post

DOMAIN = 'ci.attnd.test'
DEMO_CODE = 'TIXDEMO'


def unb64(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


class TicketsReadOnlyTests(LiveBase):
    def test_tokens_required(self):
        self.assertStatus(get('/api/v2/tickets/mine/'), 401)
        self.assertStatus(get('/api/v2/door/events/'), 401)
        self.assertStatus(get('/api/v2/tickets/mine/', headers={'X-Attnd-Buyer': 'nope'}), 401)

    def test_ci_key_only_test_addresses(self):
        resp = post('/api/v2/tickets/code/', {'email': 'someone@example.com'})
        self.assertIn(resp.status, (403, 401))

    def test_bundle_has_tickets_key(self):
        resp = get(f'/api/v2/events/{self.demo_id}/bundle/')
        self.assertStatus(resp, 200)
        self.assertIn('tickets', resp.json())
        self.assertIn('tickets_url', resp.json()['event'])


class TicketsFlowTests(LiveBase):
    """Dev only: needs TICKETS_DEV_TOOLS on the server (the test inbox answers) and the TIXDEMO event."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        probe = get('/api/v2/tickets/test-inbox/', params={'email': f'probe@{DOMAIN}'})
        if probe.status == 401 or (probe.status == 404 and probe.json().get('message') == 'Not found'):
            raise unittest.SkipTest('ticket dev tools are off on this server (production): flow skipped')
        lookup = get('/api/v2/events/lookup/', params={'q': DEMO_CODE})
        if lookup.status != 200:
            raise unittest.SkipTest(f'{DEMO_CODE} not loaded (manage.py load_ticket_demo)')
        cls.event = lookup.json()['event']
        cls.bundle = get(f'/api/v2/events/{cls.event["id"]}/bundle/').json()

    def sign_in(self, email, kind='tickets'):
        self.assertStatus(post(f'/api/v2/{kind}/code/', {'email': email}), 200)
        code = get('/api/v2/tickets/test-inbox/', params={'email': email}).json()['code']
        resp = post(f'/api/v2/{kind}/verify/', {'email': email, 'code': code, 'device_name': 'live tests'})
        self.assertStatus(resp, 200)
        return resp.json()

    def test_bundle_tickets(self):
        t = self.bundle['tickets']
        self.assertTrue(t['url'].endswith(f'/t/{DEMO_CODE}/'))
        self.assertTrue(t['prices_include_fees'])
        self.assertEqual(t['min_price_cents'], 0)

    def test_free_ticket_my_tickets_and_door(self):
        email = f'live-{RUN_ID}-{uuid.uuid4().hex[:6]}@{DOMAIN}'
        door = self.sign_in(f'door@{DOMAIN}', 'door')
        manifest = get(f'/api/v2/door/events/{self.event["id"]}/manifest/', headers={'X-Attnd-Door': door['door_token']})
        self.assertStatus(manifest, 200)
        kids = next(t for t in manifest.json()['ticket_types'] if t['name'] == 'Kids under 12')
        order = post('/api/v2/tickets/test/order/', {'event_id': self.event['id'], 'email': email,
                                                     'lines': [{'type_id': kids['id'], 'quantity': 1}]})
        self.assertStatus(order, 200)
        self.assertEqual(order.json()['status'], 'paid')
        buyer = self.sign_in(email)
        mine = get('/api/v2/tickets/mine/', headers={'X-Attnd-Buyer': buyer['buyer_token']}).json()
        ticket = mine['tickets'][0]
        head, payload, sig = ticket['qr'].split('.')
        self.assertEqual(head, 'ATK1')
        self.assertEqual(json.loads(unb64(payload))['t'], ticket['id'])
        try:  # offline verification, as the door apps do it (needs `cryptography`; stdlib-only runs skip it)
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            keys = {k['kid']: k['public_key'] for k in manifest.json()['keys']}
            Ed25519PublicKey.from_public_bytes(unb64(keys[json.loads(unb64(payload))['k']])).verify(
                unb64(sig), f'{head}.{payload}'.encode())
        except ImportError:
            pass
        scan = {'client_id': f'live-{uuid.uuid4().hex}', 'token': ticket['qr'],
                'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'device_result': 'ok'}
        h = {'X-Attnd-Door': door['door_token']}
        first = post(f'/api/v2/door/events/{self.event["id"]}/checkins/', {'device_id': 'live', 'gate': 'CI',
                                                                          'checkins': [scan]}, headers=h)
        self.assertStatus(first, 200)
        self.assertEqual(first.json()['results'][0]['result'], 'ok')
        again = post(f'/api/v2/door/events/{self.event["id"]}/checkins/', {'checkins': [scan]}, headers=h).json()
        self.assertEqual(again['results'][0]['result'], 'ok')  # idempotent
        self.assertStatus(get(f'/api/v2/door/events/{self.event["id"]}/counts/', headers=h), 200)

    def test_paid_ticket_with_stripe_test_mode(self):
        email = f'live-paid-{RUN_ID}-{uuid.uuid4().hex[:6]}@{DOMAIN}'
        bundle_types = get(f'/api/v2/door/events/{self.event["id"]}/manifest/',
                           headers={'X-Attnd-Door': self.sign_in(f'door@{DOMAIN}', 'door')['door_token']}).json()
        ga = next(t for t in bundle_types['ticket_types'] if t['name'] == 'General Admission')
        order = post('/api/v2/tickets/test/order/', {'event_id': self.event['id'], 'email': email,
                                                     'lines': [{'type_id': ga['id'], 'quantity': 1}]})
        if order.status == 400:
            self.skipTest(f'paid tickets unavailable: {order.json().get("message")}')
        self.assertStatus(order, 200)
        self.assertEqual(order.json()['total_cents'], 2606)  # $25 + the buyer's processing fee
        pay = post('/api/v2/tickets/test/pay/', {'order_id': order.json()['order_id']})
        if pay.status == 404:
            self.skipTest('Stripe is not in test mode on this server')
        self.assertStatus(pay, 200)
        buyer = self.sign_in(email)
        for _ in range(30):  # the webhook issues the ticket
            mine = get('/api/v2/tickets/mine/', headers={'X-Attnd-Buyer': buyer['buyer_token']}).json()
            if mine['tickets']:
                break
            time.sleep(2)
        self.assertEqual(len(mine['tickets']), 1, f'no ticket after the payment on {BASE}')
