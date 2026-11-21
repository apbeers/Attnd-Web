"""API-024 screen layouts: the automatic layout per preset and per data present, the validation of organizer layouts,
the catalogue version filter (X-Attnd-Sections), the ETag per catalogue version and the Home tab."""

from datetime import timedelta

from django.contrib.auth.models import User
from django.core.cache import caches
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from events import layout as L
from events.display import resolve_display
from events.models import Event, Location, ScheduledItem

AUTH = {'HTTP_AUTHORIZATION': 'Bearer layout-key'}


def types(sections):
    return [s['type'] for s in sections]


class CatalogueTests(SimpleTestCase):
    def test_defaults_are_valid_and_match_the_apps(self):
        for screen in L.SCREENS:
            self.assertEqual(L.clean_sections(L.DEFAULTS[screen], screen), L.DEFAULTS[screen], screen)
        # The same lists as ScreenLayout.defaults in the iOS app (today's screens).
        self.assertEqual(types(L.DEFAULTS['item_detail']),
                         ['header', 'about', 'performers', 'good_to_know', 'links', 'place_link', 'ask_about'])
        self.assertEqual(types(L.DEFAULTS['place_detail']),
                         ['header', 'walk_map', 'ask_about', 'about', 'hours', 'good_to_know', 'menu', 'links',
                          'at_this_location'])
        self.assertEqual(types(L.DEFAULTS['map_overlay']), ['now_playing', 'selected_place'])
        for preset, home in L.HOME_BY_PRESET.items():
            self.assertEqual(L.clean_sections(home, 'home'), home, preset)

    def test_every_need_is_a_known_fact_or_state(self):
        for screen, types_ in L.CATALOGUE.items():
            for name, spec in types_.items():
                for need in spec['needs']:
                    self.assertTrue(need in L.FACTS or need in L.STATES, f'{screen}.{name}: {need}')
                self.assertEqual(spec['since'], 1)

    def test_clean_section(self):
        self.assertEqual(L.clean_section({'type': ' HEADER ', 'show': ['tags', 'nope', 'tags']}, 'item_detail'),
                         {'type': 'header', 'show': ['tags']})
        self.assertIsNone(L.clean_section({'type': 'menu'}, 'item_detail'), 'menu belongs to places')
        self.assertIsNone(L.clean_section({'type': 'hologram'}, 'home'))
        self.assertIsNone(L.clean_section('header', 'home'))
        s = L.clean_section({'type': 'faq', 'title': '  Big   questions ', 'limit': 99, 'when': 'during'}, 'home')
        self.assertEqual(s, {'type': 'faq', 'title': 'Big questions', 'limit': 20, 'when': ['during']})
        self.assertEqual(len(L.clean_section({'type': 'faq', 'title': 'x' * 90}, 'home')['title']), L.MAX_TITLE)
        self.assertNotIn('limit', L.clean_section({'type': 'faq', 'limit': True}, 'home'))
        self.assertEqual(L.clean_section({'type': 'faq', 'when': ['not:after_end', 'moon', 'has:secret', 'day:2026-11-14',
                                                                   'has:context.faq', 'day:soon']}, 'home')['when'],
                         ['not:after_end', 'day:2026-11-14', 'has:context.faq'])

    def test_clean_config(self):
        raw = {'screens': {
            'home': {'sections': [{'type': 'faq'}, {'type': 'faq'}, {'type': 'zzz'}] + [{'type': t} for t in L.CATALOGUE['home']]},
            'place_detail': {'sections': [{'type': 'menu'}], 'overrides': [
                {'match': 'category: Food  Trucks', 'sections': [{'type': 'hours'}]},
                {'match': 'TAG:BBQ', 'sections': [{'type': 'menu'}]},
                {'match': 'location:5', 'sections': [{'type': 'menu'}]},
                {'match': 'tag:bbq', 'sections': [{'type': 'hours'}]},
                {'match': 'tag:empty', 'sections': [{'type': 'zzz'}]}]},
            'home_alone': {'sections': [{'type': 'header'}]},
            'event_info': {'sections': []},
            'map_overlay': {'sections': [{'type': 'now_playing'}], 'overrides': [{'match': 'tag:x', 'sections': [{'type': 'now_playing'}]}]},
        }}
        c = L.clean_config(raw)['screens']
        self.assertEqual(set(c), {'home', 'place_detail', 'map_overlay'})
        self.assertEqual(types(c['home']['sections'])[0], 'faq')
        self.assertEqual(len(c['home']['sections']), len(L.CATALOGUE['home']))
        self.assertEqual([o['match'] for o in c['place_detail']['overrides']], ['category:Food Trucks', 'tag:bbq'])
        self.assertNotIn('overrides', c['map_overlay'], 'overrides only on the detail screens')
        self.assertEqual(L.clean_config(None), {})
        self.assertEqual(L.clean_config({'screens': 'x'}), {})


class AutomaticLayoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('o', 'o@example.com', 'pw')
        start = timezone.now() + timedelta(days=1)
        self.event = Event.objects.create(name='Fair', event_code='FAIR', user=self.user, start_time=start,
                                          end_time=start + timedelta(hours=8), timezone='America/Chicago',
                                          status='published')
        self.stage = Location.objects.create(event=self.event, user=self.user, name='Stage', category='Stage',
                                             latitude=32.75, longitude=-97.33)
        self.start = start

    def lists(self):
        return list(self.event.location_set.all()), list(self.event.scheduleditem_set.all())

    def auto(self, preset='standard', website=None):
        locs, items = self.lists()
        return L.automatic(self.event, locs, items, preset, website)

    def test_sections_without_data_are_left_out(self):
        a = self.auto()
        self.assertEqual(types(a['item_detail']['sections']), ['header', 'place_link', 'ask_about'])
        self.assertEqual(types(a['place_detail']['sections']), ['header', 'walk_map', 'ask_about'])
        self.assertEqual(types(a['event_info']['sections']), ['header'])
        self.assertEqual(types(a['options_event']['sections']), ['header'])
        self.assertNotIn('overrides', a['place_detail'])

    def test_data_turns_sections_on(self):
        self.event.description = 'A fair'
        self.event.context = {'faq': [{'q': 'Q?', 'a': 'A.'}], 'getting_there': 'Bus 1'}
        self.stage.context = {'hours': '9–5'}
        self.stage.save()
        ScheduledItem.objects.create(event=self.event, user=self.user, location=self.stage, name='Band',
                                     start_time=self.start, context={'price': '$5'})
        a = self.auto(website='https://sites.example/fair/')
        self.assertEqual(types(a['item_detail']['sections']), ['header', 'good_to_know', 'place_link', 'ask_about'])
        self.assertEqual(types(a['place_detail']['sections']), ['header', 'walk_map', 'ask_about', 'hours', 'at_this_location'])
        self.assertEqual(types(a['event_info']['sections']), ['header', 'about', 'faq', 'getting_there'])
        self.assertEqual(types(a['options_event']['sections']), ['header', 'website', 'event_info_link'])
        self.assertIn('website', types(a['home']['sections']))

    def test_food_places_with_menus_put_the_menu_first(self):
        Location.objects.create(event=self.event, user=self.user, name='Taco Row', category='Food Trucks',
                                context={'menu': [{'name': 'Taco', 'price': '$4'}], 'hours': '11–9'})
        Location.objects.create(event=self.event, user=self.user, name='Bar', category='Drinks')  # no menu
        a = self.auto()
        overrides = a['place_detail']['overrides']
        self.assertEqual([o['match'] for o in overrides], ['category:Food Trucks'])
        self.assertEqual(types(overrides[0]['sections'])[:3], ['header', 'menu', 'hours'])

    def test_tags_with_performers_put_the_performers_first(self):
        for i, tags in enumerate((['live music', 'rock'], ['live music'], ['comedy'])):
            ScheduledItem.objects.create(event=self.event, user=self.user, name=f'Act {i}', start_time=self.start,
                                         description='Loud', tags=tags,
                                         context={'performers': [{'name': f'P{i}'}]} if i < 2 else {})
        a = self.auto()
        overrides = a['item_detail']['overrides']
        self.assertEqual([o['match'] for o in overrides], ['tag:live music', 'tag:rock'])
        self.assertEqual(types(overrides[0]['sections'])[:3], ['header', 'performers', 'about'])
        self.assertEqual(types(a['item_detail']['sections'])[:3], ['header', 'about', 'performers'])

    def test_home_per_preset(self):
        ScheduledItem.objects.create(event=self.event, user=self.user, location=self.stage, name='Band',
                                     start_time=self.start)
        self.assertEqual(types(self.auto('festival')['home']['sections']),
                         ['header', 'urgent', 'countdown', 'plan_today', 'liked', 'whats_on', 'map'])
        self.assertEqual(types(self.auto('standard')['home']['sections']),
                         ['header', 'urgent', 'countdown', 'plan_today', 'liked', 'map', 'whats_on',
                          'places'])
        intimate = self.auto('intimate')['home']['sections']
        self.assertEqual(types(intimate), ['header', 'urgent', 'countdown', 'whats_on', 'places'])
        self.assertEqual(intimate[3]['title'], 'Agenda')

    def test_organizer_screens_win_and_the_catalogue_filter(self):
        locs, items = self.lists()
        self.assertIsNone(L.resolve_layout(self.event, locs, items, 0))
        self.event.layout_config = L.clean_config({'screens': {'place_detail': {'sections': [
            {'type': 'at_this_location'}, {'type': 'header', 'show': ['image']}]}}})
        out = L.resolve_layout(self.event, locs, items, 7)
        self.assertEqual(out['catalogue'], L.CATALOGUE_VERSION)
        self.assertEqual(out['source']['place_detail'], 'organizer')
        self.assertEqual(out['source']['home'], 'automatic')
        self.assertEqual(out['screens']['place_detail']['sections'],
                         [{'type': 'at_this_location'}, {'type': 'header', 'show': ['image']}])
        self.assertEqual(set(out['screens']), set(L.SCREENS))
        # A type from a newer catalogue never reaches an older app.
        original = L.CATALOGUE['place_detail']['at_this_location']['since']
        L.CATALOGUE['place_detail']['at_this_location']['since'] = 2
        try:
            out = L.resolve_layout(self.event, locs, items, 1)
            self.assertEqual(types(out['screens']['place_detail']['sections']), ['header'])
        finally:
            L.CATALOGUE['place_detail']['at_this_location']['since'] = original

    def test_home_tab_only_for_apps_that_draw_layouts(self):
        self.event.display_config = {'tabs': ['home', 'map', 'locations', 'schedule', 'flyer', 'notifications'],
                                     'default_tab': 'home'}
        old = resolve_display(self.event)
        self.assertNotIn('home', old['tabs'])
        self.assertNotEqual(old['default_tab'], 'home')
        new = resolve_display(self.event, sections=1)
        self.assertEqual(new['tabs'], ['home', 'map', 'locations', 'schedule', 'notifications'])
        self.assertEqual(new['default_tab'], 'home')
        self.event.display_config = {'tabs': ['home', 'map', 'locations', 'schedule', 'notifications']}
        self.event.flyer = 'flyers/x.png'
        self.assertEqual(len(resolve_display(self.event, sections=1)['tabs']), 5)

    def test_automatic_tabs_lead_with_home_for_apps_that_draw_it(self):
        """Batch 5 (owner, Nov 21, 2026): Home first and the default tab for apps sending X-Attnd-Sections; never
        for older apps."""
        for preset in ('intimate', 'standard', 'festival'):
            self.event.display_config = {'preset': preset}
            cfg = resolve_display(self.event, sections=1)
            self.assertEqual((cfg['tabs'][0], cfg['default_tab']), ('home', 'home'))
            old = resolve_display(self.event, sections=0)
            self.assertNotIn('home', old['tabs'])
            self.assertNotEqual(old['default_tab'], 'home')
        self.event.display_config = {'tabs': ['schedule', 'map'], 'default_tab': 'schedule'}
        self.assertEqual(resolve_display(self.event, sections=1)['tabs'], ['schedule', 'map'])  # the organizer's own


class EmptyTabsTests(TestCase):
    """No tab with nothing to show (owner, Nov 21, 2026): ended items still count, hidden ones don't."""

    def setUp(self):
        from events.models import Location, Notification, ScheduledItem
        user = User.objects.create_user('e', 'e@example.com', 'pw')
        self.event = Event.objects.create(name='Walk', event_code='WALK1', user=user, start_time=timezone.now(),
                                          status=Event.STATUS_PUBLISHED)
        self.L, self.N, self.S = Location, Notification, ScheduledItem

    def tabs(self, sections=1):
        return resolve_display(self.event, sections=sections)['tabs']

    def test_nothing_yet_is_home_only(self):
        self.assertEqual(self.tabs(), ['home'])
        self.assertEqual(self.tabs(0), ['map', 'notifications'])  # older apps always get something

    def test_each_tab_needs_its_content(self):
        place = self.L.objects.create(event=self.event, name='Gallery')  # no coordinates
        self.assertEqual(self.tabs(), ['home', 'locations'])
        place.latitude, place.longitude = 32.7, -97.3
        place.save()
        self.assertEqual(self.tabs(), ['home', 'map', 'locations'])
        past = timezone.now() - timedelta(days=2)
        item = self.S.objects.create(event=self.event, name='Opening', start_time=past, end_time=past + timedelta(hours=2),
                                     location=place)
        self.assertIn('schedule', self.tabs())  # ended items still count
        item.hidden = True
        item.save()
        self.assertNotIn('schedule', self.tabs())
        item.hidden = False
        item.save()
        place.hidden = True
        place.save()
        self.assertNotIn('schedule', self.tabs())  # its only item sits at a hidden place
        self.N.objects.create(event=self.event, title='Hi', body='x', send_time=timezone.now())
        self.assertNotIn('notifications', self.tabs())  # scheduled, not sent
        self.N.objects.update(sent=True)
        self.assertIn('notifications', self.tabs())


@override_settings(API_KEY='layout-key')
class BundleLayoutTests(TestCase):
    def setUp(self):
        caches['local'].clear()
        user = User.objects.create_user('b', 'b@example.com', 'pw')
        start = timezone.now() + timedelta(hours=3)
        self.event = Event.objects.create(name='Fair', event_code='FAIR', user=user, start_time=start,
                                          end_time=start + timedelta(hours=8), timezone='America/Chicago',
                                          status='published', public=True,
                                          display_config={'tabs': ['home', 'map', 'notifications']})
        Location.objects.create(event=self.event, user=user, name='Stage', latitude=32.75, longitude=-97.33)
        self.event.refresh_from_db()  # the location bumped content_version
        self.url = f'/api/v2/events/{self.event.id}/bundle/'
        self.client = Client()

    def get(self, sections=None, etag=None):
        headers = dict(AUTH)
        if sections is not None:
            headers['HTTP_X_ATTND_SECTIONS'] = sections
        if etag:
            headers['HTTP_IF_NONE_MATCH'] = etag
        return self.client.get(self.url, secure=True, **headers)

    def test_older_apps_get_no_layout_and_no_home_tab(self):
        resp = self.get()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNone(data['layout'])
        self.assertEqual(data['display']['tabs'], ['map', 'notifications'])
        self.assertEqual(resp['ETag'], f'"e{self.event.id}-v{self.event.content_version}"')
        for junk in ('', 'abc', '-1'):
            self.assertIsNone(self.get(junk).json()['layout'])

    def test_apps_with_the_header_get_the_layout(self):
        resp = self.get('1')
        data = resp.json()
        self.assertEqual(data['layout']['catalogue'], 1)
        self.assertIn('home', data['layout']['screens'])
        self.assertEqual(data['display']['tabs'], ['home', 'map', 'notifications'])
        self.assertEqual(resp['ETag'], f'"e{self.event.id}-v{self.event.content_version}-s1"')
        # A newer app than the server: the server's catalogue.
        self.assertEqual(self.get('5').json()['layout']['catalogue'], L.CATALOGUE_VERSION)

    def test_etag_per_catalogue_version(self):
        etag = self.get('1')['ETag']
        self.assertEqual(self.get('1', etag).status_code, 304)
        # The same app before the update (no header) or a newer catalogue: a full body, not a 304.
        self.assertEqual(self.get(None, etag).status_code, 200)
        old = self.get()['ETag']
        self.assertEqual(self.get('1', old).status_code, 200)
        self.assertEqual(self.get(None, old).status_code, 304)

    def test_a_layout_edit_bumps_the_version(self):
        version = self.event.content_version
        self.event.layout_config = {'screens': {'home': {'sections': [{'type': 'header'}]}}}
        self.event.save(update_fields=['layout_config'])
        self.event.refresh_from_db()
        self.assertGreater(self.event.content_version, version)
        data = self.get('1').json()
        self.assertEqual(data['layout']['screens']['home']['sections'], [{'type': 'header'}])
        self.assertEqual(data['layout']['source']['home'], 'organizer')
