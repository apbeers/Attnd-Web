"""Permissions (owner, Nov 22, 2026: deny by default, Django's permission model; attnd/permissions.py).

* coverage: every URL pattern, component and Ask Attnd action declares a permission (CI fails on a new undeclared one);
* the backend: event roles × permissions, the Staff role for is_staff accounts, the role groups in the database;
* the matrix: every event URL × anonymous / stranger / contributor / event admin / owner / superuser, with another
  event's ids (IDOR), every platform URL × organizer / staff / Staff-group member / Platform-admins member /
  superuser, every component × roles, the agent's tool lists per role;
* the signed-in endpoints that take ids (jobs, tasks, AI reviews, undo, agent runs, proposals) against strangers.
"""
import uuid
from datetime import timedelta

from django.contrib.auth.models import AnonymousUser, Group, User
from django.http import Http404, HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.utils import timezone

from attnd import permissions as P
from console.testing import TwoFactorClient
from events.models import Event, EventMember, Location, Notification, ScheduledItem

UNCHECKED = P.UNCHECKED_NAMESPACES


def walk(patterns=None, prefix='', namespaces=()):
    for p in patterns if patterns is not None else get_resolver().url_patterns:
        if isinstance(p, URLResolver):
            yield from walk(p.url_patterns, prefix + str(p.pattern), namespaces + ((p.namespace,) if p.namespace else ()))
        elif isinstance(p, URLPattern):
            yield prefix + str(p.pattern), ':'.join(namespaces + (p.name or '',)), namespaces, p


def checked_patterns():
    return [(route, name, p) for route, name, ns, p in walk() if not UNCHECKED & set(ns)]


class CoverageTests(TestCase):
    def test_every_url_declares_a_permission(self):
        missing = [f'{name} ({route})' for route, name, p in checked_patterns() if P.declared(p.callback) is None]
        self.assertEqual(missing, [], 'Declare a permission on these views (CLAUDE.md → Declaring permissions)')

    def test_declared_permissions_exist(self):
        from django.contrib.auth.models import Permission
        known = {f'{ct}.{code}' for ct, code in Permission.objects.values_list('content_type__app_label', 'codename')}
        for route, name, p in checked_patterns():
            perm = P.declared(p.callback)
            if perm not in (P.PUBLIC, P.SIGNED_IN):
                self.assertIn(perm, known, f'{name}: {perm} is not a permission')

    def test_event_routes_check_the_event(self):
        """A view whose URL names an event declares a per-event permission (or stays signed-in with its own
        check, listed here on purpose)."""
        own_check = {'platform:event', 'platform:event_save', 'platform:record_payment', 'platform:send_invoice'}  # Platform admin: every event
        for route, name, p in checked_patterns():
            if '<int:event_id>' in route and name not in own_check and P.declared(p.callback) != P.PUBLIC:
                self.assertIn(P.declared(p.callback), P.EVENT_PERMS, name)

    def test_every_component_declares_a_permission(self):
        from console import components as C
        missing = [n for n, c in C.registry().items() if not c.permission]
        self.assertEqual(missing, [])

    def test_every_agent_action_has_a_permission(self):
        from agent import registry
        reg = registry.registry()
        missing = [a.name for a in list(reg['writes'].values()) + list(reg['reads'].values())
                   if not a.permission or a.permission == P.PUBLIC]
        self.assertEqual(missing, [])

    def test_role_groups_are_in_the_database(self):
        for name, perms in P.ROLES.items():
            group = Group.objects.get(name=name)
            have = {f'{a}.{c}' for a, c in group.permissions.values_list('content_type__app_label', 'codename')}
            self.assertEqual(have, set(perms), name)

    def test_platform_permissions_are_not_event_permissions(self):
        self.assertFalse(P.PLATFORM_PERMS & P.EVENT_PERMS)


class MiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.mw = P.DeclaredPermissionMiddleware(lambda r: HttpResponse('ok'))

    def _request(self):
        request = self.factory.get('/console/x/')
        request.resolver_match = None
        return request

    def test_undeclared_view_is_refused(self):
        def view(request):
            return HttpResponse('secret')
        self.assertEqual(self.mw.process_view(self._request(), view, (), {}).status_code, 403)
        self.assertEqual(self.mw.process_view(self._request(), view, (), {'event_id': 3}).status_code, 404)

    def test_declared_and_public_views_pass(self):
        @P.declare(P.SIGNED_IN)
        def declared_view(request):
            return HttpResponse('ok')
        from attnd.public import public

        @public
        def open_view(request):
            return HttpResponse('ok')
        self.assertIsNone(self.mw.process_view(self._request(), declared_view, (), {}))
        self.assertIsNone(self.mw.process_view(self._request(), open_view, (), {}))


class Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        mk = User.objects.create_user
        cls.owner = mk('owner', 'owner@example.com', 'pw')
        cls.admin = mk('admin', 'admin@example.com', 'pw')
        cls.contributor = mk('contrib', 'contrib@example.com', 'pw')
        cls.stranger = mk('stranger', 'stranger@example.com', 'pw')
        cls.staff = mk('staff', 'staff@example.com', 'pw', is_staff=True)
        cls.superuser = User.objects.create_superuser('root', 'root@example.com', 'pw')
        cls.staff_member = mk('grouped', 'grouped@example.com', 'pw')  # not is_staff: in the Staff group
        cls.staff_member.groups.add(Group.objects.get(name=P.STAFF_GROUP))
        cls.platform_member = mk('platform', 'platform@example.com', 'pw')
        cls.platform_member.groups.add(Group.objects.get(name=P.PLATFORM_ADMIN_GROUP))

        def event(code, owner):
            return Event.objects.create(name=code, event_code=code, user=owner, timezone='America/Chicago',
                                        start_time=now + timedelta(days=3), end_time=now + timedelta(days=3, hours=8),
                                        status=Event.STATUS_PUBLISHED)
        cls.event = event('PERMA', cls.owner)
        EventMember.objects.create(event=cls.event, user=cls.admin, role=EventMember.ROLE_ADMIN)
        EventMember.objects.create(event=cls.event, user=cls.contributor, role=EventMember.ROLE_CONTRIBUTOR)
        cls.loc = Location.objects.create(event=cls.event, name='Main Stage')
        cls.item = ScheduledItem.objects.create(event=cls.event, name='Opening', location=cls.loc,
                                                start_time=now + timedelta(days=3))
        cls.note = Notification.objects.create(event=cls.event, title='Hi', body='Hello', send_time=now + timedelta(days=1))
        # Another event the people above aren't on (IDOR targets).
        cls.other_owner = mk('other', 'other@example.com', 'pw')
        cls.other = event('PERMB', cls.other_owner)
        cls.other_loc = Location.objects.create(event=cls.other, name='Other Stage')
        cls.other_item = ScheduledItem.objects.create(event=cls.other, name='Other', start_time=now + timedelta(days=3))
        cls.other_note = Notification.objects.create(event=cls.other, title='Other', body='Other',
                                                    send_time=now + timedelta(days=1))

    def client_for(self, user):
        client = TwoFactorClient()
        if user is not None:
            client.force_login(user)
        return client


class BackendTests(Fixture):
    def test_event_roles(self):
        e = self.event
        for user, role in ((self.owner, 'owner'), (self.admin, 'admin'), (self.contributor, 'contributor')):
            for perm in P.EVENT_PERMS:
                self.assertEqual(user.has_perm(perm, e), perm in P.EVENT_ROLES[role], (role, perm))
        for perm in P.EVENT_PERMS:
            self.assertFalse(self.stranger.has_perm(perm, e))
            self.assertFalse(self.staff.has_perm(perm, e), perm)  # staff aren't on every team
            self.assertTrue(self.superuser.has_perm(perm, e))
            self.assertFalse(AnonymousUser().has_perm(perm, e))

    def test_objects_of_an_event_follow_its_roles(self):
        self.assertTrue(self.contributor.has_perm('events.change_location', self.loc))
        self.assertFalse(self.contributor.has_perm('events.change_location', self.other_loc))
        self.assertFalse(self.contributor.has_perm('events.delete_event', self.event))
        self.assertTrue(self.owner.has_perm('events.delete_event', self.event))

    def test_platform_roles(self):
        for perm in P.PLATFORM_PERMS:
            self.assertEqual(self.staff.has_perm(perm), perm in P.STAFF, perm)
            self.assertEqual(self.staff_member.has_perm(perm), perm in P.STAFF, perm)
            self.assertTrue(self.platform_member.has_perm(perm), perm)
            self.assertTrue(self.superuser.has_perm(perm))
            self.assertFalse(self.owner.has_perm(perm), perm)
            self.assertFalse(AnonymousUser().has_perm(perm))

    def test_inactive_accounts_have_nothing(self):
        self.staff.is_active = False
        self.assertFalse(self.staff.has_perm('prospects.view_prospect'))

    def test_for_user(self):
        def ids(user, perm='events.change_event'):
            return set(Event.objects.for_user(user, perm).values_list('pk', flat=True))
        self.assertEqual(ids(self.contributor), {self.event.pk})
        self.assertEqual(ids(self.contributor, 'events.manage_event'), set())
        self.assertEqual(ids(self.admin, 'events.manage_event'), {self.event.pk})
        self.assertEqual(ids(self.admin, 'events.delete_event'), set())
        self.assertEqual(ids(self.owner, 'events.delete_event'), {self.event.pk})
        self.assertEqual(ids(self.stranger), set())
        self.assertEqual(ids(self.staff), set())
        self.assertEqual(ids(self.superuser), {self.event.pk, self.other.pk})
        self.assertEqual(ids(AnonymousUser()), set())
        self.assertEqual(set(Location.objects.for_user(self.contributor).values_list('pk', flat=True)), {self.loc.pk})


# --- the URL matrix ---------------------------------------------------------------------------------------------

GOOD_SLUGS = {'kind': 'schedule', 'form_name': 'contact'}


def _kwargs(route, f, other=False):
    """Values for a route's kwargs: the fixture's event, and either its own rows or (other=True) the other event's."""
    import re
    out = {}
    for conv, name in re.findall(r'<(?:(\w+):)?(\w+)>', route):
        if name == 'event_id':
            out[name] = f.event.pk
        elif name == 'location_id':
            out[name] = (f.other_loc if other else f.loc).pk
        elif name == 'item_id':
            out[name] = (f.other_item if other else f.item).pk
        elif name == 'notification_id':
            out[name] = (f.other_note if other else f.note).pk
        elif conv == 'uuid':
            out[name] = uuid.uuid4()
        elif conv == 'slug' or conv == 'str':
            out[name] = GOOD_SLUGS.get(name, 'x')
        else:
            out[name] = 999999
    return out


def _event_routes():
    return [(route, name, p) for route, name, p in checked_patterns()
            if '<int:event_id>' in route and P.declared(p.callback) in P.EVENT_PERMS]


@override_settings(CSP_MODE='off')
class EventMatrixTests(Fixture):
    def _hit(self, client, route, name, kwargs):
        url = reverse(name, kwargs=kwargs)
        response = client.get(url)
        if response.status_code == 405:
            response = client.post(url, {})
        return response

    def test_anonymous_is_sent_to_sign_in(self):
        client = self.client_for(None)
        for route, name, p in _event_routes():
            response = self._hit(client, route, name, _kwargs(route, self))
            self.assertIn(response.status_code, (302, 401, 403, 404), name)
            if response.status_code == 302:
                self.assertIn('/accounts/login/', response['Location'], name)

    def test_strangers_and_staff_get_404_on_every_event_url(self):
        for user in (self.stranger, self.staff, self.staff_member, self.other_owner):
            client = self.client_for(user)
            for route, name, p in _event_routes():
                self.assertEqual(self._hit(client, route, name, _kwargs(route, self)).status_code, 404,
                                 f'{user.username} {name}')

    def test_team_members_open_the_pages_their_role_allows(self):
        own_rows = {'event_id', 'location_id', 'item_id', 'notification_id', 'kind'}
        for user, role in ((self.contributor, 'contributor'), (self.admin, 'admin'), (self.owner, 'owner'),
                           (self.superuser, 'owner')):
            client = self.client_for(user)
            for route, name, p in _event_routes():
                kwargs = _kwargs(route, self)
                url = reverse(name, kwargs=kwargs)
                response = client.get(url)
                if response.status_code == 405:
                    continue  # POST-only (never POSTed blindly as an allowed person: it would act)
                allowed = P.declared(p.callback) in P.EVENT_ROLES[role]
                if not allowed:
                    self.assertEqual(response.status_code, 404, f'{role} {name}')
                elif set(kwargs) <= own_rows:
                    self.assertNotIn(response.status_code, (403, 404), f'{role} {name}')

    def test_another_events_rows_are_404_through_this_event(self):
        """IDOR: a contributor of PERMA uses PERMA's event id with PERMB's location / item / notification ids."""
        client = self.client_for(self.contributor)
        hit = 0
        for route, name, p in _event_routes():
            if not ({'location_id', 'item_id', 'notification_id'} & set(_kwargs(route, self))):
                continue
            kwargs = _kwargs(route, self, other=True)
            url = reverse(name, kwargs=kwargs)
            self.assertEqual(client.get(url).status_code in (404, 405), True, name)
            self.assertEqual(client.post(url, {'action': 'delete'}).status_code, 404, name)
            hit += 1
        self.assertGreaterEqual(hit, 5)
        self.assertTrue(Location.objects.filter(pk=self.other_loc.pk).exists())
        self.assertTrue(ScheduledItem.objects.filter(pk=self.other_item.pk).exists())
        self.assertTrue(Notification.objects.filter(pk=self.other_note.pk).exists())

    def test_contributors_cannot_do_what_admins_do(self):
        client = self.client_for(self.contributor)
        Event.objects.filter(pk=self.event.pk).update(status=Event.STATUS_DRAFT)
        client.post(reverse('console:event_status', args=[self.event.pk]), {'action': 'publish'})
        self.assertEqual(Event.objects.get(pk=self.event.pk).status, Event.STATUS_DRAFT)
        client.post(reverse('console:event_sources', args=[self.event.pk]), {'source_urls': 'https://evil.example/'})
        self.assertEqual(Event.objects.get(pk=self.event.pk).source_urls, [])
        client.post(reverse('console:team', args=[self.event.pk]), {'action': 'invite', 'email': 'x@example.com',
                                                                    'role': 'admin'})
        self.assertFalse(self.event.invites.exists())
        client.post(reverse('console:event_paid', args=[self.event.pk]), {'paid': '1'})
        self.assertFalse(Event.objects.get(pk=self.event.pk).paid)

    def test_admins_cannot_do_what_owners_do(self):
        client = self.client_for(self.admin)
        client.post(reverse('console:event_delete', args=[self.event.pk]), {'confirm_code': 'PERMA'})
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())
        member = EventMember.objects.get(event=self.event, user=self.contributor)
        client.post(reverse('console:team', args=[self.event.pk]), {'action': 'transfer', 'member': member.pk})
        self.assertEqual(Event.objects.get(pk=self.event.pk).user_id, self.owner.pk)

    def test_an_admin_cannot_demote_another_admin_by_inviting_them(self):
        other_admin = User.objects.create_user('admin2', 'admin2@example.com', 'pw')
        EventMember.objects.create(event=self.event, user=other_admin, role=EventMember.ROLE_ADMIN)
        self.client_for(self.admin).post(reverse('console:team', args=[self.event.pk]),
                                         {'action': 'invite', 'email': 'admin2@example.com', 'role': 'contributor'})
        self.assertEqual(EventMember.objects.get(event=self.event, user=other_admin).role, EventMember.ROLE_ADMIN)


# --- platform areas ---------------------------------------------------------------------------------------------

def _platform_routes():
    return [(route, name, p) for route, name, p in checked_patterns() if P.is_platform(P.declared(p.callback) or '')]


@override_settings(CSP_MODE='off')
class PlatformMatrixTests(Fixture):
    def test_platform_urls_per_role(self):
        routes = _platform_routes()
        self.assertGreaterEqual(len(routes), 25)
        for user in (self.owner, self.contributor, self.stranger, self.staff, self.staff_member,
                     self.platform_member, self.superuser):
            client = self.client_for(user)
            for route, name, p in routes:
                perm = P.declared(p.callback)
                kwargs = _kwargs(route, self)
                if 'event_id' in kwargs:
                    kwargs['event_id'] = self.event.pk
                status = client.get(reverse(name, kwargs=kwargs)).status_code
                if user.has_perm(perm):
                    if len(kwargs) == 0:
                        self.assertIn(status, (200, 302, 405), f'{user.username} {name}')
                else:
                    self.assertEqual(status, 404, f'{user.username} {name}')

    def test_platform_customer_pages_and_writes_are_refused_to_organizers(self):
        client = self.client_for(self.owner)
        self.assertEqual(client.get(reverse('platform:customer', args=[self.other_owner.pk])).status_code, 404)
        self.assertEqual(client.post(reverse('platform:event_save', args=[self.event.pk]), {}).status_code, 404)
        self.assertEqual(client.post(reverse('credit:limits'), {}).status_code, 404)


# --- components and the agent -----------------------------------------------------------------------------------

class ComponentAndAgentTests(Fixture):
    def test_components_per_role(self):
        from console import components as C
        for name, comp in C.registry().items():
            for user in (self.owner, self.staff, self.staff_member, self.platform_member, self.superuser):
                expected = user.has_perm(comp.permission) if P.is_platform(comp.permission) else True
                self.assertEqual(C.allowed(comp, user), expected, f'{name} {user.username}')
            self.assertFalse(C.allowed(comp, AnonymousUser()), name)

    def test_event_components_are_404_for_strangers_and_other_events_rows(self):
        from console import components as C
        for name, comp in C.registry().items():
            if comp.ids != ('event_id',) or P.is_platform(comp.permission):
                continue
            with self.assertRaises(Http404, msg=name):
                C.load(self.stranger, name, {'event_id': self.event.pk})
        for name, key, row in (('location_row', 'location_id', self.other_loc),
                               ('schedule_item_row', 'item_id', self.other_item),
                               ('notification_row', 'notification_id', self.other_note),
                               ('location_form', 'location_id', self.other_loc),
                               ('schedule_item_form', 'item_id', self.other_item)):
            with self.assertRaises(Http404, msg=name):
                C.load(self.contributor, name, {'event_id': self.event.pk, key: row.pk})

    def test_agent_tools_per_role(self):
        from agent import registry
        writes = registry.registry()['writes']
        for user in (self.owner, self.staff, self.platform_member, self.superuser):
            mine = registry.writes_for(user)
            for name, action in writes.items():
                expected = user.has_perm(action.permission) if P.is_platform(action.permission) else True
                self.assertEqual(name in mine, expected, f'{name} {user.username}')
        self.assertNotIn('assign_event_owner', registry.writes_for(self.staff))
        self.assertIn('assign_event_owner', registry.writes_for(self.superuser))
        self.assertEqual(registry.writes_for(AnonymousUser()), {})


# --- signed-in endpoints that take ids --------------------------------------------------------------------------

@override_settings(CSP_MODE='off')
class SignedInIdorTests(Fixture):
    def test_jobs_tasks_reviews_undo_runs_proposals(self):
        from agent.models import AgentRun, ChangeProposal
        from console.models import UndoEntry
        from scrape.models import ChangeSet, IngestJob
        job = IngestJob.objects.create(kind=IngestJob.KIND_REFRESH, event=self.other, created_by=self.other_owner,
                                       status=IngestJob.FAILED, urls=['https://example.com/'])
        cs = ChangeSet.objects.create(event=self.other)
        undo = UndoEntry.objects.create(user=self.other_owner, event=self.other, label='x', operations=[],
                                        expires_at=timezone.now() + timedelta(hours=1))
        run = AgentRun.objects.create(user=self.other_owner, goal='x')
        proposal = ChangeProposal.objects.create(run=run, event=self.other, title='x', changes=[])
        client = self.client_for(self.owner)
        self.assertEqual(client.get(reverse('console:job', args=[job.pk])).status_code, 404)
        self.assertEqual(client.post(reverse('console:task_retry', args=[job.pk])).status_code, 404)
        feed = client.get(reverse('console:tasks') + f'?ids={job.pk}').json()
        self.assertEqual([t for t in feed['tasks'] if t.get('id') == str(job.pk)], [])
        self.assertEqual(client.get(reverse('console:changeset', args=[cs.pk])).status_code, 404)
        self.assertEqual(client.post(reverse('console:undo', args=[undo.pk])).status_code, 404)
        self.assertEqual(client.get(reverse('agent:run', args=[run.pk])).status_code, 404)
        self.assertEqual(client.post(reverse('agent:cancel', args=[run.pk])).status_code, 404)
        self.assertEqual(client.post(reverse('agent:proposal_apply', args=[proposal.pk])).status_code, 404)
        self.assertEqual(IngestJob.objects.filter(event=self.other).count(), 1)

    def test_undo_of_a_team_change_needs_the_role_that_made_it(self):
        from console import undo as U
        from console.models import UndoEntry
        entry = UndoEntry.objects.create(user=self.contributor, event=self.event, label='Removed someone',
                                         operations=[{'model': 'events.eventmember', 'pk': 999, 'op': 'delete',
                                                      'before': None}],
                                         expires_at=timezone.now() + timedelta(hours=1))
        with self.assertRaises(U.CannotUndo):
            U.undo(entry, self.contributor)

    def test_agent_confirm_summaries_never_name_another_events_review(self):
        from agent import summaries
        from scrape.models import ChangeSet
        cs = ChangeSet.objects.create(event=self.other)

        from agent import registry
        from agent.internal import ActionContext
        action = registry.registry()['writes']['discard_ai_changes']
        run = type('Run', (), {'user': self.owner})()
        Ctx = lambda: ActionContext(run, action, {'changeset_id': cs.pk})  # noqa: E731
        self.assertNotIn('PERMB', summaries.discard_changes(Ctx()) or '')
        self.assertEqual(summaries.build_apply_changes({'changeset_id': cs.pk}, Ctx()), {})


# --- lint: raw lookups of event-owned rows in views -------------------------------------------------------------

LINT_FILES = ['console/views.py', 'console/views_events.py', 'console/views_undo.py', 'studio/views.py',
              'agent/views.py', 'agent/components.py', 'agent/proposals.py', 'platform_admin/views.py',
              'credit/views.py', 'prospects/views.py', 'diagnostics/views.py', 'console/components/catalog.py',
              'console/components/catalog_more.py', 'console/components/catalog_chat.py',
              'console/components/catalog_website.py', 'console/components/catalog_screens.py']
OWNED = {'Event', 'Location', 'ScheduledItem', 'Notification', 'ChangeSet', 'EventRevision', 'StudioAsset',
         'SiteVersion', 'EventMember', 'EventInvite', 'Site', 'StudioRun', 'Change', 'IngestJob', 'ChangeProposal',
         'UndoEntry', 'AgentRun'}
SCOPED_BY = {'event', 'event_id', 'site', 'site__event', 'event__in', 'event_id__in', 'user', 'created_by', 'run',
             'run__user', 'changeset', 'prospect'}
# (file, function): why a lookup without an event / owner filter is safe there. Anything new must use
# Model.objects.for_user(user, perm) or filter by the already-checked event, or be added here with its reason.
LINT_ALLOWED = {
    ('events/views_photos.py', 'place_photo'): 'public photo of a published event, team only for drafts, checked right after',
    ('console/views.py', 'account'): 'names of the events in the person\'s own remembered notes',
    ('console/views_events.py', 'wrapper'): 'event_view: has_perm on the event right after',
    ('console/views_events.py', 'event_view'): 'event_view: has_perm on the event right after',
    ('console/views_events.py', 'job_detail'): 'created_by or can_edit on the job\'s event right after',
    ('console/views_events.py', 'tasks_feed'): 'Q(created_by=user) | Q(event__in=for_user) as a positional Q',
    ('console/views_events.py', 'changeset_detail'): 'can_edit on the review\'s event right after',
    ('console/views_events.py', '_set_owner'): 'the event the team view already checked',
    ('console/views_events.py', 'admin_usage'): 'events.view_all_usage (platform admins)',
    ('console/views_undo.py', 'undo_change'): '_answer checks entry.user and undo() the event role',
    ('studio/views.py', 'site_settings'): 'address uniqueness check, nothing is read back',
    ('studio/views.py', 'reports'): 'studio.view_sitereport (staff)',
    ('platform_admin/views.py', 'record_payment'): 'platform admins: any event (change_platformsettings)',
    ('platform_admin/views.py', 'send_invoice'): 'platform admins: any event (change_platformsettings)',
    ('agent/views.py', 'start'): 'can_edit on the event right after (else no event)',
    ('agent/views.py', 'assistant_page'): 'can_edit on the event right after (else no event)',
    ('agent/views.py', '_wait_for_news'): 'the run was loaded for request.user',
    ('agent/components.py', 'act'): 'the run was loaded for request.user',
    ('agent/components.py', 'matching_events'): 'ids from the person\'s own event list',
    ('agent/proposals.py', 'propose_tool'): 'can_edit on the event right after',
    ('platform_admin/views.py', 'event_detail'): 'the Platform admin (every event)',
    ('platform_admin/views.py', 'save_event'): 'the Platform admin (every event)',
    ('diagnostics/views.py', '_event_names'): 'staff diagnostics: names only',
    ('diagnostics/views.py', 'overview'): 'staff diagnostics',
    ('diagnostics/views.py', 'session_detail'): 'staff diagnostics',
    ('console/components/catalog.py', 'editable_event'): 'has_perm on the event right after',
    ('console/components/catalog.py', 'annotated_events'): 'a count subquery of the events already listed',
}


def raw_lookups():
    import ast
    import pathlib
    from django.conf import settings
    found = []
    for name in LINT_FILES:
        tree = ast.parse((pathlib.Path(settings.BASE_DIR) / name).read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                model = None
                if isinstance(node.func, ast.Name) and node.func.id == 'get_object_or_404' and node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Name):
                        model = first.id
                    elif (isinstance(first, ast.Call) and isinstance(first.func, ast.Attribute)
                          and isinstance(first.func.value, ast.Attribute)
                          and isinstance(first.func.value.value, ast.Name)):
                        model = first.func.value.value.id
                elif (isinstance(node.func, ast.Attribute) and node.func.attr in ('get', 'filter')
                      and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == 'objects'
                      and isinstance(node.func.value.value, ast.Name)):
                    model = node.func.value.value.id
                if model in OWNED and not ({k.arg for k in node.keywords} & SCOPED_BY):
                    found.append((name, fn.name, node.lineno, model))
    return found


class RawLookupLintTests(TestCase):
    def test_event_owned_rows_are_looked_up_through_the_event_or_for_user(self):
        bad = [f'{f}:{line} {fn}() {model}' for f, fn, line, model in raw_lookups() if (f, fn) not in LINT_ALLOWED]
        self.assertEqual(bad, [], 'Use Model.objects.for_user(user, perm) or filter by the checked event')
