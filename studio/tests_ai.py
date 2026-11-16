"""Studio AI: the agent loop and tools (providers mocked), the OpenAI and Anthropic adapters, image generation, the
spend cap, and bringing a website in (fetches mocked)."""

import io
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse

from studio import agent, ai, importer, publishing
from studio.models import SiteVersion, StudioAsset, StudioMessage, StudioRun, StudioUsage
from studio.tests import PAGE, StudioTestCase, png_bytes


class FakeProvider:
    """Plays back scripted turns; records what the agent sent."""
    name = 'fake'
    model = 'gpt-5.5'

    def __init__(self, turns):
        self.turns = list(turns)
        self.seen = []

    def start(self, text):
        self.context = text
        return [{'role': 'user', 'content': text}]

    def chat(self, system, messages, tools):
        self.system, self.tools = system, tools
        return self.turns.pop(0)

    def add_turn(self, messages, turn):
        messages.append({'role': 'assistant', 'content': turn.text})

    def add_results(self, messages, results):
        self.seen += [text for _, text in results]


def call(tool, **args):
    return ai.ToolCall(id=f'c-{tool}', name=tool, args=args)


# Responses API objects as the SDK returns them (real SDK types, so attribute names are checked).
def function_call(call_id, name, args):
    from openai.types.responses import ResponseFunctionToolCall
    return ResponseFunctionToolCall.model_validate({'type': 'function_call', 'id': f'fc_{call_id}', 'call_id': call_id,
                                                    'name': name, 'arguments': json.dumps(args), 'status': 'completed'})


def message_item(text):
    from openai.types.responses import ResponseOutputMessage
    return ResponseOutputMessage.model_validate({'type': 'message', 'id': 'msg_1', 'role': 'assistant',
                                                 'status': 'completed',
                                                 'content': [{'type': 'output_text', 'text': text, 'annotations': []}]})


def reasoning_item(item_id):
    from openai.types.responses import ResponseReasoningItem
    return ResponseReasoningItem.model_validate({'type': 'reasoning', 'id': item_id, 'summary': []})


def responses_reply(response_id, output, input_tokens=50, output_tokens=5, cached=0):
    from openai.types.responses import Response, ResponseUsage
    usage = ResponseUsage.model_validate({
        'input_tokens': input_tokens, 'output_tokens': output_tokens, 'total_tokens': input_tokens + output_tokens,
        'input_tokens_details': {'cached_tokens': cached, 'cache_write_tokens': 0},
        'output_tokens_details': {'reasoning_tokens': 0}})
    return Response.model_construct(id=response_id, status='completed', output=output, usage=usage, error=None,
                                    incomplete_details=None)


class AgentTests(StudioTestCase):
    def run_with(self, turns, prompt='Design the site', **kwargs):
        run = StudioRun.objects.create(site=self.site, created_by=self.owner, prompt=prompt, **kwargs)
        provider = FakeProvider(turns)
        with mock.patch('studio.ai.get_provider', return_value=provider):
            agent.execute(run.pk)
        run.refresh_from_db()
        return run, provider

    def test_tools_build_a_version(self):
        run, provider = self.run_with([
            ai.Turn(calls=[call('write_file', path='index.html', content='<h1>New</h1>'),
                           call('edit_file', path='styles.css', find='red', replace='blue'),
                           call('delete_file', path='about.html'),
                           call('set_app_look', accent_color='#ff0000', tabs=['schedule', 'map', 'nope'],
                                default_tab='schedule')],
                    input_tokens=10_000, output_tokens=2_000),
            ai.Turn(text='I rebuilt the home page and made it blue.', input_tokens=12_000, output_tokens=100),
        ])
        self.assertEqual(run.status, StudioRun.DONE, run.error)
        version = run.result_version
        self.assertEqual(version.files, {'index.html': '<h1>New</h1>', 'styles.css': 'body{color:blue}'})
        self.assertEqual(version.app_look['accent_color'], '#FF0000')
        self.assertEqual(version.app_look['display'], {'tabs': ['schedule', 'map'], 'default_tab': 'schedule'})
        self.assertEqual(version.source, SiteVersion.SOURCE_AI)
        self.site.refresh_from_db()
        self.assertEqual(self.site.draft, version)
        self.assertEqual(run.files_rev, 4)
        self.assertIn('Created index.html', [s['text'] for s in run.steps] + ['Created index.html'])
        self.assertEqual(StudioMessage.objects.get(run=run, role='assistant').text, run.reply)
        self.assertEqual(StudioUsage.objects.filter(run=run).count(), 2)
        self.assertEqual(run.cost_usd, ai.token_cost('gpt-5.5', 22_000, 2_100))
        # The model saw the event, the files and the data library rules.
        self.assertIn('Arts Fair', provider.context)
        self.assertIn('=== index.html ===', provider.context)
        self.assertIn('<attnd-schedule', provider.system)
        self.assertIn('frame', provider.system)

    def test_tool_errors_go_back_to_the_model(self):
        run, provider = self.run_with([
            ai.Turn(calls=[call('write_file', path='/etc/passwd.html', content='x'),
                           call('write_file', path='_attnd/attnd.js', content='x'),
                           call('edit_file', path='index.html', find='nothing like this', replace='x'),
                           call('read_file', path='missing.html'),
                           call('set_app_look', accent_color='red'),
                           call('set_flyer', asset_id='00000000-0000-0000-0000-000000000000'),
                           call('mystery')]),
            ai.Turn(text='Sorry.'),
        ])
        self.assertEqual(run.status, StudioRun.DONE)
        self.assertTrue(all(text.startswith('Error:') for text in provider.seen), provider.seen)
        self.assertIsNone(run.result_version)  # nothing changed, no version

    def test_selection_and_history_are_in_the_context(self):
        StudioMessage.objects.create(site=self.site, role='user', text='Earlier: use yellow')
        _, provider = self.run_with([ai.Turn(text='OK')], prompt='Bigger',
                                    selection={'selector': 'header > h1', 'tag': 'h1', 'text': 'Fair'})
        self.assertIn('SELECTED ELEMENT', provider.context)
        self.assertIn('header > h1', provider.context)
        self.assertIn('Earlier: use yellow', provider.context)

    def test_generate_image_and_flyer(self):
        with mock.patch('studio.ai.generate_image', return_value=(png_bytes((8, 12)), 'gpt-image-2')) as gen:
            run, provider = self.run_with([
                ai.Turn(calls=[call('generate_image', prompt='Bold poster, ARTS FAIR, Nov 21', purpose='flyer',
                                    name='flyer-art')]),
                ai.Turn(text='Made flyer art.'),
            ])
        self.assertEqual(run.status, StudioRun.DONE, run.error)
        self.assertEqual(gen.call_count, 1)
        asset = StudioAsset.objects.get()
        self.assertTrue(asset.generated)
        self.assertEqual((asset.width, asset.height, asset.name), (8, 12, 'flyer-art.png'))
        self.assertIn(str(asset.id), provider.seen[0])
        self.assertEqual(StudioUsage.objects.get(kind='image').cost_usd, Decimal('0.2500'))

    def test_set_flyer(self):
        from studio.assets import create_asset
        with self.captureOnCommitCallbacks(execute=False):
            asset = create_asset(self.event, png_bytes(), 'art.png')
        run, _ = self.run_with([ai.Turn(calls=[call('set_flyer', asset_id=str(asset.id))]), ai.Turn(text='Done')])
        self.assertEqual(run.result_version.flyer_asset, asset)

    def test_limits(self):
        files = {f'p{i}.html': 'x' for i in range(agent.MAX_FILES)}
        with self.assertRaises(agent.ToolError):
            agent.check_files({**files, 'one-more.html': 'x'})
        for bad in ('../x.html', 'a/../b.html', 'X.html', 'x.php', '.env', '_preview/a.html', 'a//b.html'):
            with self.assertRaises(agent.ToolError, msg=bad):
                agent.check_path(bad)
        self.assertEqual(agent.check_path('./js/site.js'), 'js/site.js')

    @override_settings(STUDIO_DAILY_SPEND_CAP_USD=0.01)
    def test_spend_cap_stops_the_run(self):
        run, _ = self.run_with([
            ai.Turn(calls=[call('write_file', path='index.html', content='<p>partial</p>')], input_tokens=500_000,
                    output_tokens=10_000),
            ai.Turn(text='never reached'),
        ])
        self.assertEqual(run.status, StudioRun.FAILED)
        self.assertIn('budget', run.error)
        self.assertEqual(run.result_version.files['index.html'], '<p>partial</p>')  # what was done is kept
        self.assertIn('Sorry', StudioMessage.objects.get(run=run).text)

    @override_settings(STUDIO_MAX_STEPS=2)
    def test_step_limit(self):
        loop = [ai.Turn(calls=[call('read_file', path='index.html')]) for _ in range(3)]
        run, _ = self.run_with(loop)
        self.assertEqual(run.status, StudioRun.DONE)
        self.assertIn('stopped after many steps', run.reply)

    def test_provider_failure_fails_the_run(self):
        class Broken(FakeProvider):
            def chat(self, *a):
                raise ai.AIError('The AI request failed (500)')
        run = StudioRun.objects.create(site=self.site, created_by=self.owner, prompt='x')
        with mock.patch('studio.ai.get_provider', return_value=Broken([])):
            agent.execute(run.pk)
        run.refresh_from_db()
        self.assertEqual((run.status, run.error), (StudioRun.FAILED, 'The AI request failed (500)'))
        self.assertIsNone(agent.execute(run.pk))  # a finished run is never claimed again

    def test_run_preview_shows_working_files(self):
        from studio import hosting
        run = StudioRun.objects.create(site=self.site, created_by=self.owner, prompt='x', status=StudioRun.RUNNING,
                                       working_files={'index.html': '<p>in progress</p>'})
        token = hosting.make_preview_token(self.site, run=run)
        self.assertContains(self.sites(f'/{self.site.slug}/_preview/{token}/'), 'in progress')

    def test_recover_stale(self):
        from datetime import timedelta

        from django.utils import timezone
        run = StudioRun.objects.create(site=self.site, prompt='x', status=StudioRun.RUNNING,
                                       created_at=timezone.now() - timedelta(hours=2))
        self.assertEqual(agent.recover_stale(), 1)
        run.refresh_from_db()
        self.assertEqual(run.status, StudioRun.FAILED)


class ProviderTests(StudioTestCase):
    @override_settings(ANTHROPIC_API_KEY='sk-ant-test', STUDIO_AI_PROVIDER='auto')
    def test_anthropic_over_urllib(self):
        self.assertIsInstance(ai.get_provider(), ai.AnthropicProvider)
        reply = {'model': 'claude-opus-5-5', 'stop_reason': 'tool_use', 'usage': {'input_tokens': 100, 'output_tokens': 20},
                 'content': [{'type': 'thinking', 'thinking': '', 'signature': 'sig'},
                             {'type': 'text', 'text': 'Writing it.'},
                             {'type': 'tool_use', 'id': 'tu1', 'name': 'write_file',
                              'input': {'path': 'index.html', 'content': '<p>x</p>'}}]}
        sent = {}

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(request, timeout):
            sent['request'] = request
            return Response(json.dumps(reply).encode())

        provider = ai.get_provider()
        messages = provider.start('hello')
        with mock.patch('urllib.request.urlopen', fake_urlopen):
            turn = provider.chat('SYSTEM', messages, agent.TOOLS)
        request = sent['request']
        body = json.loads(request.data)
        self.assertEqual(request.full_url, 'https://api.anthropic.com/v1/messages')
        self.assertEqual(request.headers['X-api-key'], 'sk-ant-test')
        self.assertEqual(body['model'], 'claude-opus-5-5')
        self.assertEqual(body['fallbacks'], 'default')
        self.assertNotIn('temperature', body)
        self.assertNotIn('thinking', body)
        self.assertEqual(body['tools'][0]['input_schema']['required'], ['path', 'content'])
        self.assertEqual((turn.text, turn.calls[0].name, turn.calls[0].args['path']), ('Writing it.', 'write_file', 'index.html'))
        provider.add_turn(messages, turn)
        provider.add_results(messages, [(turn.calls[0], 'ok')])
        self.assertEqual(messages[1]['content'][0]['type'], 'thinking')  # blocks go back unchanged
        self.assertEqual(messages[2]['content'][0], {'type': 'tool_result', 'tool_use_id': 'tu1', 'content': 'ok'})

    @override_settings(ANTHROPIC_API_KEY='sk-ant-test')
    def test_anthropic_refusal(self):
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        with mock.patch('urllib.request.urlopen', return_value=Response(b'{"stop_reason": "refusal", "content": []}')):
            with self.assertRaises(ai.AIError):
                ai.AnthropicProvider().chat('s', [], [])

    @override_settings(ANTHROPIC_API_KEY='', STUDIO_AI_PROVIDER='auto')
    def test_no_key_is_a_clear_error(self):
        with mock.patch.dict('os.environ', {'OPENAI_API_KEY': ''}):
            with self.assertRaisesMessage(ai.AIError, 'No AI key'):
                ai.get_provider()

    @override_settings(ANTHROPIC_API_KEY='', STUDIO_AI_PROVIDER='auto')
    @mock.patch.dict('os.environ', {'OPENAI_API_KEY': 'sk-test'})
    def test_openai_responses_api_tool_round_trip(self):
        """A whole agent run on the real OpenAIProvider with a mocked SDK client: function_call → our tool →
        function_call_output chained with previous_response_id, reasoning on, then the final text."""
        from studio.prompts import SYSTEM_PROMPT
        provider = ai.get_provider()
        self.assertIsInstance(provider, ai.OpenAIProvider)
        self.assertEqual(provider.model, 'gpt-5.5')
        client = mock.MagicMock()
        client.responses.create.side_effect = [
            responses_reply('resp_1', [reasoning_item('rs_1'), message_item('Building the home page.'),
                                       function_call('call_1', 'write_file', {'path': 'index.html', 'content': PAGE}),
                                       function_call('call_2', 'read_file', {'path': 'missing.html'})],
                            input_tokens=10_000, output_tokens=1_000, cached=0),
            responses_reply('resp_2', [reasoning_item('rs_2'), message_item('I built your home page.')],
                            input_tokens=12_000, output_tokens=100, cached=10_000),
        ]
        run = StudioRun.objects.create(site=self.site, created_by=self.owner, prompt='Design the site')
        with mock.patch('openai.OpenAI', return_value=client):
            agent.execute(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, StudioRun.DONE, run.error)
        self.assertEqual(run.reply, 'I built your home page.')
        self.assertEqual(run.result_version.files['index.html'], PAGE)
        self.assertIn('Building the home page.', [s['text'] for s in run.steps])
        first, second = (c.kwargs for c in client.responses.create.call_args_list)
        self.assertEqual(first['model'], 'gpt-5.5')
        self.assertEqual(first['reasoning'], {'effort': 'medium'})
        self.assertNotIn('reasoning_effort', first)
        self.assertEqual(first['instructions'], SYSTEM_PROMPT)
        self.assertNotIn('previous_response_id', first)
        self.assertEqual([i['role'] for i in first['input']], ['user'])
        self.assertIn('Design the site', first['input'][0]['content'])
        self.assertEqual(first['tools'][0], {'type': 'function', 'name': 'write_file',
                                             'description': agent.TOOLS[0]['description'],
                                             'parameters': agent.TOOLS[0]['parameters'], 'strict': False})
        # The second call only sends the outputs of the two calls, chained to the first response.
        self.assertEqual(second['previous_response_id'], 'resp_1')
        self.assertEqual(second['instructions'], SYSTEM_PROMPT)
        self.assertEqual([(i['type'], i['call_id']) for i in second['input']],
                         [('function_call_output', 'call_1'), ('function_call_output', 'call_2')])
        self.assertTrue(second['input'][0]['output'].startswith('Updated index.html'))
        self.assertTrue(second['input'][1]['output'].startswith('Error:'))
        # Usage: 10k fresh + 2k fresh and 10k cached (a tenth of the price), 1,100 output.
        self.assertEqual(run.cost_usd, ai.token_cost('gpt-5.5', 10_000, 1_000)
                         + ai.token_cost('gpt-5.5', 12_000, 100, cached_tokens=10_000))
        self.assertLess(ai.token_cost('gpt-5.5', 12_000, 100, cached_tokens=10_000), ai.token_cost('gpt-5.5', 12_000, 100))

    @override_settings(ANTHROPIC_API_KEY='', STUDIO_AI_PROVIDER='auto')
    @mock.patch.dict('os.environ', {'OPENAI_API_KEY': 'sk-test'})
    def test_openai_fallback_model_only_when_the_model_is_unavailable(self):
        import httpx
        from openai import BadRequestError, NotFoundError
        request = httpx.Request('POST', 'https://api.openai.com/v1/responses')
        not_found = NotFoundError('The model `gpt-5.5` does not exist or you do not have access to it.', body=None,
                                  response=httpx.Response(404, request=request))
        client = mock.MagicMock()
        client.responses.create.side_effect = [not_found, responses_reply('r1', [message_item('Hi')])]
        provider = ai.get_provider()
        with mock.patch('openai.OpenAI', return_value=client):
            turn = provider.chat('S', provider.start('x'), agent.TOOLS)
        self.assertEqual((turn.model, turn.text, turn.calls), ('gpt-5', 'Hi', []))
        self.assertEqual(client.responses.create.call_args.kwargs['model'], 'gpt-5')
        ai._unavailable_models.clear()
        # The error dev saw on Chat Completions is a parameter error, not a missing model: no fallback, a clear error.
        bad = BadRequestError('Error code: 400 - Function tools with reasoning_effort are not supported for gpt-5.5 '
                              'in /v1/chat/completions.', body=None, response=httpx.Response(400, request=request))
        client.responses.create.side_effect = [bad]
        provider = ai.get_provider()
        with mock.patch('openai.OpenAI', return_value=client):
            with self.assertRaisesMessage(ai.AIError, 'The AI request failed'):
                provider.chat('S', provider.start('x'), agent.TOOLS)
        self.assertEqual(provider.model, 'gpt-5.5')

    @mock.patch.dict('os.environ', {'OPENAI_API_KEY': 'sk-test'})
    def test_openai_failed_or_empty_responses(self):
        client = mock.MagicMock()
        failed = responses_reply('r1', [])
        failed.status, failed.error = 'failed', SimpleNamespace(message='server_error')
        empty = responses_reply('r2', [])
        empty.status, empty.incomplete_details = 'incomplete', SimpleNamespace(reason='max_output_tokens')
        client.responses.create.side_effect = [failed, empty]
        provider = ai.OpenAIProvider()
        with mock.patch('openai.OpenAI', return_value=client):
            with self.assertRaisesMessage(ai.AIError, 'server_error'):
                provider.chat('S', provider.start('x'), [])
            with self.assertRaisesMessage(ai.AIError, 'max_output_tokens'):
                provider.chat('S', provider.start('x'), [])

    @override_settings(ANTHROPIC_API_KEY='sk-ant-test')
    def test_anthropic_caches_the_conversation(self):
        messages = [{'role': 'user', 'content': 'hello'}]
        body = ai.AnthropicProvider._cached(messages)
        self.assertEqual(body[-1]['content'], [{'type': 'text', 'text': 'hello', 'cache_control': {'type': 'ephemeral'}}])
        self.assertEqual(messages, [{'role': 'user', 'content': 'hello'}])  # the agent's list is unchanged

    def test_image_generation_falls_back(self):
        import base64

        import httpx
        from openai import BadRequestError
        bad = BadRequestError('Invalid model gpt-image-2', body=None,
                              response=httpx.Response(400, request=httpx.Request('POST', 'https://api.openai.com')))
        client = mock.MagicMock()
        client.images.generate.side_effect = [bad, SimpleNamespace(data=[SimpleNamespace(
            b64_json=base64.b64encode(b'PNG').decode())])]
        with mock.patch('openai.OpenAI', return_value=client):
            data, model = ai.generate_image('art')
        self.assertEqual((data, model), (b'PNG', 'gpt-image-1'))
        self.assertEqual(client.images.generate.call_args.kwargs['size'], '1024x1536')

    def test_prices(self):
        self.assertEqual(ai.token_cost('gpt-5', 1_000_000, 100_000), Decimal('2.25'))
        self.assertEqual(ai.price('unknown-model'), (10.0, 40.0))


def fake_fetch(pages, log=None):
    """`pages`: url -> (bytes, content type[, final url after a redirect])."""
    def fetch(url, limit):
        if log is not None:
            log.append(url)
        if url not in pages:
            raise importer.SiteImportError(f'Could not load {url}')
        data, ctype, *final = pages[url]
        if len(data) > limit:
            raise importer.SiteImportError(f'{url} is too big.')
        return data, ctype, (final[0] if final else url)
    return fetch


def woff2():
    return b'wOF2' + b'\x00' * 60


class ImporterTests(StudioTestCase):
    HOME = b"""<html><head><title>Arts Fair 2026</title><link rel="stylesheet" href="/css/s.css">
      <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Anton&display=swap">
      <link rel="icon" href="/favicon.png"><link rel="apple-touch-icon" href="/touch.png">
      <meta property="og:image" content="https://artsfair.example/hero.png">
      <script>var tracking = 1;</script></head>
      <body style="background:#FFCC00"><header><a href="/"><img src="/logo-200.png"
        srcset="https://res.cdn.example/w_200,h_80/logo.png 200w, https://res.cdn.example/w_600,h_240/logo.png 600w"
        alt="Arts Fair logo"></a><nav><a href="/lineup">Lineup</a> <a href="/about/">About</a>
        <a href="https://elsewhere.example/">Tickets</a> <a href="/wp-admin/">Admin</a>
        <a href="/docs/guide.pdf">Festival guide (PDF)</a></nav></header>
      <section class="hero" style="background-image:url('/img/hero-bg.png')"><h1>THIS SATURDAY</h1>
        <a class="button" href="/map">Maps</a></section>
      <section class="sponsors"><h2>Our sponsors</h2><img src="https://cdn.example/sponsor-frost.png" alt="Frost Bank"></section>
      <picture><source srcset="https://cdn.example/photo-400.png 400w, https://cdn.example/photo-1600.png 1600w">
        <img src="https://cdn.example/photo-400.png" alt="Crowd at night"></picture>
      <img data-src="/lazy.png" src="data:image/gif;base64,R0lGOD" alt="Lazy">
      <img src="/copy-of-hero.png" alt="Same file again">
      <a href="/files/map.pdf">Site map</a></body></html>"""
    CSS = (b'@import url("more.css"); @font-face{font-family:"Bold";font-weight:700;src:url(fonts/bold.woff2) '
           b'format("woff2"),url(fonts/bold.woff) format("woff")} h1{font-family:"Bold",sans-serif;color:#111111} '
           b'a{color:#ffcc00} .banner{background:url("../img/banner.png") no-repeat}')

    def pages(self):
        hero = png_bytes((6, 4), (10, 20, 30))
        return {
            'https://artsfair.example/': (self.HOME, 'text/html'),
            'https://artsfair.example/css/s.css': (self.CSS, 'text/css'),
            'https://artsfair.example/css/more.css': (b'body{color:#123456}', 'text/css'),
            'https://artsfair.example/lineup': (b'<html><body><h1>Lineup</h1><a href="/">Home</a>'
                                                b'<img src="/img/band.png" alt="The band"></body></html>', 'text/html'),
            'https://artsfair.example/about/': (b'<html><body><h1>About</h1></body></html>', 'text/html'),
            'https://artsfair.example/map': (b'<html><body><h1>Map</h1></body></html>', 'text/html',
                                             'https://artsfair.example/map/'),
            'https://artsfair.example/hero.png': (hero, 'image/png'),
            'https://artsfair.example/copy-of-hero.png': (hero, 'image/png'),
            'https://res.cdn.example/w_600,h_240/logo.png': (png_bytes((60, 24), (1, 1, 1)), 'image/png'),
            'https://artsfair.example/favicon.png': (png_bytes((2, 2), (2, 2, 2)), 'image/png'),
            'https://artsfair.example/touch.png': (png_bytes((3, 3), (3, 3, 3)), 'image/png'),
            'https://artsfair.example/img/hero-bg.png': (png_bytes((8, 4), (4, 4, 4)), 'image/png'),
            'https://artsfair.example/img/banner.png': (png_bytes((9, 3), (5, 5, 5)), 'image/png'),
            'https://artsfair.example/img/band.png': (png_bytes((5, 5), (6, 6, 6)), 'image/png'),
            'https://artsfair.example/lazy.png': (png_bytes((4, 4), (7, 7, 7)), 'image/png'),
            'https://cdn.example/sponsor-frost.png': (png_bytes((7, 2), (8, 8, 8)), 'image/png'),
            'https://cdn.example/photo-1600.png': (png_bytes((16, 9), (9, 9, 9)), 'image/png'),
            'https://artsfair.example/css/fonts/bold.woff2': (woff2(), 'font/woff2'),
            'https://artsfair.example/docs/guide.pdf': (b'%PDF-1.7 guide', 'application/pdf'),
            'https://artsfair.example/files/map.pdf': (b'%PDF-1.7 map', 'application/pdf'),
        }

    def test_capture_finds_everything(self):
        log = []
        with mock.patch('studio.importer.fetch', fake_fetch(self.pages(), log)):
            look = importer.capture('https://artsfair.example/')
        urls = {a['url']: a for a in look['assets']}
        # srcset: the largest candidate (a URL with commas survives); picture sources; lazy-load attributes.
        self.assertIn('https://res.cdn.example/w_600,h_240/logo.png', urls)
        self.assertEqual(look['logo_url'], 'https://res.cdn.example/w_600,h_240/logo.png')
        self.assertIn('https://cdn.example/photo-1600.png', urls)
        self.assertIn('https://artsfair.example/lazy.png', urls)
        # CSS backgrounds resolve against the stylesheet's URL; inline style backgrounds; og:image; icons.
        self.assertIn('https://artsfair.example/img/banner.png', urls)
        self.assertIn('https://artsfair.example/img/hero-bg.png', urls)
        self.assertIn('social', urls['https://artsfair.example/hero.png']['tags'])
        self.assertIn('icon', urls['https://artsfair.example/touch.png']['tags'])
        self.assertIn('sponsor', urls['https://cdn.example/sponsor-frost.png']['tags'])
        self.assertEqual(urls['https://cdn.example/sponsor-frost.png']['alt'], 'Frost Bank')
        # Fonts: self-hosted @font-face (woff2 preferred), Google Fonts kept as Google Fonts; @import followed.
        self.assertEqual([f['url'] for f in look['font_faces']], ['https://artsfair.example/css/fonts/bold.woff2'])
        self.assertEqual(look['google_fonts'], ['Anton'])
        self.assertIn('#123456', look['colors'])
        # PDFs with their role.
        self.assertIn('guide', urls['https://artsfair.example/docs/guide.pdf']['tags'])
        self.assertIn('map', urls['https://artsfair.example/files/map.pdf']['tags'])
        # Same-site pages only, no admin pages, redirects followed once.
        self.assertEqual([p['url'] for p in look['pages']], [
            'https://artsfair.example/', 'https://artsfair.example/lineup', 'https://artsfair.example/about/',
            'https://artsfair.example/map/'])
        self.assertNotIn('https://elsewhere.example/', log)
        self.assertNotIn('https://artsfair.example/wp-admin/', log)
        self.assertIn('https://artsfair.example/img/band.png', urls)  # images of inner pages too
        self.assertNotIn('tracking', look['pages'][0]['html'])  # scripts are dropped

    def test_crawl_limits(self):
        links = ''.join(f'<a href="/p{i}">Page {i}</a>' for i in range(40)).encode()
        pages = {'https://big.example/': (b'<html><body><nav>' + links + b'</nav></body></html>', 'text/html')}
        for i in range(40):
            pages[f'https://big.example/p{i}'] = (b'<html><body><a href="/p%d">next</a></body></html>' % (i + 1),
                                                   'text/html')
        log = []
        with mock.patch('studio.importer.fetch', fake_fetch(pages, log)):
            look = importer.capture('https://big.example/')
        self.assertEqual(len(look['pages']), importer.MAX_PAGES)
        self.assertEqual(len(log), importer.MAX_PAGES)
        # The byte cap stops the crawl but keeps what was read.
        with mock.patch('studio.importer.fetch', fake_fetch(pages)):
            look = importer.capture('https://big.example/', limits=importer.Limits(max_bytes=len(pages[
                'https://big.example/'][0]) + 10))
        self.assertEqual(len(look['pages']), 2)
        self.assertIn('more files than one import can copy', ' '.join(look['notes']))
        # Out of time before anything was read: a clear error.
        with mock.patch('studio.importer.fetch', fake_fetch(pages)):
            with self.assertRaises(importer.SiteImportError):
                importer.capture('https://big.example/', limits=importer.Limits(seconds=-1))

    def test_srcset_parsing(self):
        self.assertEqual(importer.parse_srcset('a.png 1x, b.png 2x'), [('a.png', 1000.0), ('b.png', 2000.0)])
        self.assertEqual(importer.best_from_srcset('https://x/w_1,h_2/a.jpg 100w,https://x/w_3,h_4/a.jpg 900w'),
                         'https://x/w_3,h_4/a.jpg')
        self.assertEqual(importer.best_from_srcset('only.png'), 'only.png')
        self.assertEqual(importer.best_from_srcset(''), '')

    def bring_in(self, provider, pages=None, note=''):
        run = importer.start_import(self.site, 'https://artsfair.example/', self.owner, note=note)
        with mock.patch('studio.importer.fetch', fake_fetch(self.pages() if pages is None else pages)), \
                mock.patch('studio.ai.get_provider', return_value=provider):
            agent.execute(run.pk)
        run.refresh_from_db()
        return run

    def test_bring_in_copies_everything_and_rebuilds_the_site(self):
        provider = FakeProvider([
            ai.Turn(calls=[call('write_file', path='index.html', content='<img src="https://artsfair.example/hero.png">'),
                           call('write_file', path='styles.css',
                                content='h1{background:url(https://cdn.example/photo-1600.png)}'),
                           call('write_file', path='lineup.html', content=PAGE)]),
            ai.Turn(text='Rebuilt your site.')])
        run = self.bring_in(provider, note='Keep the yellow')
        self.assertEqual(run.status, StudioRun.DONE, run.error)
        self.assertEqual(run.result_version.source, SiteVersion.SOURCE_IMPORT)
        assets = StudioAsset.objects.filter(event=self.event)
        # 10 images (hero and its identical copy are one asset; <picture> only its largest source), 1 font, 2 PDFs.
        self.assertEqual(assets.filter(kind='image').count(), 10)
        self.assertEqual(assets.filter(kind='font').count(), 1)
        self.assertEqual(assets.filter(kind='pdf').count(), 2)
        self.assertTrue(all(a.sha256 and 'imported' in a.tags for a in assets))
        logo = assets.get(origin_url='https://res.cdn.example/w_600,h_240/logo.png')
        self.assertIn('logo', logo.tags)
        self.assertEqual(run.context['logo_asset_id'], str(logo.id))
        sponsor = assets.get(origin_url='https://cdn.example/sponsor-frost.png')
        self.assertEqual(sponsor.alt, 'Frost Bank')
        self.assertIn('Our sponsors', sponsor.description)
        # The pages the model sees use the local copies and the new file names.
        home = run.context['pages'][0]
        self.assertEqual([p['path'] for p in run.context['pages']], ['index.html', 'lineup.html', 'about.html',
                                                                     'map.html'])
        self.assertIn(sponsor.site_path, home['html'])
        self.assertNotIn('cdn.example/sponsor-frost.png', home['html'])
        self.assertIn('href="lineup.html"', home['html'])
        font = assets.get(kind='font')
        self.assertEqual(run.context['font_faces'], [{'family': 'Bold', 'weight': '700', 'style': 'normal',
                                                      'path': font.site_path}])
        self.assertIn(font.site_path, run.context['css'])
        self.assertIn('IMPORTED SITE', provider.context)
        self.assertIn('THIS SATURDAY', provider.context)
        self.assertIn('lineup.html', provider.context)
        self.assertIn('Keep the yellow', provider.context)
        # Hotlinking the old site (or its CDN) is refused; the clean page went in.
        self.assertTrue(provider.seen[0].startswith('Error:') and 'old website' in provider.seen[0])
        self.assertTrue(provider.seen[1].startswith('Error:'))
        self.assertEqual(sorted(run.result_version.files), ['about.html', 'index.html', 'lineup.html', 'styles.css'])
        self.assertIn('copied', ' '.join(s['text'] for s in run.steps))

    def test_dedupe_across_imports(self):
        from studio.assets import create_asset
        with self.captureOnCommitCallbacks(execute=False):
            first = create_asset(self.event, png_bytes(), 'a.png', dedupe=True, tags=['imported'])
            again = create_asset(self.event, png_bytes(), 'b.png', dedupe=True, tags=['logo'])
            upload = create_asset(self.event, png_bytes(), 'c.png')  # uploads are not deduplicated
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(again.tags, ['imported', 'logo'])
        self.assertNotEqual(upload.pk, first.pk)

    def test_failure_keeps_the_copies_and_retry_reuses_them(self):
        class Broken(FakeProvider):
            def chat(self, *a):
                raise ai.AIError('The AI request failed (500)')
        run = self.bring_in(Broken([]))
        self.assertEqual(run.status, StudioRun.FAILED)
        self.assertTrue(run.context['captured'])
        copied = StudioAsset.objects.filter(event=self.event).count()
        self.assertEqual(copied, 13)
        editor = self.client.get(reverse('studio:editor', args=[self.event.pk]))
        self.assertContains(editor, reverse('studio:run_retry', args=[self.event.pk, run.pk]))
        self.assertContains(editor, 'The AI request failed (500)')
        overview = self.client.get(reverse('console:event', args=[self.event.pk]))
        self.assertContains(overview, 'data-studio-import="failed"')
        # Retry: a new import run with the same crawl; nothing is fetched again.
        with self.captureOnCommitCallbacks(execute=False):
            response = self.client.post(reverse('studio:run_retry', args=[self.event.pk, run.pk]),
                                        HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 202)
        retry = StudioRun.objects.get(pk=response.json()['run']['id'])
        self.assertEqual((retry.kind, retry.context['pages']), (StudioRun.KIND_IMPORT, run.context['pages']))
        log = []
        provider = FakeProvider([ai.Turn(calls=[call('write_file', path='index.html', content=PAGE)]),
                                 ai.Turn(text='Rebuilt.')])
        with mock.patch('studio.importer.fetch', fake_fetch({}, log)), \
                mock.patch('studio.ai.get_provider', return_value=provider):
            agent.execute(retry.pk)
        retry.refresh_from_db()
        self.assertEqual(retry.status, StudioRun.DONE, retry.error)
        self.assertEqual(log, [])
        self.assertEqual(StudioAsset.objects.filter(event=self.event).count(), copied)
        # A finished run can't be retried.
        self.assertEqual(self.client.post(reverse('studio:run_retry', args=[self.event.pk, retry.pk])).status_code, 404)

    def test_unreadable_site_fails_clearly(self):
        run = self.bring_in(FakeProvider([]), pages={})
        self.assertEqual(run.status, StudioRun.FAILED)
        self.assertIn('Could not load https://artsfair.example/', run.error)
        self.assertIn('kept', run.error)

    def test_only_the_events_own_websites(self):
        with self.assertRaises(importer.SiteImportError):
            importer.start_import(self.site, 'https://someone-else.example/', self.owner)
        self.assertTrue(importer.allowed_url(self.event, 'https://www.artsfair.example/about'))
        response = self.client.post(f'/console/events/{self.event.pk}/studio/import/', {'url': 'https://evil.example/'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(StudioRun.objects.exists())

    def test_ssrf_guard(self):
        for url in ('http://127.0.0.1/', 'http://localhost/', 'http://10.0.0.5/', 'http://169.254.169.254/latest/',
                    'file:///etc/passwd', 'http://[::1]/', 'https://artsfair.example:8443/'):
            with self.assertRaises(importer.SiteImportError, msg=url):
                importer.check_url(url)
            with self.assertRaises(importer.SiteImportError, msg=url):
                importer.fetch(url, 1000)  # refused before any connection
        with mock.patch('studio.importer.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 0))]):
            self.assertEqual(importer.check_url('https://example.com/'), 'https://example.com/')
        # Every redirect hop is checked: a public page can't send the fetch to an internal address.
        import urllib.request
        handler = importer._SafeRedirects()
        request = urllib.request.Request('https://example.com/')
        for target in ('http://169.254.169.254/latest/meta-data/', 'http://127.0.0.1:80/', 'ftp://example.com/x'):
            with self.assertRaises(importer.SiteImportError, msg=target):
                handler.redirect_request(request, None, 302, 'Found', {}, target)
        with mock.patch('studio.importer.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 0))]):
            self.assertIsNotNone(handler.redirect_request(request, None, 302, 'Found', {}, 'https://example.com/b'))

    def test_import_creates_the_studio_run_automatically(self):
        from scrape import jobs
        from scrape.models import IngestJob
        from scrape.tests import _sample_data
        with self.captureOnCommitCallbacks(execute=False):
            self.client.post('/console/new/', {'mode': 'ai', 'ai-urls': 'https://new.example/'})  # no checkbox needed
        job = IngestJob.objects.get()
        self.assertTrue(job.studio_import)
        with mock.patch('scrape.jobs.scrape_event', return_value=_sample_data()), \
                mock.patch('scrape.watch.sync_pages'), mock.patch('studio.agent.start') as start:
            jobs.run_job(job.pk)
        job.refresh_from_db()
        event = job.event
        self.assertEqual(event.plan, 'studio')
        run = StudioRun.objects.get(site__event=event)
        self.assertEqual((run.kind, run.context['import_url']), (StudioRun.KIND_IMPORT, 'https://new.example/'))
        start.assert_called_once_with(run)
        self.assertTrue(job.result['studio_import'])
        overview = self.client.get(reverse('console:event', args=[event.pk]))
        self.assertContains(overview, 'data-studio-import="running"')

    def test_import_opt_out_and_access(self):
        from scrape import jobs
        from scrape.models import IngestJob
        with self.captureOnCommitCallbacks(execute=False):
            self.client.post('/console/new/', {'mode': 'ai', 'ai-urls': 'https://new.example/',
                                               'studio_import_shown': '1'})  # the box was unticked
            self.client.post('/console/new/', {'mode': 'ai', 'ai-text': 'A fair on Saturday'})  # no website
        self.assertEqual(list(IngestJob.objects.order_by('created_at').values_list('studio_import', flat=True)),
                         [False, False])
        self.assertContains(self.client.get(reverse('console:event_new')), 'name="studio_import" value="1" checked')
        # Someone without Studio access never gets a run, even with the flag set.
        stranger = User.objects.create_user('s', 's@example.com', 'pw')
        job = IngestJob.objects.create(kind=IngestJob.KIND_CREATE, urls=['https://artsfair.example/'],
                                       created_by=stranger, studio_import=True)
        with mock.patch('studio.importer.start_import') as start:
            jobs._bring_into_studio(job, self.event)
        start.assert_not_called()

    def test_import_option_on_ai_import(self):
        from scrape import jobs
        from scrape.models import IngestJob
        job = IngestJob.objects.create(kind=IngestJob.KIND_CREATE, urls=['https://artsfair.example/'],
                                       created_by=self.owner, studio_import=True)
        event = self.event
        event.plan = 'standard'
        event.save()
        with mock.patch('studio.importer.start_import') as start:
            jobs._bring_into_studio(job, event)
        event.refresh_from_db()
        self.assertEqual(event.plan, 'studio')
        start.assert_called_once()

    def test_ai_import_form_sets_the_flag(self):
        from scrape.models import IngestJob
        with self.captureOnCommitCallbacks(execute=False):
            self.client.post('/console/new/', {'mode': 'ai', 'ai-urls': 'https://new.example/', 'studio_import': '1',
                                               'studio_import_shown': '1'})
        self.assertTrue(IngestJob.objects.get().studio_import)


class PublishLookTests(StudioTestCase):
    def test_logo_from_the_app_look(self):
        from studio.assets import create_asset
        with self.captureOnCommitCallbacks(execute=False):
            logo = create_asset(self.event, png_bytes(), 'logo.png')
        version = publishing.new_version(self.site, self.owner, app_look={'logo_asset_id': str(logo.id)})
        publishing.publish(self.site, version, self.owner)
        self.event.refresh_from_db()
        self.assertEqual(self.event.logo_url, f'https://sites.test/{self.site.slug}/{logo.site_path}')
        self.assertEqual(self.sites(f'/{self.site.slug}/{logo.site_path}').status_code, 200)
