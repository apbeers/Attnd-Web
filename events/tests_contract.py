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


@override_settings(API_KEY='contract-key')
class ApiContractTests(TestCase):
    """Builds a fully populated event so every field has a real (non-null) value."""

    def setUp(self):
        user = User.objects.create_user('contract', 'c@example.com', 'pw')
        start = timezone.now() + timedelta(hours=2)
        self.event = Event.objects.create(
            name='Contract Fair', event_code='CONTRACT', description='Desc', address='1 Main St',
            latitude=32.75, longitude=-97.33, start_time=start, end_time=start + timedelta(hours=8),
            timezone='America/Chicago', user=user, public=True, paid=True, accent_color='#FF6600',
            logo_url='https://example.com/logo.png')
        loc = Location.objects.create(
            event=self.event, user=user, name='Stage', snippet='Main stage', description='Big',
            category='Stage', website='https://example.com', image_url='https://example.com/s.jpg',
            address='2 Main St', latitude=32.751, longitude=-97.331)
        ScheduledItem.objects.create(
            event=self.event, user=user, location=loc, name='Band', description='Live',
            start_time=start, end_time=start + timedelta(hours=1), tags=['Music'])
        self.notification = Notification.objects.create(
            event=self.event, user=user, title='Hello', body='Doors open', send_time=start, sent=True, sent_at=start)
        Device.objects.create(id='contract-device', platform='ios', push_token='tok', subscribed=True,
                              event=self.event)
        self.client = Client()

    def call(self, method, path, body=None):
        if method == 'get':
            resp = self.client.get(path, secure=True, **AUTH)
        else:
            resp = self.client.post(path, json.dumps(body or {}), content_type='application/json',
                                    secure=True, **AUTH)
        self.assertEqual(resp.status_code, 200, f'{method.upper()} {path}: {resp.content[:300]}')
        return resp.json()

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
        }
        payloads = {}

        def apns(tokens, payload, **kwargs):
            payloads[f"apns:{payload['type']}"] = payload
            return push.SendResult()

        def fcm(tokens, data, notification=None):
            payloads[f"fcm:{data['type']}"] = {'data': data, 'notification': notification}
            return push.SendResult()

        Device.objects.create(id='contract-android', platform='android', push_token='tok2', subscribed=True,
                              event=self.event)
        with patch('events.push.send_apns', side_effect=apns), patch('events.push.send_fcm', side_effect=fcm):
            Notification.objects.filter(pk=self.notification.pk).update(sent=False)
            push.send_notification(self.notification)
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
