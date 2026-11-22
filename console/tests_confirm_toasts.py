"""Batch 7 Track B: one in-app confirm (no system dialogs) and floating toasts."""
import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase


class NoSystemDialogsTests(TestCase):
    def test_no_window_confirm_alert_or_prompt(self):
        root = Path(settings.BASE_DIR)
        bad = re.compile(r'(?<![\w.])(?:window\.)?(confirm|alert|prompt)\s*\(')
        for path in list(root.glob('*/static/**/*.js')) + list(root.glob('*/templates/**/*.html')):
            if '.venv' in path.parts or 'site_static' in path.parts:
                continue
            for line in path.read_text().splitlines():
                code = line.split('//', 1)[0]
                self.assertIsNone(bad.search(code.replace('attndConfirm(', '')), f'{path}: {line.strip()[:120]}')

    def test_console_js_has_the_confirm_component(self):
        js = (Path(settings.BASE_DIR) / 'console/static/console/console.js').read_text()
        self.assertIn('window.attndConfirm = function', js)
        self.assertIn('window.attndToast = function', js)


class ToastTests(TestCase):
    def test_a_message_is_a_toast(self):
        """A real view's success message (Account: save the display name … here: switching Chat as default off)."""
        user = User.objects.create_user('t', 't@example.com', 'pw')
        self.client.force_login(user)
        from django.urls import reverse
        response = self.client.post(reverse('console:undo_last'), secure=True, follow=True)
        body = response.content.decode()
        self.assertIn('data-toasts', body)
        self.assertRegex(body, r'class="notice toast notice--')
