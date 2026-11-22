"""Studio improvements and safeguards (batch 7 §6): the pre-publish check and its fixes, drift, translations, SEO on
served pages, the Get the app banner, cookie-free analytics, quick text edits, the content scan and held sites,
forms / links / scripts at save and serve, publish limits, report alerts, rights to imported sites, EXIF stripping."""

import io
import json
from unittest import mock

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import User
from django.core import mail
from django.urls import reverse

from events.models import Event, ScheduledItem
from studio import agent, analytics, hosting, publishing, quality, safety, seo
from studio.models import Site, SiteHit, SiteReport, SiteVersion
from studio.tests import PAGE, StudioTestCase

GOOD = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
        'content="width=device-width, initial-scale=1"><title>Arts Fair</title><meta name="description" '
        'content="A fair"></head><body><h1>Arts Fair</h1><p>See you there.</p></body></html>')


def version_with(site, user, files, summary='Test'):
    return publishing.new_version(site, user, files=files, summary=summary, source=SiteVersion.SOURCE_CODE)


class QualityCheckTests(StudioTestCase):
    def codes(self, files):
        v = version_with(self.site, self.owner, files)
        return {i['code']: i for i in quality.check(self.site, v)['issues']}

    def test_a_clean_page_passes(self):
        v = version_with(self.site, self.owner, {'index.html': GOOD})
        report = quality.check(self.site, v)
        self.assertEqual([i['code'] for i in report['issues']], [])
        self.assertFalse(report['blocked'])

    def test_finds_the_common_problems(self):
        page = ('<html><head><style>.x{color:#777;background:#888}.w{width:900px}</style></head><body>'
                '<h1>A</h1><h1>B</h1><h4>c</h4><img src="missing.png"><a href="nope.html">x</a>'
                '<a href="https://bit.ly/abc">short</a></body></html>')
        found = self.codes({'index.html': page})
        for code in ('broken_link', 'broken_image', 'alt', 'h1', 'headings', 'contrast', 'viewport', 'wide', 'title',
                     'description', 'lang', 'risky_link'):
            self.assertIn(code, found, code)
        self.assertEqual(found['contrast']['level'], quality.WARNING)

    def test_safety_errors_block(self):
        page = (GOOD.replace('<p>See you there.</p>', '<form action="https://evil.example/x"><input type="password" '
                'name="pw"></form><script src="https://cdn.example/x.js"></script>'))
        v = version_with(self.site, self.owner, {'index.html': page})
        report = quality.check(self.site, v)
        self.assertTrue(report['blocked'])
        self.assertTrue({'form_origin', 'password_field', 'outside_script'} <= {i['code'] for i in report['issues']})

    def test_autofix_fixes_only_the_trivial(self):
        Event.objects.filter(pk=self.event.pk).update(description='Art all day.')
        self.site.event.refresh_from_db()
        page = '<html><head></head><body><h1>x</h1><img src="img/big-stage.png"></body></html>'
        files, count = quality.autofix(self.site, {'index.html': page, 'styles.css': 'a{}'})
        self.assertEqual(count, 5)  # alt, viewport, lang, title, description
        self.assertIn('alt="Big stage"', files['index.html'])
        self.assertIn('lang="en"', files['index.html'])
        self.assertIn('<title>Arts Fair</title>', files['index.html'])
        self.assertEqual(files['styles.css'], 'a{}')

    def test_fix_view_makes_a_version(self):
        version_with(self.site, self.owner, {'index.html': '<html><head></head><body><h1>Arts Fair</h1></body></html>'})
        before = self.site.versions.count()
        response = self.client.post(reverse('studio:fix', args=[self.event.pk]))
        self.assertEqual(response.status_code, 302)
        self.site.refresh_from_db()
        self.assertEqual(self.site.versions.count(), before + 1)
        self.assertIn('name="viewport"', self.site.draft.files['index.html'])


class DriftTests(StudioTestCase):
    def test_dates_times_and_name(self):
        tz_start = self.event.start_time.astimezone(__import__('zoneinfo').ZoneInfo('America/Chicago'))
        wrong_day = (tz_start.replace(day=1) if tz_start.day > 5 else tz_start.replace(day=20))
        month = wrong_day.strftime('%B')
        page = GOOD.replace('Arts Fair</h1>', 'Old Fair</h1>').replace('<title>Arts Fair', '<title>Old Fair').replace(
            '<p>See you there.</p>', f'<p>Join us {month} {wrong_day.day}! Band at 11:45 PM.</p>')
        v = version_with(self.site, self.owner, {'index.html': page})
        codes = {i['code'] for i in quality.drift(self.site, v)}
        self.assertEqual(codes, {'drift_date', 'drift_time', 'drift_name'})

    def test_matching_text_is_fine(self):
        start = self.event.start_time.astimezone(__import__('zoneinfo').ZoneInfo('America/Chicago'))
        text = f'{start.strftime("%b")} {start.day}. Band at {start.strftime("%-I:%M %p")}.'
        v = version_with(self.site, self.owner, {'index.html': GOOD.replace('See you there.', text)})
        self.assertEqual(quality.drift(self.site, v), [])


class TranslationTests(StudioTestCase):
    def test_stale_when_the_source_changed(self):
        es = GOOD.replace('lang="en"', 'lang="es"').replace(
            '<head>', f'<head><meta name="attnd-source" content="index.html {quality.source_hash(GOOD)}">')
        v = version_with(self.site, self.owner, {'index.html': GOOD, 'es/index.html': es})
        self.assertEqual(quality.translations(v)['es']['stale'], [])
        v2 = version_with(self.site, self.owner, {'index.html': GOOD.replace('See you', 'Meet you'), 'es/index.html': es})
        self.assertEqual(quality.translations(v2)['es']['stale'], ['index.html'])
        self.assertIn('translation_stale', {i['code'] for i in quality.check(self.site, v2)['issues']})

    def test_translate_starts_a_run_with_the_hashes(self):
        version_with(self.site, self.owner, {'index.html': GOOD})
        with mock.patch('studio.agent.start'), mock.patch('studio.ai.budget_left', return_value=5):
            response = self.client.post(reverse('studio:translate', args=[self.event.pk]), {'language': 'es'})
        self.assertEqual(response.status_code, 202)
        run = self.site.runs.latest('created_at')
        self.assertIn(quality.source_hash(GOOD), run.prompt)
        self.assertIn('es/', run.prompt)

    def test_hreflang_on_served_pages(self):
        version_with(self.site, self.owner, {'index.html': GOOD, 'es/index.html': GOOD.replace('lang="en"', 'lang="es"')})
        self.publish()
        body = self.sites(f'/{self.site.slug}/').content.decode()
        self.assertIn('hreflang="es"', body)
        self.assertIn('hreflang="x-default"', body)


class ServedPageTests(StudioTestCase):
    def test_seo_banner_beacon_and_sanitising(self):
        page = GOOD.replace('<p>See you there.</p>', '<a href="https://example.org/x">Out</a><a href="about.html">In</a>'
                            '<form action="https://evil.example/f" method="post"><input name="email"><input '
                            'type="password" name="pw"></form><script src="https://cdn.evil/x.js"></script>'
                            '<img src="_attnd/assets/x/a.png" alt="">')
        version_with(self.site, self.owner, {'index.html': page, 'about.html': GOOD})
        self.publish()
        body = self.sites(f'/{self.site.slug}/').content.decode()
        self.assertIn('rel="noopener nofollow ugc"', body)
        self.assertNotIn('evil.example/f', body)
        self.assertIn('data-attnd-blocked', body)
        self.assertNotIn('type="password"', body)
        self.assertNotIn('cdn.evil', body)
        self.assertIn('"@type": "Event"', body)
        self.assertIn('og:image', body)
        self.assertIn('data-attnd-app-banner', body)
        self.assertIn('_attnd/qr.svg', body)
        self.assertIn("sendBeacon('_attnd/hit'", body)
        about = self.sites(f'/{self.site.slug}/about').content.decode()
        self.assertNotIn('application/ld+json', about)  # the Event JSON-LD is on the home page

    def test_previews_have_no_beacon(self):
        token = hosting.make_preview_token(self.site)
        body = self.sites(f'/{self.site.slug}/_preview/{token}/').content.decode()
        self.assertIn('data-attnd-app-banner', body)
        self.assertNotIn('_attnd/hit', body)

    def test_json_ld_from_real_data(self):
        data = seo.event_json_ld(self.site, 'https://sites.test/x/')
        self.assertEqual(data['name'], 'Arts Fair')
        self.assertTrue(data['startDate'].endswith(('-06:00', '-05:00')))

    def test_site_sitemap_and_robots(self):
        self.publish()
        sitemap = self.sites(f'/{self.site.slug}/sitemap.xml')
        self.assertEqual(sitemap.status_code, 200)
        self.assertIn(f'/{self.site.slug}/about.html', sitemap.content.decode())
        robots = self.sites(f'/{self.site.slug}/robots.txt').content.decode()
        self.assertIn(f'Sitemap: https://sites.test/{self.site.slug}/sitemap.xml', robots)

    def test_unpaid_event_gets_the_demo_ribbon(self):
        self.publish()
        self.assertIn('data-attnd-demo', self.sites(f'/{self.site.slug}/').content.decode())
        Event.objects.filter(pk=self.event.pk).update(paid=True)
        self.assertNotIn('data-attnd-demo', self.sites(f'/{self.site.slug}/').content.decode())

    def test_draft_event_site_is_preview_only(self):
        self.publish()
        Event.objects.filter(pk=self.event.pk).update(status=Event.STATUS_DRAFT)
        self.assertEqual(self.sites(f'/{self.site.slug}/').status_code, 404)
        self.assertIsNone(publishing.website_url(Event.objects.get(pk=self.event.pk)))


class AnalyticsTests(StudioTestCase):
    def hit(self, payload, base=None):
        return self.sites(f'{base or "/" + self.site.slug + "/"}_attnd/hit', method='post',
                          data=json.dumps(payload), content_type='text/plain')

    def test_counts_without_identifiers(self):
        self.publish()
        for _ in range(3):
            self.assertEqual(self.hit({'k': 'view', 'p': '/arts/', 'r': 'www.Google.com'}).status_code, 204)
        self.hit({'k': 'open_app', 'p': '/arts/'})
        self.hit({'k': 'bogus'})
        row = SiteHit.objects.get(site=self.site, kind='view')
        self.assertEqual((row.count, row.referrer), (3, 'google.com'))
        summary = analytics.summary(self.site)
        self.assertEqual((summary['views'], summary['open_app']), (3, 1))
        self.assertEqual(summary['referrers'], [('google.com', 3)])

    def test_previews_never_count(self):
        token = hosting.make_preview_token(self.site)
        self.hit({'k': 'view'}, base=f'/{self.site.slug}/_preview/{token}/')
        self.assertFalse(SiteHit.objects.exists())

    def test_component_renders(self):
        self.publish()
        self.hit({'k': 'view', 'p': '/x'})
        response = self.client.get(reverse('studio:editor', args=[self.event.pk]))
        self.assertContains(response, 'Website visits')


class TextEditTests(StudioTestCase):
    def test_replaces_text_not_markup(self):
        version_with(self.site, self.owner, {'index.html': GOOD})
        url = reverse('studio:text_edit', args=[self.event.pk])
        response = self.client.post(url, {'page': 'index.html', 'old': 'See you there.', 'new': 'See you soon & bring a friend'})
        self.assertEqual(response.status_code, 200, response.content)
        self.site.refresh_from_db()
        self.assertIn('See you soon &amp; bring a friend', self.site.draft.files['index.html'])
        self.assertEqual(self.site.draft.source, SiteVersion.SOURCE_CODE)
        # Undo goes back
        self.client.post(reverse('studio:undo', args=[self.event.pk]))
        self.site.refresh_from_db()
        self.assertIn('See you there.', self.site.draft.files['index.html'])

    def test_never_inside_tags_or_scripts(self):
        page = GOOD.replace('<p>See you there.</p>', '<script>var t="Arts Fair";</script>')
        version_with(self.site, self.owner, {'index.html': page})
        response = self.client.post(reverse('studio:text_edit', args=[self.event.pk]),
                                    {'page': 'index.html', 'old': 'viewport', 'new': 'x'})
        self.assertEqual(response.status_code, 400)


class SaveRulesTests(StudioTestCase):
    def test_code_save_refuses_outside_forms_and_password_fields(self):
        for bad in ('<form action="https://evil.example/">', '<input type="password">', '<input name="card_number">',
                    '<script src="https://x.example/a.js"></script>'):
            with self.assertRaises(agent.ToolError):
                agent.check_files({'index.html': GOOD.replace('<p>See you there.</p>', bad)})
        agent.check_files({'index.html': GOOD.replace('<p>See you there.</p>',
                           '<form data-attnd-form="news" action="_attnd/forms/news"><input name="email"></form>')})


class PublishSafeguardTests(StudioTestCase):
    def post_publish(self, **data):
        return self.client.post(reverse('studio:publish', args=[self.event.pk]), data)

    def test_quality_errors_block_publishing(self):
        version_with(self.site, self.owner, {'index.html': GOOD.replace('<p>See you there.</p>',
                     '<script src="https://x.example/a.js"></script>')})
        self.post_publish()
        self.site.refresh_from_db()
        self.assertIsNone(self.site.published_id)

    def test_imported_site_needs_rights_once_and_history_logs_it(self):
        publishing.new_version(self.site, self.owner, files={'index.html': GOOD}, source=SiteVersion.SOURCE_IMPORT)
        self.post_publish()
        self.site.refresh_from_db()
        self.assertIsNone(self.site.published_id)
        self.post_publish(confirm_rights='1')
        self.site.refresh_from_db()
        self.assertIsNotNone(self.site.published_id)
        self.assertEqual(self.site.rights_confirmed_by, self.owner)
        entry = LogEntry.objects.filter(object_id=str(self.site.pk), content_type__model='site').first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.attnd.event_id, self.event.pk)

    def test_scan_hold_waits_for_staff(self):
        version_with(self.site, self.owner, {'index.html': GOOD})
        verdict = {'verdict': 'hold', 'reasons': ['Asks for bank details'], 'categories': ['phishing']}
        with mock.patch('attnd.claude.available', return_value=True), \
                mock.patch('attnd.claude.structured', return_value=(verdict, 0)):
            self.post_publish()
        self.site.refresh_from_db()
        self.assertIsNone(self.site.published_id)
        self.assertEqual(self.site.review_status, Site.REVIEW_HELD)
        staff = User.objects.create_user('staff', 'staff@example.com', 'pw', is_staff=True)
        from console.testing import TwoFactorClient
        client = TwoFactorClient()
        client.force_login(staff)
        page = client.get(reverse('studio:reports'))
        self.assertContains(page, 'Held for review')
        client.post(reverse('studio:reports'), {'site': self.site.pk, 'action': 'approve'})
        self.site.refresh_from_db()
        self.assertIsNotNone(self.site.published_id)
        self.assertEqual(self.site.review_status, '')

    def test_scan_ok_publishes_and_is_stored(self):
        version_with(self.site, self.owner, {'index.html': GOOD})
        with mock.patch('attnd.claude.available', return_value=True), \
                mock.patch('attnd.claude.structured', return_value=({'verdict': 'ok', 'reasons': [], 'categories': []}, 0)) as call:
            self.post_publish()
        self.site.refresh_from_db()
        self.assertIsNotNone(self.site.published_id)
        self.assertEqual(self.site.published.scan['verdict'], 'ok')
        self.assertEqual(call.call_count, 1)

    def test_two_reports_email_staff_once(self):
        self.publish()
        User.objects.create_user('staff2', 'staff2@example.com', 'pw', is_staff=True)
        mail.outbox.clear()
        for _ in range(3):
            SiteReport.objects.create(site=self.site, reason='scam')
            safety.report_filed(self.site)
        self.assertEqual(len([m for m in mail.outbox if 'reports about' in m.subject]), 1)


class AssetMetadataTests(StudioTestCase):
    def test_exif_is_stripped(self):
        from PIL import Image
        img = Image.new('RGB', (8, 8), (10, 200, 30))
        exif = Image.Exif()
        exif[0x010F] = 'CameraMaker'
        buf = io.BytesIO()
        img.save(buf, 'JPEG', exif=exif.tobytes())
        data = buf.getvalue()
        self.assertIn(b'CameraMaker', data)
        from studio.assets import create_asset
        asset = create_asset(self.event, data, 'photo.jpg', user=self.owner, describe=False)
        with asset.file.open('rb') as fh:
            self.assertNotIn(b'CameraMaker', fh.read())


class OverviewComponentTests(StudioTestCase):
    def test_check_and_rights_in_the_overview(self):
        publishing.new_version(self.site, self.owner, files={'index.html': PAGE}, source=SiteVersion.SOURCE_IMPORT)
        from console.components.catalog_website import website_overview
        data = website_overview(self.owner, event=self.event)
        self.assertTrue(data['check']['issues'])
        self.assertTrue(data['rights_text'])
        publish = [a for a in data['actions'] if a['name'] == 'publish_website'][0]
        self.assertEqual(publish['fields'].get('confirm_rights'), '1')
        response = self.client.get(reverse('studio:editor', args=[self.event.pk]))
        self.assertContains(response, 'Before you publish')
        self.assertContains(response, 'Confirm rights and publish')

    def test_drift_offers_update_the_site(self):
        ScheduledItem.objects.filter(event=self.event).update(name='Bandstand Finale')
        version_with(self.site, self.owner, {'index.html': GOOD.replace('See you there.', 'Bandstand Finale at 3:07 AM')})
        from console.components.catalog_website import website_overview
        data = website_overview(self.owner, event=self.event)
        self.assertTrue(data['check']['needs_update'])
        self.assertIn('update_website_from_check', [a['name'] for a in data['actions']])
