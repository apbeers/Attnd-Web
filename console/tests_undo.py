"""Undo for every change (console/undo.py): per change, through the real views; permissions, expiry, single use,
sent notifications, newer edits by someone else, content_version, Redo, the messages' Undo button and Ask Attnd."""

from datetime import timedelta

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from agent import pages
from console import undo as U
from console.models import UndoEntry
from events.models import Event, EventInvite, EventMember, Location, Notification, ScheduledItem


def form_data(client, path, **changes):
    """The form on `path` as the browser would post it (its current values), with `changes` on top."""
    html = client.get(path, secure=True).content.decode()
    form = next(f for f in pages.forms(html, path) if f['method'] == 'post' and f['action'] == path)
    data = {}
    for name, value in form['fields']:
        data.setdefault(name, []).append(value)
    data.pop('csrfmiddlewaretoken', None)
    for key, value in changes.items():
        data[key] = [value] if value is not None else []
    return {k: v for k, v in data.items() if v}


@override_settings(SECURE_SSL_REDIRECT=False)
class UndoTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user('owner', 'owner@example.com', 'pw')
        self.other = User.objects.create_user('other', 'other@example.com', 'pw')
        start = timezone.now().replace(minute=0, second=0, microsecond=0) + timedelta(days=3)
        self.event = Event.objects.create(name='Prairie Fest', event_code='PRAIRIE', user=self.owner,
                                          timezone='America/Chicago', start_time=start,
                                          end_time=start + timedelta(hours=10), status=Event.STATUS_PUBLISHED)
        EventMember.objects.create(event=self.event, user=self.other, role=EventMember.ROLE_ADMIN)
        self.stage = Location.objects.create(event=self.event, name='The Nobleman Hotel', category='Stage', sort_order=1)
        self.item = ScheduledItem.objects.create(event=self.event, name='Prairie Lights', start_time=start,
                                                 end_time=start + timedelta(hours=1), location=self.stage)
        self.client = Client()
        self.client.force_login(self.owner)
        self.edit_url = reverse('console:location_edit', args=[self.event.pk, self.stage.pk])

    def version(self):
        return Event.objects.get(pk=self.event.pk).content_version

    def rename(self, name, client=None):
        client = client or self.client
        response = client.post(self.edit_url, form_data(client, self.edit_url, name=name), secure=True)
        self.assertEqual(response.status_code, 302, response.content[:500])
        return UndoEntry.objects.get(pk=int(response['X-Attnd-Undo']))

    def undo(self, entry, client=None, json=True):
        headers = {'HTTP_ACCEPT': 'application/json'} if json else {}
        return (client or self.client).post(reverse('console:undo', args=[entry.pk]), secure=True, **headers)


class UpdateTests(UndoTestCase):
    def test_save_offers_undo_and_undo_restores(self):
        entry = self.rename('The Nobleman Hote')
        self.assertEqual(entry.label, 'Saved “The Nobleman Hote”.')
        self.assertEqual(entry.event, self.event)
        self.assertEqual([op['op'] for op in entry.operations], ['update'])
        self.assertIn('name', entry.operations[0]['fields'])
        page = self.client.get(self.edit_url, secure=True).content.decode()
        self.assertIn(f'data-undo-id="{entry.pk}"', page)  # the message's Undo button
        self.assertIn(reverse('console:undo', args=[entry.pk]), page)
        before = self.version()
        response = self.undo(entry)
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertIn('Undone', data['message'])
        self.assertIn('The Nobleman Hotel', data['message'])
        self.stage.refresh_from_db()
        self.assertEqual(self.stage.name, 'The Nobleman Hotel')
        self.assertGreater(self.version(), before)  # the apps refresh
        self.assertIsNotNone(data['redo'])

    def test_single_use_and_redo(self):
        entry = self.rename('Hotel B')
        data = self.undo(entry).json()
        self.assertEqual(self.undo(entry).status_code, 409)  # single use
        redo = UndoEntry.objects.get(pk=data['redo'])
        self.assertEqual(redo.reverses, entry)
        self.assertTrue(self.undo(redo).json()['ok'])  # Redo
        self.stage.refresh_from_db()
        self.assertEqual(self.stage.name, 'Hotel B')

    def test_html_undo_redirects_with_a_redo_button(self):
        entry = self.rename('Hotel C')
        response = self.client.post(reverse('console:undo', args=[entry.pk]), {'next': self.edit_url}, secure=True)
        self.assertEqual(response['Location'], self.edit_url)
        page = self.client.get(self.edit_url, secure=True).content.decode()
        self.assertIn('Undone:', page)
        self.assertIn('data-redo-id=', page)
        self.assertIn('>Redo<', page)

    def test_only_the_person_who_made_it(self):
        entry = self.rename('Hotel D')
        other = Client()
        other.force_login(self.other)
        self.assertEqual(self.undo(entry, other).status_code, 404)
        stranger = User.objects.create_user('x', 'x@example.com', 'pw')
        entry.user = stranger
        entry.save()
        c = Client()
        c.force_login(stranger)
        self.assertEqual(self.undo(entry, c).status_code, 409)  # no access to the event any more

    def test_expired(self):
        entry = self.rename('Hotel E')
        entry.expires_at = timezone.now() - timedelta(minutes=1)
        entry.save()
        self.assertEqual(self.undo(entry).status_code, 409)
        self.stage.refresh_from_db()
        self.assertEqual(self.stage.name, 'Hotel E')

    def test_newer_change_by_someone_else_is_never_overwritten(self):
        entry = self.rename('Hotel F')
        other = Client()
        other.force_login(self.other)
        self.rename('Hotel G', client=other)
        response = self.undo(entry)
        self.assertEqual(response.status_code, 409)
        self.assertIn('other@example.com', response.json()['error'])
        self.stage.refresh_from_db()
        self.assertEqual(self.stage.name, 'Hotel G')
        entry.refresh_from_db()
        self.assertIsNone(entry.used_at)

    def test_someone_elses_change_to_another_field_is_kept(self):
        entry = self.rename('Hotel H')
        Location.objects.filter(pk=self.stage.pk).update(category='Venue')
        self.assertTrue(self.undo(entry).json()['ok'])
        self.stage.refresh_from_db()
        self.assertEqual((self.stage.name, self.stage.category), ('The Nobleman Hotel', 'Venue'))

    def test_event_details(self):
        url = reverse('console:event_edit', args=[self.event.pk])
        response = self.client.post(url, form_data(self.client, url, name='Prairie Fest 2026'), secure=True)
        entry = UndoEntry.objects.get(pk=int(response['X-Attnd-Undo']))
        self.assertEqual(entry.label, 'Event details saved.')
        self.assertTrue(self.undo(entry).json()['ok'])
        self.assertEqual(Event.objects.get(pk=self.event.pk).name, 'Prairie Fest')

    def test_schedule_item(self):
        url = reverse('console:item_edit', args=[self.event.pk, self.item.pk])
        response = self.client.post(url, form_data(self.client, url, name='Prairie Lights (late)'), secure=True)
        entry = UndoEntry.objects.get(pk=int(response['X-Attnd-Undo']))
        self.undo(entry)
        self.item.refresh_from_db()
        self.assertEqual(self.item.name, 'Prairie Lights')

    def test_nothing_changed_records_nothing(self):
        response = self.client.post(self.edit_url, form_data(self.client, self.edit_url), secure=True)
        self.assertFalse(response.has_header('X-Attnd-Undo'))
        self.assertFalse(UndoEntry.objects.exists())


class CreateDeleteTests(UndoTestCase):
    def test_create_location_then_undo_deletes_it(self):
        url = reverse('console:location_new', args=[self.event.pk])
        response = self.client.post(url, form_data(self.client, url, name='Taco Row'), secure=True)
        entry = UndoEntry.objects.get(pk=int(response['X-Attnd-Undo']))
        self.assertEqual(entry.operations[0]['op'], 'create')
        self.assertTrue(Location.objects.filter(name='Taco Row').exists())
        self.assertTrue(self.undo(entry).json()['ok'])
        self.assertFalse(Location.objects.filter(name='Taco Row').exists())

    def test_delete_location_then_undo_brings_it_back_with_its_items(self):
        url = reverse('console:location_action', args=[self.event.pk, self.stage.pk])
        response = self.client.post(url, {'action': 'delete'}, secure=True)
        entry = UndoEntry.objects.get(pk=int(response['X-Attnd-Undo']))
        self.assertEqual(entry.operations[0]['op'], 'delete')
        self.item.refresh_from_db()
        self.assertIsNone(self.item.location_id)
        before = self.version()
        data = self.undo(entry).json()
        self.assertTrue(data['ok'], data)
        self.assertIn('is back', data['message'])
        stage = Location.objects.get(pk=self.stage.pk)
        self.assertEqual(stage.name, 'The Nobleman Hotel')
        self.item.refresh_from_db()
        self.assertEqual(self.item.location_id, stage.pk)
        self.assertGreater(self.version(), before)

    def test_delete_schedule_item_then_undo(self):
        url = reverse('console:item_action', args=[self.event.pk, self.item.pk])
        entry = UndoEntry.objects.get(pk=int(self.client.post(url, {'action': 'delete'}, secure=True)['X-Attnd-Undo']))
        self.undo(entry)
        self.assertTrue(ScheduledItem.objects.filter(pk=self.item.pk, name='Prairie Lights').exists())

    def test_deleted_since_refuses(self):
        entry = self.rename('Hotel I')
        Location.objects.filter(pk=self.stage.pk).delete()
        response = self.undo(entry)
        self.assertEqual(response.status_code, 409)
        self.assertIn('deleted since', response.json()['error'])


class NotificationTests(UndoTestCase):
    def notify(self, when='later'):
        url = reverse('console:notifications', args=[self.event.pk])
        send_time = (timezone.now() + timedelta(days=1)).astimezone(timezone.get_current_timezone())
        send_time = send_time.replace(minute=0, second=0, microsecond=0)
        data = {'title': 'Gates open', 'body': 'Come in!', 'when': when,
                'send_time': send_time.strftime('%Y-%m-%dT%H:%M') if when == 'later' else ''}
        response = self.client.post(url, data, secure=True)
        self.assertEqual(response.status_code, 302)
        return response

    def test_scheduled_update_can_be_undone(self):
        response = self.notify('later')
        entry = UndoEntry.objects.get(pk=int(response['X-Attnd-Undo']))
        self.assertTrue(self.undo(entry).json()['ok'])
        self.assertFalse(Notification.objects.exists())

    def test_sent_update_is_refused(self):
        response = self.notify('now')
        entry = UndoEntry.objects.get(pk=int(response['X-Attnd-Undo']))
        response = self.undo(entry)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error'], 'This update was already sent — it can’t be unsent.')
        self.assertTrue(Notification.objects.filter(sent=True).exists())

    def test_scheduled_then_sent_by_the_scheduler_is_refused(self):
        entry = UndoEntry.objects.get(pk=int(self.notify('later')['X-Attnd-Undo']))
        Notification.objects.update(sent=True, sent_at=timezone.now())
        self.assertEqual(self.undo(entry).status_code, 409)


class TeamTests(UndoTestCase):
    def test_invite_and_member_rows(self):
        with U.recording(self.owner) as rec:
            EventInvite.objects.create(event=self.event, email='new@example.com', role='contributor',
                                       invited_by=self.owner)
            EventMember.objects.filter(user=self.other).first().delete()
        entry = U.finalize(rec, 'Team changes')
        self.assertEqual({op['model'] for op in entry.operations}, {'events.eventinvite', 'events.eventmember'})
        message, _ = U.undo(entry, self.owner)
        self.assertFalse(EventInvite.objects.exists())
        self.assertTrue(EventMember.objects.filter(user=self.other, role=EventMember.ROLE_ADMIN).exists())

    def test_deleting_a_whole_event_is_not_recorded(self):
        with U.recording(self.owner) as rec:
            Event.objects.get(pk=self.event.pk).delete()
        self.assertIsNone(U.finalize(rec, 'x'))


class AgentUndoTests(UndoTestCase):
    def test_undo_last_change_endpoint(self):
        self.rename('Hotel J')
        response = self.client.post(reverse('console:undo_last'), secure=True, HTTP_ACCEPT='application/json')
        self.assertTrue(response.json()['ok'])
        self.stage.refresh_from_db()
        self.assertEqual(self.stage.name, 'The Nobleman Hotel')
        response = self.client.post(reverse('console:undo_last'), secure=True, HTTP_ACCEPT='application/json')
        self.assertTrue(response.json()['ok'])  # the latest is now the undo itself: undoing it = redo
        self.stage.refresh_from_db()
        self.assertEqual(self.stage.name, 'Hotel J')

    def test_registered_for_the_agent(self):
        from agent import registry
        writes = registry.registry()['writes']
        self.assertIn('undo_change', writes)
        self.assertIn('undo_last_change', writes)
        self.assertFalse(writes['undo_last_change'].confirm)

    def test_agent_step_gets_an_undo_and_the_model_can_undo(self):
        from agent import loop
        from agent.models import AgentRun, AgentStep
        from agent.tests import FakeProvider
        from unittest import mock
        from agent import registry
        run = AgentRun.objects.create(user=self.owner, goal='rename', session_key=self.client.session.session_key,
                                      host='testserver', secure=True, event=self.event)
        args = {k: None for k in registry.tool_schema(registry.registry()['writes']['edit_location'])['properties']}
        args.update(event_id=self.event.pk, location_id=self.stage.pk, name='Hotel K')
        provider = FakeProvider([[('edit_location', args)], [('undo_last_change', {})], 'Done.'])
        with mock.patch('agent.loop._provider', return_value=provider):
            loop.execute(run.pk)
        step = AgentStep.objects.get(run=run, tool='edit_location')
        self.assertIsNotNone(step.undo_id)
        self.assertEqual(UndoEntry.objects.get(pk=step.undo_id).label, 'Saved “Hotel K”.')
        self.stage.refresh_from_db()
        self.assertEqual(self.stage.name, 'The Nobleman Hotel')  # the model's undo_last_change
        data = self.client.get(reverse('agent:run', args=[run.pk]), secure=True).json()
        shown = next(s for s in data['steps'] if s['tool'] == 'edit_location')
        self.assertEqual(shown['undo'], {'id': step.undo_id, 'state': 'used'})
        undo_step = AgentStep.objects.get(run=run, tool='undo_last_change')
        self.assertTrue(undo_step.ok, undo_step.text)
        self.assertIn('Undone', undo_step.text)
