"""The tool list Ask Attnd sends stays inside the API's schema limits for every audience and area (Nov 21, 2026: a
request failed with 33 union-typed parameters, the limit is 16), and provider errors reach the chat only as a friendly
line (details for staff)."""

import anthropic
from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse

from agent import loop, providers
from agent.models import AgentRun
from agent.tests import AgentTestCase
from agent.tests_claude import FakeClient, api_error

UNION_LIMIT = 8  # the API allows 16; keep headroom
STRICT_OPTIONAL_LIMIT = 24  # optional parameters across all strict schemas


def unions(schema):
    n = 0
    if isinstance(schema, dict):
        if isinstance(schema.get('type'), list) or 'anyOf' in schema or 'oneOf' in schema:
            n += 1
        n += sum(unions(v) for v in schema.values())
    elif isinstance(schema, list):
        n += sum(unions(v) for v in schema)
    return n


def optional(schema):
    n = 0
    if isinstance(schema, dict):
        props = schema.get('properties')
        if isinstance(props, dict):
            n += len([k for k in props if k not in (schema.get('required') or [])])
        n += sum(optional(v) for v in schema.values())
    elif isinstance(schema, list):
        n += sum(optional(v) for v in schema)
    return n


class SchemaLimitTests(AgentTestCase):
    def sent_tools(self, user, goal='', page='', target=None):
        run = AgentRun.objects.create(user=user, goal=goal, page_url=page, event=self.event, target=target or {})
        provider = providers.ClaudeProvider(model='claude-haiku-5-5', client=FakeClient([]))
        return provider.tools(loop.tools_for(user, run))

    def test_every_audience_and_area_stays_inside_the_limits(self):
        staff = User.objects.create_user('st', 'st@example.com', 'pw', is_staff=True)
        root = User.objects.create_user('su', 'su@example.com', 'pw', is_staff=True, is_superuser=True)
        goals = [''] + [f'about the {words[0].strip()}' for words in loop.AREAS.values()] + ['studio prospect crash log']
        target = {'component': 'event_theme', 'ids': {'event_id': self.event.pk}}
        for user in (self.owner, staff, root):
            for goal in goals:
                for tgt in (None, target):
                    tools = self.sent_tools(user, goal, target=tgt)
                    with self.subTest(user=user.username, goal=goal, target=bool(tgt)):
                        total = sum(unions(t['input_schema']) for t in tools)
                        self.assertLessEqual(total, UNION_LIMIT, [t['name'] for t in tools if unions(t['input_schema'])])
                        strict = [t for t in tools if t.get('strict')]
                        self.assertLessEqual(sum(unions(t['input_schema']) for t in strict), UNION_LIMIT)
                        self.assertLessEqual(sum(optional(t['input_schema']) for t in strict), STRICT_OPTIONAL_LIMIT)

    def test_nullable_becomes_optional_plain(self):
        schema = {'type': 'object', 'additionalProperties': False, 'required': ['a', 'b', 'c', 'd'], 'properties': {
            'a': {'type': ['string', 'null']}, 'b': {'type': ['string', 'null'], 'enum': ['x', 'y', None]},
            'c': {'type': 'integer'}, 'd': {'anyOf': [{'type': 'string', 'enum': ['p']}, {'type': 'null'}]}}}
        out = providers.tool_input_schema(schema)
        self.assertEqual(out['required'], ['c'])
        self.assertEqual(out['properties']['a'], {'type': 'string'})
        self.assertEqual(out['properties']['b'], {'type': 'string', 'enum': ['x', 'y']})
        self.assertEqual(out['properties']['d'], {'type': 'string', 'enum': ['p']})
        self.assertEqual(unions(out), 0)

    def test_a_missing_argument_keeps_the_value_like_null(self):
        from agent import internal, registry
        run = self.make_run()
        out = internal.execute(run, registry.registry()['writes']['edit_location'],
                               {'event_id': self.event.pk, 'location_id': self.stage.pk, 'snippet': 'Big tent'})
        self.assertTrue(out['ok'], out)
        self.stage.refresh_from_db()
        self.assertEqual(self.stage.name, 'Main Stage')  # not sent: kept
        self.assertEqual(self.stage.snippet, 'Big tent')


@override_settings(ANTHROPIC_API_KEY='sk-ant-test', AGENT_ANTHROPIC_MODEL='claude-haiku-5-5', AGENT_EFFORT='medium')
class FriendlyErrorTests(AgentTestCase):
    RAW = ("Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'Schemas "
           "contains too many parameters with union types'}}")

    def fail_run(self, user=None, web=None):
        client = FakeClient([api_error(anthropic.BadRequestError, 400, self.RAW)])
        run = self.make_run(user=user, client=web)
        from unittest import mock
        with mock.patch('agent.providers.get_provider',
                        side_effect=lambda name=None, model=None: providers.ClaudeProvider(model=model, client=client)), \
                self.assertLogs('attnd.agent', level='WARNING') as logs:
            loop.execute(run.pk)
        run.refresh_from_db()
        return run, logs

    def test_raw_error_never_reaches_the_chat(self):
        run, logs = self.fail_run()
        self.assertEqual(run.status, AgentRun.FAILED)
        self.assertEqual(run.error, providers.FRIENDLY_ERROR)
        self.assertNotIn('invalid_request_error', run.error)
        self.assertIn('400', run.error_details)
        self.assertTrue(any('union types' in line for line in logs.output))  # the details are logged
        data = self.client.get(reverse('agent:run', args=[run.pk]), secure=True).json()['run']
        self.assertEqual(data['error'], providers.FRIENDLY_ERROR)
        self.assertEqual(data['error_details'], '')  # organizers: no details

    def test_staff_see_details(self):
        from console.testing import TwoFactorClient
        staff = User.objects.create_user('st', 'st@example.com', 'pw', is_staff=True)
        client = TwoFactorClient()
        client.force_login(staff)
        run, _ = self.fail_run(user=staff, web=client)
        data = client.get(reverse('agent:run', args=[run.pk]), secure=True).json()['run']
        self.assertIn('400', data['error_details'])
        self.assertNotIn('Schemas', data['error_details'])  # status, type, request id only
