"""Lighthouse hygiene (Nov 22, 2026): every page has exactly one <main> landmark; private pages (console, sign-in)
carry no canonical and say noindex on every host, public pages keep their self-canonicals."""

import re
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import Event

MAIN = re.compile(r'<main[\s>]')


@override_settings(SITE_URL='https://attnd.app')
class LandmarkAndRobotsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('lh', 'lh@example.com', 'pw')
        start = timezone.now() + timedelta(days=2)
        self.event = Event.objects.create(name='LH Fest', event_code='LHFEST', user=self.user, start_time=start,
                                          end_time=start + timedelta(hours=5), timezone='America/Chicago')
        self.client = Client()
        self.client.force_login(self.user)

    def private_pages(self):
        e = self.event.pk
        return [reverse('console:dashboard') + '?page=1', reverse('console:assistant') + f'?event={e}',
                reverse('console:event', args=[e]) + '?page=1', reverse('console:locations', args=[e]),
                reverse('console:schedule', args=[e]), reverse('console:notifications', args=[e]),
                reverse('console:account'), reverse('studio:editor', args=[e])]

    def test_one_main_landmark_on_console_pages(self):
        for url in self.private_pages():
            response = self.client.get(url, secure=True)
            self.assertEqual(response.status_code, 200, url)
            body = response.content.decode()
            self.assertEqual(len(MAIN.findall(body)), 1, url)

    def test_private_pages_say_noindex_and_have_no_canonical(self):
        for url in self.private_pages():
            response = self.client.get(url, secure=True)
            body = response.content.decode()
            self.assertIn('<meta name="robots" content="noindex, nofollow">', body, url)
            self.assertNotIn('rel="canonical"', body, url)
            self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow', url)
        signed_out = Client()
        for url in (reverse('login'), reverse('console:signup'), reverse('password_reset')):
            response = signed_out.get(url, secure=True)
            body = response.content.decode()
            self.assertEqual(len(MAIN.findall(body)), 1, url)
            self.assertIn('name="robots" content="noindex', body, url)
            self.assertNotIn('rel="canonical"', body, url)
            self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow', url)

    def test_public_pages_keep_their_canonical_and_one_main(self):
        signed_out = Client()
        for url in ('/home/', '/privacy/'):
            response = signed_out.get(url, secure=True)
            body = response.content.decode()
            self.assertIn('rel="canonical"', body, url)
            self.assertNotIn('noindex', response.get('X-Robots-Tag', ''), url)
            self.assertEqual(len(MAIN.findall(body)), 1, url)
