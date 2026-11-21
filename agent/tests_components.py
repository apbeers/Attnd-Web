"""Console components in the Ask Attnd chat (agent/components.py): the show tool validates and stores JSON only, the
next request gets that JSON (older ones by name), taps and renders check permissions, chat forms go through the real
views, and a component's always-ask button can't get past the confirm gate."""

import json

from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from agent import loop
from agent.models import AgentRun, AgentStep
from agent.tests import AgentTestCase
from events.models import Event, Location, Notification


class ShowToolTests(AgentTestCase):
    def ids(self, **ids):
        return {'event_id': None, 'location_id': None, 'item_id': None, 'notification_id': None, **ids}

    def test_show_is_a_tool_and_the_prompt_prefers_it(self):
        run, provider = self.run_with(['Done.'])
        names = [t['name'] for t in provider.tools]
        self.assertIn('show', names)
        self.assertIn('get_component', names)
        show = next(t for t in provider.tools if t['name'] == 'show')
        self.assertIn('event_tile', show['parameters']['properties']['component']['enum'])
        self.assertIn('show("event_tile"', provider.system)

    def test_show_stores_json_and_the_model_gets_it(self):
        run, provider = self.run_with([
            [('show', {'component': 'event_tile', 'items': [self.ids()]})],
            [('finish', {'summary': 'Here are your events.'})],
        ])
        self.assertEqual(run.status, AgentRun.DONE, run.error)
        step = run.steps.get(kind=AgentStep.KIND_COMPONENT)
        self.assertEqual(step.arguments, {'component': 'event_tile', 'items': [{'event_id': self.event.pk}],
                                          'picked': False})
        self.assertNotIn('<', step.result)  # JSON, never HTML
        self.assertEqual(json.loads(step.result)['name'], 'Prairie Fest')
        result = dict(provider.results)['show']
        self.assertIn('Prairie Fest', result)
        self.assertNotIn('<', result)
        status = self.client.get(reverse('agent:run', args=[run.pk]), secure=True).json()
        shown = next(s for s in status['steps'] if s['kind'] == 'component')
        self.assertEqual(shown['component'], {'name': 'event_tile', 'items': [{'event_id': self.event.pk}],
                                              'picked': False})

    def test_show_refuses_unknown_components_and_other_peoples_ids(self):
        other = User.objects.create_user('o', 'o@example.com', 'pw')
        theirs = Event.objects.create(name='Theirs', event_code='THEIRS', user=other, start_time=self.start)
        run, provider = self.run_with([
            [('show', {'component': 'history_page', 'items': [self.ids()]})],
            [('show', {'component': 'event_overview', 'items': [self.ids(event_id=theirs.pk)]})],
            [('show', {'component': 'location_row', 'items': [self.ids(event_id=self.event.pk, item_id=1)]})],
            [('finish', {'summary': 'No.'})],
        ])
        results = [text for name, text in provider.results if name == 'show']
        self.assertIn('no component', results[0])
        self.assertIn('access', results[1])
        self.assertIn('location_row takes', results[2])
        self.assertFalse(run.steps.filter(kind=AgentStep.KIND_COMPONENT).exists())

    def test_list_components_show_every_row(self):
        run, provider = self.run_with([
            [('show', {'component': 'location_row', 'items': [self.ids(event_id=self.event.pk)]})],
            [('finish', {'summary': 'Your locations.'})],
        ])
        step = run.steps.get(kind=AgentStep.KIND_COMPONENT)
        self.assertEqual([i['location_id'] for i in step.arguments['items']], [self.stage.pk, self.booth.pk])

    def test_the_next_request_gets_earlier_components_as_json_older_ones_by_name(self):
        first, _ = self.run_with([
            [('show', {'component': 'event_tile', 'items': [self.ids()]})],
            [('show', {'component': 'location_row', 'items': [self.ids(event_id=self.event.pk,
                                                                        location_id=self.stage.pk)]})],
            [('show', {'component': 'schedule_item_row', 'items': [self.ids(event_id=self.event.pk,
                                                                             item_id=self.item.pk)]})],
            [('show', {'component': 'live_on_phone', 'items': [self.ids(event_id=self.event.pk)]})],
            [('finish', {'summary': 'Shown.'})],
        ], goal='Show me things')
        second, provider = self.run_with(['Done.'], goal='Move the first one')
        context = provider.first
        self.assertIn('COMPONENTS IN THE CHAT', context)
        self.assertIn(f'event_tile(event_id={self.event.pk}) (use get_component', context)  # older: name + ids
        self.assertIn('"name":"Prairie Lights"', context)  # latest: the JSON
        self.assertNotIn('<li', context)
        self.assertNotIn('class=', context)

    def test_get_component(self):
        run, provider = self.run_with([
            [('get_component', {'component': 'location_form', 'ids': self.ids(event_id=self.event.pk,
                                                                                 location_id=self.stage.pk)})],
            [('finish', {'summary': 'ok'})],
        ])
        result = dict(provider.results)['get_component']
        self.assertIn('Main Stage', result)
        self.assertFalse(run.steps.filter(kind=AgentStep.KIND_COMPONENT).exists())


class PanelEndpointTests(AgentTestCase):
    def setUp(self):
        super().setUp()
        self.run = self.make_run()
        self.run.status = AgentRun.DONE
        self.run.save()
        self.other = User.objects.create_user('o', 'o@example.com', 'pw')
        self.other_client = Client()
        self.other_client.force_login(self.other)

    def render(self, client, name, items):
        return client.get(reverse('agent:component_render'), {'component': name, 'items': json.dumps(items)},
                          secure=True)

    def test_render_is_html_from_our_templates_as_the_person(self):
        response = self.render(self.client, 'location_row', [{'event_id': self.event.pk}])
        self.assertEqual(response.status_code, 200)
        items = response.json()['items']
        self.assertEqual(len(items), 2)
        self.assertIn('Main Stage', items[0]['html'])
        self.assertIn('data-component-act="delete_location"', items[0]['html'])
        self.assertEqual(items[0]['keys'], [f'event:{self.event.pk}', f'location:{self.stage.pk}'])
        self.assertEqual(items[0]['detail'], 'location_form')
        self.assertEqual(self.render(self.other_client, 'location_row', [{'event_id': self.event.pk}]).status_code, 404)
        self.assertEqual(self.render(self.client, 'nope', [{}]).status_code, 400)

    def test_a_tap_shows_the_detail_and_is_remembered(self):
        url = reverse('agent:component_pick')
        data = {'run': self.run.pk, 'component': 'location_row',
                'ids': json.dumps({'event_id': self.event.pk, 'location_id': self.stage.pk})}
        response = self.client.post(url, data, secure=True)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body['items'][0]['component'], 'location_form')
        self.assertIn('name="snippet"', body['items'][0]['html'])
        step = AgentStep.objects.get(pk=body['step']['id'])
        self.assertTrue(step.arguments['picked'])
        self.assertNotIn('<', step.result)
        # Someone else: not their run, not their event.
        self.assertEqual(self.other_client.post(url, data, secure=True).status_code, 404)
        theirs = self.make_run(user=self.other, client=self.other_client)
        self.assertEqual(self.other_client.post(url, {**data, 'run': theirs.pk}, secure=True).status_code, 404)

    def test_a_tap_is_part_of_the_reply(self):
        self.run.status = AgentRun.WAITING_REPLY
        self.run.save()
        question = loop.add_step(self.run, kind=AgentStep.KIND_QUESTION, tool='ask_user', text='Which one?')
        self.client.post(reverse('agent:component_pick'), {
            'run': self.run.pk, 'component': 'event_tile', 'ids': json.dumps({'event_id': self.event.pk})}, secure=True)
        loop.add_step(self.run, kind=AgentStep.KIND_REPLY, text='this one')
        outcome = loop._pending_outcome(self.run, question)
        self.assertIn('this one', outcome)
        self.assertIn('event_overview', outcome)

    def test_a_chat_form_saves_through_the_real_view(self):
        html = self.render(self.client, 'schedule_item_form', [{'event_id': self.event.pk,
                                                                'item_id': self.item.pk}]).json()['items'][0]['html']
        action = reverse('console:item_edit', args=[self.event.pk, self.item.pk])
        self.assertIn(f'action="{action}"', html)
        self.assertIn('data-component-form', html)
        response = self.client.post(action, {'name': 'Prairie Lights (encore)', 'start_time': '2026-11-14T21:00',
                                             'location': self.stage.pk, 'tags_text': 'live music'},
                                    HTTP_X_ATTND_COMPONENT='schedule_item_form', secure=True)
        # Saved: JSON for the chat (what happened, the Undo id, where the page would go), not the next page's HTML
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['saved'])
        self.assertTrue(data['messages'])
        self.assertTrue(data['undo'])
        self.item.refresh_from_db()
        self.assertEqual(self.item.name, 'Prairie Lights (encore)')
        self.assertIn('name', self.item.locked_fields)  # exactly like the page: hand edits are locked

    def act(self, client=None, **data):
        payload = {'run': self.run.pk, 'component': 'location_row', 'action': 'delete_location',
                   'ids': json.dumps({'event_id': self.event.pk, 'location_id': self.booth.pk}), **data}
        return (client or self.client).post(reverse('agent:component_act'), payload, secure=True)

    def test_a_gated_button_waits_for_confirm_and_cannot_skip_it(self):
        response = self.act()
        self.assertEqual(response.status_code, 200, response.content)
        pending = response.json()['run']['pending']
        self.assertIn('Taco Booth', pending['summary'])
        self.assertTrue(Location.objects.filter(pk=self.booth.pk).exists())  # nothing ran yet
        step = AgentStep.objects.get(pk=pending['step'])
        self.assertEqual(step.kind, AgentStep.KIND_BUTTON)
        # A second gated click waits for the first; a wrong hash does nothing.
        self.assertEqual(self.act().status_code, 409)
        bad = self.client.post(reverse('agent:confirm', args=[self.run.pk]), {'step': step.pk, 'hash': 'x'}, secure=True)
        self.assertEqual(bad.status_code, 409)
        self.assertTrue(Location.objects.filter(pk=self.booth.pk).exists())
        ok = self.client.post(reverse('agent:confirm', args=[self.run.pk]),
                              {'step': step.pk, 'hash': pending['hash']}, secure=True)
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.json()['step']['changed'])
        self.assertFalse(Location.objects.filter(pk=self.booth.pk).exists())
        # Single use.
        again = self.client.post(reverse('agent:confirm', args=[self.run.pk]),
                                 {'step': step.pk, 'hash': pending['hash']}, secure=True)
        self.assertEqual(again.status_code, 409)

    def test_a_gated_button_works_before_anything_was_asked(self):
        # The full-screen canvas shows the event before the person typed anything: there is no run yet, so the
        # server starts a quiet one (no model call) to hold the confirmation; the gate is the same.
        response = self.act(run='')
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertTrue(data['run']['buttons'])
        self.assertNotEqual(data['run']['id'], self.run.pk)
        run = AgentRun.objects.get(pk=data['run']['id'])
        self.assertEqual((run.user, run.status, run.step_count), (self.owner, AgentRun.DONE, 0))
        self.assertTrue(Location.objects.filter(pk=self.booth.pk).exists())
        ok = self.client.post(reverse('agent:confirm', args=[run.pk]),
                              {'step': data['run']['pending']['step'], 'hash': data['run']['pending']['hash']},
                              secure=True)
        self.assertEqual(ok.status_code, 200)
        self.assertFalse(Location.objects.filter(pk=self.booth.pk).exists())
        # Someone else's event: refused before any run is made.
        before = AgentRun.objects.count()
        self.assertEqual(self.act(client=self.other_client, run='').status_code, 404)
        self.assertEqual(AgentRun.objects.count(), before)

    def test_cancel_runs_nothing(self):
        pending = self.act().json()['run']['pending']
        self.client.post(reverse('agent:cancel', args=[self.run.pk]), {'step': pending['step']}, secure=True)
        self.assertTrue(Location.objects.filter(pk=self.booth.pk).exists())
        self.assertEqual(AgentRun.objects.get(pk=self.run.pk).status, AgentRun.DONE)  # no model call resumed

    def test_the_gate_only_takes_offered_always_ask_actions(self):
        self.assertEqual(self.act(action='toggle_location_hidden').status_code, 400)  # posts to its view instead
        self.assertEqual(self.act(action='delete_event').status_code, 409)  # not offered by this component
        self.assertEqual(self.act(client=self.other_client).status_code, 404)  # not their run
        theirs = self.make_run(user=self.other, client=self.other_client)
        self.assertEqual(self.act(client=self.other_client, run=theirs.pk).status_code, 404)  # not their event
        self.assertFalse(AgentStep.objects.filter(kind=AgentStep.KIND_BUTTON).exists())

    def test_a_gated_form_is_checked_first_then_waits_for_confirm(self):
        self.event.status = Event.STATUS_PUBLISHED
        self.event.save()
        url = reverse('agent:component_act')
        base = {'run': self.run.pk, 'component': 'notification_form', 'action': 'send_notification',
                'ids': json.dumps({'event_id': self.event.pk})}
        invalid = self.client.post(url, {**base, 'title': 'Hi', 'body': 'x', 'when': 'later', 'send_time': ''},
                                   secure=True).json()
        self.assertTrue(invalid['invalid'])
        self.assertIn('Pick a time', invalid['items'][0]['html'])
        self.assertFalse(AgentStep.objects.filter(kind=AgentStep.KIND_BUTTON).exists())
        response = self.client.post(url, {**base, 'title': 'Gates open late', 'body': 'At 11', 'when': 'now'},
                                    secure=True).json()
        self.assertIn('Gates open late', response['run']['pending']['summary'])
        self.assertFalse(Notification.objects.exists())  # not sent until confirmed
        step = AgentStep.objects.get(kind=AgentStep.KIND_BUTTON)
        self.assertEqual(step.arguments['title'], 'Gates open late')
        self.assertFalse(step.arguments['urgent'])
        ok = self.client.post(reverse('agent:confirm', args=[self.run.pk]),
                              {'step': step.pk, 'hash': step.confirm_hash}, secure=True)
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertTrue(Notification.objects.filter(title='Gates open late', sent=True).exists())
