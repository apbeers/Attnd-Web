"""History of everything, the Django way (console/history.py): every create / update / delete made through the
console, Ask Attnd and the chat's cards is a LogEntry (ADDITION / CHANGE / DELETION, Django's JSON change message),
attributed to the person, with "(Ask Attnd)" when the agent made it; the admin's History pages and the console's
timeline read it."""

import json
from datetime import timedelta

from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from agent.tests import AgentTestCase
from console import components as C
from console.models import HistoryRecord
from console.testing import TwoFactorClient
from events.models import Event, EventInvite, Location, ScheduledItem


def entries_for(obj):
    return LogEntry.objects.filter(content_type=ContentType.objects.get_for_model(obj), object_id=str(obj.pk)) \
        .select_related('attnd').order_by('pk')


class HistoryTests(AgentTestCase):
    def location_post(self, loc=None, **fields):
        data = {'name': loc.name if loc else 'New', 'category': loc.category if loc else '', 'snippet': '',
                'description': '', 'tags_text': '', 'address': '', 'latitude': '', 'longitude': '', 'website': '',
                'image_url': '', **fields}
        url = (reverse('console:location_edit', args=[self.event.pk, loc.pk]) if loc
               else reverse('console:location_new', args=[self.event.pk]))
        return data, url

    def test_console_create_update_delete(self):
        data, url = self.location_post(name='Lemonade Stand', category='Food')
        self.assertEqual(self.client.post(url, data, secure=True).status_code, 302)
        loc = Location.objects.get(name='Lemonade Stand')
        data, url = self.location_post(loc, name='Lemonade Stand & Shade', snippet='Cold drinks')
        self.client.post(url, data, secure=True)
        self.client.post(reverse('console:location_action', args=[self.event.pk, loc.pk]), {'action': 'delete'},
                         secure=True)
        added, changed, deleted = entries_for(loc)
        self.assertEqual([e.action_flag for e in (added, changed, deleted)], [ADDITION, CHANGE, DELETION])
        self.assertEqual(added.user, self.owner)
        self.assertEqual(json.loads(added.change_message), [{'added': {}}])
        fields = json.loads(changed.change_message)[0]['changed']['fields']
        self.assertIn('Name', fields)
        self.assertEqual(changed.get_change_message()[:8], 'Changed ')  # Django reads it
        self.assertEqual(changed.attnd.values['name'], ['Lemonade Stand', 'Lemonade Stand & Shade'])
        self.assertEqual(changed.attnd.event_id, self.event.pk)
        self.assertEqual(changed.attnd.via, HistoryRecord.VIA_CONSOLE)
        self.assertIsNotNone(changed.attnd.undo_id)  # the same capture as Undo
        self.assertEqual(deleted.object_repr, 'Lemonade Stand & Shade')
        self.assertEqual(deleted.attnd.event_id, self.event.pk)  # kept after the row is gone

    def test_ask_attnd_changes_say_so(self):
        run, _ = self.run_with([
            [('edit_schedule_item', self.edit_args(name='Prairie Lights (late show)'))],
            [('finish', {'summary': 'Renamed.'})],
        ])
        self.assertEqual(run.status, 'done', run.error)
        entry = entries_for(self.item).get(action_flag=CHANGE)
        self.assertEqual(entry.user, self.owner)
        self.assertEqual(entry.attnd.via, HistoryRecord.VIA_AGENT)
        from console.history import admin_message
        self.assertIn('(Ask Attnd)', admin_message(entry))
        self.assertIn('“Prairie Lights” → “Prairie Lights (late show)”', admin_message(entry))

    def test_chat_cards_and_undo(self):
        data, url = self.location_post(self.stage, name='Main Stage (north)')
        self.client.post(url, data, HTTP_X_ATTND_COMPONENT='location_form', secure=True)
        change = entries_for(self.stage).get(action_flag=CHANGE)
        self.assertEqual(change.attnd.via, HistoryRecord.VIA_CHAT)
        response = self.client.post(reverse('console:undo', args=[change.attnd.undo_id]),
                                    HTTP_ACCEPT='application/json', secure=True)
        self.assertEqual(response.status_code, 200, response.content)
        undone = entries_for(self.stage).filter(action_flag=CHANGE).last()
        self.assertEqual(undone.attnd.via, HistoryRecord.VIA_UNDO)
        self.assertEqual(undone.attnd.values['name'], ['Main Stage (north)', 'Main Stage'])

    def test_team_invites_notifications_and_event_deletion(self):
        self.client.post(reverse('console:team', args=[self.event.pk]), {'action': 'invite',
                                                                         'email': 'new@example.com',
                                                                         'role': 'contributor'}, secure=True)
        invite = EventInvite.objects.get()
        self.assertEqual(entries_for(invite).get().action_flag, ADDITION)
        self.client.post(reverse('console:notifications', args=[self.event.pk]),
                         {'title': 'Gates open', 'body': 'Come in', 'when': 'later',
                          'send_time': (self.start + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')}, secure=True)
        self.assertTrue(LogEntry.objects.filter(content_type__model='notification', action_flag=ADDITION).exists())
        event_pk = self.event.pk
        self.client.post(reverse('console:event_delete', args=[event_pk]), {'confirm_code': 'PRAIRIE'}, secure=True)
        self.assertFalse(Event.objects.filter(pk=event_pk).exists())
        gone = LogEntry.objects.get(content_type__model='event', object_id=str(event_pk), action_flag=DELETION)
        self.assertEqual(gone.object_repr, 'Prairie Fest')
        self.assertTrue(LogEntry.objects.filter(content_type__model='location', action_flag=DELETION,
                                                attnd__event_id=event_pk).exists())

    def test_the_timeline_and_the_item_history(self):
        data, url = self.location_post(self.stage, name='Main Stage (north)')
        self.client.post(url, data, secure=True)
        page = self.client.get(reverse('console:history', args=[self.event.pk]), secure=True).content.decode()
        self.assertIn('Changed location “Main Stage (north)”', page)
        self.assertIn('“Main Stage”', page)
        self.assertIn('Undo', page)
        self.assertIn('You', page)
        detail = self.client.get(reverse('console:location_edit', args=[self.event.pk, self.stage.pk]),
                                 secure=True).content.decode()
        self.assertIn('component--object_history', detail)
        self.assertIn('Main Stage (north)', detail)
        rows = C.load(self.owner, 'object_history', {'event_id': self.event.pk, 'item_id': self.item.pk})['rows']
        self.assertEqual(rows, [])  # the item didn't change
        other = User.objects.create_user('x', 'x@example.com', 'pw')
        with self.assertRaises(C.Http404):
            C.load(other, 'object_history', {'event_id': self.event.pk})

    def test_the_admin_history_pages_show_it(self):
        admin = User.objects.create_superuser('admin', 'admin@example.com', 'pw')
        client = TwoFactorClient()
        client.force_login(admin)
        run, _ = self.run_with([
            [('edit_schedule_item', self.edit_args(name='Prairie Lights (late show)'))],
            [('finish', {'summary': 'Renamed.'})],
        ])
        url = reverse('admin:events_scheduleditem_history', args=[self.item.pk])
        page = client.get(url, secure=True).content.decode()
        self.assertIn('(Ask Attnd)', page)
        self.assertIn('owner@example.com', page)
        for name in ('eventmember', 'eventinvite'):
            self.assertEqual(client.get(reverse(f'admin:events_{name}_changelist'), secure=True).status_code, 200)
        ScheduledItem.objects.filter(pk=self.item.pk).update(name='x')  # (not through the console: not recorded)
        self.assertEqual(entries_for(self.item).count(), 1)
