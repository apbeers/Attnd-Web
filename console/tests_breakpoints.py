"""One responsive codebase (Nov 22, 2026): layouts follow the screen's width, never the device.

Fails on
- a CSS width breakpoint in the console's stylesheets other than the three named ones (phone ≤ 600px, medium,
  wide ≥ 1024px; written at the top of console.css): `@media` can't read custom properties, so the four query texts
  below are the only ones allowed. Components use container queries (not checked here);
- a `matchMedia` width check in our JavaScript outside console/static/console/screen.js (`window.attndScreen`);
- a user-agent check outside the files that use it for an OS capability, not layout: Open in Attnd (console.js),
  the home page's app buttons (home.js), passkey names (passkeys.js, webauthn, views), the sign-in sessions' labels
  and new-device emails (user_sessions), the join / shared-plan redirects (join/views.py) and
  the retired API's update link (events/app_status.py)."""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)
CONSOLE_CSS_DIRS = ('console', 'agent', 'platform_admin', 'studio', 'diagnostics', 'credit', 'prospects')
ALLOWED_MEDIA = {'(max-width: 600px)', '(min-width: 601px)', '(max-width: 1023px)', '(min-width: 1024px)'}
SCREEN_JS = 'console/static/console/screen.js'
UA_ALLOWED = {
    'console/static/console/console.js',     # Open in Attnd: attnd:// on iPhone, intent:// on Android
    'home/static/home/home.js',              # the home page's App Store / Google Play buttons
    'console/static/console/passkeys.js',    # which passkey manager to name on Apple devices
    'console/passkeys.py', 'console/webauthn.py', 'console/views_auth.py', 'console/views.py',  # passkey names
    'console/user_sessions.py',              # "Safari on iPhone" in Account, new-device emails
    'join/views.py',                         # /join/ and /p/: App Store, Google Play or the landing page
    'events/app_status.py',                  # a retired API version: the App Store or Google Play update link
}
UA_RE = re.compile(r"navigator\.userAgent|navigator\.platform|HTTP_USER_AGENT|headers\.get\(['\"]User-Agent")
SKIP = ('.venv', 'node_modules', 'staticfiles', '/migrations/', 'site_static')


def _files(pattern):
    for path in ROOT.rglob(pattern):
        rel = path.relative_to(ROOT).as_posix()
        if any(s in '/' + rel for s in SKIP) or rel.startswith(('live_tests/', 'scripts/')) or '/tests' in '/' + rel:
            continue
        yield rel, path.read_text(errors='ignore')


class BreakpointTests(SimpleTestCase):
    def test_only_the_three_breakpoints_in_the_consoles_css(self):
        found = {}
        for rel, text in _files('*.css'):
            if not rel.startswith(CONSOLE_CSS_DIRS):
                continue
            for query in re.findall(r'@media\s+([^{]*)\{', text):
                for part in re.findall(r'\((?:min|max)-width:[^)]*\)', query):
                    if part not in ALLOWED_MEDIA:
                        found.setdefault(rel, []).append(part)
        self.assertEqual(found, {})

    def test_the_breakpoints_are_documented_once(self):
        head = (ROOT / 'console/static/console/console.css').read_text()[:2500]
        for name in ('phone', 'medium', 'wide'):
            self.assertIn(name, head)
        screen = (ROOT / SCREEN_JS).read_text()
        for query in ('(max-width: 600px)', '(min-width: 1024px)'):
            self.assertIn(query, screen)

    def test_width_checks_in_js_only_in_the_shared_helper(self):
        bad = [rel for rel, text in _files('*.js')
               if rel != SCREEN_JS and re.search(r'matchMedia\([^)]*width', text)]
        bad += [rel for rel, text in _files('*.js') if re.search(r'\binnerWidth\b|screen\.width', text)]
        self.assertEqual(bad, [])

    def test_no_user_agent_checks_for_layout(self):
        bad = []
        for pattern in ('*.py', '*.js', '*.html'):
            for rel, text in _files(pattern):
                if rel in UA_ALLOWED or rel == 'console/tests_breakpoints.py':
                    continue
                if UA_RE.search(text):
                    bad.append(rel)
        self.assertEqual(bad, [])
