"""One Platform entry (owner, Nov 22, 2026): organizers and staff see the same top bar and canvas tabs except
Platform; every admin tool is a Platform sub-tab gated by its own permission; old URLs keep working."""
import re

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from console.testing import TwoFactorClient
from platform_admin import areas


def topbar(html):
    block = html.split('class="topbar__actions"', 1)[1].split('</header>', 1)[0]
    texts = [re.sub(r'<[^>]+>', '', m).strip() for m in re.findall(r'<a [^>]*>(.*?)</a>', block, re.S)]
    return [t for t in texts if '@' not in t]  # the account link shows the person's own email


@override_settings(CSP_MODE='off')
class PlatformNavTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organizer = User.objects.create_user('org', 'org@example.com', 'pw')
        cls.staff = User.objects.create_user('staff', 'staff@example.com', 'pw', is_staff=True)
        cls.root = User.objects.create_superuser('root', 'root@example.com', 'pw')

    def client_for(self, user):
        c = TwoFactorClient()
        c.force_login(user)
        return c

    def page(self, user):
        return self.client_for(user).get(reverse('console:account'), secure=True).content.decode()

    def test_top_bar_differs_only_by_platform(self):
        org, root, staff = topbar(self.page(self.organizer)), topbar(self.page(self.root)), topbar(self.page(self.staff))
        self.assertNotIn('Platform', org)
        self.assertEqual([x for x in root if x != 'Platform'], org)
        self.assertEqual([x for x in staff if x != 'Platform'], org)
        self.assertEqual(root.count('Platform'), 1)
        self.assertEqual(staff.count('Platform'), 1)
        for gone in ('Diagnostics', 'Prospects', 'AI spend', 'Studio reports', 'App usage', 'Admin',
                     'Preview onboarding'):
            self.assertNotIn(gone, root)

    def test_canvas_tabs_differ_only_by_platform(self):
        from agent import canvas
        factory = RequestFactory()

        def labels(user):
            request = factory.get('/console/assistant/')
            request.user = user
            return [t['label'] for t in canvas.build(request)]
        org = labels(self.organizer)
        self.assertNotIn('Platform', org)
        for user in (self.staff, self.root):
            got = labels(user)
            self.assertEqual([x for x in got if x != 'Platform'], org)
            self.assertIn('Platform', got)
            for gone in ('Prospects', 'AI spend'):
                self.assertNotIn(gone, got)

    def test_sub_tabs_follow_permissions(self):
        staff = {a['key'] for a in areas.visible(self.staff)}
        root = {a['key'] for a in areas.visible(self.root)}
        self.assertEqual(areas.visible(self.organizer), [])
        self.assertTrue({'diagnostics', 'prospects', 'claims', 'studio_reports', 'ai'} <= staff)
        self.assertFalse({'health', 'customers', 'requests', 'pricing', 'overrides', 'app_usage'} & staff)
        self.assertEqual(root, {a[0] for a in areas.AREAS})

    def test_platform_home_per_role(self):
        self.assertEqual(self.client_for(self.organizer).get(reverse('platform:home'), secure=True).status_code, 404)
        r = self.client_for(self.staff).get(reverse('platform:home'), secure=True)
        self.assertEqual(r.status_code, 302)  # Health is for platform admins: staff land on their first area
        self.assertEqual(r['Location'], areas.first_url(self.staff))
        self.assertEqual(self.client_for(self.staff).get(reverse('platform:home') + '?tab=ai', secure=True).status_code,
                         200)
        self.assertEqual(self.client_for(self.root).get(reverse('platform:home'), secure=True).status_code, 200)

    def test_every_area_page_draws_the_platform_nav_and_old_urls_work(self):
        client = self.client_for(self.root)
        for key, label, name, query, perm in areas.AREAS:
            r = client.get(reverse(name) + query, secure=True)
            self.assertEqual(r.status_code, 200, key)
            self.assertContains(r, 'aria-label="Platform"', msg_prefix=key)
        self.assertEqual(client.get('/console/ai-spend/', secure=True).status_code, 200)
        self.assertContains(client.get(reverse('platform:home'), secure=True), 'Preview onboarding')
        self.assertContains(client.get(reverse('platform:home'), secure=True), 'Django admin')
