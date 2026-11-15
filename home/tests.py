from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch


class HomePageTests(TestCase):
    @patch('django.contrib.staticfiles.storage.staticfiles_storage.url', return_value='/static/home/img/favicon.png')
    def test_home_page_loads(self, mock_static_url):
        response = self.client.get(reverse('home:homepage'), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'home/home.html')


class PrivacyPageTests(TestCase):
    @patch('django.contrib.staticfiles.storage.staticfiles_storage.url', return_value='/static/home/img/favicon.png')
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
