"""Batch 7 Track D: Studio's site styles: the set, the pick per event, the first design's instruction and the
staff gallery (Platform → Site styles)."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from console.testing import TwoFactorClient
from events.models import Event, Location, ScheduledItem
from studio import styles
from studio.prompts import DATA_LIBRARY, SYSTEM_PROMPT


class StyleTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('o', 'o@example.com', 'pw')
        start = timezone.now() + timedelta(days=5)
        self.event = Event.objects.create(name='Prairie Fest', event_code='STYFEST', user=self.owner, start_time=start,
                                          end_time=start + timedelta(hours=8), timezone='America/Chicago')

    def test_the_set_is_complete(self):
        self.assertTrue(3 <= len(styles.STYLES) <= 5)
        for s in styles.STYLES:
            for key in ('key', 'name', 'best_for', 'pages', 'layout', 'type', 'colours', 'components', 'do', 'dont'):
                self.assertTrue(s[key], (s['key'], key))
            self.assertIn('index.html', s['pages'])
            for element in s['components']:  # only elements attnd.js really has
                self.assertIn(f'<{element}', DATA_LIBRARY, element)
            self.assertIn(s['name'], SYSTEM_PROMPT)

    def test_pick(self):
        self.assertEqual(styles.pick(self.event)['key'], 'single')  # small: one page
        for i in range(4):
            Location.objects.create(event=self.event, name=f'Stage {i}')
        for i in range(8):
            ScheduledItem.objects.create(event=self.event, name=f'Set {i}', start_time=self.event.start_time)
        self.assertEqual(styles.pick(self.event)['key'], 'poster')
        Location.objects.create(event=self.event, name='Peach stand', category='Farmers market vendors')
        self.assertEqual(styles.pick(self.event)['key'], 'market')
        self.event.name = 'Turkey Trot 5K'
        self.assertEqual(styles.pick(self.event)['key'], 'course')

    def test_first_design_names_the_style(self):
        from studio.views import starter_instruction
        text = starter_instruction(self.event)
        self.assertIn('SITE STYLE "One page"', text)

    def test_previews_are_static_and_use_the_event(self):
        for s in styles.STYLES:
            html = styles.preview_html(s, self.event)
            self.assertIn('Prairie Fest', html)
            self.assertNotIn('<script', html)
            self.assertNotIn('http', html.replace('initial-scale', ''))  # nothing loaded from anywhere
        self.assertIn('#431F86'.lower(), styles.preview_html(styles.BY_KEY['poster']).lower())

    def test_gallery_is_staff_only(self):
        client = TwoFactorClient()
        client.force_login(self.owner)
        url = reverse('platform:site_styles')
        self.assertEqual(client.get(url, secure=True).status_code, 404)
        staff = User.objects.create_user('s', 's@example.com', 'pw', is_staff=True)
        client.force_login(staff)
        page = client.get(url, secure=True)
        for s in styles.STYLES:
            self.assertContains(page, f'data-site-style="{s["key"]}"')
        self.assertContains(page, 'sandbox srcdoc=')
        self.assertContains(page, 'aria-current="page">Site styles<')
