"""Round 3 (stage A): Add with AI on every form + fill_form, step labels without dumps, one answer, Team / Metrics
components with per-button confirm values, the notifications' Scheduled / Sent sections and editing a scheduled
one, the location form's helpers (address search, Fill from this website)."""

import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from agent.models import AgentRun, AgentStep
from agent.tests import AgentTestCase
from agent.views import step_label
from console import components as C
from events.models import EventInvite, EventMember, Notification


class AddWithAITests(AgentTestCase):
    def test_every_form_component_offers_add_with_ai(self):
        from agent.components import form_components
        forms = form_components()
        self.assertGreaterEqual(len(forms), 5)
        for comp in forms:
            ids = {k: self.event.pk for k in comp.ids if k == 'event_id'}
            data = C.load(self.owner, comp.name, ids)
            offers = [f['ai'] for f in C.forms_of(data) if f.get('ai')]
            self.assertTrue(offers, comp.name)
            self.assertTrue(offers[0]['prompt'])
            self.assertTrue(all(f['component'] == comp.name for f in C.forms_of(data)))
            html = C.render(comp.name, data, surface='chat')
            self.assertIn('data-add-with-ai', html, comp.name)
            self.assertIn(f'data-form-component="{comp.name}"', html)
        edit = C.load(self.owner, 'location_form', {'event_id': self.event.pk, 'location_id': self.stage.pk})
        self.assertEqual(edit['form']['ai']['label'], 'Edit with AI')

    def test_fill_form_records_the_values_and_saves_nothing(self):
        ids = {'event_id': self.event.pk, 'location_id': None, 'item_id': None, 'notification_id': None}
        run, provider = self.run_with([
            [('fill_form', {'component': 'location_form', 'ids': ids, 'submit': False, 'values': [
                {'name': 'name', 'value': 'Taco Row'}, {'name': 'snippet', 'value': 'Six taco trucks'},
                {'name': 'ctx_hours', 'value': '11 AM to 9 PM'}, {'name': 'nope', 'value': 'x'}]})],
            [('finish', {'summary': 'Filled it in.', 'suggest': []})],
        ])
        self.assertEqual(run.status, AgentRun.DONE, run.error)
        step = run.steps.get(tool='fill_form')
        self.assertEqual(step.kind, AgentStep.KIND_COMPONENT)
        self.assertEqual(step.arguments['fill'], {'name': 'Taco Row', 'snippet': 'Six taco trucks',
                                                  'ctx_hours': '11 AM to 9 PM'})
        self.assertFalse(self.event.location_set.filter(name='Taco Row').exists())  # nothing saved
        result = dict(provider.results)['fill_form']
        self.assertIn('Nothing is saved', result)
        self.assertIn('Skipped: nope', result)
        status = self.client.get(reverse('agent:run', args=[run.pk]), secure=True).json()
        shown = next(s for s in status['steps'] if s['tool'] == 'fill_form')
        self.assertEqual(shown['component']['fill']['name'], 'Taco Row')
        self.assertFalse(shown['component']['submit'])

    def test_fill_form_never_overwrites_assistant_details_already_there(self):
        self.stage.context = {'hours': 'Noon to 10'}
        self.stage.save()
        ids = {'event_id': self.event.pk, 'location_id': self.stage.pk, 'item_id': None, 'notification_id': None}
        run, provider = self.run_with([
            [('fill_form', {'component': 'location_form', 'ids': ids, 'submit': False, 'values': [
                {'name': 'ctx_hours', 'value': 'All day'}, {'name': 'description', 'value': 'Shorter.'}]})],
            [('finish', {'summary': 'Done.', 'suggest': []})],
        ])
        step = run.steps.get(tool='fill_form')
        self.assertEqual(step.arguments['fill'], {'description': 'Shorter.'})

    def test_the_form_on_screen_reaches_the_model(self):
        response = self.client.post(reverse('agent:start'), {
            'goal': 'Taco Row, six trucks', 'event_id': self.event.pk,
            'target': json.dumps({'component': 'location_form', 'ids': {'event_id': self.event.pk}})}, secure=True)
        self.assertEqual(response.status_code, 202)
        run = AgentRun.objects.get(pk=response.json()['run']['id'])
        self.assertEqual(run.target, {'component': 'location_form', 'ids': {'event_id': self.event.pk}})
        from agent.prompts import context_message
        self.assertIn('FORM ON SCREEN', context_message(run, '', '', [], []))


class ChatTextTests(AgentTestCase):
    def test_step_labels_are_one_short_line_without_data(self):
        step = AgentStep(kind=AgentStep.KIND_TOOL, text='✗ Edited a location: {"errors": {"name": ["Required"]}}\nmore')
        self.assertEqual(step_label(step), '✗ Edited a location')
        step.text = 'Read ' + 'Usage · Demo Festival ' * 10
        self.assertLessEqual(len(step_label(step)), 80)
        self.assertTrue(step_label(step).endswith('…'))
        note = AgentStep(kind=AgentStep.KIND_NOTE, text='A long answer\nwith lines')
        self.assertEqual(step_label(note), note.text)

    def test_text_next_to_finish_is_not_a_second_answer(self):
        run, _ = self.run_with([[('finish', {'summary': 'Go to Account.', 'suggest': []})]])
        self.assertFalse(run.steps.filter(kind=AgentStep.KIND_NOTE).exists())


class TeamMetricsTests(AgentTestCase):
    def setUp(self):
        super().setUp()
        self.helper = User.objects.create_user('helper', 'helper@example.com', 'pw')
        self.member = EventMember.objects.create(event=self.event, user=self.helper, role=EventMember.ROLE_CONTRIBUTOR)
        EventInvite.objects.create(event=self.event, email='new@example.com', role=EventMember.ROLE_CONTRIBUTOR)

    def test_team_rows_carry_their_own_member(self):
        data = C.load(self.owner, 'event_team', {'event_id': self.event.pk})
        remove = next(a for a in data['members'][0]['actions'] if a['name'] == 'remove_team_member')
        self.assertTrue(remove['confirm'])
        self.assertEqual(remove['params'], {'member': str(self.member.pk)})
        html = C.render('event_team', data, surface='chat')
        self.assertIn('data-component-fields=', html)
        self.assertIn('new@example.com', html)

    def test_a_chat_remove_button_confirms_exactly_that_member(self):
        url = reverse('agent:component_act')
        base = {'component': 'event_team', 'ids': json.dumps({'event_id': self.event.pk}),
                'action': 'remove_team_member'}
        bad = self.client.post(url, {**base, 'fields': json.dumps({'member': '999'})}, secure=True)
        self.assertEqual(bad.status_code, 409)
        ok = self.client.post(url, {**base, 'fields': json.dumps({'member': str(self.member.pk)})}, secure=True)
        self.assertEqual(ok.status_code, 200, ok.content)
        step = AgentStep.objects.get(pk=ok.json()['step']['id'])
        self.assertEqual(step.arguments['member'], self.member.pk)
        self.assertEqual(step.confirm_state, AgentStep.CONFIRM_PENDING)
        self.assertTrue(EventMember.objects.filter(pk=self.member.pk).exists())  # nothing ran yet

    def test_metrics_render(self):
        data = C.load(self.owner, 'event_metrics', {'event_id': self.event.pk})
        self.assertEqual([p['label'] for p in data['platforms']], ['iOS', 'Android'])
        self.assertIn('iOS and Android', C.render('event_metrics', data, surface='page'))


class NotificationSectionTests(AgentTestCase):
    def test_scheduled_first_then_sent_and_editing_a_scheduled_one(self):
        now = timezone.now()
        later = Notification.objects.create(event=self.event, title='Later', body='b',
                                            send_time=(now + timedelta(hours=5)).replace(minute=0, second=0, microsecond=0))
        soon = Notification.objects.create(event=self.event, title='Soon', body='b',
                                           send_time=(now + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0))
        old = Notification.objects.create(event=self.event, title='Old', body='b', send_time=now - timedelta(days=1),
                                          sent=True, sent_at=now - timedelta(days=1))
        found = C.instances(self.owner, 'notification_row', [{'event_id': self.event.pk}])
        self.assertEqual([d['title'] for _, d in found], ['Soon', 'Later', 'Old'])
        self.assertEqual([d['group'] for _, d in found], ['scheduled', 'scheduled', 'sent'])
        self.assertTrue(found[0][1]['relative'].startswith('in '))
        page = self.client.get(reverse('console:notifications', args=[self.event.pk]), secure=True)
        self.assertContains(page, 'Scheduled · 2')
        self.assertContains(page, 'Sent · 1')
        url = reverse('console:notification_action', args=[self.event.pk, soon.pk])
        self.client.post(url, {'action': 'edit', 'title': 'Soon (moved)', 'body': '', 'send_time': ''}, secure=True)
        soon.refresh_from_db()
        self.assertEqual((soon.title, soon.body), ('Soon (moved)', 'b'))  # empty = keep
        self.client.post(reverse('console:notification_action', args=[self.event.pk, old.pk]),
                         {'action': 'edit', 'title': 'Changed'}, secure=True)
        old.refresh_from_db()
        self.assertEqual(old.title, 'Old')  # sent ones can't change


class LocationHelperTests(AgentTestCase):
    def test_place_search_uses_nominatim_without_a_server_key(self):
        class Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return [{'name': 'Taco Row', 'display_name': 'Taco Row, Austin, TX', 'lat': '30.1', 'lon': '-97.7'}]
        with self.settings(GOOGLE_PLACES_API_KEY=''), mock.patch('httpx.get', return_value=Resp()) as get:
            response = self.client.get(reverse('console:place_search', args=[self.event.pk]), {'q': 'Taco Row'},
                                       secure=True)
        self.assertEqual(response.json()['results'][0]['lat'], 30.1)
        self.assertIn('nominatim', get.call_args[0][0])

    def test_fill_from_website_fills_suggestions_only(self):
        html = (b'<html><head><title>Taco Row</title><meta property="og:image" content="https://x.example/p.jpg">'
                b'</head><body>Six trucks. Open 11-9.</body></html>')
        answer = {'name': 'Taco Row', 'snippet': 'Six taco trucks', 'description': 'Tacos.', 'category': 'Food',
                  'tags': ['live music', 'invented'], 'address': '', 'latitude': None, 'longitude': None,
                  'image_url': 'https://x.example/p.jpg', 'context': {'about': '', 'menu': [], 'hours': '11 AM - 9 PM',
                                                                     'accessibility': '', 'age_limit': '', 'links': []}}
        from decimal import Decimal
        with mock.patch('studio.importer.fetch', return_value=(html, 'text/html', 'https://x.example/')), \
                mock.patch('attnd.claude.available', return_value=True), \
                mock.patch('attnd.claude.structured', return_value=(answer, Decimal('0.001'))):
            response = self.client.post(reverse('console:location_fill', args=[self.event.pk]),
                                        {'url': 'https://x.example/'}, secure=True)
        fields = response.json()['fields']
        self.assertEqual(fields['name'], 'Taco Row')
        self.assertEqual(fields['tags_text'], 'live music')  # only the event's own tags
        self.assertEqual(fields['image_url'], 'https://x.example/p.jpg')
        self.assertEqual(fields['ctx_hours'], '11 AM - 9 PM')
        self.assertEqual(self.event.location_set.count(), 2)  # nothing saved
