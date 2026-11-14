"""Production security settings: evaluated with DEBUG off, the way Heroku runs."""
import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

BASE_DIR = Path(__file__).resolve().parent.parent


class ProductionSecuritySettingsTests(SimpleTestCase):
    def production_settings(self):
        env = {k: v for k, v in os.environ.items() if k not in ('DEBUG', 'CI')}
        env.update(SECRET_KEY='x' * 50, DJANGO_SETTINGS_MODULE='attnd.settings')
        code = ('import json; from django.conf import settings as s; print(json.dumps({k: getattr(s, k) for k in '
                '["DEBUG", "SECURE_SSL_REDIRECT", "SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE", '
                '"SECURE_HSTS_SECONDS", "SECURE_HSTS_INCLUDE_SUBDOMAINS", "SESSION_COOKIE_HTTPONLY"]}))')
        out = subprocess.run([sys.executable, '-c', code], cwd=BASE_DIR, env=env, capture_output=True, text=True, check=True)
        import json
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_https_and_secure_cookies_are_on_in_production(self):
        s = self.production_settings()
        self.assertFalse(s['DEBUG'])
        self.assertTrue(s['SECURE_SSL_REDIRECT'])
        self.assertTrue(s['SESSION_COOKIE_SECURE'])
        self.assertTrue(s['CSRF_COOKIE_SECURE'])
        self.assertTrue(s['SESSION_COOKIE_HTTPONLY'])
        self.assertGreaterEqual(s['SECURE_HSTS_SECONDS'], 31536000)
        self.assertTrue(s['SECURE_HSTS_INCLUDE_SUBDOMAINS'])
