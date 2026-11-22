"""Batch 7 Track B: width and phone usability.

Always: every component's root is a container (`component component--<name>`, container queries decide its layout in
the 26rem chat and on a wide canvas alike), the phone rules exist (16px fields, 44px targets), and Platform →
Components draws every component at 360 / 768 / 1280 px for staff only.

With a browser (ATTND_BROWSER_CHECKS=1, Chrome and the `playwright` npm package; not in CI): scripts/ci/phone_check.cjs
opens the console's pages at 360, 768 and 1280 px and fails on a horizontal page scroll, a component sticking out of
its gallery frame, a tap target under 44 × 44 px or a field under 16px at 360 px."""
import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from console import components as C
from console.testing import TwoFactorClient

ROOT = Path(settings.BASE_DIR)
CSS = (ROOT / 'console/static/console/console.css').read_text()


class ComponentWidthTests(TestCase):
    def test_every_component_root_is_a_container(self):
        missing = []
        for name, comp in C.registry().items():
            path = ROOT / 'console/templates' / comp.template
            text = re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', '', path.read_text(), flags=re.S)
            text = re.sub(r'\{%\s*partialdef \w+\s*%\}.*?\{%\s*endpartialdef\s*%\}', '', text, flags=re.S)  # parts
            first = re.search(r'<(div|section|article|form|li|ul|details|aside)\b[^>]*>', text)
            stem = Path(comp.template).stem  # a variant drawn by another component's template (event_theme_editor)
            if not first or not re.search(rf'class="[^"]*\bcomponent\b[^"]*\bcomponent--({name}|{stem})\b',
                                          first.group(0)):
                missing.append(name)
        self.assertEqual(missing, [])

    def test_phone_rules(self):
        block = CSS[CSS.index('/* Phones (batch 7 phone pass'):]
        self.assertIn('font-size: 16px', block)
        self.assertIn('min-height: 44px', block)
        self.assertIn('.component { container: component / inline-size; }', CSS)

    def test_gallery_is_staff_only_and_draws_three_widths(self):
        owner = User.objects.create_user('gallery', 'gallery@example.com', 'pw')
        call_command('load_demo_event', '--owner', 'gallery@example.com', stdout=open(os.devnull, 'w'))
        client = TwoFactorClient()
        client.force_login(owner)
        self.assertEqual(client.get(reverse('platform:components'), secure=True).status_code, 404)
        owner.is_staff = True
        owner.save()
        page = client.get(reverse('platform:components'), secure=True)
        self.assertEqual(page.status_code, 200)
        body = page.content.decode()
        for width in (360, 768, 1280):
            self.assertIn(f'data-gallery-frame="{width}"', body)
        drawn = set(re.findall(r'data-gallery-component="([a-z_]+)"', body))
        self.assertTrue({'event_header', 'location_row', 'schedule_item_row', 'event_overview'} <= drawn)


def _playwright():
    if os.environ.get('ATTND_BROWSER_CHECKS') != '1' or not shutil.which('node'):
        return None
    root = subprocess.run(['npm', 'root', '-g'], capture_output=True, text=True).stdout.strip()
    chrome = os.environ.get('CHROME_PATH', '/usr/bin/google-chrome')
    if not (Path(root) / 'playwright').exists() or not Path(chrome).exists():
        return None
    return root


@unittest.skipUnless(_playwright(), 'ATTND_BROWSER_CHECKS=1 with Chrome and the playwright npm package')
class PhoneBrowserTests(StaticLiveServerTestCase):
    def test_console_pages_at_three_widths(self):
        user = User.objects.create_user('phone', 'phone@example.com', 'pw', is_staff=True)
        call_command('load_demo_event', '--owner', 'phone@example.com', stdout=open(os.devnull, 'w'))
        client = TwoFactorClient()
        client.force_login(user)
        session = client.cookies[settings.SESSION_COOKIE_NAME].value
        from events.models import Event
        e = Event.objects.get(user=user).pk
        paths = ['/console/?page=1', f'/console/events/{e}/?page=1', f'/console/events/{e}/locations/',
                 f'/console/events/{e}/locations/new/', f'/console/events/{e}/schedule/',
                 f'/console/events/{e}/schedule/new/', f'/console/events/{e}/notifications/',
                 f'/console/events/{e}/team/', f'/console/events/{e}/edit/', f'/console/events/{e}/theme/',
                 f'/console/events/{e}/billing/', f'/console/events/{e}/history/', f'/console/events/{e}/usage/',
                 f'/console/assistant/?event={e}', '/console/account/', f'/console/platform/components/?event={e}',
                 '/console/platform/emails/', '/console/platform/site-styles/']
        result = subprocess.run(['node', str(ROOT / 'scripts/ci/phone_check.cjs'), self.live_server_url, session, *paths],
                                capture_output=True, text=True, timeout=600,
                                env={**os.environ, 'NODE_PATH': _playwright()})
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        problems = [f"{r['width']} {r['path']}: {r.get('error') or ''}{r.get('overflow') or ''}{r.get('small') or ''}"
                    f"{r.get('inputs') or ''}{r.get('frames') or ''}"
                    for r in json.loads(result.stdout)
                    if r.get('error') or r.get('overflow') or r.get('small') or r.get('inputs') or r.get('frames')]
        self.assertEqual(problems, [])
