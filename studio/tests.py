"""Attnd Studio: beta access, team roles, sites-host isolation, pages, previews, data.json, forms, assets, versions,
publishing, reports (docs/specs/studio_spec.md)."""

import io
import json
import shutil
import tempfile
import time
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from console.testing import TwoFactorClient
from events.models import Event, EventMember, Location, ScheduledItem
from studio import hosting, publishing
from studio.access import has_studio_access
from studio.models import (FormSubmission, Site, SiteReport, SiteVersion, StudioAsset, StudioRun,
                           StudioUsage)

SITES = 'sites.test'
CONSOLE = 'https://attnd.app'
PAGE = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Fair</title></head>'
        '<body><h1>Fair</h1><a href="about">About</a></body></html>')


def png_bytes(size=(4, 3), color=(200, 30, 30)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, format='PNG')
    return buf.getvalue()


class StudioTestCase(TestCase):
    """An owner with Studio access, a Studio event with a site whose draft has a page."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media = tempfile.mkdtemp()
        cls._settings = override_settings(SITES_HOST=SITES, STUDIO_CONSOLE_ORIGINS=[CONSOLE], MEDIA_ROOT=cls._media,
                                          STUDIO_BETA_EMAILS=['owner@example.com'], API_KEY='test-key',
                                          ALLOWED_HOSTS=['testserver', SITES, 'localhost'])
        cls._settings.enable()

    @classmethod
    def tearDownClass(cls):
        cls._settings.disable()
        shutil.rmtree(cls._media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user('owner', 'owner@example.com', 'pw')
        start = timezone.now() + timedelta(days=3)
        self.event = Event.objects.create(name='Arts Fair', event_code='ARTS1', user=self.owner, timezone='America/Chicago',
                                          start_time=start, end_time=start + timedelta(hours=10),
                                          plan=Event.PLAN_STUDIO, status=Event.STATUS_PUBLISHED,
                                          source_urls=['https://artsfair.example/'])
        self.loc = Location.objects.create(event=self.event, name='Main Stage', category='Stage')
        ScheduledItem.objects.create(event=self.event, name='Band', start_time=start, location=self.loc)
        self.site = publishing.ensure_site(self.event, self.owner)
        self.v2 = publishing.new_version(self.site, self.owner, files={'index.html': PAGE, 'about.html': PAGE.replace(
            'Fair</h1>', 'About us</h1>'), 'styles.css': 'body{color:red}'}, summary='First')
        self.client = Client()
        self.client.force_login(self.owner)

    def sites(self, path, method='get', **kwargs):
        client = Client()
        return getattr(client, method)(path, HTTP_HOST=SITES, secure=True, **kwargs)

    def publish(self, version=None):
        publishing.publish(self.site, version or self.site.draft, self.owner)
        self.site.refresh_from_db()


class AccessTests(StudioTestCase):
    def test_who_has_access(self):
        """Every signed-in organizer designs (D1); publishing is plan-gated (can_publish)."""
        self.assertTrue(has_studio_access(self.owner))
        other = User.objects.create_user('o', 'Other@Example.com', 'pw')
        self.assertTrue(has_studio_access(other))
        from django.contrib.auth.models import AnonymousUser
        self.assertFalse(has_studio_access(AnonymousUser()))

    def test_default_setting_is_empty(self):
        import attnd.settings as module  # the real default, not this class's override
        self.assertEqual(module.STUDIO_BETA_EMAILS, [])

    def test_every_team_member_designs_but_publishing_needs_studio(self):
        member = User.objects.create_user('m', 'member@example.com', 'pw')
        EventMember.objects.create(event=self.event, user=member, role=EventMember.ROLE_ADMIN)
        client = Client()
        client.force_login(member)
        self.assertEqual(client.get(reverse('studio:editor', args=[self.event.pk])).status_code, 200)
        from agent import registry as agent_registry
        from console import components as C
        self.assertIn('design_website', agent_registry.writes_for(member))
        self.assertIn('website_overview', C.for_user(member))
        from studio.access import can_publish
        self.assertTrue(can_publish(self.event, member))  # this event is on the Studio plan
        event = Event.objects.create(name='Market', event_code='MKT9', user=self.owner, start_time=timezone.now())
        self.assertFalse(can_publish(event, self.owner))  # free: Upgrade to publish
        stranger = User.objects.create_user('x2', 'x2@example.com', 'pw')
        client.force_login(stranger)
        self.assertEqual(client.get(reverse('studio:editor', args=[self.event.pk])).status_code, 404)

    def test_access_user_sees_the_entry_points(self):
        overview = self.client.get(reverse('console:event', args=[self.event.pk]))
        self.assertContains(overview, reverse('studio:editor', args=[self.event.pk]))
        # Bringing an imported website into Studio automatically is for staff only.
        self.assertNotContains(self.client.get(reverse('console:event_new')), 'studio_import')
        staff = User.objects.create_user('staff', 'staff@example.com', 'pw', is_staff=True)
        client = TwoFactorClient()
        client.force_login(staff)
        self.assertContains(client.get(reverse('console:event_new')), 'studio_import')

    def test_not_on_the_team_is_404(self):
        stranger = User.objects.create_user('x', 'x@example.com', 'pw')
        client = Client()
        client.force_login(stranger)
        with override_settings(STUDIO_BETA_EMAILS=['owner@example.com', 'x@example.com']):
            self.assertEqual(client.get(reverse('studio:editor', args=[self.event.pk])).status_code, 404)

    def test_no_beta_access_page(self):
        staff = User.objects.create_user('staff', 'staff@example.com', 'pw', is_staff=True)
        client = TwoFactorClient()
        client.force_login(staff)
        self.assertEqual(client.get('/console/studio/access/').status_code, 404)
        dashboard = client.get(reverse('console:dashboard'))
        self.assertNotContains(dashboard, '/console/studio/access/')
        self.assertContains(dashboard, reverse('studio:reports'))

    def test_a_new_event_goes_straight_to_the_empty_state(self):
        event = Event.objects.create(name='Market', event_code='MKT1', user=self.owner, start_time=timezone.now())
        page = self.client.get(reverse('studio:editor', args=[event.pk]))
        self.assertNotContains(page, 'Use Studio for this event')
        self.assertContains(page, 'No website yet')
        self.assertContains(page, 'Start from scratch')
        self.assertTrue(Site.objects.filter(event=event).exists())
        event.refresh_from_db()
        self.assertEqual(event.plan, Event.PLAN_STANDARD)

    def test_contributor_cannot_publish(self):
        helper = User.objects.create_user('h', 'helper@example.com', 'pw')
        EventMember.objects.create(event=self.event, user=helper, role=EventMember.ROLE_CONTRIBUTOR)
        client = Client()
        client.force_login(helper)
        self.assertEqual(client.get(reverse('studio:editor', args=[self.event.pk])).status_code, 200)
        saved = client.post(reverse('studio:code', args=[self.event.pk]),
                            {'files': json.dumps({'index.html': PAGE + '<!-- x -->'})}).json()
        self.assertTrue(saved['saved'])
        client.post(reverse('studio:publish', args=[self.event.pk]))
        self.site.refresh_from_db()
        self.assertIsNone(self.site.published)


class SitesHostTests(StudioTestCase):
    def test_only_site_routes_exist_on_the_sites_host(self):
        self.publish()
        for path in ('/console/', f'/{settings.ADMIN_URL_PATH}', '/api/v2/events/lookup/?q=ARTS1', '/accounts/login/',
                     '/static/console/console.css', '/favicon.ico', '/join/ARTS1/', '/health/'):
            self.assertEqual(self.sites(path).status_code, 404, path)
        self.assertEqual(self.sites('/').status_code, 301)  # the bare root → Attnd's home page

    def test_no_cookies_csp_and_framing(self):
        self.publish()
        response = self.sites(f'/{self.site.slug}/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.cookies)
        self.assertNotIn('Set-Cookie', response)
        self.assertNotIn('X-Frame-Options', response)
        csp = response['Content-Security-Policy']
        for part in (f'frame-ancestors {CONSOLE}', "worker-src 'none'", "form-action 'self'", "base-uri 'self'",
                     "object-src 'none'", "script-src 'self' 'unsafe-inline'", "connect-src 'self'"):
            self.assertIn(part, csp)
        self.assertNotIn('nonce-', csp)
        self.assertNotIn('Service-Worker-Allowed', response)

    def test_signed_in_cookie_is_never_echoed(self):
        self.publish()
        client = Client()
        client.cookies['sessionid'] = 'abc'
        client.cookies['csrftoken'] = 'def'
        response = client.post(f'/{self.site.slug}/_attnd/forms/newsletter', {'email': 'a@b.co'}, HTTP_HOST=SITES,
                               secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.cookies)

    def test_service_worker_scripts_are_refused(self):
        self.publish()
        response = self.sites(f'/{self.site.slug}/styles.css', HTTP_SERVICE_WORKER='script')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.sites(f'/{self.site.slug}/styles.css').status_code, 200)

    def test_attnd_app_never_serves_site_html(self):
        self.publish()
        response = Client().get(f'/{self.site.slug}/', secure=True)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b'<h1>Fair</h1>', response.content)
        self.assertEqual(Client().get(f'/{self.site.slug}/_attnd/data.json', secure=True).status_code, 404)

    def test_robots(self):
        self.assertContains(self.sites('/robots.txt'), 'Allow: /')

    def test_console_allows_framing_previews_from_the_sites_origin_only(self):
        policy = self.client.get(reverse('studio:editor', args=[self.event.pk]))['Content-Security-Policy']
        self.assertIn('frame-src', policy)
        self.assertIn("frame-ancestors 'none'", policy)


class PageTests(StudioTestCase):
    def test_published_pages(self):
        self.publish()
        slug = self.site.slug
        self.assertRedirects(self.sites(f'/{slug}'), f'/{slug}/', fetch_redirect_response=False)
        home = self.sites(f'/{slug}/')
        body = home.content.decode()
        self.assertIn(f'<base href="/{slug}/">', body)
        self.assertLess(body.index('<base'), body.index('<title>'))
        self.assertIn('Report this site', body)
        self.assertNotIn('bridge.js', body)
        self.assertNotIn('data-attnd-preview-ribbon', body)
        self.assertContains(self.sites(f'/{slug}/about'), 'About us')
        self.assertContains(self.sites(f'/{slug}/about.html'), 'About us')
        css = self.sites(f'/{slug}/styles.css')
        self.assertEqual(css['Content-Type'], 'text/css; charset=utf-8')
        self.assertEqual(self.sites(f'/{slug}/missing').status_code, 404)
        self.assertEqual(self.sites(f'/{slug}/_attnd/attnd.js')['Content-Type'], 'text/javascript; charset=utf-8')
        self.assertEqual(self.sites(f'/{slug}/_attnd/bridge.js').status_code, 404)  # previews only

    def test_custom_404_page(self):
        publishing.new_version(self.site, self.owner, files={**self.site.draft.files, '404.html': '<p>Lost?</p>'})
        self.publish()
        response = self.sites(f'/{self.site.slug}/nope')
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'Lost?', status_code=404)

    def test_not_served_unless_live(self):
        slug = self.site.slug
        self.assertEqual(self.sites(f'/{slug}/').status_code, 404)  # not published
        self.publish()
        self.assertEqual(self.sites(f'/{slug}/').status_code, 200)
        publishing.suspend(self.site, 'spam')
        self.assertEqual(self.sites(f'/{slug}/').status_code, 404)
        publishing.reinstate(self.site)
        Event.objects.filter(pk=self.event.pk).update(plan=Event.PLAN_STANDARD)
        self.assertEqual(self.sites(f'/{slug}/').status_code, 404)

    def test_render_page_without_head_or_body(self):
        out = hosting.render_page('<p>hi</p>', '/x/')
        self.assertTrue(out.startswith('<head><base href="/x/">'))
        self.assertIn('Report this site', out)
        replaced = hosting.render_page('<html><head><base href="https://evil.example/"></head><body></body></html>', '/x/')
        self.assertNotIn('evil.example', replaced)


class PreviewTests(StudioTestCase):
    def test_draft_preview_with_a_token(self):
        url = hosting.preview_url(self.site)
        self.assertTrue(url.startswith(f'https://{SITES}/{self.site.slug}/_preview/'))
        path = url.split(SITES, 1)[1]
        response = self.sites(path)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn(f'<base href="{path}">', body)
        self.assertIn('noindex', body)
        self.assertIn('_attnd/bridge.js', body)
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertContains(self.sites(path + 'about'), 'About us')
        bridge = self.sites(path + '_attnd/bridge.js')
        self.assertContains(bridge, CONSOLE)

    def test_bad_expired_and_revoked_tokens(self):
        slug = self.site.slug
        self.assertEqual(self.sites(f'/{slug}/_preview/nonsense/').status_code, 404)
        token = hosting.make_preview_token(self.site, seconds=60)
        with mock.patch('studio.hosting.time.time', return_value=time.time() + 120):
            self.assertEqual(self.sites(f'/{slug}/_preview/{token}/').status_code, 404)
        self.assertEqual(self.sites(f'/{slug}/_preview/{token}/').status_code, 200)
        self.site.preview_secret = 'f' * 32
        self.site.save()
        self.assertEqual(self.sites(f'/{slug}/_preview/{token}/').status_code, 404)

    def test_token_of_another_site(self):
        other_event = Event.objects.create(name='Other', event_code='OTH1', user=self.owner, start_time=timezone.now(),
                                           plan=Event.PLAN_STUDIO)
        other = publishing.ensure_site(other_event)
        token = hosting.make_preview_token(other)
        self.assertEqual(self.sites(f'/{self.site.slug}/_preview/{token}/').status_code, 404)

    def test_version_preview(self):
        first = self.site.versions.get(number=1)
        token = hosting.make_preview_token(self.site, version=self.v2)
        self.assertContains(self.sites(f'/{self.site.slug}/_preview/{token}/'), '<h1>Fair</h1>')
        token = hosting.make_preview_token(self.site, version=first)
        self.assertEqual(self.sites(f'/{self.site.slug}/_preview/{token}/').status_code, 404)  # v1 has no pages
        redirect = self.client.get(reverse('studio:version_preview', args=[self.event.pk, self.v2.pk]))
        self.assertTrue(redirect['Location'].startswith(f'https://{SITES}/'))

    def test_private_link_lasts_days(self):
        data = self.client.post(reverse('studio:preview_link', args=[self.event.pk])).json()
        token = data['url'].rstrip('/').rsplit('/', 1)[1]
        with mock.patch('studio.hosting.time.time', return_value=time.time() + 6 * 86400):
            self.assertIsNotNone(hosting.read_preview_token(self.site, token))
        with mock.patch('studio.hosting.time.time', return_value=time.time() + 8 * 86400):
            self.assertIsNone(hosting.read_preview_token(self.site, token))

    def test_reset_previews(self):
        token = hosting.make_preview_token(self.site)
        self.client.post(reverse('studio:settings', args=[self.event.pk]), {'action': 'reset_previews'})
        self.site.refresh_from_db()
        self.assertIsNone(hosting.read_preview_token(self.site, token))


class DataTests(StudioTestCase):
    def test_data_json(self):
        self.publish()
        response = self.sites(f'/{self.site.slug}/_attnd/data.json')
        self.assertEqual(response['Access-Control-Allow-Origin'], '*')
        data = response.json()
        self.assertEqual(data['event']['name'], 'Arts Fair')
        self.assertEqual(data['event']['timezone'], 'America/Chicago')
        self.assertEqual(data['event']['website_url'], f'https://{SITES}/{self.site.slug}/')
        self.assertEqual([i['name'] for i in data['schedule']], ['Band'])
        self.assertEqual(data['locations'][0]['name'], 'Main Stage')
        self.assertEqual(data['app']['join_url'], 'https://attnd.app/join/ARTS1/')
        etag = response['ETag']
        self.assertEqual(self.sites(f'/{self.site.slug}/_attnd/data.json', HTTP_IF_NONE_MATCH=etag).status_code, 304)
        ScheduledItem.objects.create(event=self.event, name='Late show', start_time=self.event.start_time)
        changed = self.sites(f'/{self.site.slug}/_attnd/data.json', HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(len(changed.json()['schedule']), 2)

    def test_draft_event_leaks_nothing(self):
        Event.objects.filter(pk=self.event.pk).update(status=Event.STATUS_DRAFT)
        self.publish()
        data = self.sites(f'/{self.site.slug}/_attnd/data.json').json()
        self.assertEqual(data['event']['name'], 'Arts Fair')
        self.assertEqual((data['schedule'], data['locations'], data['updates']), ([], [], []))

    def test_qr(self):
        self.publish()
        response = self.sites(f'/{self.site.slug}/_attnd/qr.svg')
        self.assertEqual(response['Content-Type'], 'image/svg+xml')
        self.assertIn(b'<svg', response.content)

    def test_bundle_website_url_api_016(self):
        url = f'/api/v2/events/{self.event.pk}/bundle/'
        auth = {'HTTP_AUTHORIZATION': 'Bearer test-key'}
        self.assertIsNone(Client().get(url, secure=True, **auth).json()['event']['website_url'])
        before = Event.objects.get(pk=self.event.pk).content_version
        self.publish()
        event = Event.objects.get(pk=self.event.pk)
        self.assertGreater(event.content_version, before)
        self.assertEqual(Client().get(url, secure=True, **auth).json()['event']['website_url'],
                         f'https://{SITES}/{self.site.slug}/')
        publishing.unpublish(self.site)
        self.assertIsNone(Client().get(url, secure=True, **auth).json()['event']['website_url'])


@override_settings(RATE_LIMITS={**settings.RATE_LIMITS, 'studio_form_ip': (3, 600), 'studio_form_site': (100, 3600)})
class FormTests(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.publish()
        self.url = f'/{self.site.slug}/_attnd/forms/newsletter'

    def test_stores_and_emails(self):
        response = self.sites(self.url, 'post', data={'email': 'fan@example.com', 'name': 'Fan', '_page': '/x/',
                                                       '_ts': str((time.time() - 10) * 1000)},
                              HTTP_ACCEPT='application/json')
        self.assertEqual(response.json(), {'ok': True, 'preview': False})
        entry = FormSubmission.objects.get()
        self.assertEqual(entry.data, {'email': 'fan@example.com', 'name': 'Fan'})
        self.assertEqual(entry.form, 'newsletter')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('owner@example.com', mail.outbox[0].to)
        self.assertIn('fan@example.com', mail.outbox[0].body)
        html = self.sites(self.url, 'post', data={'email': 'b@example.com'})
        self.assertContains(html, 'Thanks!')
        self.assertEqual(FormSubmission.objects.count(), 2)

    def test_spam_is_answered_but_not_kept(self):
        self.assertEqual(self.sites(self.url, 'post', data={'email': 'x@y.z', '_hp': 'bot'}).status_code, 200)
        self.assertEqual(self.sites(self.url, 'post', data={'email': 'x@y.z', '_ts': str(time.time() * 1000)}).status_code, 200)
        self.assertFalse(FormSubmission.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_rate_limits_and_limits(self):
        for _ in range(3):
            self.assertEqual(self.sites(self.url, 'post', data={'email': 'x@y.z'}).status_code, 200)
        self.assertEqual(self.sites(self.url, 'post', data={'email': 'x@y.z'}).status_code, 429)
        self.assertEqual(FormSubmission.objects.count(), 3)
        big = self.sites(self.url, 'post', data={'message': 'x' * 30_000})
        self.assertEqual(big.status_code, 413)
        self.assertEqual(self.sites(f'/{self.site.slug}/_attnd/forms/Bad Name', 'post', data={'a': 'b'}).status_code, 404)
        self.assertEqual(self.sites(self.url).status_code, 405)

    def test_preview_entries_are_not_stored(self):
        token = hosting.make_preview_token(self.site)
        response = self.sites(f'/{self.site.slug}/_preview/{token}/_attnd/forms/newsletter', 'post',
                              data={'email': 'x@y.z'}, HTTP_ACCEPT='application/json')
        self.assertEqual(response.json()['preview'], True)
        self.assertFalse(FormSubmission.objects.exists())

    def test_console_lists_and_exports(self):
        FormSubmission.objects.create(site=self.site, form='vendors', data={'name': '=HYPERLINK("x")', 'email': 'v@x.co'})
        page = self.client.get(reverse('studio:editor', args=[self.event.pk]))
        self.assertContains(page, 'vendors')
        csv_response = self.client.get(reverse('studio:form_csv', args=[self.event.pk, 'vendors']))
        text = csv_response.content.decode()
        self.assertIn('submitted_at,page,name,email', text)
        self.assertIn("'=HYPERLINK", text)
        entry = FormSubmission.objects.get()
        self.client.post(reverse('studio:form_delete', args=[self.event.pk]), {'entry': entry.pk})
        self.assertFalse(FormSubmission.objects.exists())


class AssetTests(StudioTestCase):
    def upload(self, name, data, client=None):
        return (client or self.client).post(reverse('studio:assets', args=[self.event.pk]),
                                            {'files': [SimpleUploadedFile(name, data, content_type='image/png')]})

    def test_upload_sniffs_types(self):
        with mock.patch('studio.assets.start_describe'):
            ok = self.upload('Logo Final!.png', png_bytes())
        self.assertEqual(ok.status_code, 200, ok.content)
        asset = StudioAsset.objects.get()
        self.assertEqual((asset.kind, asset.content_type, asset.name, asset.width), ('image', 'image/png',
                                                                                     'logo-final.png', 4))
        fake = self.upload('evil.png', b'MZ\x90\x00 not an image')
        self.assertEqual(fake.status_code, 400)
        self.assertIn('only PNG', fake.json()['errors'][0])
        html = self.upload('page.svg', b'<html><script>alert(1)</script></html>')
        self.assertEqual(html.status_code, 400)
        self.assertEqual(StudioAsset.objects.count(), 1)

    def test_size_limits(self):
        from studio import assets
        with mock.patch.dict(assets.LIMITS, {'image': 100}):
            response = self.upload('big.png', png_bytes((200, 200)))
        self.assertEqual(response.status_code, 400)
        self.assertIn('too big', response.json()['errors'][0])
        with mock.patch.object(assets, 'MAX_ASSETS', 0):
            self.assertIn('full', self.upload('a.png', png_bytes()).json()['errors'][0])

    def test_served_on_the_sites_host(self):
        from studio.assets import create_asset
        with self.captureOnCommitCallbacks(execute=False):
            image = create_asset(self.event, png_bytes(), 'photo.png')
            svg = create_asset(self.event, b'<svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>', 'x.svg')
        response = self.sites(f'/{self.site.slug}/{image.site_path}')
        self.assertEqual(response['Content-Type'], 'image/png', response.content[:300])
        self.assertEqual(response.content, png_bytes())
        svg_response = self.sites(f'/{self.site.slug}/{svg.site_path}')
        self.assertIn('sandbox', svg_response['Content-Security-Policy'])
        self.assertEqual(self.sites(f'/{self.site.slug}/_attnd/assets/{image.id}/other.png').status_code, 404)
        self.assertEqual(self.sites(f'/{self.site.slug}/_attnd/assets/not-a-uuid/photo.png').status_code, 404)

    def test_describe_with_vision(self):
        from studio.assets import create_asset, describe
        with self.captureOnCommitCallbacks(execute=False):
            asset = create_asset(self.event, png_bytes(), 'photo.png')
        result = {'description': 'A red square', 'alt': 'Red square', 'tags': ['Logo', 'red'], 'colors': ['#ff0000', 'bad']}
        from attnd.testing import fake_claude
        with fake_claude(result) as claude:
            describe(asset.pk)
        self.assertEqual(claude.last['messages'][0]['content'][0]['type'], 'image')  # Claude Haiku's vision
        asset.refresh_from_db()
        self.assertEqual((asset.description, asset.tags, asset.colors), ('A red square', ['logo', 'red'], ['#ff0000']))
        self.assertEqual((StudioUsage.objects.get().kind, StudioUsage.objects.get().provider), ('describe', 'anthropic'))

    def test_edit_delete_and_flyer(self):
        from studio.assets import create_asset
        with self.captureOnCommitCallbacks(execute=False):
            asset = create_asset(self.event, png_bytes(), 'flyer.png')
        url = reverse('studio:asset', args=[self.event.pk, asset.pk])
        self.client.post(url, {'description': 'Our flyer', 'tags': 'Flyer, art'})
        asset.refresh_from_db()
        self.assertEqual(asset.tags, ['flyer', 'art'])
        number = self.client.post(reverse('studio:flyer', args=[self.event.pk]), {'asset_id': asset.pk}).json()['number']
        self.site.refresh_from_db()
        self.assertEqual((self.site.draft.number, self.site.draft.flyer_asset_id), (number, asset.pk))
        delete = reverse('studio:asset_delete', args=[self.event.pk])
        self.client.post(delete, {'asset': str(asset.pk)})
        self.assertTrue(type(asset).objects.filter(pk=asset.pk).exists())  # it's the draft's flyer
        self.publish()
        event = Event.objects.get(pk=self.event.pk)
        self.assertTrue(event.flyer.name)
        self.assertEqual(event.flyer.read(), png_bytes())


class VersionTests(StudioTestCase):
    def test_code_save_restore_undo(self):
        url = reverse('studio:code', args=[self.event.pk])
        self.assertEqual(self.client.get(url).json()['files']['index.html'], PAGE)
        bad = self.client.post(url, {'files': json.dumps({'../etc.html': 'x'})})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client.post(url, {'files': json.dumps({'_attnd/x.js': 'x'})}).status_code, 400)
        self.assertEqual(self.client.post(url, {'files': json.dumps({'big.html': 'x' * 400_000})}).status_code, 400)
        same = self.client.post(url, {'files': json.dumps(self.site.draft.files)}).json()
        self.assertFalse(same['saved'])
        saved = self.client.post(url, {'files': json.dumps({'index.html': '<p>v3</p>'}), 'summary': 'Rewrote'}).json()
        self.assertEqual(saved['number'], 3)
        self.site.refresh_from_db()
        self.assertEqual(self.site.draft.files, {'index.html': '<p>v3</p>'})
        self.client.post(reverse('studio:undo', args=[self.event.pk]))
        self.site.refresh_from_db()
        self.assertEqual((self.site.draft.number, self.site.draft.files), (4, self.v2.files))
        self.assertEqual(self.site.draft.source, SiteVersion.SOURCE_RESTORE)
        v3 = self.site.versions.get(number=3)
        self.client.post(reverse('studio:restore', args=[self.event.pk]), {'version': v3.pk})
        self.site.refresh_from_db()
        self.assertEqual(self.site.draft.files, {'index.html': '<p>v3</p>'})

    def test_publish_rollback_and_look(self):
        version = publishing.new_version(self.site, self.owner, app_look={
            'accent_color': '#FF5A1F', 'display': {'default_tab': 'schedule', 'bogus': 1}})
        self.client.post(reverse('studio:publish', args=[self.event.pk]))
        self.site.refresh_from_db()
        self.assertEqual(self.site.published, version)
        event = Event.objects.get(pk=self.event.pk)
        self.assertEqual(event.accent_color, '#FF5A1F')
        self.assertEqual(event.display_config.get('default_tab'), 'schedule')
        self.assertNotIn('bogus', event.display_config)
        # Publish an older version: the live site rolls back, the draft stays.
        self.client.post(reverse('studio:publish_version', args=[self.event.pk]), {'version': self.v2.pk})
        self.site.refresh_from_db()
        self.assertEqual((self.site.published, self.site.draft), (self.v2, version))
        self.client.post(reverse('studio:unpublish', args=[self.event.pk]))
        self.site.refresh_from_db()
        self.assertIsNone(self.site.published)

    def test_publish_needs_a_home_page(self):
        publishing.new_version(self.site, self.owner, files={'about.html': PAGE})
        self.client.post(reverse('studio:publish', args=[self.event.pk]))
        self.site.refresh_from_db()
        self.assertIsNone(self.site.published)

    def test_slug_settings(self):
        other_event = Event.objects.create(name='Other', event_code='OTH2', user=self.owner, start_time=timezone.now(),
                                           plan=Event.PLAN_STUDIO)
        other = publishing.ensure_site(other_event)
        url = reverse('studio:settings', args=[self.event.pk])
        self.client.post(url, {'slug': other.slug})
        self.client.post(url, {'slug': 'console'})
        self.client.post(url, {'slug': 'X!'})
        self.site.refresh_from_db()
        self.assertEqual(self.site.slug, 'arts-fair')
        self.client.post(url, {'slug': 'arts-fair-2026'})
        self.site.refresh_from_db()
        self.assertEqual((self.site.slug, self.site.notify_on_forms), ('arts-fair-2026', False))

    def test_unique_slugs(self):
        from studio.models import unique_slug
        self.assertEqual(unique_slug('Arts Fair'), 'arts-fair-2')
        self.assertEqual(unique_slug('!!'), 'event')
        self.assertEqual(unique_slug('Console'), 'console-2')

    def test_website_page_and_components(self):
        """The Website page draws the same components as the chat's Website tab (no Studio chat of its own)."""
        page = self.client.get(reverse('studio:editor', args=[self.event.pk]))
        self.assertContains(page, 'component--website_overview')
        self.assertContains(page, 'sandbox="allow-scripts allow-forms allow-popups"')
        self.assertNotContains(page, 'allow-same-origin')
        self.assertNotContains(page, '<h1>Fair</h1>')  # site HTML never rendered on attnd.app
        self.assertNotContains(page, 'studio.js')
        from console import components as C
        data = C.load(self.owner, 'website_overview', {'event_id': self.event.pk})
        self.assertTrue(data['preview'].startswith(f'https://{SITES}/'))
        self.assertEqual(data['draft'], self.site.draft.number)
        self.assertIn('publish_website', [a['name'] for a in data['actions']])
        versions = C.load(self.owner, 'website_versions', {'event_id': self.event.pk})
        self.assertTrue(any(a['name'] == 'restore_website_version' for r in versions['rows'] for a in r['actions']))
        # Publish and the status sit above the preview; Full screen preview opens a dialog with Desktop | Mobile and
        # Publish too (owner, Nov 21, 2026).
        html = page.content.decode()
        self.assertLess(html.index('Not published'), html.index('<iframe'))
        self.assertLess(html.index('Publish'), html.index('<iframe'))
        self.assertIn('Full screen preview', html)
        dialog = html[html.index('data-site-full'):]
        self.assertIn('data-site-size="mobile"', dialog)
        self.assertIn('Publish', dialog[:dialog.index('</dialog>')])


class ReportTests(StudioTestCase):
    def test_report_and_take_down(self):
        self.publish()
        url = f'/{self.site.slug}/_attnd/report'
        self.assertContains(self.sites(url), 'Report this site')
        self.assertEqual(self.sites(url, 'post', data={'reason': 'nope'}).status_code, 400)
        self.assertContains(self.sites(url, 'post', data={'reason': 'scam', 'details': 'Fake tickets'}), 'Thank you')
        self.sites(url, 'post', data={'reason': 'spam', '_hp': 'x'})
        report = SiteReport.objects.get()
        self.assertEqual((report.reason, report.details), ('scam', 'Fake tickets'))
        staff = User.objects.create_user('staff', 'staff@example.com', 'pw', is_superuser=True, is_staff=True)
        client = TwoFactorClient()
        client.force_login(staff)
        self.assertContains(client.get(reverse('studio:reports')), 'Fake tickets')
        client.post(reverse('studio:reports'), {'site': self.site.pk, 'action': 'suspend', 'reason': 'Scam'})
        self.site.refresh_from_db()
        self.assertTrue(self.site.suspended)
        self.assertEqual(self.sites(f'/{self.site.slug}/').status_code, 404)
        self.client.post(reverse('studio:publish', args=[self.event.pk]))  # the organizer can't republish
        client.post(reverse('studio:reports'), {'site': self.site.pk, 'action': 'reinstate'})
        self.assertEqual(self.sites(f'/{self.site.slug}/').status_code, 200)
        self.assertEqual(Client().get(reverse('studio:reports')).status_code, 302)  # sign-in first

    @override_settings(RATE_LIMITS={**settings.RATE_LIMITS, 'studio_report_ip': (1, 3600)})
    def test_reports_are_rate_limited(self):
        self.publish()
        url = f'/{self.site.slug}/_attnd/report'
        self.sites(url, 'post', data={'reason': 'spam'})
        self.assertEqual(self.sites(url, 'post', data={'reason': 'spam'}).status_code, 429)


class WebsiteTabTests(StudioTestCase):
    """Owner feedback on the Website tab (Nov 21, 2026): no design box of its own (the chat is the one place to ask),
    an empty state with Build from your current website / Start from scratch, a preview that works, and no address
    while the site isn't published."""

    def test_the_preview_url_answers_200_for_a_built_draft(self):
        from console import components as C
        data = C.load(self.owner, 'website_overview', {'event_id': self.event.pk})
        self.assertTrue(data['built'])
        path = data['preview'].split(SITES, 1)[1]
        response = self.sites(path)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Fair')
        self.assertEqual(data['public_url'], '')  # not published: no address shown
        page = self.client.get(reverse('studio:editor', args=[self.event.pk]))
        self.assertNotContains(page, 'What should change on the website?')
        self.assertNotContains(page, 'Bring your current website into Studio')
        self.assertNotContains(page, SITES + '/' + self.site.slug + '/<')  # no raw address before publishing
        self.publish()
        self.assertTrue(C.load(self.owner, 'website_overview', {'event_id': self.event.pk})['public_url'])

    def test_an_empty_draft_shows_the_placeholder_not_an_iframe(self):
        from console import components as C
        publishing.new_version(self.site, self.owner, files={}, summary='Empty')
        self.event.source_urls = ['https://www.artsfair.example/']
        self.event.save()
        data = C.load(self.owner, 'website_overview', {'event_id': self.event.pk})
        self.assertFalse(data['built'])
        self.assertEqual(data['preview'], '')
        labels = [f['submit'][0]['label'] for f in data['starts']]
        self.assertEqual(labels, ['Build from your current website', 'Start from scratch'])
        page = self.client.get(reverse('studio:editor', args=[self.event.pk]))
        self.assertContains(page, 'No website yet')
        self.assertNotContains(page, '<iframe')
        # Start from scratch: a design run from the event's own details, as a task (the chat's live card).
        with self.captureOnCommitCallbacks(execute=False):
            response = self.client.post(reverse('studio:start', args=[self.event.pk]), HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 202)
        run = StudioRun.objects.get()
        self.assertIn(self.event.name, run.prompt)
        self.assertEqual(response.json()['task']['id'], f'studio-{run.pk}')
        # While it works the empty state shows the progress, not the buttons.
        self.assertEqual(C.load(self.owner, 'website_overview', {'event_id': self.event.pk})['starts'], [])
        from agent import registry as agent_registry
        self.assertIn('start_website_from_scratch', agent_registry.writes_for(self.owner))


class DesignWebsiteTests(StudioTestCase):
    """Ask Attnd is the one chat: design_website starts Studio's engine in the background and answers a task (the
    chat's live card); the canvas has a Website tab for people with Studio access."""

    def test_design_creates_a_run_and_a_task(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            response = self.client.post(reverse('studio:design', args=[self.event.pk]), {'instruction': 'Make it bold'})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(len(callbacks), 1)  # the background thread starts after commit
        run = StudioRun.objects.get()
        self.assertEqual(run.prompt, 'Make it bold')
        task = response.json()['task']
        self.assertEqual((task['id'], task['status'], task['kind']), (f'studio-{run.pk}', 'queued', 'website'))
        busy = self.client.post(reverse('studio:design', args=[self.event.pk]), {'instruction': 'Again'})
        self.assertEqual(busy.status_code, 409)
        feed = self.client.get(f'/console/tasks/?ids=studio-{run.pk}').json()['tasks']
        self.assertIn(f'studio-{run.pk}', [t['id'] for t in feed])
        # The agent step's JSON answer becomes a live task card.
        from agent.models import AgentRun, AgentStep
        from agent.views import step_json
        agent_run = AgentRun.objects.create(user=self.owner, goal='make the hero bigger')
        step = AgentStep.objects.create(run=agent_run, seq=1, tool='design_website', result=response.content.decode())
        self.assertEqual(step_json(step)['task'], f'studio-{run.pk}')

    def test_spend_cap(self):
        StudioUsage.objects.create(event=self.event, kind='chat', provider='openai', model='x',
                                   cost_usd=settings.STUDIO_DAILY_SPEND_CAP_USD + 1)
        response = self.client.post(reverse('studio:design', args=[self.event.pk]), {'instruction': 'Hi'})
        self.assertEqual(response.status_code, 429)
        self.assertFalse(StudioRun.objects.exists())

    def test_actions_and_canvas_tab(self):
        from agent import loop, registry as agent_registry
        writes = agent_registry.writes_for(self.owner)
        for name in ('design_website', 'publish_website', 'unpublish_website', 'restore_website_version',
                     'bring_into_studio'):
            self.assertIn(name, writes)
        for name in ('publish_website', 'unpublish_website', 'restore_website_version'):
            self.assertTrue(writes[name].confirm, name)
        self.assertFalse(writes['design_website'].confirm)
        # "make the hero bigger" names no area: design_website is always among the tools for Studio users.
        run = type('R', (), {'goal': 'make the hero bigger', 'page_url': ''})()
        self.assertIn('design_website', {t['name'] for t in loop.tools_for(self.owner, run)})
        page = self.client.get(reverse('console:assistant') + f'?event={self.event.pk}&tab=website')
        self.assertContains(page, 'data-tab="Website"')
        self.assertContains(page, 'component--website_overview')


class SitesDomainMoveTests(StudioTestCase):
    """attnd.site (owner, Nov 21, 2026): the old herokuapp.com sites host only redirects site paths to the new host."""

    def setUp(self):
        super().setUp()
        self.enterContext(override_settings(
            SITES_HOST='dev.attnd.site', OLD_SITES_HOST='attnd-dev-7ab893f37020.herokuapp.com',
            ALLOWED_HOSTS=['testserver', 'dev.attnd.site', 'attnd-dev-7ab893f37020.herokuapp.com']))

    def old(self, path):
        return Client().get(path, HTTP_HOST='attnd-dev-7ab893f37020.herokuapp.com', secure=True)

    def test_old_host_redirects_site_paths_and_nothing_else(self):
        response = self.old(f'/{self.site.slug}/about.html?x=1')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], f'https://dev.attnd.site/{self.site.slug}/about.html?x=1')
        for path in ('/console/', '/api/v2/app-status/', '/accounts/login/', '/static/console/console.css', '/'):
            self.assertEqual(self.old(path).status_code, 404, path)
        self.assertNotIn('Set-Cookie', response)

    @override_settings(SITE_URL='https://dev.attnd.app')
    def test_bare_sites_root_goes_to_attnd_home_and_unknown_slugs_stay_404(self):
        new = Client()
        response = new.get('/', HTTP_HOST='dev.attnd.site', secure=True)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], 'https://dev.attnd.app/home/')
        self.assertNotIn('Set-Cookie', response)
        self.assertEqual(new.get('/no-such-site/', HTTP_HOST='dev.attnd.site', secure=True).status_code, 404)

    def test_addresses_follow_the_sites_host(self):
        from studio import publishing
        self.publish()
        self.assertEqual(self.site.public_url(), f'https://dev.attnd.site/{self.site.slug}/')
        self.assertEqual(publishing.website_url(self.event), f'https://dev.attnd.site/{self.site.slug}/')
        from io import StringIO

        from django.core.management import call_command
        version = Event.objects.get(pk=self.event.pk).content_version
        call_command('refresh_site_addresses', stdout=StringIO())
        self.assertGreater(Event.objects.get(pk=self.event.pk).content_version, version)


class ProgressStepTests(StudioTestCase):
    """A progress step is one row that counts up, then one ✓ with its done text (owner, Nov 21, 2026)."""

    def test_progress_updates_in_place(self):
        from studio.agent import Workspace
        from studio.models import StudioRun
        from studio.views import run_task
        run = StudioRun.objects.create(site=self.site, created_by=self.owner, kind=StudioRun.KIND_IMPORT,
                                       status=StudioRun.RUNNING, prompt='x')
        ws = Workspace.__new__(Workspace)
        ws.run = run
        for n in (25, 29, 34):
            ws.step(f'Copied {n} of 52 images, fonts and PDFs…', key='copy', done=f'Copied {n} images, fonts and PDFs')
        self.assertEqual([s['text'] for s in run_task(run)['steps']], ['Copied 34 of 52 images, fonts and PDFs…'])
        ws.step('Copied 52 of 52 images, fonts and PDFs…', key='copy', done='Copied 52 images, fonts and PDFs')
        ws.step('Rebuilding the pages')
        steps = run_task(run)['steps']
        self.assertEqual([(s['text'], s['state']) for s in steps],
                         [('Copied 52 images, fonts and PDFs', 'done'), ('Rebuilding the pages', 'running')])


class SitesSeoTests(StudioTestCase):
    def test_canonical_and_the_sites_sitemap(self):
        self.publish()
        body = self.sites(f'/{self.site.slug}/').content.decode()
        self.assertIn(f'<link rel="canonical" href="https://{SITES}/{self.site.slug}/">', body)
        sitemap = self.sites('/sitemap.xml').content.decode()
        self.assertIn(f'<loc>https://{SITES}/{self.site.slug}/</loc>', sitemap)
        self.assertIn('Sitemap:', self.sites('/robots.txt').content.decode())
