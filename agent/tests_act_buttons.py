"""Regression (batch 5, Nov 21, 2026): every always-ask button a component offers can be acted on through
components/act in the same state (Overview's "Show in nearby events" answered "That button isn't available here"),
including buttons of a composite's parts; buttons that can't be used are drawn disabled with their reason."""
import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from agent.models import AgentRun
from console import components as C
from console.testing import TwoFactorClient
from events.models import Event, Location, Notification, ScheduledItem


def _confirm_buttons(data, name):
    """(component, ids, action, params) for every enabled always-ask button, the composite's parts included."""
    out = []
    for a in data.get('actions', []):
        if a.get('confirm') and not a.get('disabled'):
            out.append((name, a['name'], a.get('params') or {}))
    for part in data.get('parts') or []:
        out += [(name, n, p) for _, n, p in _confirm_buttons(part, part.get('component', ''))]
    return out


class EveryOfferedButtonActs(TestCase):
    def make(self, paid):
        now = timezone.now()
        event = Event.objects.create(name='Fest', event_code='FEST' + ('P' if paid else 'U'), user=self.owner,
                                     timezone='UTC', status=Event.STATUS_PUBLISHED, paid=paid,
                                     start_time=now + timedelta(days=1), end_time=now + timedelta(days=1, hours=6))
        stage = Location.objects.create(event=event, name='Stage', latitude=32.7, longitude=-97.3)
        ScheduledItem.objects.create(event=event, name='Set', location=stage, start_time=now + timedelta(days=1))
        Notification.objects.create(event=event, title='Hi', body='Welcome', send_time=now + timedelta(hours=2))
        return event

    def setUp(self):
        self.owner = User.objects.create_user('owner', 'owner@example.com', 'pw')

    def check(self, client, user, event):
        tried = 0
        for name in C.registry():
            comp = C.get(name)
            if set(comp.ids) != {'event_id'} or not C.allowed(comp, user):
                continue
            ids = {'event_id': event.pk}
            try:
                data = C.load(user, name, ids)
            except Exception:
                continue
            for component, action, params in _confirm_buttons(data, name):
                AgentRun.objects.all().delete()
                resp = client.post(reverse('agent:component_act'), {
                    'run': '', 'component': component, 'action': action, 'ids': json.dumps(ids),
                    'fields': json.dumps(params) if params else ''}, secure=True)
                self.assertNotEqual(resp.status_code, 409, f'{component} offers {action} but acting on it fails: '
                                                           f'{resp.content[:200]}')
                self.assertIn(resp.status_code, (200,), f'{component}/{action}: {resp.content[:200]}')
                tried += 1
        return tried

    def test_unpaid_event_owner(self):
        event = self.make(paid=False)
        self.client.force_login(self.owner)
        self.assertGreater(self.check(self.client, self.owner, event), 3)
        # Overview offers nearby listing disabled, with its reason, never as a live button.
        data = C.load(self.owner, 'event_header', {'event_id': event.pk})
        nearby = [a for a in data['actions'] if a['name'] == 'list_event_nearby']
        self.assertTrue(nearby and nearby[0]['disabled'])
        html = self.client.get(reverse('agent:component_render') + '?component=event_overview&items='
                               + json.dumps([{'event_id': event.pk}]), secure=True).json()['items'][0]['html']
        self.assertIn('Paid events only', html)
        self.assertIn('Remove the limit', html)
        self.assertNotIn('data-component-act="list_event_nearby"', html)

    def test_paid_event_nearby_goes_through_the_confirm_card_from_the_overview(self):
        event = self.make(paid=True)
        self.client.force_login(self.owner)
        self.assertGreater(self.check(self.client, self.owner, event), 3)
        AgentRun.objects.all().delete()
        resp = self.client.post(reverse('agent:component_act'), {
            'run': '', 'component': 'event_overview', 'action': 'list_event_nearby',
            'ids': json.dumps({'event_id': event.pk})}, secure=True)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertIn('nearby', resp.json()['run']['pending']['summary'].lower())

    def test_staff(self):
        staff = User.objects.create_user('staff', 'staff@example.com', 'pw', is_staff=True)
        event = self.make(paid=False)
        event.user = staff
        event.save()
        client = TwoFactorClient()
        client.force_login(staff)
        self.assertGreater(self.check(client, staff, event), 3)
