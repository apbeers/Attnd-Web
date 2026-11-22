"""The Screens editor (API-024): the component on App layout, saving an order through the view (form and JSON),
parts, titles, limits, orders for a category or tag, reset, Undo, permissions, the preview and Ask Attnd's actions."""

import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from agent import registry
from console.components import get, load
from console.models import UndoEntry
from events import layout as L
from events.models import Event, Location, ScheduledItem


@override_settings(SECURE_SSL_REDIRECT=False)
class ScreensEditorTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user('owner', 'owner@example.com', 'pw')
        self.stranger = User.objects.create_user('x', 'x@example.com', 'pw')
        start = timezone.now() + timedelta(days=2)
        self.event = Event.objects.create(name='Prairie Fest', event_code='PRAIRIE', user=self.owner,
                                          timezone='America/Chicago', start_time=start, end_time=start + timedelta(hours=8),
                                          status=Event.STATUS_PUBLISHED, description='Music and tacos',
                                          context={'faq': [{'q': 'Parking?', 'a': 'Lot B'}]})
        self.taco = Location.objects.create(event=self.event, name='Taco Row', category='Food',
                                            latitude=32.75, longitude=-97.33,
                                            context={'menu': [{'name': 'Taco', 'price': '$4'}], 'hours': '11–9'})
        ScheduledItem.objects.create(event=self.event, name='Band', start_time=start, location=self.taco,
                                     tags=['live music'], context={'performers': [{'name': 'The Band'}]})
        self.client = Client()
        self.client.force_login(self.owner)
        self.url = reverse('console:event_screens', args=[self.event.pk])

    def post(self, data, json_reply=True, client=None):
        headers = {'HTTP_ACCEPT': 'application/json'} if json_reply else {}
        return (client or self.client).post(self.url, data, secure=True, **headers)

    def stored(self, screen):
        self.event.refresh_from_db()
        return (self.event.layout_config.get('screens') or {}).get(screen)

    def test_page_draws_the_editor_and_preview(self):
        html = self.client.get(reverse('console:display', args=[self.event.pk]), secure=True).content.decode()
        self.assertIn('component--app_screens', html)
        self.assertIn('data-section-sortable', html)
        for screen in L.SCREENS:
            self.assertIn(f'screen-{screen}', html)
        self.assertIn('pv-section--menu', html, 'the food place leads with its menu in the preview')
        self.assertIn('Places in “Food”', html)
        self.assertIn('The Home tab is off', html)

    def test_save_from_the_form(self):
        version = self.event.content_version
        resp = self.post({'screen': 'event_info', 'order': 'faq,header,about', 'on': ['faq', 'header', 'about'],
                          'show_header': [], 'title_faq': 'Questions', 'limit_faq': '2'})
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['saved'])
        self.assertIn('pv-section--faq', data['preview'])
        self.assertEqual(self.stored('event_info')['sections'],
                         [{'type': 'faq', 'title': 'Questions', 'limit': 2}, {'type': 'header'}, {'type': 'about'}])
        self.assertGreater(self.event.content_version, version)
        self.assertTrue(UndoEntry.objects.filter(event=self.event).exists())
        self.assertTrue(resp.has_header('X-Attnd-Undo'))

    def test_parts_and_unknowns(self):
        self.post({'screen': 'place_detail', 'order': 'menu,hologram,header', 'on': ['menu', 'header', 'hologram'],
                   'show_header': ['image', 'category', 'nope']})
        self.assertEqual(self.stored('place_detail')['sections'][:2],
                         [{'type': 'menu'}, {'type': 'header', 'show': ['image', 'category']}])
        # The automatic per-category order came along and stays editable.
        self.assertEqual([o['match'] for o in self.stored('place_detail')['overrides']], ['category:Food'])

    def test_json_mode_and_errors(self):
        resp = self.post({'screen': 'home', 'mode': 'json', 'sections': json.dumps([{'type': 'countdown'}, {'type': 'faq', 'limit': 3}])})
        self.assertTrue(resp.json()['saved'])
        self.assertEqual(L.clean_config(Event.objects.get(pk=self.event.pk).layout_config)['screens']['home']['sections'],
                         [{'type': 'countdown'}, {'type': 'faq', 'limit': 3}])
        self.assertEqual(self.post({'screen': 'home', 'mode': 'json', 'sections': 'nope'}).status_code, 400)
        self.assertEqual(self.post({'screen': 'home', 'mode': 'json', 'sections': '[{"type": "menu"}]'}).status_code, 400)
        self.assertEqual(self.post({'screen': 'tickets', 'mode': 'json', 'sections': '[]'}).status_code, 400)
        self.assertEqual(self.post({'screen': 'home', 'match': 'tag:x', 'mode': 'json', 'sections': '[]'}).status_code, 400)

    def test_category_and_tag_orders(self):
        # Items tagged "live music" have performers: the automatic layout already leads with them.
        self.assertEqual(self.post({'screen': 'item_detail', 'action': 'add_override', 'match': 'tag:Live Music'}).status_code, 400)
        resp = self.post({'screen': 'item_detail', 'action': 'add_override', 'match': 'category:Food'})
        self.assertTrue(resp.json()['saved'])
        self.assertEqual([o['match'] for o in self.stored('item_detail')['overrides']], ['tag:live music', 'category:Food'])
        self.post({'screen': 'item_detail', 'match': 'category:Food', 'order': 'place_link,header', 'on': ['place_link', 'header']})
        self.assertEqual(self.stored('item_detail')['overrides'][1]['sections'], [{'type': 'place_link'}, {'type': 'header'}])
        self.post({'screen': 'item_detail', 'action': 'remove_override', 'match': 'tag:live music'})
        self.assertEqual([o['match'] for o in self.stored('item_detail')['overrides']], ['category:Food'])
        self.assertEqual(self.post({'screen': 'item_detail', 'action': 'remove_override', 'match': 'tag:nope'}).status_code, 400)

    def test_reset(self):
        self.post({'screen': 'home', 'mode': 'json', 'sections': '[{"type": "header"}]'})
        self.assertIsNotNone(self.stored('home'))
        resp = self.post({'screen': 'home', 'action': 'reset'}, json_reply=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIsNone(self.stored('home'))
        self.assertEqual(self.post({'screen': 'home', 'action': 'reset'}).status_code, 400)

    def test_undo_restores_the_layout(self):
        resp = self.post({'screen': 'home', 'mode': 'json', 'sections': '[{"type": "header"}]'})
        undo = resp['X-Attnd-Undo']
        r = self.client.post(reverse('console:undo', args=[undo]), secure=True, HTTP_ACCEPT='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNone(self.stored('home'))

    def test_only_the_team(self):
        other = Client()
        other.force_login(self.stranger)
        self.assertEqual(self.post({'screen': 'home', 'action': 'reset'}, client=other).status_code, 404)
        self.assertEqual(self.client.get(self.url, secure=True).status_code, 302)

    def test_the_bundle_follows(self):
        self.post({'screen': 'place_detail', 'mode': 'json', 'sections': '[{"type": "menu"}, {"type": "header"}]'})
        resp = Client().get(f'/api/v2/events/{self.event.pk}/bundle/', secure=True, HTTP_X_ATTND_SECTIONS='1',
                            HTTP_AUTHORIZATION='Bearer k')
        if resp.status_code == 401:  # no static key in this settings module
            return
        self.assertEqual(resp.json()['layout']['screens']['place_detail']['sections'][0], {'type': 'menu'})

    def test_component_data_and_actions(self):
        data = load(self.owner, 'app_screens', {'event_id': self.event.pk})
        self.assertEqual([s['screen'] for s in data['screens']], L.SCREENS)
        self.post({'screen': 'home', 'mode': 'json', 'sections': '[{"type": "header"}]'})
        data = load(self.owner, 'app_screens', {'event_id': self.event.pk})
        home = next(s for s in data['screens'] if s['screen'] == 'home')
        self.assertEqual([a['name'] for a in home['actions']], ['reset_screen_layout'])
        self.assertEqual(home['actions'][0]['fields'], {'action': 'reset', 'screen': 'home'})
        self.assertTrue(get('app_screens').page({'event_id': self.event.pk}).endswith('#screens'))

    def test_ask_attnd_actions(self):
        writes = registry.registry()['writes']
        for name in ('update_screen_layout', 'reset_screen_layout', 'add_screen_override', 'remove_screen_override'):
            self.assertIn(name, writes)
            self.assertEqual(writes[name].url_name, 'console:event_screens')
        self.assertEqual(writes['update_screen_layout'].fixed, {'mode': 'json'})
        self.assertIn('place_detail', writes['update_screen_layout'].description)
