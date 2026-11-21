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
            if comp.audience:
                continue
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


class FillFormConfirmGateTests(AgentTestCase):
    """The audit's confirm-gate fix: fill_form never submits a form that always asks."""

    def test_submit_is_refused_on_forms_that_always_ask(self):
        ids = {'event_id': self.event.pk}
        run, provider = self.run_with([
            [('fill_form', {'component': 'notification_form', 'ids': ids, 'submit': True, 'values': [
                {'name': 'title', 'value': 'Gates open'}, {'name': 'body', 'value': 'Now'}]})],
            [('finish', {'summary': 'Filled.', 'suggest': []})],
        ])
        step = run.steps.get(tool='fill_form')
        self.assertFalse(step.arguments['submit'])
        self.assertIn('NOT submitted', dict(provider.results)['fill_form'])
        self.assertFalse(self.event.notification_set.exists())

    def test_ordinary_forms_may_still_be_saved_on_request(self):
        ids = {'event_id': self.event.pk, 'location_id': self.stage.pk}
        run, provider = self.run_with([
            [('fill_form', {'component': 'location_form', 'ids': ids, 'submit': True,
                            'values': [{'name': 'snippet', 'value': 'Big'}]})],
            [('finish', {'summary': 'Saved.', 'suggest': []})],
        ])
        self.assertTrue(run.steps.get(tool='fill_form').arguments['submit'])

    def test_the_panel_fills_only_its_own_cards_and_never_submits_gated_forms(self):
        from pathlib import Path
        js = (Path(__file__).parent / 'static/agent/agent.js').read_text()
        self.assertIn("const inCard = (f) => !!(f && f.closest('.agent__card')", js)
        self.assertIn(".filter((f) => f.dataset.formIds === want && inCard(f))", js)
        self.assertIn('if (submit && inCard(form) && !form.dataset.confirmAction)', js)
class ProspectComponentTests(AgentTestCase):
    def setUp(self):
        super().setUp()
        from prospects.models import Prospect, ProspectContact, ProspectRun
        self.staff = User.objects.create_user('staffer', 'staff@example.com', 'pw', is_staff=True)
        self.prospect = Prospect.objects.create(name='River Days', city='Austin', fit_score=80,
                                                urls=['https://river.example/'])
        self.contact = ProspectContact.objects.create(prospect=self.prospect, name='Ana', email='ana@example.org',
                                                      source_url='https://river.example/contact')
        self.dnc = ProspectContact.objects.create(prospect=self.prospect, name='Bo', email='bo@example.org',
                                                  source_url='https://river.example/team', do_not_contact=True)
        self.run_row = ProspectRun.objects.create(status=ProspectRun.DONE, found=3, new=1)

    def test_staff_get_every_prospect_component(self):
        rows = C.instances(self.staff, 'prospect_row', [{}])
        self.assertEqual(rows[0][1]['name'], 'River Days')
        detail = C.load(self.staff, 'prospect_detail', {'prospect_id': self.prospect.pk})
        html = C.render('prospect_detail', detail, surface='chat')
        self.assertIn('ana@example.org', html)
        self.assertNotIn('bo@example.org', html)  # do not contact: never shown
        self.assertIn('https://river.example/team', html)  # every contact keeps its source
        create = next(a for a in detail['actions'] if a['name'] == 'create_test_event')
        self.assertEqual(create['url'], reverse('prospects:detail', args=[self.prospect.pk]))
        self.assertTrue(C.instances(self.staff, 'prospect_run_row', [{}]))
        self.assertIn('form', C.load(self.staff, 'prospect_settings_form', {}))

    def test_organizers_cannot_see_or_call_any_of_it(self):
        from django.http import Http404

        from agent.components import tool_definitions
        for name in ('prospect_row', 'prospect_detail', 'prospect_run_row', 'prospect_settings_form'):
            with self.assertRaises(Http404):
                C.instances(self.owner, name, [{'prospect_id': self.prospect.pk}] if name == 'prospect_detail' else [{}])
            response = self.client.get(reverse('agent:component_render'),
                                       {'component': name, 'items': json.dumps([{'prospect_id': self.prospect.pk}])},
                                       secure=True)
            self.assertIn(response.status_code, (400, 404))
            self.assertNotIn('River', response.content.decode())
        show = next(t for t in tool_definitions(self.owner) if t['name'] == 'show')
        self.assertFalse([n for n in show['parameters']['properties']['component']['enum'] if n.startswith('prospect')])
        show_staff = next(t for t in tool_definitions(self.staff) if t['name'] == 'show')
        self.assertIn('prospect_row', show_staff['parameters']['properties']['component']['enum'])
        response = self.client.post(reverse('agent:component_act'), {
            'component': 'prospect_detail', 'ids': json.dumps({'prospect_id': self.prospect.pk}),
            'action': 'create_test_event', 'fields': '{}'}, secure=True)
        self.assertIn(response.status_code, (400, 404))
        from prospects.models import Prospect
        self.assertIsNone(Prospect.objects.get(pk=self.prospect.pk).event_id)


class ChatDefaultTests(AgentTestCase):
    def test_chat_as_default_view(self):
        dashboard = reverse('console:dashboard')
        overview = reverse('console:event', args=[self.event.pk])
        self.assertEqual(self.client.get(dashboard, secure=True).status_code, 200)
        self.client.post(reverse('console:chat_default'), {'on': '1', 'next': dashboard}, secure=True)
        response = self.client.get(dashboard, secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith(reverse('console:assistant')))
        response = self.client.get(overview, secure=True)
        self.assertIn(f'event={self.event.pk}', response['Location'])
        self.assertEqual(self.client.get(overview + '?page=1', secure=True).status_code, 200)  # Exit lands on the page
        self.assertEqual(self.client.get(dashboard, secure=True, HTTP_X_ATTND_AGENT='1/1').status_code, 302)  # a header
        from agent import internal  # only Ask Attnd's own internal request skips the landing
        run = AgentRun.objects.create(user=self.owner, goal='x', session_key=self.client.session.session_key,
                                      host='testserver', secure=True)
        self.assertEqual(internal.request(run, 'GET', dashboard).status, 200)
        page = self.client.get(reverse('console:assistant') + f'?event={self.event.pk}&from={overview}', secure=True)
        self.assertContains(page, 'page=1')
        self.assertContains(page, 'aria-checked="true"')
        self.client.post(reverse('console:chat_default'), {'on': '0'}, secure=True)
        self.assertEqual(self.client.get(dashboard, secure=True).status_code, 200)
