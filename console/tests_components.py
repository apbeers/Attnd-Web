"""Console components (console/components): every component renders from its JSON on pages and in the chat, data
functions check permissions like the views, every action is a registered Ask Attnd action, the JSON holds no HTML,
and the pages that are compositions of components keep working (forms post, errors come back in place)."""

import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from agent import registry as agent_registry
from console import components as C
from events.models import Event, EventMember, Location, Notification, ScheduledItem
from scrape.models import ChangeSet


class ComponentTestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('owner', 'owner@example.com', 'pw')
        self.other = User.objects.create_user('other', 'other@example.com', 'pw')
        self.helper = User.objects.create_user('helper', 'helper@example.com', 'pw')
        start = timezone.now().replace(minute=0, second=0, microsecond=0) + timedelta(days=3)
        self.event = Event.objects.create(name='Prairie Fest', event_code='PRAIRIE', user=self.owner,
                                          timezone='America/Chicago', start_time=start,
                                          end_time=start + timedelta(hours=10), status=Event.STATUS_DRAFT)
        EventMember.objects.create(event=self.event, user=self.helper, role=EventMember.ROLE_CONTRIBUTOR)
        self.stage = Location.objects.create(event=self.event, name='Main Stage', category='Stage',
                                             snippet='The big one', tags=['live music'], sort_order=1)
        self.item = ScheduledItem.objects.create(event=self.event, name='Prairie Lights', start_time=start,
                                                 end_time=start + timedelta(hours=1), location=self.stage,
                                                 tags=['folk'])
        self.update = Notification.objects.create(event=self.event, title='Gates open late', body='At 11 instead',
                                                  send_time=start + timedelta(hours=-1))
        self.changeset = ChangeSet.objects.create(event=self.event, summary='2 schedule changes', status=ChangeSet.PENDING)
        from agent.models import AgentRun, ChangeProposal
        run = AgentRun.objects.create(user=self.owner, goal='Shift', event=self.event)
        self.proposal = ChangeProposal.objects.create(run=run, event=self.event, title='Move sets later', changes=[
            {'action': 'edit_schedule_item', 'args': {}, 'label': 'Prairie Lights',
             'rows': [['start time', '19:00', '19:30']]}])
        self.ids = {
            'event_tile': {'event_id': self.event.pk}, 'event_header': {'event_id': self.event.pk},
            'event_stats': {'event_id': self.event.pk}, 'live_on_phone': {'event_id': self.event.pk},
            'needs_review': {'event_id': self.event.pk}, 'event_overview': {'event_id': self.event.pk},
            'location_row': {'event_id': self.event.pk, 'location_id': self.stage.pk},
            'location_form': {'event_id': self.event.pk, 'location_id': self.stage.pk},
            'schedule_item_row': {'event_id': self.event.pk, 'item_id': self.item.pk},
            'schedule_item_form': {'event_id': self.event.pk, 'item_id': self.item.pk},
            'notification_row': {'event_id': self.event.pk, 'notification_id': self.update.pk},
            'notification_form': {'event_id': self.event.pk},
            'object_history': {'event_id': self.event.pk}, 'event_form': {'event_id': self.event.pk},
            'event_new_form': {}, 'event_team': {'event_id': self.event.pk},
            'event_metrics': {'event_id': self.event.pk}, 'event_map': {'event_id': self.event.pk},
            'changeset_review': {'event_id': self.event.pk, 'changeset_id': self.changeset.pk},
            'app_layout': {'event_id': self.event.pk}, 'website_watching': {'event_id': self.event.pk},
            'event_context_form': {'event_id': self.event.pk}, 'event_flyer': {'event_id': self.event.pk},
            'sharing_kit': {'event_id': self.event.pk}, 'event_lifecycle': {'event_id': self.event.pk},
            'event_versions': {'event_id': self.event.pk}, 'event_exports': {'event_id': self.event.pk},
            'change_preview': {'event_id': self.event.pk, 'proposal_id': self.proposal.pk},
            'event_readiness': {'event_id': self.event.pk}, 'attention': {},
            'usage_answer': {'event_id': self.event.pk}, 'event_rollover': {'event_id': self.event.pk},
            'first_run': {}, 'event_theme': {'event_id': self.event.pk},
            'app_screens': {'event_id': self.event.pk},  # API-024
            'event_theme_editor': {'event_id': self.event.pk},
            'ai_credit_meter': {},  # credit/
            'live_activity_settings': {'event_id': self.event.pk},
            'unpublished_changes': {'event_id': self.event.pk},
        }
        self.key_text = {
            'event_tile': 'Prairie Fest', 'event_header': 'PRAIRIE', 'event_stats': 'Attendees joined',
            'live_on_phone': 'Live on your phone', 'needs_review': '1 AI update waiting for review',
            'event_overview': 'Live on your phone', 'location_row': 'Main Stage', 'location_form': 'One-line summary',
            'schedule_item_row': 'Prairie Lights', 'schedule_item_form': 'Prairie Lights',
            'notification_row': 'Gates open late', 'notification_form': 'Send a notification',
            'object_history': 'No changes recorded yet', 'event_form': 'Prairie Fest', 'event_new_form': 'Create with AI',
            'event_team': 'Owner', 'event_metrics': 'iOS and Android', 'event_map': 'No location has a map pin yet',
            'changeset_review': '2 schedule changes', 'app_layout': 'Changes save as you make them',
            'website_watching': 'Source websites', 'event_context_form': 'Only add what attendees may see',
            'event_flyer': 'No flyer yet', 'sharing_kit': 'PRAIRIE', 'event_lifecycle': 'Now:',
            'event_versions': 'No saved versions yet', 'event_exports': 'CSV files',
            'change_preview': 'Move sets later', 'event_readiness': 'to fix',
            'attention': 'What needs attention', 'usage_answer': 'numbers', 'event_rollover': 'Make the copy', 'first_run': 'Get started',
            'event_theme': 'Generate with AI', 'app_screens': 'What each screen of the apps shows', 'event_theme_editor': 'Where the colours show',
            'ai_credit_meter': 'used this month',
            'live_activity_settings': 'Urgent announcements always show',
            'unpublished_changes': "attendees can't open it yet",
        }

    def request(self, user=None):
        request = RequestFactory().get('/console/')
        request.user = user or self.owner
        return request


class RegistryTests(ComponentTestCase):
    def test_the_set(self):
        staff_only = {n for n, c in C.registry().items() if c.audience == 'staff'}
        studio = {n for n, c in C.registry().items() if c.audience == 'studio'}
        self.assertEqual(studio, {'website_overview', 'website_versions', 'website_assets', 'website_forms'})
        superuser = {n for n, c in C.registry().items() if c.audience == 'superuser'}
        self.assertEqual(superuser, {'platform_health'})
        self.assertEqual(set(C.registry()) - staff_only - studio - superuser, set(self.ids))
        self.assertEqual(staff_only, {'prospect_row', 'prospect_detail', 'prospect_run_row', 'prospect_settings_form',
                                      'ai_spend_summary', 'ai_limits_form', 'ai_limit_overrides'})

    def test_every_action_is_a_registered_agent_action(self):
        writes = agent_registry.registry()['writes']
        for comp in C.registry().values():
            for name, label in comp.actions:
                self.assertIn(name, writes, f'{comp.name}: {name} is not an Ask Attnd action')
                self.assertTrue(label)
            if comp.detail:
                self.assertIn(comp.detail, C.registry())
            for part in comp.parts:
                self.assertIn(part, C.registry())

    def test_offered_actions_carry_the_registry_confirm_flag_and_url(self):
        writes = agent_registry.registry()['writes']
        data = C.load(self.owner, 'location_row', self.ids['location_row'])
        delete = next(a for a in data['actions'] if a['name'] == 'delete_location')
        self.assertTrue(delete['confirm'])
        self.assertEqual(delete['confirm'], writes['delete_location'].confirm)
        self.assertEqual(delete['url'], reverse('console:location_action', args=[self.event.pk, self.stage.pk]))
        self.assertEqual(delete['fields'], {'action': 'delete'})
        hide = next(a for a in data['actions'] if a['name'] == 'toggle_location_hidden')
        self.assertFalse(hide['confirm'])

    def test_buttons_the_pages_guard_with_data_confirm_are_always_ask_actions(self):
        writes = agent_registry.registry()['writes']
        guarded = set()
        for name, ids in self.ids.items():
            for action in C.load(self.owner, name, ids).get('actions', []):
                if action.get('confirm_text'):
                    guarded.add(action['name'])
                    self.assertTrue(writes[action['name']].confirm, action['name'])
        self.assertTrue({'delete_location', 'delete_schedule_item', 'publish_event', 'delete_event',
                         'discard_ai_changes'} <= guarded, guarded)

    def test_every_component_renders_on_pages_and_in_the_narrow_chat(self):
        for name, ids in self.ids.items():
            data = C.load(self.owner, name, ids)
            json.dumps(data)  # plain JSON
            for surface in ('page', 'chat'):
                html = C.render(name, data, self.request(), surface=surface, uid='c1-' if surface == 'chat' else '')
                self.assertIn(self.key_text[name], html, f'{name} on {surface}')
                self.assertIn('component--', html)
                self.assertNotIn('{#', html)
                self.assertNotIn('{%', html)

    def test_rows_open_their_detail_for_editing(self):
        # The whole row is the tap target (a stretched title link) with a chevron + "Edit" and a name that says so.
        for name, label in (('schedule_item_row', 'Edit Prairie Lights at Main Stage'),
                            ('location_row', 'Edit Main Stage (Stage)')):
            data = C.load(self.owner, name, self.ids[name])
            for surface in ('page', 'chat'):
                html = C.render(name, data, self.request(), surface=surface)
                self.assertIn(f'aria-label="{label}"', html, (name, surface))
                self.assertIn('row-link__target', html)
                self.assertIn('row-link__chevron', html)
        css = open('console/static/console/console.css').read()
        self.assertIn('.row-link__target::after', css)
        self.assertIn('cursor: pointer', css)
        # Narrow rows put the day and time on their own line instead of a fixed column the text runs into.
        self.assertIn('@container component (max-width: 34rem)', css)
        self.assertNotIn('.schedule-time { width: 6.5rem', css)

    def test_the_qr_card_opens_the_app_on_a_phone(self):
        from urllib.parse import unquote
        from attnd.links import open_in_app_links
        links = open_in_app_links('IFICCK70')
        self.assertEqual(links['ios'], 'attnd://join/IFICCK70')  # Safari won't open a Universal Link to its own domain
        self.assertTrue(links['android'].startswith('intent://attnd.app/join/IFICCK70/#Intent;scheme=https;'
                                                    'package=com.arcu.attnd;S.browser_fallback_url='))
        self.assertTrue(links['android'].endswith(';end'))
        self.assertIn(unquote(links['android'].split('S.browser_fallback_url=')[1][:-4]), links['fallback'])
        self.assertEqual(links['fallback'], 'https://attnd.app/join/IFICCK70/')
        with override_settings(SITE_URL='https://dev.attnd.app'):
            self.assertTrue(open_in_app_links('X')['android'].startswith('intent://dev.attnd.app/join/X/'))
        data = C.load(self.owner, 'live_on_phone', self.ids['live_on_phone'])
        for surface in ('page', 'chat'):
            html = C.render('live_on_phone', data, self.request(), surface=surface)
            self.assertIn('data-open-app', html)
            self.assertIn('data-ios="attnd://join/PRAIRIE"', html)
            self.assertIn('hidden>', html)  # shown only on phones (console.js)
        self.assertNotIn('attnd://', C.model_json(data))
        js = open('console/static/console/console.js').read()
        self.assertIn("visibilitychange", js)
        self.assertIn('1500', js)

    def test_no_coloured_left_border_accents(self):
        # Owner, Nov 21, 2026: no rounded boxes with a coloured left border; status is a small leading mark.
        import re
        for path in ('console/static/console/console.css', 'agent/static/agent/agent.css', 'home/static/home/site.css'):
            css = open(path).read()
            self.assertIsNone(re.search(r'border-left(-color)?\s*:', css), path)
            self.assertIsNone(re.search(r'inset\s+\d+px\s+0\s+0', css), path)
        self.assertIn('.status-mark--success', open('console/static/console/console.css').read())

    def test_list_components_expand_from_their_parent(self):
        Location.objects.create(event=self.event, name='Taco Booth', sort_order=2)
        rows = C.instances(self.owner, 'location_row', [{'event_id': self.event.pk}])
        self.assertEqual([d['name'] for _, d in rows], ['Main Stage', 'Taco Booth'])
        tiles = C.instances(self.owner, 'event_tile', [{}])
        self.assertEqual([d['name'] for _, d in tiles], ['Prairie Fest'])
        self.assertEqual(C.instances(self.other, 'event_tile', [{}]), [])

    def test_ids_are_validated(self):
        with self.assertRaises(C.IdError):
            C.instances(self.owner, 'location_row', [{'event_id': self.event.pk, 'item_id': 3}])
        with self.assertRaises(C.IdError):
            C.instances(self.owner, 'location_form', [{'event_id': 'x'}])
        with self.assertRaises(C.IdError):
            C.clean_ids(C.get('location_row'), {'location_id': self.stage.pk})

    def test_model_json_has_no_markup_or_urls(self):
        for name, ids in self.ids.items():
            text = C.model_json(C.load(self.owner, name, ids))
            self.assertNotIn('<', text, name)
            self.assertNotIn('/console/', text, name)
        form = json.loads(C.model_json(C.load(self.owner, 'location_form', self.ids['location_form'])))
        self.assertEqual(form['form']['name']['value'], 'Main Stage')


class PermissionTests(ComponentTestCase):
    def test_another_persons_event_is_not_found(self):
        for name, ids in self.ids.items():
            if not ids:
                continue  # event_new_form belongs to no event
            with self.assertRaises(Http404, msg=name):
                C.load(self.other, name, ids)

    def test_an_item_of_another_event_is_not_found(self):
        mine = Event.objects.create(name='Mine', event_code='MINE', user=self.other, start_time=self.event.start_time)
        with self.assertRaises(Http404):
            C.load(self.other, 'location_row', {'event_id': mine.pk, 'location_id': self.stage.pk})

    def test_contributors_see_content_but_not_the_owners_buttons(self):
        header = C.load(self.helper, 'event_header', {'event_id': self.event.pk})
        self.assertEqual(header['actions'], [])
        owner = C.load(self.owner, 'event_header', {'event_id': self.event.pk})
        self.assertEqual([a['name'] for a in owner['actions']], ['publish_event'])
        row = C.load(self.helper, 'location_row', self.ids['location_row'])
        self.assertEqual(row['name'], 'Main Stage')


@override_settings(SECURE_SSL_REDIRECT=False)
class PageTests(ComponentTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_pages_are_compositions_of_components(self):
        pages = {
            reverse('console:dashboard'): ['component--event_tile'],
            reverse('console:event', args=[self.event.pk]): ['component--event_header', 'component--needs_review',
                                                              'component--live_on_phone', 'Attendees joined',
                                                              'Most liked'],
            reverse('console:locations', args=[self.event.pk]): ['component--location_row', 'drag-handle'],
            reverse('console:location_edit', args=[self.event.pk, self.stage.pk]): ['data-pin-map',
                                                                                    'data-fill-site',
                                                                                    'category-options'],
            reverse('console:schedule', args=[self.event.pk]): ['component--schedule_item_row'],
            reverse('console:item_edit', args=[self.event.pk, self.item.pk]): ['name="start_time"'],
            reverse('console:notifications', args=[self.event.pk]): ['component--notification_row',
                                                                      'data-show-if="when:later"'],
        }
        for url, needles in pages.items():
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            for needle in needles:
                self.assertContains(response, needle, msg_prefix=url)

    def test_a_page_form_still_saves_through_its_view(self):
        url = reverse('console:item_edit', args=[self.event.pk, self.item.pk])
        response = self.client.post(url, {'name': 'Prairie Lights (late)', 'start_time': '2026-11-14T20:00',
                                          'end_time': '', 'location': self.stage.pk, 'description': '',
                                          'tags_text': 'folk'})
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.name, 'Prairie Lights (late)')

    def test_a_chat_form_with_errors_comes_back_as_the_component(self):
        url = reverse('console:item_edit', args=[self.event.pk, self.item.pk])
        response = self.client.post(url, {'name': '', 'start_time': 'not a time'},
                                    HTTP_X_ATTND_COMPONENT='schedule_item_form')
        self.assertEqual(response['Content-Type'], 'application/json')
        data = response.json()
        self.assertTrue(data['invalid'])
        self.assertEqual(data['ids'], {'event_id': self.event.pk, 'item_id': self.item.pk})
        self.assertIn('field__error', data['html'])
        self.assertIn('data-component-form', data['html'])
        # Without the header the page comes back as before.
        response = self.client.post(url, {'name': '', 'start_time': 'not a time'})
        self.assertContains(response, 'field__error')
        self.assertContains(response, '<html')

    def test_confirm_actions_never_post_from_the_chat(self):
        data = C.load(self.owner, 'location_row', self.ids['location_row'])
        html = C.render('location_row', data, self.request(), surface='chat', uid='c1-')
        self.assertIn('data-component-act="delete_location"', html)
        self.assertNotIn('value="delete"', html)  # no form that posts the delete directly
        self.assertIn('value="toggle_hidden"', html)  # not gated: posts to the view


class ReadinessTests(ComponentTestCase):
    def test_checks_find_the_problems_and_score_them(self):
        from datetime import timedelta

        from console.components.catalog_chat import checks, score
        from events.models import ScheduledItem
        ScheduledItem.objects.create(event=self.event, name='Clash', location=self.stage,
                                     start_time=self.item.start_time + timedelta(minutes=15))
        ScheduledItem.objects.create(event=self.event, name='Way later', start_time=self.item.start_time + timedelta(days=30))
        found = {c['key']: c for c in checks(self.event)}
        self.assertEqual(found['overlaps']['state'], 'problem')
        self.assertIn('Clash', found['overlaps']['text'])
        self.assertIn('Way later', found['dates']['text'])
        self.assertEqual(found['review']['state'], 'problem')
        self.assertLess(score(list(found.values())), 100)
        data = C.load(self.owner, 'attention', {})
        self.assertTrue(any(r['key'] == 'overlaps' for r in data['rows']))
        stranger = User.objects.create_user('s2', 's2@example.com', 'pw')
        self.assertEqual(C.load(stranger, 'attention', {})['rows'], [])


class UsageAnswerTests(ComponentTestCase):
    def test_hourly_bars_busiest_hour_and_likes(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from events.models import UsageCount
        tz = ZoneInfo(self.event.timezone or 'UTC')
        day = datetime(2026, 11, 14, tzinfo=tz)
        for hour, n in ((18, 5), (19, 40), (21, 3)):
            UsageCount.objects.create(event=self.event, bucket=day.replace(hour=hour), metric='tab_view', count=n)
        UsageCount.objects.create(event=self.event, bucket=day.replace(hour=21), metric='location_heart', detail=str(self.stage.pk), count=7)
        data = C.load(self.owner, 'usage_answer', {'event_id': self.event.pk})
        self.assertEqual(data['busiest'], {'label': '7 PM', 'count': 40})
        self.assertEqual([b['count'] for b in data['bars']], [5, 40, 0, 10])
        self.assertEqual(data['liked'][0], {'kind': 'place', 'name': self.stage.name, 'likes': 7})
        html = C.render('usage_answer', data, surface='chat')
        self.assertIn('<svg', html)


class RolloverTests(ComponentTestCase):
    def test_a_copy_moves_everything_to_the_new_first_day_and_keeps_the_team(self):
        from datetime import timedelta

        from django.urls import reverse
        from django.utils import timezone

        from events.models import Event, EventMember, Notification
        helper = User.objects.create_user('h', 'h@example.com', 'pw')
        EventMember.objects.create(event=self.event, user=helper, role='contributor')
        data = C.load(self.owner, 'event_rollover', {'event_id': self.event.pk})
        self.assertEqual(data['shift_days'] % 7, 0)  # the same weekday
        self.client.force_login(self.owner)
        new_day = (timezone.localtime(self.event.start_time).date() + timedelta(days=364))
        response = self.client.post(reverse('console:event_duplicate', args=[self.event.pk]), {
            'name': 'Prairie Fest 2027', 'start_date': new_day.isoformat(), 'keep_team': 'on'}, secure=True)
        copy = Event.objects.get(name='Prairie Fest 2027')
        self.assertRedirects(response, reverse('console:event', args=[copy.pk]), fetch_redirect_response=False)
        self.assertEqual(copy.status, 'draft')
        self.assertNotEqual(copy.event_code, self.event.event_code)
        item = copy.scheduleditem_set.get()
        self.assertEqual(item.start_time - self.item.start_time, timedelta(days=364))
        self.assertEqual(item.location.event_id, copy.pk)  # linked to the copied place
        self.assertEqual(copy.location_set.count(), self.event.location_set.count())
        self.assertTrue(EventMember.objects.filter(event=copy, user=helper).exists())
        self.assertFalse(Notification.objects.filter(event=copy).exists())


class WalkthroughTests(ComponentTestCase):
    def test_steps_follow_the_event_and_skip_hides_it_for_good(self):
        from django.urls import reverse

        from console.components.catalog_chat import show_walkthrough
        data = C.load(self.owner, 'first_run', {})
        done = {s['key']: s['done'] for s in data['steps']}
        self.assertTrue(done['import'])
        self.assertFalse(done['review'])  # a change set is waiting
        self.assertEqual(data['current']['key'], 'review')
        self.assertTrue(show_walkthrough(self.owner))
        self.client.force_login(self.owner)
        self.client.post(reverse('console:walkthrough_skip'), secure=True)
        self.assertFalse(show_walkthrough(self.owner))
