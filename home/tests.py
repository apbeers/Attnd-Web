from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch


class HomePageTests(TestCase):
    @patch('django.contrib.staticfiles.storage.staticfiles_storage.url', return_value='/static/home/img/demo-map.webp')
    def test_home_page_loads(self, mock_static_url):
        response = self.client.get(reverse('home:homepage'), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'home/home.html')

    def test_footer_shows_the_heroku_release_only_when_known(self):
        import os
        with patch.dict(os.environ, {'HEROKU_RELEASE_VERSION': 'v321'}):
            self.assertContains(self.client.get('/', secure=True), 'release v321')
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('HEROKU_RELEASE_VERSION', None)
            self.assertNotContains(self.client.get('/', secure=True), 'release v')


class PrivacyPageTests(TestCase):
    @patch('django.contrib.staticfiles.storage.staticfiles_storage.url', return_value='/static/home/img/demo-map.webp')
    def test_privacy_page_is_available(self, mock_static_url):
        response = self.client.get(reverse('home:privacy'), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'home/privacy.html')
        self.assertContains(response, 'A random app ID.')
        self.assertContains(response, 'We don\'t sell or share personal information.')


class StoreLinksTests(TestCase):
    def test_home_links_to_the_real_store_pages(self):
        from django.conf import settings
        html = self.client.get('/', secure=True).content.decode()
        self.assertIn(settings.IOS_APP_STORE_URL, html)
        self.assertIn('https://play.google.com/store/apps/details?id=com.arcu.attnd', html)
        self.assertNotIn('bit.ly', html)
        self.assertNotIn('apple.co/', html)


class SiteIconTests(TestCase):
    """The icon set from scripts/make_icons.py: full resolution, linked from the pages."""

    def test_icon_files_have_every_size(self):
        from pathlib import Path
        from PIL import Image
        public = Path(__file__).resolve().parent.parent / 'public'
        ico = Image.open(public / 'favicon.ico')
        self.assertEqual({16, 32, 48, 64, 128, 256}, {w for w, _ in ico.info['sizes']})
        for name, size in [('icon-32.png', 32), ('icon-96.png', 96), ('icon-192.png', 192), ('icon-512.png', 512),
                           ('icon-maskable-512.png', 512), ('apple-touch-icon.png', 180)]:
            self.assertEqual((size, size), Image.open(public / name).size, name)
        # Apple touch icons must be opaque (iOS fills transparency with black).
        self.assertEqual('RGB', Image.open(public / 'apple-touch-icon.png').mode)

    def test_pages_link_the_icons(self):
        for url in (reverse('home:homepage'), reverse('home:privacy'), '/accounts/login/'):
            response = self.client.get(url, follow=True)
            self.assertContains(response, '<link rel="icon" href="/favicon.ico" sizes="any">')
            self.assertContains(response, '<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">')
            self.assertContains(response, '<link rel="manifest" href="/site.webmanifest">')

    def test_webmanifest_content_type(self):
        from django.conf import settings
        self.assertEqual('application/manifest+json', settings.WHITENOISE_MIMETYPES['.webmanifest'])


class RootFileCacheTests(TestCase):
    def test_icons_cache_for_a_day_and_robots_for_an_hour(self):
        icon = self.client.get('/favicon.ico')
        self.assertIn('max-age=86400', icon['Cache-Control'])
        self.assertIn('max-age=3600', self.client.get('/robots.txt')['Cache-Control'])
