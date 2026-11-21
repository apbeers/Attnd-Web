"""API-023 colour themes: the colour rules (events/theme.py), the AI and its fallback (events/theme_ai.py, the SDK
faked), the console card and views (save, lock, undo, switches, reset, generate, Ask Attnd), the bundle key and the
pages that draw it (event pages, the canvas, the join and shared-plan pages)."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from attnd.colors import as_hex, html_colors, most_used_hex
from attnd.testing import fake_claude
from events import theme as T
from events import theme_ai
from events.models import Event, Location

SUNSET = {'light': {'primary': '#B23A1E', 'secondary': '#F2A65A', 'tertiary': '#1F7A8C'},
          'dark': {'primary': '#F4895F', 'secondary': '#7A3B24', 'tertiary': '#4FB3C4'}, 'source': 'manual'}


class ColourMathTests(SimpleTestCase):
    def test_contrast_matches_known_wcag_values(self):
        self.assertAlmostEqual(T.contrast('#000000', '#FFFFFF'), 21.0, places=2)
        self.assertAlmostEqual(T.contrast('#FFFFFF', '#FFFFFF'), 1.0, places=2)
        self.assertAlmostEqual(T.contrast('#777777', '#FFFFFF'), 4.48, places=2)  # the classic "just fails AA"
        self.assertAlmostEqual(T.contrast('#767676', '#FFFFFF'), 4.54, places=2)
        self.assertAlmostEqual(T.relative_luminance('#FFFFFF'), 1.0)
        self.assertAlmostEqual(T.relative_luminance('#000000'), 0.0)

    def test_clean_hex(self):
        self.assertEqual(T.clean_hex('#abc'), '#AABBCC')
        self.assertEqual(T.clean_hex('431f86'), '#431F86')
        for bad in ('', None, '#12345', 'red', '#GGGGGG', 'rgb(1,2,3)'):
            self.assertEqual(T.clean_hex(bad), '')


class NormalizeTests(SimpleTestCase):
    def test_uppercases_and_keeps_meta(self):
        t = T.normalize({'light': {'primary': '#b23a1e', 'secondary': '#f2a65a', 'tertiary': '#1f7a8c'},
                         'dark': {'primary': '#f4895f', 'secondary': '#7a3b24', 'tertiary': '#4fb3c4'},
                         'source': 'ai', 'reason': 'Sunset', 'page_colors': ['#e4572e', 'nope'], 'junk': 1})
        self.assertEqual(t['light']['primary'], '#B23A1E')
        self.assertEqual(t['source'], 'ai')
        self.assertEqual(t['reason'], 'Sunset')
        self.assertEqual(t['page_colors'], ['#E4572E'])
        self.assertNotIn('junk', t)

    def test_missing_mode_and_roles_are_derived(self):
        t = T.normalize({'light': {'primary': '#1F4E8C'}})
        self.assertEqual(set(t['dark']), set(T.ROLES))
        self.assertTrue(all(T.clean_hex(v) for m in T.MODES for v in t[m].values()))
        # dark's primary is lighter than light's
        self.assertGreater(T.relative_luminance(t['dark']['primary']), T.relative_luminance(t['light']['primary']))
        t = T.normalize({'dark': {'primary': '#F4895F', 'secondary': '#7A3B24', 'tertiary': '#4FB3C4'}})
        self.assertLess(T.relative_luminance(t['light']['primary']), T.relative_luminance(t['dark']['primary']))

    def test_nothing_usable_is_empty(self):
        for bad in (None, [], {}, {'light': {'secondary': '#FFFFFF'}}, {'light': {'primary': 'blue'}}, 'x'):
            self.assertEqual(T.normalize(bad), {})
        self.assertEqual(T.normalize({'light': {'primary': '#123456'}, 'source': 'hacker'})['source'], 'manual')


class TokenTests(SimpleTestCase):
    COLOURS = ['#FFCC00', '#FFFFFF', '#000000', '#777777', '#431F86', '#9D7BEA', '#00FF00', '#1F7A8C', '#F2A65A',
               '#FF0000', '#0000FF', '#222222', '#EEEEEE', '#B23A1E', '#7F7F7F', '#5A5A5A', '#A0A0A0']

    def test_every_colour_ends_readable_in_both_modes(self):
        for c in self.COLOURS:
            for mode in T.MODES:
                t = T.tokens({'primary': c, 'secondary': c, 'tertiary': c}, mode)
                page = T.PAGE[mode]
                with self.subTest(colour=c, mode=mode):
                    self.assertGreaterEqual(T.contrast(t['--color-brand'], t['--color-on-brand']), 4.5)
                    for surface in (page['bg'], page['surface'], t['--color-brand-soft']):
                        self.assertGreaterEqual(T.contrast(t['--color-brand-text'], surface), 4.5)
                    for surface in (page['bg'], page['surface']):
                        self.assertGreaterEqual(T.contrast(t['--color-brand-mid'], surface), 3.0)

    def test_a_readable_primary_is_kept_as_is(self):
        t = T.tokens(SUNSET['light'], 'light')
        self.assertEqual(t['--color-brand'], '#B23A1E')
        self.assertEqual(t['--color-on-brand'], T.WHITE)
        self.assertEqual(t['--color-secondary'], '#F2A65A')
        self.assertEqual(t['--color-tertiary'], '#1F7A8C')

    def test_light_fill_gets_dark_text(self):
        t = T.tokens({'primary': '#FFCC00', 'secondary': '#FFF2B3', 'tertiary': '#FF9900'}, 'light')
        self.assertEqual(t['--color-brand'], '#FFCC00')
        self.assertEqual(t['--color-on-brand'], T.INK)
        self.assertNotEqual(t['--color-brand-text'], '#FFCC00')  # too light as text on white: darkened

    def test_checks_explain_adjustments(self):
        notes = T.checks({'light': {'primary': '#FFCC00', 'secondary': '#FFFFFF', 'tertiary': '#FFEE00'}})
        self.assertIn('links use a darker shade', notes['light']['primary']['note'])
        self.assertIn('faint tint', notes['light']['secondary']['note'])
        self.assertIn('darker shade', notes['light']['tertiary']['note'])
        self.assertEqual(T.checks(SUNSET)['light']['primary']['note'], '')

    def test_css_covers_the_three_token_places(self):
        css = T.css(SUNSET)
        self.assertIn(':root{--color-brand:#B23A1E', css)
        self.assertIn('@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--color-brand:#F4895F', css)
        self.assertIn(':root[data-theme="dark"]{', css)
        self.assertEqual(T.css({}), '')
        self.assertNotIn('<', css)

    def test_adjusted_and_bundle(self):
        adjusted = T.adjusted({'light': {'primary': '#FFEE00', 'secondary': '#FFFFFF', 'tertiary': '#FFEE00'}})
        self.assertEqual(set(adjusted), {'light', 'dark'})
        self.assertEqual(set(adjusted['light']), set(T.ROLES))
        self.assertIsNone(T.adjusted({}))
        e = Event(theme=SUNSET, theme_apps=False)
        self.assertIsNone(T.for_bundle(e))
        e.theme_apps = True
        self.assertEqual(T.for_bundle(e)['light']['primary'], '#B23A1E')

    def test_from_page_colors_skips_greys(self):
        self.assertIsNone(T.from_page_colors(['#ffffff', '#000000', '#777777']))
        t = T.from_page_colors(['#ffffff', '#e4572e', '#333333'])
        self.assertEqual(t['source'], 'site')
        self.assertIn('#E4572E', t['reason'])


class PageColourTests(SimpleTestCase):
    def test_html_colours_are_counted(self):
        html = ('<meta name="theme-color" content="#e4572e"><style>a{color:#1f7a8c} b{color:#1F7A8C}'
                ' .x{background:rgba(0,0,0,.2)} .y{color:rgb(10, 20, 30)}</style><p style="color:#abc">x</p>')
        found = most_used_hex(html_colors(html))
        self.assertEqual(found[0], '#e4572e')  # theme-color counts three times
        self.assertIn('#1f7a8c', found)
        self.assertIn('#aabbcc', found)
        self.assertIn('#0a141e', found)
        self.assertEqual(as_hex('rgba(0,0,0,.2)'), '')  # translucent: skipped

    def test_studio_importer_uses_the_shared_helper(self):
        from attnd import colors
        from studio import importer
        self.assertIs(importer.COLOR_RE, colors.COLOR_RE)
        self.assertEqual(importer._norm_color('#ABC'), '#aabbcc')


def _event(**extra):
    owner = extra.pop('user', None) or User.objects.create_user('owner', 'owner@example.com', 'pw')
    start = timezone.now().replace(minute=0, second=0, microsecond=0) + timedelta(days=3)
    return Event.objects.create(name='Prairie Fest', event_code='PRAIRIE', user=owner, timezone='America/Chicago',
                                start_time=start, end_time=start + timedelta(hours=10),
                                status=Event.STATUS_PUBLISHED, **extra)


class ThemeAITests(TestCase):
    def setUp(self):
        self.event = _event(description='A jazz night by the river')
        Location.objects.create(event=self.event, name='Main Stage', category='Stage')

    def test_generate_with_the_sdk_faked(self):
        answer = {'light': {'primary': '#1f3a8c', 'secondary': '#9ec5fe', 'tertiary': '#e0a800'},
                  'dark': {'primary': '#8fb0ff', 'secondary': '#22335e', 'tertiary': '#ffd24d'},
                  'reason': 'Midnight blue and brass for jazz.'}
        with fake_claude(answer) as claude:
            theme, cost = theme_ai.generate(self.event, page_colors=['#1f3a8c'], instruction='warmer')
        self.assertEqual(theme['source'], 'ai')
        self.assertEqual(theme['light']['primary'], '#1F3A8C')
        self.assertEqual(theme['reason'], 'Midnight blue and brass for jazz.')
        self.assertTrue(theme['made_at'])
        self.assertGreaterEqual(cost, 0)
        call = claude.last
        text = call['messages'][0]['content'][-1]['text']
        self.assertIn('Prairie Fest', text)
        self.assertIn('#1f3a8c', text)
        self.assertIn('THE ORGANIZER ASKS: warmer', text)
        self.assertIn('Stage', text)
        self.assertEqual(call['output_config']['format']['type'], 'json_schema')

    def test_bad_reply_and_errors_fall_back(self):
        with fake_claude({'light': {'primary': 'blue'}, 'dark': {}, 'reason': ''}):
            theme, _ = theme_ai.generate(self.event, page_colors=['#e4572e'])
        self.assertEqual(theme['source'], 'site')
        from attnd.claude import ClaudeError
        with fake_claude(ClaudeError('down')):
            theme, cost = theme_ai.generate(self.event)
        self.assertEqual(theme['source'], 'default')
        self.assertEqual(cost, 0)

    def test_no_key_uses_the_fallback_without_calling(self):
        with mock.patch('attnd.claude.available', return_value=False), mock.patch('attnd.claude.client') as client:
            theme, _ = theme_ai.generate(self.event, page_colors=['#e4572e'])
        client.assert_not_called()
        self.assertEqual(theme['source'], 'site')

    def test_apply_keeps_a_locked_theme_and_saves_default_as_empty(self):
        self.event.theme = SUNSET
        self.event.locked_fields = ['theme']
        self.event.save()
        self.assertIsNone(theme_ai.apply(self.event))
        self.assertEqual(Event.objects.get(pk=self.event.pk).theme['light']['primary'], '#B23A1E')
        with mock.patch('attnd.claude.available', return_value=False):
            theme_ai.apply(self.event, force=True)
        self.assertEqual(Event.objects.get(pk=self.event.pk).theme, {})  # Attnd's colours = no theme

    def test_import_picks_colours_with_a_step(self):
        from scrape import jobs
        from scrape.models import IngestJob
        data = {'name': 'River Jazz', 'description': '', 'start_time': '2026-12-01T19:00:00', 'end_time': None,
                'timezone': 'America/Chicago', 'address': '', 'latitude': None, 'longitude': None, 'context': {},
                'locations': [], 'schedule': [], 'notes': '', 'source_pages': [], 'page_colors': ['#e4572e']}
        job = IngestJob.objects.create(kind=IngestJob.KIND_CREATE, urls=['https://river.example'],
                                       created_by=self.event.user, timezone='America/Chicago')
        with mock.patch('scrape.jobs.scrape_event', return_value=data), \
                mock.patch('attnd.claude.available', return_value=False), mock.patch('scrape.watch.sync_pages'):
            jobs.run_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, IngestJob.DONE, job.error)
        self.assertIn('Picked colours for the event', [s['text'] for s in job.steps])
        self.assertEqual(job.event.theme['source'], 'site')
        self.assertEqual(job.event.theme['page_colors'], ['#E4572E'])


@override_settings(SECURE_SSL_REDIRECT=False)
class ConsoleThemeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user('owner', 'owner@example.com', 'pw')
        self.event = _event(user=self.owner)
        self.client = Client()
        self.client.force_login(self.owner)
        self.url = reverse('console:event_theme', args=[self.event.pk])

    def colours(self, **changes):
        data = {f'{m}_{r}': SUNSET[m][r] for m in T.MODES for r in T.ROLES}
        data.update(changes)
        return data

    def test_overview_shows_the_card_and_no_style_without_a_theme(self):
        html = self.client.get(reverse('console:event', args=[self.event.pk]), secure=True).content.decode()
        self.assertIn('component--event_theme', html)
        # Compact on the Overview (owner, Nov 21, 2026): swatches + switches + Generate + Edit; the editor is a dialog.
        self.assertIn('theme-compact', html)
        self.assertIn('theme-swatch', html)
        self.assertIn('data-dialog-open', html)
        self.assertIn('<dialog class="dialog theme-dialog"', html)
        self.assertIn('Use custom theme on the website', html)
        self.assertIn('Use custom theme in the apps', html)
        # The style block is always there (empty without a theme), so a change can swap the colours in place.
        self.assertIn(f'data-event-theme="{self.event.pk}"></style>', html)
        # The theme page and the canvas overlay show the full editor.
        page = self.client.get(self.url, secure=True).content.decode()
        self.assertIn('Where the colours show', page)
        self.assertNotIn('theme-compact', page)

    def test_theme_css_endpoint_for_swapping_in_place(self):
        url = reverse('console:event_theme_css', args=[self.event.pk])
        empty = self.client.get(url, secure=True).json()
        self.assertEqual(empty['css'], '')
        self.assertEqual(len(empty['swatches']), 6)
        self.event.theme = SUNSET
        self.event.save()
        data = self.client.get(url, secure=True).json()
        self.assertIn('--color-brand', data['css'])
        self.assertEqual(data['swatches'][0], '#B23A1E')
        self.event.theme_web = False
        self.event.save()
        self.assertEqual(self.client.get(url, secure=True).json()['css'], '', 'website switch off: nothing changes')
        other = User.objects.create_user('other-css', 'other-css@example.com', 'pw')
        c = Client()
        c.force_login(other)
        self.assertEqual(c.get(url, secure=True).status_code, 404)

    def test_save_locks_bumps_and_offers_undo(self):
        version = self.event.content_version
        response = self.client.post(self.url, self.colours(light_primary='b23a1e'), secure=True)
        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.theme['light']['primary'], '#B23A1E')
        self.assertEqual(self.event.theme['source'], 'manual')
        self.assertIn('theme', self.event.locked_fields)
        self.assertGreater(self.event.content_version, version)
        undo_id = response['X-Attnd-Undo']
        self.client.post(reverse('console:undo', args=[undo_id]), secure=True, HTTP_ACCEPT='application/json')
        self.event.refresh_from_db()
        self.assertEqual(self.event.theme, {})

    def test_bad_hex_shows_the_error(self):
        response = self.client.post(self.url, self.colours(dark_primary='orange'), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Enter a colour as #RRGGBB')
        self.event.refresh_from_db()
        self.assertEqual(self.event.theme, {})

    def test_low_contrast_colour_saves_with_a_note(self):
        self.client.post(self.url, self.colours(light_primary='#FFEE00'), secure=True)
        html = self.client.get(self.url, secure=True).content.decode()
        self.assertIn('links use a darker shade', html)
        self.event.refresh_from_db()
        self.assertEqual(self.event.theme['light']['primary'], '#FFEE00')  # never refused

    def test_style_block_on_event_pages_only_with_theme_web(self):
        self.event.theme = SUNSET
        self.event.save()
        for name in ('console:event', 'console:locations', 'console:schedule'):
            html = self.client.get(reverse(name, args=[self.event.pk]), secure=True).content.decode()
            self.assertIn(f'data-event-theme="{self.event.pk}"', html, name)
            self.assertRegex(html, r'<style nonce="[^"]+" data-event-theme')
            self.assertIn('--color-brand:#B23A1E', html)
        canvas = self.client.get(reverse('console:assistant') + f'?event={self.event.pk}', secure=True).content.decode()
        self.assertIn('data-event-theme', canvas)
        dashboard = self.client.get(reverse('console:dashboard') + '?page=1', secure=True).content.decode()
        self.assertNotIn('data-event-theme', dashboard)
        self.assertNotIn('data-event-theme', self.client.get(reverse('console:assistant'), secure=True).content.decode())
        self.event.theme_web = False
        self.event.save()
        html = self.client.get(reverse('console:locations', args=[self.event.pk]), secure=True).content.decode()
        # Website switch off: the block stays (so turning it on swaps in place) but is empty.
        self.assertIn(f'data-event-theme="{self.event.pk}"></style>', html)
        self.assertNotIn('--color-brand:#B23A1E', html)

    def test_switches_are_independent(self):
        self.event.theme = SUNSET
        self.event.save()
        url = reverse('console:event_theme_switches', args=[self.event.pk])
        version = Event.objects.get(pk=self.event.pk).content_version
        r = self.client.post(url, {'theme_apps': 'on'}, secure=True, HTTP_ACCEPT='application/json')
        self.assertTrue(r.json()['saved'])
        self.event.refresh_from_db()
        self.assertFalse(self.event.theme_web)
        self.assertTrue(self.event.theme_apps)
        self.assertEqual(self.event.content_version, version)  # the website only: the apps' bundle is unchanged
        self.client.post(url, {'theme_web': 'on'}, secure=True, HTTP_ACCEPT='application/json')
        self.event.refresh_from_db()
        self.assertTrue(self.event.theme_web)
        self.assertFalse(self.event.theme_apps)
        self.assertGreater(self.event.content_version, version)

    def test_reset_clears_and_unlocks(self):
        self.event.theme, self.event.locked_fields = SUNSET, ['theme']
        self.event.save()
        r = self.client.post(self.url, {'action': 'reset'}, secure=True)
        self.assertEqual(r.status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.theme, {})
        self.assertNotIn('theme', self.event.locked_fields)

    def test_generate_runs_a_background_task(self):
        from scrape.models import IngestJob
        url = reverse('console:event_theme_generate', args=[self.event.pk])
        with mock.patch('scrape.jobs.enqueue') as enqueue:
            r = self.client.post(url, {'instruction': 'warmer'}, secure=True, HTTP_ACCEPT='application/json')
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.json()['task']['title'], 'Picking colours for Prairie Fest')
        job = IngestJob.objects.get(kind=IngestJob.KIND_THEME)
        self.assertEqual(job.instructions, 'warmer')
        enqueue.assert_called_once()
        answer = {'light': {'primary': '#1f3a8c', 'secondary': '#9ec5fe', 'tertiary': '#e0a800'},
                  'dark': {'primary': '#8fb0ff', 'secondary': '#22335e', 'tertiary': '#ffd24d'}, 'reason': 'Jazz.'}
        from scrape import jobs
        with fake_claude(answer):
            jobs.run_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, IngestJob.DONE, job.error)
        self.event.refresh_from_db()
        self.assertEqual(self.event.theme['light']['primary'], '#1F3A8C')
        self.assertEqual(self.client.get(reverse('console:tasks'), secure=True).json()['tasks'][0]['result_label'],
                         'See the colours')

    def test_someone_else_gets_404(self):
        stranger = User.objects.create_user('x', 'x@example.com', 'pw')
        c = Client()
        c.force_login(stranger)
        self.assertEqual(c.post(self.url, self.colours(), secure=True).status_code, 404)
        self.assertEqual(c.get(self.url, secure=True).status_code, 404)


class BundleAndJoinTests(TestCase):
    def setUp(self):
        cache.clear()
        self.event = _event(theme=SUNSET, paid=True)

    def bundle(self):
        r = Client().get(f'/api/v2/events/{self.event.pk}/bundle/', HTTP_AUTHORIZATION='Bearer k', secure=True)
        self.assertEqual(r.status_code, 200, r.content[:300])
        return r.json()

    @override_settings(API_KEY='k')
    def test_bundle_theme_and_its_switch(self):
        theme = self.bundle()['event']['theme']
        self.assertEqual(theme['light'], {'primary': '#B23A1E', 'secondary': '#F2A65A', 'tertiary': '#1F7A8C'})
        self.assertEqual(set(theme), {'light', 'dark'})
        self.event.theme_apps = False
        self.event.save()
        self.assertIsNone(self.bundle()['event']['theme'])
        Event.objects.filter(pk=self.event.pk).update(theme={}, theme_apps=True)
        self.event.refresh_from_db()
        self.event.bump_version()
        self.assertIsNone(self.bundle()['event']['theme'])

    @override_settings(API_KEY='k')
    def test_lookup_has_no_theme_key(self):
        r = Client().get('/api/v2/events/lookup/?q=PRAIRIE', HTTP_AUTHORIZATION='Bearer k', secure=True)
        self.assertNotIn('theme', r.json()['event'])

    @override_settings(SECURE_SSL_REDIRECT=False)
    def test_join_and_shared_plan_pages(self):
        html = Client().get('/join/PRAIRIE/', HTTP_USER_AGENT='Mozilla/5.0 (Macintosh)').content.decode()
        self.assertIn('data-scheme="auto"', html)
        self.assertRegex(html, r'<style nonce="[^"]+" data-event-theme')
        self.assertIn('--brand-gradient:linear-gradient(135deg, #B23A1E', html)
        self.assertIn(':root[data-scheme="auto"]{', html)
        self.event.theme_web = False
        self.event.save()
        html = Client().get('/join/PRAIRIE/', HTTP_USER_AGENT='Mozilla/5.0 (Macintosh)').content.decode()
        self.assertNotIn('data-event-theme', html)


from agent.tests import AgentTestCase  # noqa: E402


class AgentThemeTests(AgentTestCase):
    def test_registered_actions(self):
        from agent import registry
        reg = registry.registry()
        for name in ('update_event_theme', 'reset_event_theme', 'set_event_theme_switches', 'generate_event_theme'):
            self.assertIn(name, reg['writes'])
        self.assertIn('event_theme_page', {a.name for a in reg['reads'].values()})
        self.assertFalse(reg['writes']['update_event_theme'].confirm)

    def test_update_through_the_real_view_keeps_the_other_colours(self):
        from agent import internal, registry
        self.event.theme = SUNSET
        self.event.save()
        run = self.make_run()
        out = internal.execute(run, registry.registry()['writes']['update_event_theme'],
                               {'event_id': self.event.pk, 'dark_tertiary': '#00AAFF'})
        self.assertTrue(out['ok'], out)
        self.event.refresh_from_db()
        self.assertEqual(self.event.theme['dark']['tertiary'], '#00AAFF')
        self.assertEqual(self.event.theme['light']['primary'], '#B23A1E')  # prefilled from the page
        out = internal.execute(run, registry.registry()['writes']['set_event_theme_switches'],
                               {'event_id': self.event.pk, 'theme_apps': False})
        self.assertTrue(out['ok'], out)
        self.event.refresh_from_db()
        self.assertTrue(self.event.theme_web)
        self.assertFalse(self.event.theme_apps)


class ThemeSourcesTests(TestCase):
    """Generate with AI uses what the event already has (its websites, Studio site, logo, flyer, location photos)
    straight away; only an event with none of these gets the note that invites a link or an image."""

    PAGE = b'<html><head><meta name="theme-color" content="#e4572e"><style>a{color:#1f7a8c}</style></head></html>'
    PNG = (b'\x89PNG\r\n\x1a\n' + b'\x00' * 64)

    def setUp(self):
        cache.clear()
        from django.core.files.base import ContentFile
        self.event = _event(source_urls=['https://river.example/'], logo_url='https://river.example/logo.png')
        self.event.flyer.save('flyer.png', ContentFile(self.PNG), save=True)

    def fake_fetch(self, url, limit):
        if url.endswith('.png'):
            return self.PNG, 'image/png', url
        return self.PAGE, 'text/html', url

    def test_generation_reads_the_event_websites_and_images(self):
        answer = {'light': {'primary': '#1f3a8c', 'secondary': '#9ec5fe', 'tertiary': '#e0a800'},
                  'dark': {'primary': '#8fb0ff', 'secondary': '#22335e', 'tertiary': '#ffd24d'}, 'reason': 'River.'}
        with mock.patch('events.theme_ai._fetch', side_effect=self.fake_fetch) as fetch, fake_claude(answer) as claude:
            theme = theme_ai.apply(self.event, force=True)
        fetched = [c.args[0] for c in fetch.call_args_list]
        self.assertIn('https://river.example/', fetched)
        self.assertIn('https://river.example/logo.png', fetched)
        content = claude.last['messages'][0]['content']
        self.assertEqual([b['type'] for b in content], ['image', 'image', 'text'])  # the flyer and the logo
        self.assertIn('#e4572e', content[-1]['text'])  # the website's colours, read just now
        self.assertEqual(theme['page_colors'][0], '#E4572E')
        self.assertTrue(theme_ai.has_sources(self.event))

    @override_settings(SECURE_SSL_REDIRECT=False)
    def test_generate_view_notes_only_when_the_event_has_nothing(self):
        client = Client()
        client.force_login(self.event.user)
        url = reverse('console:event_theme_generate', args=[self.event.pk])
        with mock.patch('scrape.jobs.enqueue'):
            self.assertEqual(client.post(url, HTTP_ACCEPT='application/json').json()['note'], '')
            Event.objects.filter(pk=self.event.pk).update(source_urls=[], logo_url='', flyer='')
            note = client.post(url, HTTP_ACCEPT='application/json').json()['note']
        self.assertIn('no website, logo, flyer or photos', note)

    def test_studio_site_colours_count(self):
        from studio import publishing
        Event.objects.filter(pk=self.event.pk).update(source_urls=[])
        self.event.refresh_from_db()
        site = publishing.ensure_site(self.event, self.event.user)
        publishing.publish(site, publishing.new_version(site, self.event.user, files={
            'index.html': '<h1 style="color:#2a9d8f">Hi</h1>', 'styles.css': 'body{background:#2a9d8f}'}), self.event.user)
        self.assertEqual(theme_ai.gather_page_colors(self.event)[0], '#2a9d8f')


class AgentGenerateThemeTests(AgentTestCase):
    def test_generate_runs_without_asking_and_tells_the_model_about_sources(self):
        from agent import internal, registry
        Event.objects.filter(pk=self.event.pk).update(source_urls=['https://prairie.example/'])
        run = self.make_run(goal='Use the link already on the event to do it')
        with mock.patch('scrape.jobs.enqueue') as enqueue:
            out = internal.execute(run, registry.registry()['writes']['generate_event_theme'], {'event_id': self.event.pk})
        self.assertTrue(out['ok'], out)
        enqueue.assert_called_once()
        self.assertNotIn('no website', str(out))
        tool = registry.tool_definition(registry.registry()['writes']['generate_event_theme'])
        self.assertIn('never ask for a link', tool['description'])
