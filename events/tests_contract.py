"""API contract guard.

`contract/v2.json` is a snapshot of every v2 endpoint and the shape (keys and JSON types) of every
response and push payload the apps decode. This test rebuilds the snapshot from the real views and
compares:

- a key, endpoint or push type that disappears, or a type that changes, is a BREAKING change for
  apps already in users' hands. Restore it, or follow "Breaking changes" in API_CHANGES.md.
- a new key or endpoint is an additive change. Record it with:

      UPDATE_CONTRACT=1 uv run python manage.py test events.tests_contract

  and document it in API_V2.md and API_CHANGES.md in the same commit.
"""
import json
import os
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import get_resolver
from django.utils import timezone

from events import push
from events.models import Device, Event, Location, Notification, ScheduledItem

CONTRACT = Path(settings.BASE_DIR) / 'contract' / 'v2.json'
AUTH = {'HTTP_AUTHORIZATION': 'Bearer contract-key'}


def shape(value):
    """JSON type structure of a value: objects keep their keys, lists describe their first element."""
    if isinstance(value, dict):
        return {k: shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, int):
        return 'integer'
    if isinstance(value, float):
        return 'number'
    if isinstance(value, str):
        return 'string'
    return 'null'


def diff(old, new, path='$'):
    """Return (breaking, additive) lists of human-readable differences between two shapes."""
    breaking, additive = [], []
    if isinstance(old, dict) and isinstance(new, dict):
        for key in old:
            if key not in new:
                breaking.append(f'{path}.{key} removed')
            else:
                b, a = diff(old[key], new[key], f'{path}.{key}')
                breaking += b
                additive += a
        additive += [f'{path}.{key} added' for key in new if key not in old]
    elif isinstance(old, list) and isinstance(new, list):
        if old and new:
            return diff(old[0], new[0], f'{path}[]')
        if old and not new:
            breaking.append(f'{path}[] no longer has items in the sample')
        elif new and not old:
            additive.append(f'{path}[] now has items')
    elif old != new and 'null' not in (old, new):
        breaking.append(f'{path} changed type {old} -> {new}')
    return breaking, additive


@override_settings(API_KEY='contract-key', SITES_HOST='sites.example')
class ApiContractTests(TestCase):
    """Builds a fully populated event so every field has a real (non-null) value."""

    def setUp(self):
        user = User.objects.create_user('contract', 'c@example.com', 'pw')
        start = timezone.now() + timedelta(hours=2)
        self.event = Event.objects.create(
            name='Contract Fair', event_code='CONTRACT', description='Desc', address='1 Main St',
            latitude=32.75, longitude=-97.33, start_time=start, end_time=start + timedelta(hours=8),
            timezone='America/Chicago', user=user, public=True, paid=True, accent_color='#FF6600',
            logo_url='https://example.com/logo.png', plan=Event.PLAN_STUDIO)
        # API-016: a published Attnd Studio site, so event.website_url is a string in the sample.
        from studio import publishing
        site = publishing.ensure_site(self.event, user)
        publishing.publish(site, publishing.new_version(site, user, files={'index.html': '<h1>Fair</h1>'}), user)
        loc = Location.objects.create(
            event=self.event, user=user, name='Stage', snippet='Main stage', description='Big',
            category='Stage', website='https://example.com', image_url='https://example.com/s.jpg',
            address='2 Main St', latitude=32.751, longitude=-97.331, tags=['live music'])
        ScheduledItem.objects.create(
            event=self.event, user=user, location=loc, name='Band', description='Live',
            start_time=start, end_time=start + timedelta(hours=1), tags=['Music'])
        self.notification = Notification.objects.create(
            event=self.event, user=user, title='Hello', body='Doors open', send_time=start, sent=True, sent_at=start)
        self.urgent = Notification.objects.create(
            event=self.event, user=user, title='Cancelled', body='Rain', send_time=start, priority='urgent')
        Device.objects.create(id='contract-device', platform='ios', push_token='tok', subscribed=True,
                              event=self.event)
        # API-017: ticketing on, one ticket held by buyer@example.com, a door team member, a revocation.
        from tickets import payments
        from tickets.models import DoorStaff, Order, OrderItem, PayoutAccount, TicketSettings, TicketType
        account = PayoutAccount.objects.create(user=user, stripe_account_id='acct_contract', charges_enabled=True,
                                               details_submitted=True)
        TicketSettings.objects.create(event=self.event, enabled=True, on_sale=True, payout_account=account)
        ga = TicketType.objects.create(event=self.event, name='GA', price_cents=2500, quantity=10)
        free = TicketType.objects.create(event=self.event, name='RSVP', kind='free')
        order = Order.objects.create(event=self.event, email='buyer@example.com', name='Bea', status=Order.HELD,
                                     expires_at=start)
        OrderItem.objects.create(order=order, ticket_type=free, quantity=2, unit_price_cents=0)
        payments.fulfil(order.public_id)
        payments.void(order.tickets.last())
        DoorStaff.objects.create(event=self.event, email='door@example.com')
        self.ticket_type = ga
        self.client = Client()

    def call(self, method, path, body=None):
        if method == 'get':
            resp = self.client.get(path, secure=True, **AUTH)
        else:
            resp = self.client.post(path, json.dumps(body or {}), content_type='application/json',
                                    secure=True, **AUTH)
        self.assertEqual(resp.status_code, 200, f'{method.upper()} {path}: {resp.content[:300]}')
        return resp.json()

    def symbols(self):
        resp = self.client.post('/api/diagnostics/symbols/?platform=android&version=2.0.1&build=2001',
                                b'com.a.B -> a.a:\n    void b() -> b\n', content_type='text/plain', secure=True, **AUTH)
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()

    def tickets(self):
        """API-017: My tickets and door mode."""
        from unittest.mock import patch as mock_patch
        e = self.event.id
        out = {}
        with mock_patch('tickets.buyers.secrets.randbelow', return_value=123456):
            out['POST tickets/code/'] = self.call('post', '/api/v2/tickets/code/', {'email': 'buyer@example.com'})
            out['POST door/code/'] = self.call('post', '/api/v2/door/code/', {'email': 'door@example.com'})
        out['POST tickets/verify/'] = self.call('post', '/api/v2/tickets/verify/', {
            'email': 'buyer@example.com', 'code': '123456', 'device_name': 'iPhone'})
        out['POST door/verify/'] = self.call('post', '/api/v2/door/verify/', {
            'email': 'door@example.com', 'code': '123456', 'device_name': 'iPhone'})
        buyer = {'HTTP_X_ATTND_BUYER': out['POST tickets/verify/']['buyer_token']}
        door = {'HTTP_X_ATTND_DOOR': out['POST door/verify/']['door_token']}

        def get(path, headers):
            resp = self.client.get(path, secure=True, **AUTH, **headers)
            self.assertEqual(resp.status_code, 200, resp.content[:300])
            return resp.json()

        def post(path, body, headers):
            resp = self.client.post(path, json.dumps(body), content_type='application/json', secure=True, **AUTH,
                                    **headers)
            self.assertEqual(resp.status_code, 200, resp.content[:300])
            return resp.json()

        out['GET tickets/mine/'] = get('/api/v2/tickets/mine/', buyer)
        ticket = next(t for t in out['GET tickets/mine/']['tickets'] if t['status'] == 'valid')
        out['GET door/events/'] = get('/api/v2/door/events/', door)
        out['GET door/events/<id>/manifest/'] = get(f'/api/v2/door/events/{e}/manifest/', door)
        out['GET door/events/<id>/revocations/'] = get(f'/api/v2/door/events/{e}/revocations/?since=0', door)
        out['POST door/events/<id>/checkins/'] = post(f'/api/v2/door/events/{e}/checkins/', {
            'device_id': 'door-ipad', 'gate': 'Gate A', 'checkins': [
                {'client_id': 'scan-1', 'token': ticket['qr'], 'at': timezone.now().isoformat(),
                 'device_result': 'ok'},
                {'client_id': 'scan-2', 'token': ticket['qr'], 'at': timezone.now().isoformat()}]}, door)
        out['GET door/events/<id>/counts/'] = get(f'/api/v2/door/events/{e}/counts/', door)
        out['POST tickets/signout/'] = post('/api/v2/tickets/signout/', {}, buyer)
        out['POST door/signout/'] = post('/api/v2/door/signout/', {}, door)
        return out

    def build(self):
        e = self.event.id
        device = {'device_id': 'contract-device', 'platform': 'ios', 'push_token': 'tok',
                  'app_version': '2.0.1', 'subscribed': True}
        responses = {
            'GET events/nearby/': self.call('get', '/api/v2/events/nearby/?lat=32.75&lng=-97.33'),
            'GET events/lookup/': self.call('get', '/api/v2/events/lookup/?q=CONTRACT'),
            'GET events/<id>/bundle/': self.call('get', f'/api/v2/events/{e}/bundle/'),
            'POST events/<id>/join/': self.call('post', f'/api/v2/events/{e}/join/', device),
            'POST events/<id>/leave/': self.call('post', f'/api/v2/events/{e}/leave/', {'device_id': 'contract-device'}),
            'POST devices/': self.call('post', '/api/v2/devices/', device),
            'POST events/<id>/usage/': self.call('post', f'/api/v2/events/{e}/usage/', {
                'platform': 'ios', 'events': [{'name': 'tab_view', 'detail': 'map', 'count': 2}]}),
            # Unversioned and frozen: old builds rely on it to learn they must update.
            'GET /api/app-status/': self.call('get', '/api/app-status/?platform=ios&version=2.0.1&api=2'),
            # API-013: debug logs and anonymous crash/hang reports.
            'POST diagnostics/logs/': self.call('post', '/api/v2/diagnostics/logs/', {
                'session': 'D-7K3QX', 'platform': 'ios', 'reason': 'debug_mode', 'entries': [{
                    'v': 1, 'ts': timezone.now().isoformat(), 'level': 'info', 'cat': 'net',
                    'event': 'net.request.finished', 'msg': 'ok'}]}),
            'POST diagnostics/reports/': self.call('post', '/api/v2/diagnostics/reports/', {
                'platform': 'android', 'reports': [{'kind': 'crash', 'stack': 'x'}]}),
            # Unversioned, CI only (attnd-android's release job).
            'POST /api/diagnostics/symbols/': self.symbols(),
            **self.tickets(),
        }
        payloads = {}

        # Keys: "<service>:<type>", plus ":urgent" for urgent announcements (API-011) and "apns:liveactivity"
        # for the Live Activity update that goes with them.
        def apns(tokens, payload, push_type='alert', **kwargs):
            if push_type == 'liveactivity':
                payloads['apns:liveactivity'] = payload
            else:
                urgent = ':urgent' if payload.get('priority') == 'urgent' else ''
                payloads[f"apns:{payload['type']}{urgent}"] = payload
            return push.SendResult()

        def fcm(tokens, data, notification=None, **kwargs):
            urgent = ':urgent' if data.get('priority') == 'urgent' else ''
            payloads[f"fcm:{data['type']}{urgent}"] = {'data': data, 'notification': notification}
            return push.SendResult()

        Device.objects.create(id='contract-android', platform='android', push_token='tok2', subscribed=True,
                              event=self.event)
        Device.objects.create(id='contract-ios', platform='ios', push_token='tok3', subscribed=True,
                              event=self.event, live_activity_token='ab12')
        with patch('events.push.send_apns', side_effect=apns), patch('events.push.send_fcm', side_effect=fcm):
            Notification.objects.filter(pk=self.notification.pk).update(sent=False)
            push.send_notification(self.notification)
            push.send_notification(self.urgent)
            push.notify_content_updated(self.event)

        resolver = get_resolver().url_patterns
        v2 = next(p for p in resolver if str(p.pattern) == 'api/v2/')
        endpoints = sorted(str(p.pattern) for p in v2.url_patterns)
        return {
            'endpoints': endpoints,
            'responses': {k: shape(v) for k, v in responses.items()},
            'push': {k: shape(v) for k, v in sorted(payloads.items())},
        }

    def test_contract_matches_snapshot(self):
        current = self.build()
        if os.environ.get('UPDATE_CONTRACT'):
            CONTRACT.parent.mkdir(exist_ok=True)
            CONTRACT.write_text(json.dumps(current, indent=2) + '\n')
            self.skipTest(f'wrote {CONTRACT}')
        self.assertTrue(CONTRACT.exists(), 'contract/v2.json is missing: run with UPDATE_CONTRACT=1')
        saved = json.loads(CONTRACT.read_text())

        breaking = [f'endpoint {p} removed' for p in saved['endpoints'] if p not in current['endpoints']]
        additive = [f'endpoint {p} added' for p in current['endpoints'] if p not in saved['endpoints']]
        for section in ('responses', 'push'):
            b, a = diff(saved[section], current[section], section)
            breaking += b
            additive += a

        self.assertFalse(breaking, 'BREAKING API change for apps already installed:\n  ' + '\n  '.join(breaking)
                         + '\nRestore the old shape, or follow "Breaking changes" in API_CHANGES.md.')
        self.assertFalse(additive, 'Additive API change not recorded:\n  ' + '\n  '.join(additive)
                         + '\nRun `UPDATE_CONTRACT=1 uv run python manage.py test events.tests_contract`, then '
                           'document it in API_V2.md and add an API_CHANGES.md entry.')

    def test_diff_classifies_changes(self):
        old = {'a': 'string', 'b': {'c': 'integer'}, 'l': [{'x': 'string'}]}
        b, a = diff(old, {'a': 'integer', 'b': {'c': 'integer', 'd': 'string'}, 'l': [{}]})
        self.assertIn('$.a changed type string -> integer', b)
        self.assertIn('$.l[].x removed', b)
        self.assertEqual(a, ['$.b.d added'])
        # null <-> a type is not a change: optional values are null in some samples.
        self.assertEqual(diff({'a': 'string'}, {'a': 'null'}), ([], []))
