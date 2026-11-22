"""P0 #7 (Third Thursday, Near Southside, Nov 21, 2026): every person-made change locks the field, and website
watching / AI reviews never overwrite it: in auto mode, through the chat's Review card and through a review applied
without per-field ticks. The dev event IFICCK70: the organizer moved the event from Nov 19 to Nov 21 in the chat; the
website still says the 19th."""
import json
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from agent import internal, registry
from agent.models import AgentRun, AgentStep
from events.models import Event, ScheduledItem
from scrape import diff, jobs
from scrape.models import Change, ChangeSet, IngestJob

NOV19 = datetime(2026, 11, 19, 23, 0, tzinfo=dt_timezone.utc)  # 5 PM Chicago on the website
NOV21 = datetime(2026, 11, 21, 23, 0, tzinfo=dt_timezone.utc)  # what the organizer set in the chat


class HandEditsStayTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('o', 'o@example.com', 'pw')
        self.event = Event.objects.create(name='Third Thursday', event_code='IFICCK70', user=self.owner,
                                          timezone='America/Chicago', start_time=NOV19,
                                          end_time=NOV19 + timedelta(hours=3), status=Event.STATUS_PUBLISHED,
                                          update_mode=Event.UPDATE_AUTO, refresh_schedule='watch',
                                          source_urls=['https://nearsouthside.example/third-thursday'])
        self.item = ScheduledItem.objects.create(event=self.event, name='Allyn Fincher at The Nobleman Hotel',
                                                 start_time=NOV19, end_time=NOV19 + timedelta(hours=3))
        self.client = Client()
        self.client.force_login(self.owner)

    def chat_moves_the_dates(self):
        """The chat's change goes through the real view (Ask Attnd's internal request), like a card save."""
        run = AgentRun.objects.create(user=self.owner, goal='Move it to the 21st', event=self.event,
                                      session_key=self.client.session.session_key, host='testserver', secure=True)
        for name, ids, start in (('update_event_details', {'event_id': self.event.pk}, '2026-11-21T17:00'),
                                 ('edit_schedule_item', {'event_id': self.event.pk, 'item_id': self.item.pk},
                                  '2026-11-21T17:00')):
            action = registry.registry()['writes'][name]
            args = {k: None for k in registry.tool_schema(action)['properties']}
            args.update(ids, start_time=start, end_time='2026-11-21T20:00')
            out = internal.execute(run, action, args)
            self.assertTrue(out['ok'], out)
        self.event.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(self.event.start_time, NOV21)
        self.assertEqual(self.item.start_time, NOV21)
        for obj in (self.event, self.item):
            self.assertIn('start_time', obj.locked_fields)
            self.assertIn('end_time', obj.locked_fields)

    def website_says_the_19th(self):
        data = {'name': 'Third Thursday', 'timezone': 'America/Chicago', 'description': 'Art walk on South Main.',
                'start_time': '2026-11-19T17:00', 'end_time': '2026-11-19T20:00',
                'schedule': [{'name': self.item.name, 'start_time': '2026-11-19T17:00', 'end_time': '2026-11-19T20:00',
                              'description': 'Paintings and prints.'}]}
        job = IngestJob.objects.create(kind=IngestJob.KIND_REFRESH, event=self.event, created_by=self.owner)
        return diff.save_changeset(self.event, job, diff.compute_changes(self.event, data))

    def assert_dates_kept(self):
        self.event.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(self.event.start_time, NOV21)
        self.assertEqual(self.item.start_time, NOV21)

    def test_the_proposal_is_a_conflict(self):
        self.chat_moves_the_dates()
        cs = self.website_says_the_19th()
        ev = cs.changes.get(entity=Change.ENTITY_EVENT)
        self.assertEqual(set(ev.conflict_fields), {'start_time', 'end_time'})

    def test_auto_mode_keeps_the_hand_edit(self):
        self.chat_moves_the_dates()
        cs = self.website_says_the_19th()
        diff.apply_changeset(cs, diff.default_decisions(cs), user=None, partial_ok=True)
        self.assert_dates_kept()
        self.event.refresh_from_db()
        self.assertEqual(self.event.description, 'Art walk on South Main.')  # the rest still applies

    def test_a_review_without_field_ticks_keeps_the_hand_edit(self):
        self.chat_moves_the_dates()
        cs = self.website_says_the_19th()
        diff.apply_changeset(cs, {ch.pk: {'accept': True, 'fields': None} for ch in cs.changes.all()}, user=self.owner)
        self.assert_dates_kept()

    def test_the_chat_review_card_keeps_the_hand_edit(self):
        self.chat_moves_the_dates()
        cs = self.website_says_the_19th()
        run = AgentRun.objects.create(user=self.owner, goal='Review', event=self.event,
                                      session_key=self.client.session.session_key, host='testserver', secure=True)
        post = {'run': run.pk, 'component': 'changeset_review', 'action': 'apply_ai_changes',
                'ids': json.dumps({'event_id': self.event.pk, 'changeset_id': cs.pk})}
        for ch in cs.changes.all():  # "Apply" with every change ticked, as the card posts it
            post[f'accept_{ch.id}'] = 'on'
        response = self.client.post(reverse('agent:component_act'), post, secure=True)
        self.assertEqual(response.status_code, 200, response.content)
        step = AgentStep.objects.get(kind=AgentStep.KIND_BUTTON)
        ok = self.client.post(reverse('agent:confirm', args=[run.pk]), {'step': step.pk, 'hash': step.confirm_hash},
                              secure=True)
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assert_dates_kept()
        self.event.refresh_from_db()
        self.assertEqual(self.event.description, 'Art walk on South Main.')

    def test_asked_to_replace_my_edits(self):
        self.chat_moves_the_dates()
        cs = self.website_says_the_19th()
        from agent import summaries
        data = summaries.build_apply_changes({'changeset_id': cs.pk, 'replace_my_edits': True,
                                              'accept_change_ids': list(cs.changes.values_list('pk', flat=True))},
                                             SimpleNamespace(user=self.owner))
        ev = cs.changes.get(entity=Change.ENTITY_EVENT)
        self.assertIn(f'field_{ev.pk}_start_time', data)
