"""Ask Attnd on Anthropic (Claude Haiku 5.5, the official SDK, mocked: never a real call) and the run's context
block (now, the event's days in its zone; ask_user with tappable options)."""

import copy
import json
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import mock
from zoneinfo import ZoneInfo

import anthropic
import httpx2
from anthropic.types import Message
from django.test import override_settings
from django.urls import reverse

from agent import loop, providers, registry
from agent.models import AgentRun, AgentStep, AgentUsage
from agent.prompts import SYSTEM_PROMPT
from agent.tests import AgentTestCase, FakeProvider
from events.models import Event, Notification
from studio.ai import AIError, token_cost


def message(content, stop='tool_use', read=0, written=0, inp=200, out=40):
    return Message.model_validate({
        'id': 'msg_1', 'type': 'message', 'role': 'assistant', 'model': 'claude-haiku-5-5', 'stop_reason': stop,
        'stop_sequence': None, 'content': content,
        'usage': {'input_tokens': inp, 'output_tokens': out, 'cache_read_input_tokens': read,
                  'cache_creation_input_tokens': written}})


def tool_use(name, args, id_='toolu_1', thinking=True):
    blocks = [{'type': 'thinking', 'thinking': '', 'signature': f'sig-{id_}'}] if thinking else []
    return blocks + [{'type': 'tool_use', 'id': id_, 'name': name, 'input': args}]


class FakeClient:
    """Stands in for anthropic.Anthropic(): plays back responses (or raises), records each request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def api_error(cls, status, text):
    request = httpx2.Request('POST', 'https://api.anthropic.com/v1/messages')
    return cls(text, response=httpx2.Response(status, request=request), body=None)


@override_settings(ANTHROPIC_API_KEY='sk-ant-test', AGENT_PROVIDER='anthropic', AGENT_ANTHROPIC_MODEL='claude-haiku-5-5',
                   AGENT_PLANNING_EFFORT='medium', AGENT_STEP_EFFORT='low')
class ClaudeProviderTests(AgentTestCase):
    def run_claude(self, responses, run=None, fallback=None, **kwargs):
        client = FakeClient(responses)
        real = providers.get_provider

        def get(name=None, model=None):
            if (name or 'anthropic') == 'anthropic':
                return providers.ClaudeProvider(model=model, effort='medium', client=client)
            return fallback or real(name, model)
        run = run or self.make_run(**kwargs)
        with mock.patch('agent.providers.get_provider', side_effect=get):
            loop.execute(run.pk)
        run.refresh_from_db()
        return run, client

    def test_default_provider_is_anthropic_with_haiku(self):
        provider = providers.get_provider()
        self.assertIsInstance(provider, providers.ClaudeProvider)
        self.assertEqual(provider.model, 'claude-haiku-5-5')
        with override_settings(AGENT_PROVIDER='openai'), mock.patch.dict('os.environ', {'OPENAI_API_KEY': 'sk'}):
            self.assertEqual(providers.get_provider().name, 'openai')

    def test_request_shape(self):
        run, client = self.run_claude([message([{'type': 'text', 'text': 'Hello!'}], stop='end_turn')],
                                      goal='Hi')
        self.assertEqual(run.status, AgentRun.DONE)
        self.assertEqual(run.summary, 'Hello!')
        body = client.requests[0]
        self.assertEqual(body['model'], 'claude-haiku-5-5')
        for banned in ('temperature', 'top_p', 'top_k', 'thinking'):  # adaptive thinking is the default
            self.assertNotIn(banned, body)
        self.assertEqual(body['output_config'], {'effort': 'medium'})
        self.assertEqual(body['tool_choice'], {'type': 'auto', 'disable_parallel_tool_use': True})
        strict = {t['name'] for t in body['tools'] if t.get('strict')}
        self.assertEqual(strict, providers.STRICT_TOOLS & {t['name'] for t in body['tools']})  # the API's limits
        self.assertEqual(body['tools'][0]['name'], 'read_page')
        self.assertTrue(body['tools'][0]['strict'])
        self.assertTrue(all(t['input_schema']['additionalProperties'] is False for t in body['tools']))
        # The stable system prompt carries the breakpoint (tools + system cached); nothing volatile in it.
        self.assertEqual(body['system'], [{'type': 'text', 'text': SYSTEM_PROMPT, 'cache_control': {'type': 'ephemeral'}}])
        self.assertNotIn('now_utc', json.dumps(body['system']))
        first = body['messages'][0]
        self.assertEqual(first['role'], 'user')
        self.assertIn('CONTEXT (facts from the server)', first['content'][0]['text'])
        self.assertEqual(first['content'][-1]['cache_control'], {'type': 'ephemeral'})
        self.assertEqual(body['messages'][-1]['role'], 'user')  # never an assistant prefill

    def test_tool_loop_passes_blocks_back_and_reads_the_cache(self):
        page = reverse('console:locations', args=[self.event.pk])
        run, client = self.run_claude([
            message(tool_use('read_page', {'path': page, 'offset': None}), written=3000),
            message([{'type': 'thinking', 'thinking': '', 'signature': 'sig-2'},
                     {'type': 'text', 'text': 'Two locations.'}], stop='end_turn', read=3000),
        ])
        self.assertEqual(run.status, AgentRun.DONE)
        second = client.requests[1]
        self.assertEqual(second['output_config'], {'effort': 'low'})  # routine step
        assistant = second['messages'][1]
        self.assertEqual(assistant['role'], 'assistant')
        self.assertEqual(assistant['content'][0], {'type': 'thinking', 'thinking': '', 'signature': 'sig-toolu_1'})
        self.assertEqual(assistant['content'][1]['type'], 'tool_use')
        result = second['messages'][2]['content'][0]
        self.assertEqual(result['type'], 'tool_result')
        self.assertEqual(result['tool_use_id'], 'toolu_1')
        self.assertEqual(result['cache_control'], {'type': 'ephemeral'})
        # Append-only: the first message is byte-identical in both requests (apart from the moving breakpoint).
        a, b = client.requests[0]['messages'][0], second['messages'][0]
        self.assertEqual(a['content'][0]['text'], b['content'][0]['text'])
        usage = list(AgentUsage.objects.filter(run=run).order_by('pk'))
        self.assertEqual(usage[0].cached_tokens, 0)
        self.assertEqual(usage[1].cached_tokens, 3000)
        self.assertEqual(usage[1].input_tokens, 3200)
        self.assertEqual(usage[1].provider, 'anthropic')
        self.assertEqual(usage[1].cost_usd, token_cost('claude-haiku-5-5', 3200, 40, 3000))

    def test_schemas_fit_anthropic_strict_mode(self):
        """Found on dev: "Enum value 'new' does not match declared type ['string', 'null']"."""
        schema = {'type': 'object', 'additionalProperties': False, 'required': ['when', 'n'], 'properties': {
            'when': {'type': ['string', 'null'], 'enum': ['now', 'later']},
            'kind': {'type': 'string', 'enum': ['a']},
            'n': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 5},
            'list': {'type': 'array', 'items': {'type': ['string', 'null'], 'enum': ['x']}, 'maxItems': 3}}}
        out = providers.claude_schema(schema)
        self.assertEqual(out['properties']['when']['anyOf'], [{'type': 'string', 'enum': ['now', 'later']}, {'type': 'null'}])
        self.assertNotIn('type', out['properties']['when'])
        self.assertEqual(out['properties']['kind']['enum'], ['a'])
        self.assertNotIn('minimum', out['properties']['n'])
        self.assertEqual(out['properties']['list']['items']['anyOf'], [{'type': 'string', 'enum': ['x']}, {'type': 'null'}])
        self.assertNotIn('maxItems', out['properties']['list'])
        self.assertEqual(schema['properties']['when']['enum'], ['now', 'later'])  # the original is unchanged
        # Every real tool: no nullable enum without null.
        from agent.loop import tools_for

        def walk(node):
            if isinstance(node, dict):
                if 'enum' in node:  # never next to a type list
                    self.assertIsInstance(node.get('type'), str)
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        for tool in providers.ClaudeProvider(client=FakeClient([])).tools(tools_for(self.owner)):
            walk(tool['input_schema'])

    def test_haiku_price(self):
        cost = token_cost('claude-haiku-5-5', 1_000_000, 1_000_000)
        self.assertEqual(str(cost), '0.6')
        self.assertEqual(token_cost('claude-haiku-5-5', 1_000_000, 0, 1_000_000), token_cost('claude-haiku-5-5', 100_000, 0))

    def test_refusal_falls_back_to_openai(self):
        fallback = FakeProvider(['Done on OpenAI.'])
        refusal = Message.model_validate({
            'id': 'm', 'type': 'message', 'role': 'assistant', 'model': 'claude-haiku-5-5', 'stop_reason': 'refusal',
            'stop_sequence': None, 'content': [], 'stop_details': {'type': 'refusal', 'category': 'general_harms',
                                                                    'explanation': None},
            'usage': {'input_tokens': 10, 'output_tokens': 0}})
        with mock.patch('agent.loop._provider', wraps=loop._provider) as spy:
            run, client = self.run_claude([refusal], fallback=fallback)
        self.assertEqual(run.status, AgentRun.DONE)
        self.assertEqual(run.summary, 'Done on OpenAI.')
        self.assertEqual(run.provider, 'openai')
        spy.assert_any_call('openai', 'gpt-5-mini')
        self.assertIn('CONTEXT (facts from the server)', fallback.first)  # the same context, on the other provider

    def test_max_tokens_retries_with_a_larger_cap(self):
        run, client = self.run_claude([
            message(tool_use('read_page', {'path': '/console/', 'offset': None}), stop='max_tokens'),
            message([{'type': 'text', 'text': 'Done.'}], stop='end_turn'),
        ])
        self.assertEqual([r['max_tokens'] for r in client.requests], [16000, 32000])
        self.assertFalse(run.steps.filter(tool='read_page', call_id='toolu_1').exists())  # the cut-off call never ran
        self.assertEqual(run.status, AgentRun.DONE)

    def test_pause_turn_continues(self):
        run, client = self.run_claude([
            message([{'type': 'text', 'text': 'Working'}], stop='pause_turn'),
            message([{'type': 'text', 'text': 'All done.'}], stop='end_turn'),
        ])
        self.assertEqual(run.summary, 'All done.')
        self.assertEqual(client.requests[1]['messages'][-1]['role'], 'assistant')

    def test_typed_errors_become_messages(self):
        run, _ = self.run_claude([api_error(anthropic.RateLimitError, 429, 'slow down')])
        self.assertEqual(run.status, AgentRun.FAILED)
        self.assertIn('rate limited', run.error)
        run, _ = self.run_claude([api_error(anthropic.AuthenticationError, 401, 'bad key')])
        self.assertIn('ANTHROPIC_API_KEY', run.error)

    def test_strict_schema_refused_retries_without_strict(self):
        providers._strict_refused.clear()
        self.addCleanup(providers._strict_refused.clear)
        run, client = self.run_claude([
            api_error(anthropic.BadRequestError, 400, 'tools: too many strict tools / schema too complex'),
            message([{'type': 'text', 'text': 'Hi.'}], stop='end_turn'),
        ])
        self.assertEqual(run.status, AgentRun.DONE)
        self.assertTrue(client.requests[0]['tools'][0].get('strict'))
        self.assertNotIn('strict', client.requests[1]['tools'][0])

    def test_pause_and_resume_keeps_the_claude_conversation(self):
        args = {k: None for k in registry.tool_schema(registry.registry()['writes']['delete_location'])['properties']}
        args.update(event_id=self.event.pk, location_id=self.booth.pk)
        run, client = self.run_claude([message(tool_use('delete_location', args))])
        self.assertEqual(run.status, AgentRun.WAITING_CONFIRMATION)
        self.assertEqual(run.state['provider_name'], 'anthropic')
        step = run.steps.get(confirm_state=AgentStep.CONFIRM_PENDING)
        loop.cancel(run, step.pk)
        client.responses.append(message([{'type': 'text', 'text': 'Left it.'}], stop='end_turn'))

        def get(name=None, model=None):
            self.assertEqual(name, 'anthropic')
            return providers.ClaudeProvider(model=model, client=client)
        with mock.patch('agent.providers.get_provider', side_effect=get):
            loop.resume_run(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.summary, 'Left it.')
        resumed = client.requests[-1]['messages']
        self.assertEqual(resumed[1]['content'][0]['signature'], 'sig-toolu_1')
        self.assertIn('cancelled', resumed[2]['content'][0]['content'])


class ContextTests(AgentTestCase):
    """The failure on dev: "add a notification for 4pm the day of the event saying it is starting soon" got "Which
    date…? Reply with YYYY-MM-DD". The context block now carries the event's days, so a one-day event needs no
    question; a multi-day one asks with day chips."""

    def setUp(self):
        super().setUp()
        tz = ZoneInfo('America/Chicago')
        self.day = (datetime.now(tz) + timedelta(days=5)).date()
        self.event.start_time = datetime(self.day.year, self.day.month, self.day.day, 10, tzinfo=tz)
        self.event.end_time = datetime(self.day.year, self.day.month, self.day.day, 22, tzinfo=tz)
        self.event.status = Event.STATUS_PUBLISHED
        self.event.save()

    def notification_args(self, when, send_time):
        args = {k: None for k in registry.tool_schema(registry.registry()['writes']['send_notification'])['properties']}
        args.update(event_id=self.event.pk, title='Starting soon', body='Prairie Fest is starting soon!', when=when,
                    send_time=send_time)
        return args

    def context(self, provider):
        text = provider.first.split('CONTEXT (facts from the server):\n', 1)[1].split('\n\n', 1)[0]
        return json.loads(text)

    def test_context_block(self):
        run, provider = self.run_with(['ok'], goal='hi')
        data = self.context(provider)
        event = data['current_event']
        self.assertEqual(event['id'], self.event.pk)
        self.assertEqual(event['time_zone'], 'America/Chicago')
        self.assertEqual(event['start'], f'{self.day:%Y-%m-%d}T10:00')
        self.assertEqual(event['event_days'], [f'{self.day:%Y-%m-%d (%a)}'])
        self.assertTrue(event['single_day'])
        self.assertEqual(event['your_role'], 'owner')
        self.assertEqual(event['locations'], 2)
        self.assertTrue(data['now_utc'].endswith('Z'))
        self.assertIn('now_in_event_zone', event)
        self.assertIn('Never ask the person for a date or time format', SYSTEM_PROMPT)

    def test_single_day_event_schedules_without_asking(self):
        goal = 'add a notification for 4pm the day of the event saying it is starting soon'
        run, provider = self.run_with([[('send_notification',
                                         self.notification_args('later', f'{self.day:%Y-%m-%d}T16:00'))]], goal=goal)
        self.assertEqual(run.status, AgentRun.WAITING_CONFIRMATION)
        self.assertFalse(run.steps.filter(kind=AgentStep.KIND_QUESTION).exists())
        step = run.steps.get(confirm_state=AgentStep.CONFIRM_PENDING)
        self.assertIn(f'Schedule the update “Starting soon” for {self.day:%Y-%m-%d}T16:00', step.confirm_summary)
        self.assertFalse(Notification.objects.exists())  # nothing is scheduled before Confirm
        loop.confirm(run, step.pk, step.confirm_hash)
        provider.turns.append('Scheduled.')
        run = self.resume(run, provider)
        n = Notification.objects.get(event=self.event)
        self.assertFalse(n.sent)
        self.assertEqual(n.send_time, datetime(self.day.year, self.day.month, self.day.day, 16,
                                               tzinfo=ZoneInfo('America/Chicago')).astimezone(dt_timezone.utc))

    def test_multi_day_event_asks_with_day_chips(self):
        self.event.end_time = self.event.start_time + timedelta(days=1, hours=12)
        self.event.save()
        second = self.day + timedelta(days=1)
        run, provider = self.run_with([[('ask_user', {
            'question': 'Which day should it go out?',
            'options': [f'{self.day:%a, %b %-d}', f'{second:%a, %b %-d}']})]], goal='a 4pm starting soon update')
        self.assertEqual(self.context(provider)['current_event']['event_days'],
                         [f'{self.day:%Y-%m-%d (%a)}', f'{second:%Y-%m-%d (%a)}'])
        self.assertEqual(run.status, AgentRun.WAITING_REPLY)
        data = self.client.get(reverse('agent:run', args=[run.pk]), secure=True).json()
        self.assertEqual(data['run']['options'], [f'{self.day:%a, %b %-d}', f'{second:%a, %b %-d}'])
        question = next(s for s in data['steps'] if s['kind'] == 'question')
        self.assertEqual(question['options'], data['run']['options'])
        # Tapping a chip sends it as the reply.
        self.client.post(reverse('agent:reply', args=[run.pk]), {'text': f'{second:%a, %b %-d}'}, secure=True)
        provider.turns.append('Got it.')
        run = self.resume(run, provider)
        self.assertIn(f'The person replied: {second:%a, %b %-d}', provider.results[-1][1])

    def test_ask_user_without_options(self):
        run, _ = self.run_with([[('ask_user', {'question': 'What should it say?', 'options': None})]])
        self.assertEqual(run.steps.get(kind=AgentStep.KIND_QUESTION).arguments['options'], [])

    def test_no_current_event_lists_events_with_their_days(self):
        run = self.make_run()
        run.event = None
        run.save()
        _, provider = self.run_with(['ok'], run=run)
        events = self.context(provider)['your_events']
        self.assertEqual(events[0]['id'], self.event.pk)
        self.assertEqual(len(events[0]['event_days']), 1)
