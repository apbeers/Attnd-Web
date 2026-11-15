"""Studio AI: the agent loop and tools (providers mocked), the OpenAI and Anthropic adapters, image generation, the
spend cap, and bringing a website in (fetches mocked)."""

import io
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.test import override_settings

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
    def test_openai_by_default_with_fallback_model(self):
        import httpx
        from openai import NotFoundError
        provider = ai.get_provider()
        self.assertIsInstance(provider, ai.OpenAIProvider)
        self.assertEqual(provider.model, 'gpt-5.5')
        message = SimpleNamespace(content='Hi', tool_calls=[SimpleNamespace(
            id='t1', function=SimpleNamespace(name='read_file', arguments='{"path": "index.html"}'))])
        good = SimpleNamespace(choices=[SimpleNamespace(message=message)],
                               usage=SimpleNamespace(prompt_tokens=50, completion_tokens=5))
        not_found = NotFoundError('The model `gpt-5.5` does not exist', body=None,
                                  response=httpx.Response(404, request=httpx.Request('POST', 'https://api.openai.com')))
        client = mock.MagicMock()
        client.chat.completions.create.side_effect = [not_found, good]
        with mock.patch('openai.OpenAI', return_value=client):
            turn = provider.chat('S', provider.start('x'), agent.TOOLS)
        self.assertEqual((turn.model, turn.calls[0].args, turn.input_tokens), ('gpt-5', {'path': 'index.html'}, 50))
        kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs['tools'][0]['type'], 'function')
        self.assertEqual(kwargs['messages'][0], {'role': 'system', 'content': 'S'})
        messages = []
        provider.add_turn(messages, turn)
        provider.add_results(messages, [(turn.calls[0], 'text')])
        self.assertEqual(messages[0]['tool_calls'][0]['id'], 't1')
        self.assertEqual(messages[1], {'role': 'tool', 'tool_call_id': 't1', 'content': 'text'})
        ai._unavailable_models.clear()

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


def fake_fetch(pages):
    def fetch(url, limit):
        if url not in pages:
            raise importer.SiteImportError(f'Could not load {url}')
        data, ctype = pages[url]
        return data, ctype, url
    return fetch


class ImporterTests(StudioTestCase):
    HOME = b'''<html><head><title>Arts Fair 2026</title><link rel="stylesheet" href="/s.css">
      <meta property="og:image" content="https://artsfair.example/hero.png"></head>
      <body style="background:#FFCC00"><header><img src="/logo.png" alt="Arts Fair logo"><nav><a href="/lineup">Lineup</a>
      <a href="https://elsewhere.example/">Tickets</a></nav></header>
      <section class="hero"><h1>THIS SATURDAY</h1><a class="button" href="/map">Maps</a></section></body></html>'''
    CSS = b'@font-face{font-family:Bold;src:url(/f.woff2)} h1{font-family:"Bold",sans-serif;color:#111111} a{color:#ffcc00}'

    def pages(self):
        return {'https://artsfair.example/': (self.HOME, 'text/html'),
                'https://artsfair.example/s.css': (self.CSS, 'text/css'),
                'https://artsfair.example/lineup': (b'<html><body><h1>Lineup</h1></body></html>', 'text/html'),
                'https://artsfair.example/hero.png': (png_bytes(), 'image/png'),
                'https://artsfair.example/logo.png': (png_bytes((2, 2)), 'image/png'),
                'https://artsfair.example/f.woff2': (b'wOF2' + b'\x00' * 50, 'font/woff2')}

    def test_capture(self):
        with mock.patch('studio.importer.fetch', fake_fetch(self.pages())):
            look = importer.capture('https://artsfair.example/')
        self.assertEqual(look['logo_url'], 'https://artsfair.example/logo.png')
        self.assertIn('#ffcc00', look['colors'])
        self.assertEqual(look['fonts'][0], 'Bold')
        self.assertEqual(look['font_urls'], ['https://artsfair.example/f.woff2'])
        self.assertEqual(look['pages'][0]['outline']['headings'], ['h1: THIS SATURDAY'])
        self.assertEqual([p['url'] for p in look['pages']], ['https://artsfair.example/', 'https://artsfair.example/lineup'])

    def test_bring_in_runs_the_ai_with_the_copied_look(self):
        run = importer.start_import(self.site, 'https://artsfair.example/', self.owner, note='Keep the yellow')
        self.assertEqual(run.kind, StudioRun.KIND_IMPORT)
        provider = FakeProvider([ai.Turn(calls=[call('write_file', path='index.html', content=PAGE)]),
                                 ai.Turn(text='Rebuilt your site.')])
        with mock.patch('studio.importer.fetch', fake_fetch(self.pages())), \
                mock.patch('studio.ai.get_provider', return_value=provider), \
                mock.patch('studio.assets.start_describe'):
            agent.execute(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, StudioRun.DONE, run.error)
        self.assertEqual(run.result_version.source, SiteVersion.SOURCE_IMPORT)
        self.assertEqual(StudioAsset.objects.filter(event=self.event).count(), 3)  # hero, logo, font
        logo = StudioAsset.objects.get(origin_url='https://artsfair.example/logo.png')
        self.assertIn('logo', logo.tags)
        self.assertEqual(run.context['logo_asset_id'], str(logo.id))
        self.assertIn('IMPORTED SITE', provider.context)
        self.assertIn('THIS SATURDAY', provider.context)
        self.assertIn('Keep the yellow', provider.context)

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
        with mock.patch('studio.importer.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 0))]):
            self.assertEqual(importer.check_url('https://example.com/'), 'https://example.com/')

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
            self.client.post('/console/new/', {'mode': 'ai', 'ai-urls': 'https://new.example/', 'studio_import': '1'})
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
