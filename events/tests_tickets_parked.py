"""Attnd Tickets parked (TICKETS_ENABLED off, the default): included in nothing, API keys kept and null (API-017)."""
import sys
import unittest
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from events.models import Event

AUTH = {'HTTP_AUTHORIZATION': 'Bearer test-key'}


@unittest.skipIf(settings.TICKETS_ENABLED, 'ticketing is on')
@override_settings(API_KEY='test-key')
class TicketsParkedTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('owner', 'owner@example.com', 'pw')
        start = timezone.now() + timedelta(days=2)
        self.event = Event.objects.create(name='Fair', event_code='FAIR1', start_time=start, user=self.owner,
                                          timezone='America/Chicago', plan=Event.PLAN_STUDIO)

    def test_not_installed_and_never_imported(self):
        from django.apps import apps
        self.assertFalse(apps.is_installed('tickets'))
        self.assertNotIn('tickets.models', sys.modules)

    def test_no_ticket_urls(self):
        client = Client()
        for path in ('/t/FAIR1/', '/tickets/lookup/', '/wallet/v1/log', f'/console/events/{self.event.id}/tickets/',
                     '/console/tickets/demo/'):
            self.assertEqual(client.get(path, secure=True).status_code, 404, path)
        for path in ('/api/v2/tickets/mine/', '/api/v2/door/events/'):
            resp = client.get(path, secure=True, **AUTH)
            self.assertEqual((resp.status_code, resp.json()), (404, {'error': 'Not found'}), path)

    def test_bundle_keeps_the_keys_as_null(self):
        bundle = Client().get(f'/api/v2/events/{self.event.id}/bundle/', secure=True, **AUTH).json()
        self.assertIn('tickets', bundle)
        self.assertIsNone(bundle['tickets'])
        self.assertIn('tickets_url', bundle['event'])
        self.assertIsNone(bundle['event']['tickets_url'])

    def test_studio_data_and_prompt_have_no_tickets(self):
        from studio import prompts, publishing, sitedata
        self.event.status = Event.STATUS_PUBLISHED
        self.event.save()
        site = publishing.ensure_site(self.event, self.owner)
        self.assertIsNone(sitedata.site_data(site, 'https://sites.example/fair/')['tickets'])
        self.assertNotIn('attnd-tickets', prompts.DATA_LIBRARY)

    def test_scheduled_tasks_skip_tickets(self):
        from io import StringIO
        out = StringIO()
        call_command('run_scheduled_tasks', stdout=out)
        self.assertNotIn('tickets:', out.getvalue())
        self.assertNotIn('tickets.tasks', sys.modules)
