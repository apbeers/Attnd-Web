"""docs/architecture.toml stays true to the code (batch 7 Track E): every Django app, outside host the code names,
settings env var and push type is in it, and its edges and flows point at real nodes."""
from django.test import TestCase

from platform_admin import architecture


class ArchitectureDriftTests(TestCase):
    def test_nothing_is_missing(self):
        missing = architecture.drift()
        self.assertEqual(missing, [], 'Add these to docs/architecture.toml: ' + '; '.join(
            f'{kind} {name} ({where})' for kind, name, where in missing))

    def test_drift_is_detected(self):
        data = architecture.load()
        data['node'] = [n for n in data['node'] if n['id'] != 'studio']
        kinds = {kind for kind, _name, _where in architecture.drift(data)}
        self.assertIn('app', kinds)
        self.assertIn('env', kinds)
        self.assertIn('flow', kinds)  # studio_publish names studio

    def test_installed_packages_inside_the_project_are_ignored(self):
        """Heroku installs Python under /app/.heroku/python: Django contrib, cloudinary and the stdlib's docstrings
        (python.org, rfc-editor.org…) once showed up as 259 'missing' things on the Architecture page."""
        import tempfile
        from pathlib import Path
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkgs = root / '.heroku' / 'python' / 'lib' / 'python3.15' / 'site-packages' / 'django' / 'contrib' / 'auth'
            pkgs.mkdir(parents=True)
            (pkgs / 'hashers.py').write_text('# see https://docs.python.org/3/library/hashlib.html\n')
            (root / 'myapp').mkdir()
            (root / 'myapp' / 'views.py').write_text("URL = 'https://api.example.com/v1/'\n")
            cfg = mock.Mock(label='auth', path=str(pkgs))
            ours = mock.Mock(label='myapp', path=str(root / 'myapp'))
            with mock.patch.object(architecture, 'ROOT', root), \
                    mock.patch.object(architecture.django_apps, 'get_app_configs', return_value=[cfg, ours]):
                architecture._called_hosts.cache_clear()
                try:
                    self.assertEqual(architecture.local_apps(), ['myapp'])
                    self.assertEqual(list(architecture.called_hosts()), ['api.example.com'])
                finally:
                    architecture._called_hosts.cache_clear()

    def test_generators(self):
        self.assertIn('events', architecture.local_apps())
        self.assertEqual(architecture.push_types(), ['content_updated', 'notification'])
        self.assertTrue(any('bundle' in p for p in architecture.api_endpoints()))
        names = {m['name'] for m in architecture.models()}
        self.assertTrue({'Event', 'Location', 'Invoice'} <= names)
        d = architecture.diagram()
        self.assertEqual(len(d['nodes']), len(architecture.load()['node']))
        self.assertIn('## Flows', architecture.markdown())


class PlatformTabsTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        from console.testing import TwoFactorClient
        self.root = User.objects.create_superuser('root', 'root@example.com', 'pw')
        self.staff = User.objects.create_user('staff', 'staff@example.com', 'pw', is_staff=True)
        self.c = TwoFactorClient()

    def get(self, user, name, query=''):
        from django.urls import reverse
        self.c.force_login(user)
        return self.c.get(reverse(name) + query, secure=True)

    def test_architecture_superusers_only(self):
        page = self.get(self.root, 'platform:architecture')
        self.assertContains(page, 'data-arch-svg')
        self.assertContains(page, 'Anthropic (Claude)')
        self.assertContains(self.get(self.root, 'platform:architecture', '?tab=flows'), 'QR → App Clip')
        self.assertContains(self.get(self.root, 'platform:architecture', '?tab=data'), 'events.Event')
        md = self.get(self.root, 'platform:architecture', '?format=md')
        self.assertIn(b'# Attnd architecture snapshot', md.content)
        self.assertEqual(self.get(self.staff, 'platform:architecture').status_code, 404)
        self.assertEqual(self.get(self.staff, 'platform:app_options').status_code, 404)

    def test_app_options_and_brand(self):
        self.assertContains(self.get(self.root, 'platform:app_options'), 'catalogue')
        brand = self.get(self.staff, 'platform:brand')
        self.assertContains(brand, 'Brand purple')
        z = self.get(self.staff, 'platform:brand', '?download=zip')
        self.assertEqual(z['Content-Type'], 'application/zip')
        from django.test import Client
        public = Client().get('/brand/', secure=True)
        self.assertContains(public, '#431F86')
