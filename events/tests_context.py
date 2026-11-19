"""API-018: rich event context for the apps' on-device assistant (normaliser, bundle, AI diff, console)."""
import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from console.forms.assistant_context import parse_faq, to_text
from console.testing import TwoFactorClient
from events import content
from events import context as ctx
from events.models import Event, Location, ScheduledItem
from scrape import diff
from scrape.models import Change


class NormaliserTests(TestCase):
    def test_trims_drops_empties_and_unknown_keys(self):
        raw = {'about': '  Hello   world \n\n\n\nSecond  para ', 'faq': [{'q': ' Parking? ', 'a': ' $10 '},
                                                                       {'q': 'No answer', 'a': '  '}, 'junk'],
               'policies': '', 'links': [], 'secret': 'organizer only', 'amenities': [{'kind': 'Water', 'name': ''}]}
        self.assertEqual(ctx.normalize(ctx.EVENT, raw), {
            'about': 'Hello world\n\nSecond para', 'faq': [{'q': 'Parking?', 'a': '$10'}]})

    def test_empty_is_empty_dict_in_storage_and_null_in_bundle(self):
        self.assertEqual(ctx.normalize(ctx.ITEM, {'about': ' ', 'performers': [{'name': ''}]}), {})
        self.assertIsNone(ctx.for_bundle(ctx.ITEM, {'about': ' '}))
        self.assertIsNone(ctx.for_bundle(ctx.LOCATION, None))
        self.assertIsNone(ctx.for_bundle(ctx.EVENT, 'not a dict'))

    def test_long_text_is_cut_at_a_word_boundary(self):
        text = ' '.join(['word'] * 2000)
        out = ctx.normalize(ctx.EVENT, {'weather_plan': text})['weather_plan']
        self.assertLessEqual(len(out), 600)
        self.assertTrue(out.endswith('word…'), out[-20:])
        self.assertEqual(ctx.cut('short', 10), 'short')

    def test_limits_and_lists(self):
        out = ctx.normalize(ctx.EVENT, {'faq': [{'q': f'Q{i}', 'a': 'A'} for i in range(50)],
                                        'amenities': [{'kind': 'x', 'name': f'N{i}'} for i in range(70)]})
        self.assertEqual(len(out['faq']), 40)
        self.assertEqual(len(out['amenities']), 60)
        self.assertEqual(out['amenities'][0], {'kind': 'other', 'name': 'N0', 'location_id': None})
        self.assertEqual(ctx.amenity_kind('First Aid'), 'first_aid')
        self.assertEqual(ctx.amenity_kind('accessible-entrance'), 'accessible_entrance')

    def test_links_only_absolute_https(self):
        links = [{'title': 'A', 'url': 'https://example.com/a'}, {'title': 'B', 'url': 'http://example.com'},
                 {'title': 'C', 'url': '/relative'}, {'title': 'D', 'url': 'javascript:alert(1)'},
                 {'title': '', 'url': 'https://example.org/x'}, {'title': 'Dup', 'url': 'https://example.com/a'}]
        out = ctx.normalize(ctx.LOCATION, {'links': links})['links']
        self.assertEqual(out, [{'title': 'A', 'url': 'https://example.com/a'},
                               {'title': 'example.org', 'url': 'https://example.org/x'}])
        self.assertEqual(len(ctx.normalize(ctx.EVENT, {'links': [{'url': f'https://e.com/{i}'} for i in range(20)]})
                             ['links']), 10)
        self.assertEqual(len(ctx.normalize(ctx.ITEM, {'links': [{'url': f'https://e.com/{i}'} for i in range(20)]})
                             ['links']), 5)

    def test_performers_and_menu(self):
        out = ctx.normalize(ctx.ITEM, {'performers': [
            {'name': 'Band', 'bio': 'x' * 700, 'genres': ['Rock', 'rock', ' Blues ', 'a', 'b', 'c', 'd']}]})
        performer = out['performers'][0]
        self.assertLessEqual(len(performer['bio']), 600)
        self.assertEqual(performer['genres'], ['rock', 'blues', 'a', 'b', 'c'])
        menu = ctx.normalize(ctx.LOCATION, {'menu': [{'name': 'Taco', 'price': '$4', 'note': ''}]})['menu']
        self.assertEqual(menu, [{'name': 'Taco', 'price': '$4'}])

    def test_key_order_follows_the_contract(self):
        out = ctx.normalize(ctx.ITEM, {'links': [{'title': 'L', 'url': 'https://e.com'}], 'price': '$5',
                                       'about': 'A'})
        self.assertEqual(list(out), ['about', 'price', 'links'])

    def test_models_normalise_on_save(self):
        user = User.objects.create_user('o', 'o@example.com', 'pw')
        event = Event.objects.create(name='E', event_code='CTX1', start_time=timezone.now(), user=user,
                                     context={'about': '  A  ', 'nope': 1})
        self.assertEqual(Event.objects.get(pk=event.pk).context, {'about': 'A'})
        loc = Location.objects.create(event=event, name='L', context={'hours': ' 9–5 '})
        self.assertEqual(Location.objects.get(pk=loc.pk).context, {'hours': '9–5'})

    def test_budget_under_the_cap_changes_nothing(self):
        items = [{'context': {'about': 'x'}}]
        ctx.apply_budget(None, [], items)
        self.assertEqual(items, [{'context': {'about': 'x'}}])

    def test_budget_drops_item_then_location_about_last_first(self):
        items = [{'context': {'about': 'x' * 1000, 'price': '$1'}} for _ in range(3)]
        locations = [{'context': {'about': 'y' * 1000}} for _ in range(2)]
        with self.assertLogs('events.context', 'WARNING'):
            total = ctx.apply_budget({'about': 'e'}, locations, items, budget=1500)
        self.assertLessEqual(total, 1500)
        self.assertTrue(all(i['context'] == {'price': '$1'} for i in items))
        self.assertIsNone(locations[1]['context'])  # nothing left: null
        self.assertIn('about', locations[0]['context'])

    def test_merge_keeps_what_the_ai_left_out(self):
        old = {'about': 'Old', 'hours': '9–5', 'menu': [{'name': 'Taco', 'price': '$4', 'note': 'veg'}]}
        new = {'about': '', 'hours': '10–6', 'menu': [{'name': 'taco', 'price': '', 'note': ''},
                                                      {'name': 'Elote', 'price': '$6', 'note': ''}]}
        self.assertEqual(ctx.merge(ctx.LOCATION, old, new), {
            'about': 'Old', 'menu': [{'price': '$4', 'note': 'veg', 'name': 'taco'}, {'name': 'Elote', 'price': '$6'}],
            'hours': '10–6'})

    def test_describe_changes(self):
        text = ctx.describe_changes(ctx.EVENT, {'faq': [{'q': 'a', 'a': 'b'}], 'about': 'x'},
                                    {'faq': [{'q': 'a', 'a': 'b'}, {'q': 'c', 'a': 'd'}], 'about': 'y',
                                     'policies': 'p'})
        self.assertEqual(text, 'About changed · FAQ +1 · Policies added')


@override_settings(API_KEY='ctx-key')
class BundleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('o', 'o@example.com', 'pw')
        self.event = Event.objects.create(name='Fair', event_code='CTXFAIR', start_time=timezone.now(),
                                          user=self.user, timezone='America/Chicago')
        self.loc = Location.objects.create(event=self.event, name='Tacos', context={
            'menu': [{'name': 'Taco', 'price': '$4'}], 'links': [{'title': 'Site', 'url': 'http://insecure'}]})
        self.hidden = Location.objects.create(event=self.event, name='Back office', hidden=True)
        self.item = ScheduledItem.objects.create(event=self.event, name='Band', start_time=timezone.now())
        self.event.context = {'about': 'All about it', 'amenities': [
            {'kind': 'restrooms', 'name': 'By Tacos', 'location_id': self.loc.id},
            {'kind': 'water', 'name': 'Hidden', 'location_id': self.hidden.id}]}
        self.event.save()

    def get(self, path, **headers):
        return Client().get(path, secure=True, HTTP_AUTHORIZATION='Bearer ctx-key', **headers)

    def test_bundle_has_the_context(self):
        b = self.get(f'/api/v2/events/{self.event.id}/bundle/').json()
        self.assertEqual(b['event']['context'], {'about': 'All about it', 'amenities': [
            {'kind': 'restrooms', 'name': 'By Tacos', 'location_id': self.loc.id},
            {'kind': 'water', 'name': 'Hidden', 'location_id': None}]})  # hidden location → null
        self.assertEqual(b['locations'][0]['context'], {'menu': [{'name': 'Taco', 'price': '$4'}]})
        self.assertIsNone(b['schedule'][0]['context'])

    def test_lookup_has_no_context_key(self):
        ev = self.get('/api/v2/events/lookup/', QUERY_STRING='q=CTXFAIR').json()['event']
        self.assertNotIn('context', ev)

    def test_v1_is_unchanged(self):
        resp = self.get(f'/api/v1/events/{self.event.id}/locations/')
        if resp.status_code == 200:
            self.assertNotIn('context', json.dumps(resp.json()))

    def test_context_edit_bumps_the_version_and_the_etag(self):
        first = self.get(f'/api/v2/events/{self.event.id}/bundle/')
        etag = first['ETag']
        self.assertEqual(self.get(f'/api/v2/events/{self.event.id}/bundle/', HTTP_IF_NONE_MATCH=etag).status_code,
                         304)
        version = Event.objects.get(pk=self.event.pk).content_version
        self.item.context = {'price': '$5'}
        self.item.save()
        self.assertGreater(Event.objects.get(pk=self.event.pk).content_version, version)
        again = self.get(f'/api/v2/events/{self.event.id}/bundle/', HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()['schedule'][0]['context'], {'price': '$5'})

    def test_snapshot_restores_context(self):
        revision = content.save_revision(self.event, 'before')
        self.event.context = {'about': 'Changed'}
        self.event.save()
        self.item.context = {'price': '$9'}
        self.item.save()
        content.restore(revision)
        self.assertEqual(Event.objects.get(pk=self.event.pk).context['about'], 'All about it')
        self.assertEqual(ScheduledItem.objects.get(pk=self.item.pk).context, {})


def _extraction(**overrides):
    data = {
        'name': 'Fair', 'description': '', 'address': '', 'latitude': None, 'longitude': None,
        'timezone': 'America/Chicago', 'start_time': '2026-07-04T10:00:00', 'end_time': None,
        'context': {'about': 'A summer fair.', 'faq': [{'q': 'Parking?', 'a': '$10 lot'}],
                    'amenities': [{'kind': 'water', 'name': 'Water', 'location_name': 'Food Court', 'note': ''}],
                    'accessibility': '', 'policies': '', 'getting_there': '', 'weather_plan': '', 'links': []},
        'locations': [{'existing_id': None, 'name': 'Food Court', 'snippet': '', 'description': '', 'category': 'Food',
                       'website': None, 'image_url': None, 'address': '', 'latitude': None, 'longitude': None,
                       'tags': [], 'context': {'about': '', 'menu': [{'name': 'Corn dog', 'price': '$6', 'note': ''}],
                                               'hours': '', 'accessibility': '', 'age_limit': '', 'links': []}}],
        'schedule': [{'existing_id': None, 'name': 'Band', 'description': '', 'start_time': '2026-07-04T12:00:00',
                      'end_time': None, 'location_name': 'Food Court', 'tags': [],
                      'context': {'about': '', 'performers': [{'name': 'Band', 'bio': 'Loud.', 'genres': ['Rock']}],
                                  'age_limit': '', 'price': '', 'accessibility': '', 'links': []}}],
        'removed_location_ids': [], 'removed_item_ids': [], 'notes': '', 'tag_vocabulary': [],
    }
    data.update(overrides)
    return data


class DiffTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('o', 'o@example.com', 'pw')
        self.event, _ = diff.create_event_from_extraction(_extraction(), self.user, status=Event.STATUS_PUBLISHED,
                                                          public=True)
        self.food = self.event.location_set.get()
        self.band = self.event.scheduleditem_set.get()

    def refreshed(self, **overrides):
        data = _extraction(**overrides)
        data['locations'][0]['existing_id'] = self.food.id
        data['schedule'][0]['existing_id'] = self.band.id
        return data

    def test_import_stores_context_with_location_ids(self):
        self.assertEqual(self.event.context['amenities'],
                         [{'kind': 'water', 'name': 'Water', 'location_id': self.food.id}])
        self.assertEqual(self.event.context['faq'], [{'q': 'Parking?', 'a': '$10 lot'}])
        self.assertEqual(self.food.context, {'menu': [{'name': 'Corn dog', 'price': '$6'}]})
        self.assertEqual(self.band.context['performers'][0]['genres'], ['rock'])

    def test_same_context_no_changes_and_empty_never_blanks(self):
        self.assertEqual(diff.compute_changes(self.event, self.refreshed()), [])
        empty = self.refreshed(context={})
        empty['locations'][0]['context'] = {'about': '', 'menu': [], 'hours': '', 'accessibility': '',
                                            'age_limit': '', 'links': []}
        self.assertEqual(diff.compute_changes(self.event, empty), [])

    def test_context_change_is_one_minor_field_and_applies(self):
        data = self.refreshed()
        data['context']['faq'].append({'q': 'Pets?', 'a': 'Service animals only.'})
        data['locations'][0]['context']['menu'].append({'name': 'Lemonade', 'price': '$4', 'note': ''})
        changes = diff.compute_changes(self.event, data)
        self.assertEqual(sorted((c['entity'], list(c['data'])) for c in changes),
                         [('event', ['context']), ('location', ['context'])])
        self.assertTrue(all(c['minor'] for c in changes))
        cs = diff.save_changeset(self.event, None, changes)
        diff.apply_changeset(cs, diff.default_decisions(cs))
        self.event.refresh_from_db()
        self.assertEqual(len(self.event.context['faq']), 2)
        self.assertEqual(self.event.context['amenities'][0]['location_id'], self.food.id)
        self.assertEqual(len(Location.objects.get(pk=self.food.pk).context['menu']), 2)

    def test_locked_context_is_a_conflict(self):
        self.event.locked_fields = ['context']
        self.event.save()
        data = self.refreshed()
        data['context']['about'] = 'Rewritten'
        change = diff.compute_changes(self.event, data)[0]
        self.assertEqual(change['conflict_fields'], ['context'])
        cs = diff.save_changeset(self.event, None, [change])
        self.assertEqual(diff.default_decisions(cs), {})

    def test_amenity_on_a_new_location_resolves_when_applied(self):
        data = self.refreshed()
        data['locations'].append({**data['locations'][0], 'existing_id': None, 'name': 'First Aid Tent',
                                  'category': 'Services', 'context': {}})
        data['context']['amenities'].append({'kind': 'first_aid', 'name': 'Medics', 'location_name': 'First Aid Tent',
                                             'note': ''})
        cs = diff.save_changeset(self.event, None, diff.compute_changes(self.event, data))
        diff.apply_changeset(cs, diff.default_decisions(cs))
        self.event.refresh_from_db()
        tent = self.event.location_set.get(name='First Aid Tent')
        self.assertEqual(self.event.context['amenities'][1]['location_id'], tent.id)

    def test_review_page_shows_a_summary(self):
        self.client = TwoFactorClient()
        self.client.force_login(self.user)
        data = self.refreshed()
        data['schedule'][0]['context']['price'] = '$5'
        cs = diff.save_changeset(self.event, None, diff.compute_changes(self.event, data))
        page = self.client.get(reverse('console:changeset', args=[cs.pk]), secure=True).content.decode()
        self.assertIn('Details for the assistant', page)
        self.assertIn('Price added', page)
        self.assertNotIn('{&#x27;', page)  # no raw dict

    def test_current_state_sends_brief_contexts(self):
        state = diff.current_state(self.event)
        self.assertEqual(state['event']['context']['amenities'][0]['location_name'], 'Food Court')
        self.assertEqual(state['schedule'][0]['context']['performers'], [{'name': 'Band', 'genres': ['rock']}])

    def test_extraction_schema_is_strict(self):
        from scrape.service import EVENT_SCHEMA

        def check(schema, path='$'):
            if schema.get('type') == 'object':
                self.assertEqual(sorted(schema['required']), sorted(schema['properties']), path)
                self.assertIs(schema['additionalProperties'], False, path)
                for key, sub in schema['properties'].items():
                    check(sub, f'{path}.{key}')
            elif schema.get('type') == 'array':
                check(schema['items'], path + '[]')
        check(EVENT_SCHEMA)


class ConsoleEditTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('owner@example.com', 'owner@example.com', 'pw')
        self.event = Event.objects.create(name='Walk', event_code='CTXWALK', user=self.user, timezone='America/Chicago',
                                          start_time=timezone.now() + timedelta(days=1))
        self.loc = Location.objects.create(event=self.event, name='Gallery A')
        self.item = ScheduledItem.objects.create(event=self.event, name='Talk', start_time=self.event.start_time)
        self.client = TwoFactorClient()
        self.client.force_login(self.user)

    def url(self, name, *args):
        return reverse(f'console:{name}', args=[self.event.pk, *args])

    def event_post(self, **extra):
        start = timezone.localtime(self.event.start_time).strftime('%Y-%m-%dT%H:%M')
        return {'name': self.event.name, 'description': '', 'start_time': start, 'end_time': '',
                'timezone': 'America/Chicago', 'address': '', 'latitude': '', 'longitude': '',
                'accent_color': '#431f86', 'logo_url': '', **extra}

    def test_pages_show_the_section(self):
        for page in (self.url('event_edit'), self.url('location_edit', self.loc.pk), self.url('item_edit', self.item.pk)):
            with self.subTest(page=page):
                body = self.client.get(page, secure=True).content.decode()
                self.assertIn('Details for the assistant', body)
                self.assertIn('<details', body)

    def test_event_edit_saves_and_locks(self):
        resp = self.client.post(self.url('event_edit'), self.event_post(
            ctx_about='All day art.', ctx_faq='Q: Parking?\nA: Street parking.\n\nQ: Pets?\nA: On a leash.',
            ctx_amenities='restrooms | Restrooms | Gallery A | Ground floor\nwater | Fountain',
            ctx_links='Map | https://example.com/map'), secure=True)
        self.assertEqual(resp.status_code, 302, resp.content[:500])
        self.event.refresh_from_db()
        self.assertEqual(self.event.context, {
            'about': 'All day art.',
            'faq': [{'q': 'Parking?', 'a': 'Street parking.'}, {'q': 'Pets?', 'a': 'On a leash.'}],
            'amenities': [{'kind': 'restrooms', 'name': 'Restrooms', 'location_id': self.loc.id, 'note': 'Ground floor'},
                          {'kind': 'water', 'name': 'Fountain', 'location_id': None}],
            'links': [{'title': 'Map', 'url': 'https://example.com/map'}]})
        self.assertIn('context', self.event.locked_fields)

    def test_resaving_unchanged_does_not_lock(self):
        self.event.context = {'faq': [{'q': 'Q?', 'a': 'Line one\nLine two\n\nPara'}],
                              'amenities': [{'kind': 'info', 'name': 'Desk', 'location_id': self.loc.id}]}
        self.event.save()
        form = self.client.get(self.url('event_edit'), secure=True).context['form']
        post = self.event_post(**{f'ctx_{k}': form.initial.get(f'ctx_{k}', '') for k in ctx.EVENT_KEYS})
        self.assertEqual(self.client.post(self.url('event_edit'), post, secure=True).status_code, 302)
        self.event.refresh_from_db()
        self.assertNotIn('context', self.event.locked_fields)
        self.assertEqual(self.event.context['faq'][0]['a'], 'Line one\nLine two\n\nPara')

    def test_bad_lines_are_errors(self):
        resp = self.client.post(self.url('event_edit'), self.event_post(
            ctx_amenities='spa | Hot tub', ctx_links='Site | http://insecure.example'), secure=True)
        self.assertEqual(resp.status_code, 200)
        errors = resp.context['form'].errors
        self.assertIn('ctx_amenities', errors)
        self.assertIn('ctx_links', errors)
        resp = self.client.post(self.url('event_edit'), self.event_post(ctx_amenities='water | Tap | Nowhere'),
                                secure=True)
        self.assertIn('no location called', str(resp.context['form'].errors['ctx_amenities']))

    def test_location_and_item_edit(self):
        resp = self.client.post(self.url('location_edit', self.loc.pk), {
            'name': 'Gallery A', 'category': '', 'snippet': '', 'description': '', 'address': '', 'latitude': '',
            'longitude': '', 'website': '', 'image_url': '', 'tags_text': '',
            'ctx_menu': 'Coffee | $3 | oat milk\nTea', 'ctx_hours': '9–5'}, secure=True)
        self.assertEqual(resp.status_code, 302, resp.content[:500])
        self.loc.refresh_from_db()
        self.assertEqual(self.loc.context, {'menu': [{'name': 'Coffee', 'price': '$3', 'note': 'oat milk'},
                                                     {'name': 'Tea'}], 'hours': '9–5'})
        self.assertIn('context', self.loc.locked_fields)
        start = timezone.localtime(self.item.start_time).strftime('%Y-%m-%dT%H:%M')
        resp = self.client.post(self.url('item_edit', self.item.pk), {
            'name': 'Talk', 'start_time': start, 'end_time': '', 'location': '', 'description': '', 'tags_text': '',
            'ctx_performers': 'Ana | Jazz, Latin | Pianist from Austin.', 'ctx_price': 'Free'}, secure=True)
        self.assertEqual(resp.status_code, 302, resp.content[:500])
        self.item.refresh_from_db()
        self.assertEqual(self.item.context, {
            'performers': [{'name': 'Ana', 'bio': 'Pianist from Austin.', 'genres': ['jazz', 'latin']}],
            'price': 'Free'})

    def test_text_format_round_trips(self):
        faq = [{'q': 'A?', 'a': 'One\nTwo\n\nThree'}, {'q': 'B?', 'a': 'Yes.'}]
        self.assertEqual(parse_faq(to_text('faq', faq)), faq)
