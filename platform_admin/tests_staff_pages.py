"""The staff pages with lots of data (Lighthouse pass 3, Nov 22, 2026): long lists are paged (50 a page, filters kept),
every page stays inside a query and size budget with realistic volume (`seed_staff_volume`), the Customers list has
no per-person query, headings go down one level at a time and every table header has text, and the Site styles
previews render lazily."""

import re
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from console.testing import TwoFactorClient
from diagnostics.models import DiagnosticReport, DiagnosticSession
from events.models import Event
from platform_admin.models import EmailLog
from prospects.models import Prospect

# Lighthouse's DOM-size audit warns above ~800 elements and fails above ~1,400; a 50-row table of these pages is
# about 1,000 tags in all. Budgets are generous on purpose: they catch a list that stops paging or an N+1.
MAX_QUERIES = 30
MAX_KB = 160
ROWS = 50


def body_rows(html):
    tbody = re.search(r'<tbody>(.*?)</tbody>', html, re.S)
    return len(re.findall(r'<tr\b', tbody.group(1))) if tbody else 0


class StaffPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user('o', 'o@example.com', 'pw')
        cls.event = Event.objects.create(name='Fair', event_code='FAIR1', user=cls.owner, start_time=timezone.now())
        call_command('seed_staff_volume', entries=300, stdout=StringIO())
        cls.staff = User.objects.create_user('st', 'st@example.com', 'pw', is_staff=True)
        cls.su = User.objects.create_user('su', 'su@example.com', 'pw', is_staff=True, is_superuser=True)

    def setUp(self):
        self.client = TwoFactorClient()
        self.client.force_login(self.staff)

    def get(self, url, client=None):
        response = (client or self.client).get(url, secure=True)
        self.assertEqual(response.status_code, 200, url)
        return response.content.decode()

    def staff_pages(self):
        session = DiagnosticSession.objects.order_by('-entry_count').first()
        return ['/console/ai-spend/', '/console/diagnostics/', f'/console/diagnostics/s/{session.code}/',
                '/console/diagnostics/reports/', f'/console/diagnostics/reports/{DiagnosticReport.objects.first().pk}/',
                '/console/diagnostics/endpoints/', '/console/diagnostics/symbols/', '/console/prospects/',
                f'/console/prospects/{Prospect.objects.first().pk}/', '/console/prospects/runs/',
                '/console/prospects/settings/', '/console/prospects/claims/', '/console/studio/reports/',
                '/console/platform/emails/', '/console/platform/brand/', '/console/platform/site-styles/']

    def admin_pages(self):
        return ['/console/platform/?tab=overview', '/console/platform/?tab=customers', '/console/platform/?tab=events',
                '/console/platform/?tab=requests', f'/console/platform/events/{self.event.pk}/', '/console/usage/',
                '/console/platform/architecture/', '/console/platform/app-options/']

    def test_long_lists_are_paged_with_their_filters(self):
        self.assertGreater(EmailLog.objects.count(), 2 * ROWS)
        for url in ['/console/diagnostics/', '/console/diagnostics/reports/', '/console/platform/emails/',
                    '/console/prospects/']:
            first = self.get(url)
            self.assertLessEqual(body_rows(first), ROWS, url)
            self.assertIn('rel="next"', first, url)
            second = self.get(url + '?page=2')
            self.assertIn('rel="prev"', second, url)
            self.assertNotEqual(first, second)
        page = self.get('/console/platform/emails/?status=delivered&page=2')
        self.assertIn('status=delivered&amp;page=', page)
        self.assertEqual(self.get('/console/platform/emails/?page=999').count('rel="next"'), 0)  # last page

    def test_pages_stay_inside_query_and_size_budgets(self):
        admin = TwoFactorClient()
        admin.force_login(self.su)
        for client, urls in ((self.client, self.staff_pages()), (admin, self.admin_pages())):
            for url in urls:
                with CaptureQueriesContext(connection) as queries:
                    html = self.get(url, client)
                self.assertLessEqual(len(queries), MAX_QUERIES, url)
                self.assertLessEqual(len(html) // 1024, MAX_KB, url)

    def test_customers_cost_the_same_queries_for_more_people(self):
        admin = TwoFactorClient()
        admin.force_login(self.su)
        url = '/console/platform/?tab=customers'
        self.get(url, admin)
        with CaptureQueriesContext(connection) as before:
            self.get(url, admin)
        User.objects.bulk_create([User(username=f'extra{i}', email=f'extra{i}@example.com') for i in range(25)])
        with CaptureQueriesContext(connection) as after:
            self.get(url, admin)
        self.assertEqual(len(before), len(after))

    def test_headings_step_down_and_table_headers_have_text(self):
        admin = TwoFactorClient()
        admin.force_login(self.su)
        for client, urls in ((self.client, self.staff_pages()), (admin, self.admin_pages())):
            for url in urls:
                html = re.sub(r'<template\b.*?</template>', '', self.get(url, client), flags=re.S)
                self.assertNotRegex(html, r'<th\b[^>]*>\s*</th>', url)
                previous = 0
                for level in (int(m) for m in re.findall(r'<h([1-6])\b', html)):
                    self.assertLessEqual(level, previous + 1 if previous else level, f'{url}: h{previous} → h{level}')
                    previous = level

    def test_site_style_previews_render_lazily(self):
        html = self.get('/console/platform/site-styles/')
        self.assertEqual(html.count(' srcdoc="'), 1)  # the first phone preview; the rest when scrolled to
        self.assertGreater(html.count('data-srcdoc="'), 4)

    def test_seed_cleans_up_after_itself(self):
        call_command('seed_staff_volume', clean=True, stdout=StringIO())
        self.assertFalse(DiagnosticSession.objects.filter(model='lighthouse-seed').exists())
        self.assertFalse(EmailLog.objects.exists())
        self.assertFalse(Prospect.objects.exists())
        self.assertFalse(User.objects.filter(email__endswith='@lighthouse-seed.example').exists())
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())
