"""§4: the draft (rows, preview phones) and the published copy (attendees), Publish / Discard, bypasses, autosave."""
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from console.testing import TwoFactorClient
from events import drafts, preview
from events.models import Device, Event, Location, Notification

AUTH = {'HTTP_AUTHORIZATION': 'Bearer test-key'}


@override_settings(API_KEY='test-key')
class DraftTestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('o', 'o@example.com', 'pw')
        self.event = Event.objects.create(name='Fair', event_code='FAIRD', user=self.owner, timezone='UTC',
                                          start_time=timezone.now() + timedelta(hours=3), paid=True)
        self.stage = Location.objects.create(event=self.event, user=self.owner, name='Main Stage')
        self.api = Client()
        self.url = f'/api/v2/events/{self.event.pk}/bundle/'
        drafts.publish(self.event, push=False)
        self.api.post(f'/api/v2/events/{self.event.pk}/join/', json.dumps({
            'device_id': 'phone-p', 'platform': 'ios', 'preview_token': preview.make_token(self.owner, self.event)}),
            content_type='application/json', secure=True, **AUTH)

    def bundle(self, device='attendee', **kw):
        return self.api.get(self.url, secure=True, HTTP_X_DEVICE_ID=device, **AUTH, **kw)

    def names(self, device='attendee'):
        return [loc['name'] for loc in self.bundle(device).json()['locations']]


class DraftTests(DraftTestCase):
    def test_attendees_see_the_last_publish_preview_phones_the_draft(self):
        etag = self.bundle()['ETag']
        self.stage.name = 'Big Stage'
        self.stage.save()
        self.assertEqual(self.names(), ['Main Stage'])
        self.assertEqual(self.names('phone-p'), ['Big Stage'])
        self.assertTrue(self.bundle('phone-p')['ETag'].endswith('d"'))
        self.assertEqual(self.bundle(HTTP_IF_NONE_MATCH=etag).status_code, 304)  # no build for attendees
        self.assertEqual([r['field'] for r in drafts.changes(self.event)], ['Name'])
        drafts.publish(self.event)
        self.assertEqual(self.names(), ['Big Stage'])
        self.assertEqual(self.bundle(HTTP_IF_NONE_MATCH=etag).status_code, 200)
        self.assertEqual(drafts.changes(self.event), [])

    def test_notifications_and_status_never_wait(self):
        self.stage.name = 'Draft name'
        self.stage.save()
        Notification.objects.create(event=self.event, title='Gates open', body='Now', send_time=timezone.now(),
                                    sent=True, sent_at=timezone.now())
        data = self.bundle().json()
        self.assertEqual([n['title'] for n in data['notifications']], ['Gates open'])
        self.assertEqual([loc['name'] for loc in data['locations']], ['Main Stage'])
        self.assertEqual(data['content_version'], Event.objects.get(pk=self.event.pk).content_version)
        event = Event.objects.get(pk=self.event.pk)
        event.status = Event.STATUS_ARCHIVED
        event.save()
        self.assertEqual(self.bundle().json()['event']['status'], 'archived')

    def test_discard_and_a_new_event_published_gets_its_first_copy(self):
        self.stage.name = 'Oops'
        self.stage.save()
        drafts.discard(self.event, self.owner)
        self.assertEqual(Location.objects.get(pk=self.stage.pk).name, 'Main Stage')
        self.assertEqual(drafts.changes(self.event), [])
        draft = Event.objects.create(name='New', event_code='NEWD', user=self.owner, timezone='UTC',
                                     start_time=timezone.now(), status=Event.STATUS_DRAFT)
        self.assertIsNone(drafts.published(draft))
        draft.status = Event.STATUS_PUBLISHED
        draft.save()
        self.assertIsNotNone(drafts.published(draft))

    def test_a_draft_change_pushes_only_the_preview_phones(self):
        from events import push
        self.stage.name = 'Draft'
        self.stage.save()
        with patch('events.push.notify_previews') as previews, patch('events.push.notify_content_updated') as everyone, \
                patch('django.db.close_old_connections'), patch('events.plan_suggestions.schedule'):
            push._send_content_push(self.event.pk)
        previews.assert_called_once()
        everyone.assert_not_called()
        drafts.publish(self.event, push=False)
        with patch('events.push.notify_previews') as previews, patch('events.push.notify_content_updated') as everyone, \
                patch('django.db.close_old_connections'), patch('events.plan_suggestions.schedule'):
            push._send_content_push(self.event.pk)
        everyone.assert_called_once()

    def test_preview_phone_may_join_a_draft_event(self):
        draft = Event.objects.create(name='Soon', event_code='SOOND', user=self.owner, timezone='UTC',
                                     start_time=timezone.now(), status=Event.STATUS_DRAFT)
        join = lambda **extra: self.api.post(f'/api/v2/events/{draft.pk}/join/', json.dumps(  # noqa: E731
            {'device_id': 'p2', 'platform': 'ios', **extra}), content_type='application/json', secure=True, **AUTH)
        self.assertEqual(join().status_code, 404)
        self.assertEqual(join(preview_token=preview.make_token(self.owner, draft)).status_code, 200)
        r = self.api.get(f'/api/v2/events/{draft.pk}/bundle/', secure=True, HTTP_X_DEVICE_ID='p2', **AUTH)
        self.assertEqual(r.json()['event']['name'], 'Soon')


class ConsoleTests(DraftTestCase):
    def setUp(self):
        super().setUp()
        self.console = TwoFactorClient()
        self.console.force_login(self.owner)

    def test_bar_review_publish_discard(self):
        bar = reverse('console:event_changes', args=[self.event.pk]) + '?bar=1'
        self.assertEqual(self.console.get(bar, secure=True).content, b'')
        self.stage.name = 'Big Stage'
        self.stage.save()
        html = self.console.get(bar, secure=True).content.decode()
        self.assertIn('1 unpublished change', html)
        self.assertIn('data-confirm="Publish 1 change to 0 phones?"', html)
        page = self.console.get(reverse('console:event_changes', args=[self.event.pk]), secure=True)
        self.assertContains(page, 'Big Stage')
        self.console.post(reverse('console:event_publish_changes', args=[self.event.pk]), secure=True)
        self.assertEqual(drafts.changes(self.event), [])
        from agent import registry
        writes = registry.registry()['writes']
        self.assertTrue(writes['publish_changes'].confirm and writes['discard_changes'].confirm)

    def test_autosave_edits_are_one_undo_step_and_one_history_entry(self):
        url = reverse('console:location_edit', args=[self.event.pk, self.stage.pk])
        page = self.console.get(url, secure=True).content.decode()
        self.assertIn('data-autosave-edit', page)
        base = {'name': 'Main Stage', 'category': '', 'snippet': '', 'description': '', 'address': '',
                'latitude': '', 'longitude': '', 'tags_text': '', 'image_url': '', 'website': ''}
        for name in ('M', 'Ma', 'Market'):
            r = self.console.post(url, {**base, 'name': name}, secure=True, HTTP_X_ATTND_COMPONENT='location_form',
                                  HTTP_ACCEPT='application/json')
            self.assertTrue(r.json().get('saved'), r.content[:300])
        from console.models import UndoEntry
        self.assertEqual(UndoEntry.objects.filter(user=self.owner).count(), 1)
        entry = UndoEntry.objects.get(user=self.owner)
        self.assertEqual(entry.operations[0]['before']['fields']['name'], 'Main Stage')
        self.assertEqual(entry.operations[0]['after']['name'], 'Market')
        self.assertEqual(LogEntry.objects.filter(object_id=str(self.stage.pk)).count(), 1)
        invalid = self.console.post(url, {**base, 'name': ''}, secure=True, HTTP_X_ATTND_COMPONENT='location_form',
                                    HTTP_ACCEPT='application/json').json()
        self.assertTrue(invalid.get('invalid'))
        self.assertEqual(Location.objects.get(pk=self.stage.pk).name, 'Market')


class HomeStaysTests(DraftTestCase):
    """P0 #3 (App Clip's Home came and went): every path gives an app that draws sections the same tabs, Home first
    and the default; the ETag carries the catalogue version, so a 304 never answers for a body without Home."""

    def test_old_published_copy_still_leads_with_home(self):
        pb = self.event.published
        for key, body in pb.bundles.items():  # a copy published before Home was always on
            body['display']['tabs'] = ['map', 'schedule']
            body['display']['default_tab'] = 'map'
        pb.save(update_fields=['bundles'])
        for v in ('1', '2'):
            display = self.bundle(HTTP_X_ATTND_SECTIONS=v).json()['display']
            self.assertEqual((display['tabs'][0], display['default_tab']), ('home', 'home'), v)
        self.assertNotIn('home', self.bundle().json()['display']['tabs'])  # apps without sections never see it

    def test_a_304_only_for_the_same_catalogue_version(self):
        plain = self.bundle()
        with_sections = self.bundle(HTTP_X_ATTND_SECTIONS='2')
        self.assertNotEqual(plain['ETag'], with_sections['ETag'])
        # The offline copy from a request without the header: its ETag never gets a 304 with the header.
        self.assertEqual(self.bundle(HTTP_X_ATTND_SECTIONS='2', HTTP_IF_NONE_MATCH=plain['ETag']).status_code, 200)
        again = self.bundle(HTTP_X_ATTND_SECTIONS='2', HTTP_IF_NONE_MATCH=with_sections['ETag'])
        self.assertEqual(again.status_code, 304)
        self.assertEqual(again['ETag'], with_sections['ETag'])
        self.assertEqual(with_sections.json()['display']['tabs'][0], 'home')

    def test_saved_tab_lists_lose_home(self):
        import importlib

        from django.apps import apps
        migration = importlib.import_module('events.migrations.0052_home_always_on')
        Event.objects.filter(pk=self.event.pk).update(display_config={'tabs': ['schedule', 'home', 'map'],
                                                                      'default_tab': 'home'})
        migration.normalise(apps, None)
        self.event.refresh_from_db()
        self.assertEqual(self.event.display_config, {'tabs': ['schedule', 'map']})
        drafts.publish(self.event, push=False)
        display = self.bundle(HTTP_X_ATTND_SECTIONS='2').json()['display']
        self.assertEqual((display['tabs'], display['default_tab']), (['home', 'schedule', 'map'], 'home'))
