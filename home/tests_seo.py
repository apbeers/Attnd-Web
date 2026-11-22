"""SEO (owner, Nov 21, 2026): dev never indexed, canonicals, the sitemap's events, the sites host's sitemap, llms.txt."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from events.models import Event


class SeoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('o', 'o@example.com', 'pw')
        now = timezone.now()
        kw = dict(user=self.user, timezone='UTC', start_time=now + timedelta(days=3), status=Event.STATUS_PUBLISHED)
        self.listed = Event.objects.create(name='Big Fair', event_code='BIGFAIR', public=True, paid=True, **kw)
        self.demo = Event.objects.create(name='Demo Fair', event_code='DEMOFAIR', public=True, paid=False, **kw)
        self.test = Event.objects.create(name='Test', event_code='TESTX', public=True, paid=True, testing_only=True, **kw)

    @override_settings(SITE_URL='https://dev.attnd.app')
    def test_dev_is_never_indexed(self):
        c = Client()
        robots = c.get('/robots.txt', secure=True)
        self.assertEqual(robots.content.decode(), 'User-agent: *\nDisallow: /\n')
        self.assertEqual(c.get('/privacy/', secure=True)['X-Robots-Tag'], 'noindex, nofollow')

    @override_settings(SITE_URL='https://attnd.app')
    def test_production_robots_and_canonicals(self):
        c = Client()
        self.assertNotIn('X-Robots-Tag', c.get('/privacy/', secure=True))
        self.assertContains(c.get('/privacy/', secure=True), '<link rel="canonical" href="https://attnd.app/privacy/">')
        page = c.get('/join/BIGFAIR/', secure=True, HTTP_USER_AGENT='Mozilla/5.0 (Macintosh)')
        self.assertContains(page, '<link rel="canonical" href="https://attnd.app/join/BIGFAIR/">')
        self.assertNotContains(page, 'name="robots"')
        demo = c.get('/join/DEMOFAIR/', secure=True, HTTP_USER_AGENT='Mozilla/5.0 (Macintosh)')
        self.assertContains(demo, '<meta name="robots" content="noindex">')

    @override_settings(SITE_URL='https://attnd.app')
    def test_public_pages_have_absolute_canonicals_and_descriptions(self):
        """Lighthouse SEO (Nov 22, 2026): /brand/ had a relative canonical and no meta description."""
        import re
        c = Client()
        for path in ('/home/', '/privacy/', '/brand/'):
            body = c.get(path, secure=True).content.decode()
            canonical = re.search(r'<link rel="canonical" href="([^"]+)"', body)
            self.assertTrue(canonical and canonical.group(1).startswith('https://attnd.app/'), path)
            self.assertRegex(body, r'<meta name="description" content="[^"]{20,}"', path)

    def test_sitemap_lists_only_findable_events(self):
        body = Client().get('/sitemap.xml', secure=True).content.decode()
        self.assertIn('/join/BIGFAIR/', body)
        self.assertNotIn('DEMOFAIR', body)
        self.assertNotIn('TESTX', body)
        self.assertIn('/privacy/', body)

    def test_llms_txt_and_json_ld(self):
        from pathlib import Path

        from django.conf import settings
        text = (Path(settings.BASE_DIR) / 'public' / 'llms.txt').read_text()
        self.assertIn('# Attnd', text)
        self.assertIn('https://attnd.app/privacy/', text)
        with override_settings(SOCIAL_PROFILES='https://www.linkedin.com/company/attnd'):
            from home.views import _structured_data
            data = _structured_data('https://attnd.app')
        self.assertIn('"sameAs": ["https://www.linkedin.com/company/attnd"]', data)
        self.assertIn('SoftwareApplication', data)


@override_settings(SITE_URL='https://attnd.app', ALLOWED_HOSTS=['attnd.app', 'www.attnd.app', 'testserver'])
class HostAndDownloadTests(TestCase):
    def test_www_redirects_but_not_the_well_known_files(self):
        c = Client()
        r = c.get('/privacy/?utm_source=x', secure=True, HTTP_HOST='www.attnd.app')
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r['Location'], 'https://attnd.app/privacy/?utm_source=x')
        self.assertEqual(c.get('/.well-known/apple-app-site-association', secure=True, HTTP_HOST='www.attnd.app').status_code, 200)
        self.assertEqual(c.get('/privacy/', secure=True, HTTP_HOST='attnd.app').status_code, 200)
        # http → https: SECURE_SSL_REDIRECT = not DEBUG (attnd/settings.py), HSTS a year.

    def test_download_links(self):
        from django.conf import settings
        c = Client()
        iphone = c.get('/download/', secure=True, HTTP_HOST='attnd.app', HTTP_USER_AGENT='Mozilla/5.0 (iPhone; CPU iPhone OS 26_0)')
        self.assertEqual((iphone.status_code, iphone['Location']), (302, settings.IOS_APP_STORE_URL))
        android = c.get('/app/', secure=True, HTTP_HOST='attnd.app', HTTP_USER_AGENT='Mozilla/5.0 (Linux; Android 16)')
        self.assertEqual(android['Location'], settings.ANDROID_PLAY_STORE_URL)
        desktop = c.get('/get/', secure=True, HTTP_HOST='attnd.app', HTTP_USER_AGENT='Mozilla/5.0 (Macintosh)')
        self.assertTrue(desktop['Location'].endswith('#download'))
