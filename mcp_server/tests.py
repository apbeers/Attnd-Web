"""The MCP server (docs/mcp_plan.md): OAuth with grants, the tool service, the confirm page and the protocol over ASGI."""

import base64
import hashlib
import json
import secrets
from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from oauth2_provider.models import Application

from agent.models import AgentRun, AgentStep
from console.testing import TwoFactorClient
from events.models import Event
from mcp_server import grants, service
from mcp_server.models import McpGrant

CALLBACK = 'https://claude.ai/api/mcp/auth_callback'


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    return verifier, challenge


class McpTestCase(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('staff', 'staff@example.com', 'pw-long-enough-1', is_staff=True)
        self.organizer = User.objects.create_user('org', 'org@example.com', 'pw-long-enough-2')
        self.app = Application.objects.create(
            name='Claude', client_type=Application.CLIENT_PUBLIC, redirect_uris=CALLBACK,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE)
        self.event = Event.objects.create(name='Prairie Fest', event_code='PRAIRIE', user=self.staff,
                                          start_time=timezone.now() + timedelta(days=3))

    def connect(self, user=None, client=None, resource=None, client_id=None):
        """The whole OAuth dance as Claude does it: consent, then the code exchange. Returns the token JSON."""
        user = user or self.staff
        client = client or TwoFactorClient()
        client.force_login(user)
        verifier, challenge = pkce()
        client_id = client_id or self.app.client_id
        params = {'response_type': 'code', 'client_id': client_id, 'redirect_uri': CALLBACK,
                  'scope': 'mcp offline_access', 'state': 'xyz', 'code_challenge': challenge,
                  'code_challenge_method': 'S256', 'resource': resource or settings.MCP_RESOURCE_URL}
        page = client.get(reverse('oauth2_provider:authorize'), params, secure=True)
        self.assertEqual(page.status_code, 200, page.content[:500])
        self.assertContains(page, 'Connect Claude to Attnd?')
        self.assertContains(page, 'claude.ai')
        done = client.post(reverse('oauth2_provider:authorize'), {**params, 'allow': 'Authorize'}, secure=True)
        # The browser must be allowed to follow the redirect back to the assistant (CSP form-action)
        policy = page.headers.get('Content-Security-Policy', '')
        self.assertIn('https://claude.ai', policy.split('form-action', 1)[1].split(';', 1)[0])
        self.assertEqual(done.status_code, 302, done.content[:500])
        query = parse_qs(urlsplit(done['Location']).query)
        self.assertEqual(query['state'], ['xyz'])
        self.assertEqual(query['iss'], [settings.SITE_URL])  # RFC 9207
        tokens = Client().post(reverse('oauth2_provider:token'), {
            'grant_type': 'authorization_code', 'code': query['code'][0], 'redirect_uri': CALLBACK,
            'client_id': client_id, 'code_verifier': verifier,
            'resource': resource or settings.MCP_RESOURCE_URL}, secure=True)
        self.assertEqual(tokens.status_code, 200, tokens.content[:500])
        return tokens.json()

    def refresh(self, refresh_token):
        return Client().post(reverse('oauth2_provider:token'), {
            'grant_type': 'refresh_token', 'refresh_token': refresh_token, 'client_id': self.app.client_id},
            secure=True)


class OAuthTests(McpTestCase):
    def test_connect_makes_a_grant_with_its_own_session(self):
        tokens = self.connect()
        self.assertIn('refresh_token', tokens)
        self.assertEqual(tokens['expires_in'], 3600)
        grant = McpGrant.objects.get()
        self.assertEqual((grant.user, grant.client_name, grant.redirect_host), (self.staff, 'Claude', 'claude.ai'))
        principal = grants.principal(tokens['access_token'])
        self.assertEqual(principal.user, self.staff)
        self.assertEqual(principal.grant, grant)
        # Tokens are stored as checksums only (RFC 9700)
        from oauth2_provider.models import AccessToken
        self.assertFalse(AccessToken.objects.filter(token=tokens['access_token']).exists())

    def test_claudes_client_metadata_document(self):
        """Claude identifies itself with a metadata URL whose document also lists the jwt-bearer grant (Nov 25, 2026:
        the toolkit refused it); only the grants we offer are kept, and the whole connection works."""
        claude = 'https://claude.ai/oauth/mcp-oauth-client-metadata'
        document = {'client_id': claude, 'client_name': 'Claude', 'client_uri': 'https://claude.ai',
                    'redirect_uris': [CALLBACK], 'response_types': ['code'], 'token_endpoint_auth_method': 'none',
                    'grant_types': ['authorization_code', 'refresh_token',
                                    'urn:ietf:params:oauth:grant-type:jwt-bearer']}
        with mock.patch('oauth2_provider.cimd.SafeMetadataFetcher.fetch', return_value=(document, 3600)):
            tokens = self.connect(client_id=claude)
        self.assertIsNotNone(grants.principal(tokens['access_token']))
        self.assertEqual(McpGrant.objects.get().client_name, 'Claude')
        # Another host's document is refused
        client = TwoFactorClient()
        client.force_login(self.staff)
        other = 'https://evil.example/client.json'
        with mock.patch('oauth2_provider.cimd.SafeMetadataFetcher.fetch', return_value=({**document, 'client_id': other}, 3600)):
            response = client.get(reverse('oauth2_provider:authorize'), {
                'response_type': 'code', 'client_id': other, 'redirect_uri': CALLBACK, 'code_challenge': 'x' * 43,
                'code_challenge_method': 'S256'}, secure=True)
        self.assertEqual(response.status_code, 400)

    def test_refresh_rotates_and_a_reused_token_ends_the_connection(self):
        tokens = self.connect()
        again = self.refresh(tokens['refresh_token'])
        self.assertEqual(again.status_code, 200, again.content)
        fresh = again.json()
        self.assertNotEqual(fresh['refresh_token'], tokens['refresh_token'])
        self.assertIsNotNone(grants.principal(fresh['access_token']))
        provider = {**settings.OAUTH2_PROVIDER, 'REFRESH_TOKEN_GRACE_PERIOD_SECONDS': 0}
        with override_settings(OAUTH2_PROVIDER=provider):
            reused = self.refresh(tokens['refresh_token'])
            self.assertEqual(reused.status_code, 400)
            self.assertEqual(self.refresh(fresh['refresh_token']).status_code, 400)  # the whole family is gone

    def test_refresh_outlives_the_browser_session(self):
        browser = TwoFactorClient()
        tokens = self.connect(client=browser)
        browser.logout()  # signing out of the browser doesn't end the assistant's connection
        self.assertIsNotNone(grants.principal(tokens['access_token']))
        self.assertEqual(self.refresh(tokens['refresh_token']).status_code, 200)

    def test_revoke_password_change_staff_removal_and_age_end_it(self):
        for end in ('revoke', 'password', 'staff', 'age'):
            with self.subTest(end):
                tokens = self.connect()
                grant = McpGrant.objects.order_by('-pk').first()
                if end == 'revoke':
                    grants.revoke(grant, 'test')
                elif end == 'password':
                    self.staff.set_password('a-new-password-3')
                    self.staff.save()
                elif end == 'staff':
                    User.objects.filter(pk=self.staff.pk).update(is_staff=False)
                else:
                    McpGrant.objects.filter(pk=grant.pk).update(created_at=timezone.now() - timedelta(days=91))
                self.staff.refresh_from_db()
                self.assertIsNone(grants.principal(tokens['access_token']))
                self.assertEqual(self.refresh(tokens['refresh_token']).status_code, 400)
                grant.refresh_from_db()
                self.assertIsNotNone(grant.revoked_at)
                User.objects.filter(pk=self.staff.pk).update(is_staff=True)
                self.staff.refresh_from_db()

    def test_only_staff_and_only_this_server(self):
        client = TwoFactorClient()
        client.force_login(self.organizer)
        response = client.get(reverse('oauth2_provider:authorize'), {'client_id': self.app.client_id,
                                                                     'response_type': 'code'}, secure=True)
        self.assertEqual(response.status_code, 404)
        client.force_login(self.staff)
        response = client.get(reverse('oauth2_provider:authorize'), {
            'client_id': self.app.client_id, 'response_type': 'code', 'redirect_uri': CALLBACK,
            'resource': 'https://attnd.app.evil.example/mcp'}, secure=True)
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(grants.principal('not-a-token'))

    def test_signed_out_people_are_sent_to_sign_in(self):
        response = Client().get(reverse('oauth2_provider:authorize'), {'client_id': self.app.client_id},
                                secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_metadata(self):
        server = Client().get('/.well-known/oauth-authorization-server', secure=True).json()
        self.assertEqual(server['issuer'], settings.SITE_URL)
        self.assertEqual(server['code_challenge_methods_supported'], ['S256'])
        self.assertTrue(server['client_id_metadata_document_supported'])
        self.assertIn('none', server['token_endpoint_auth_methods_supported'])
        self.assertNotIn('registration_endpoint', server)
        self.assertEqual(sorted(server['grant_types_supported']), ['authorization_code', 'refresh_token'])
        for path in ('/.well-known/oauth-protected-resource', '/.well-known/oauth-protected-resource/mcp'):
            resource = Client().get(path, secure=True).json()
            self.assertEqual(resource['resource'], settings.MCP_RESOURCE_URL, path)
            self.assertEqual(resource['authorization_servers'], [settings.SITE_URL])
        with override_settings(MCP_ENABLED=False):
            self.assertEqual(Client().get('/.well-known/oauth-authorization-server', secure=True).status_code, 404)


class ServiceTests(McpTestCase):
    def setUp(self):
        super().setUp()
        self.tokens = self.connect()
        self.principal = grants.principal(self.tokens['access_token'])

    def test_the_tool_list(self):
        names = {t['name'] for t in service.tools(self.staff)}
        self.assertTrue({'list_events', 'search_events', 'read_page', 'get_component', 'search_everything',
                         'show_event_overview', 'set_prospects_status', 'save_prospect', 'create_test_event',
                         'approve_event_claim'} <= names, names)
        self.assertFalse(names & {'ask_user', 'finish', 'show', 'fill_form', 'remember', 'propose_changes',
                                  'run_prospect_discovery', 'delete_event'})
        # Editing (owner, Nov 26, 2026): writes run; sending and publishing come back as a confirmation link.
        self.assertTrue({'add_location', 'edit_location', 'add_schedule_item', 'edit_schedule_item',
                         'send_notification', 'publish_changes', 'draft_prospect_pitch', 'show_event_usage',
                         'show_event_billing'} <= names, names)
        app = next(t for t in service.tools(self.staff) if t['name'] == 'show_event_overview')
        self.assertEqual(app['_meta'], {'ui': {'resourceUri': service.CARD_URI}})
        cards = next(t for t in service.tools(self.staff) if t['name'] == 'show_component')
        self.assertEqual(cards['_meta'], {'ui': {'resourceUri': service.CARD_URI}})
        self.assertIn('event_tile', cards['inputSchema']['properties']['component']['enum'])

    def test_a_read_runs_as_the_person_and_is_logged_as_an_mcp_run(self):
        out = service.call(self.principal, 'list_events', {})
        self.assertFalse(out['is_error'], out['text'])
        self.assertIn('Prairie Fest', out['text'])
        run = AgentRun.objects.get(source=AgentRun.SOURCE_MCP)
        self.assertEqual((run.status, run.client, run.goal), (AgentRun.DONE, 'Claude', 'list_events'))
        self.assertEqual(run.session_key, self.principal.grant.session_key)
        # The chat's own lists leave it out
        client = TwoFactorClient()
        client.force_login(self.staff)
        runs = client.get(reverse('agent:runs'), secure=True).json()['runs']
        self.assertEqual(runs, [])

    def test_unknown_tools_are_refused(self):
        out = service.call(self.principal, 'delete_event', {'event_id': self.event.pk})
        self.assertTrue(out['is_error'])
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())

    def test_an_always_ask_action_waits_for_the_browser(self):
        from prospects.models import Prospect
        prospect = Prospect.objects.create(name='Art Walk', urls=['https://art.example/'])
        out = service.call(self.principal, 'create_test_event', {'prospect_id': prospect.pk, 'pk': prospect.pk})
        self.assertFalse(out['is_error'], out['text'])
        self.assertEqual(out['structured']['status'], 'waiting_for_confirmation')
        step = AgentStep.objects.get(confirm_state=AgentStep.CONFIRM_PENDING)
        self.assertEqual(step.kind, AgentStep.KIND_BUTTON)
        self.assertIn(reverse('mcp:confirm', args=[step.pk]), out['structured']['confirm_url'])
        browser = TwoFactorClient()
        browser.force_login(self.staff)
        page = browser.get(reverse('mcp:confirm', args=[step.pk]), secure=True)
        self.assertContains(page, 'Nothing happens until you press Confirm')
        wrong = browser.post(reverse('mcp:confirm', args=[step.pk]), {'decision': 'confirm', 'hash': 'x'}, secure=True)
        self.assertContains(wrong, 'doesn')
        with mock.patch('agent.loop._run_button') as runs:
            browser.post(reverse('mcp:confirm', args=[step.pk]), {'decision': 'confirm', 'hash': step.confirm_hash},
                         secure=True)
        runs.assert_called_once()
        self.assertEqual(runs.call_args.args[0].session_key, browser.session.session_key)  # as the browser
        # Someone else can't see it
        other = TwoFactorClient()
        other.force_login(User.objects.create_user('s2', 's2@example.com', 'pw-long-enough-4', is_staff=True))
        self.assertEqual(other.get(reverse('mcp:confirm', args=[step.pk]), secure=True).status_code, 404)

    def test_event_overview_app(self):
        out = service.call(self.principal, 'show_event_overview', {'event_id': self.event.pk})
        self.assertFalse(out['is_error'], out['text'])
        self.assertIn('Prairie Fest', out['structured']['html'])
        self.assertIn('component--event_overview', out['structured']['html'])
        self.assertNotIn('<', out['text'])  # the text is the component's JSON
        theirs = Event.objects.create(name='Theirs', event_code='THEIRS', user=self.organizer,
                                      start_time=timezone.now())
        self.assertTrue(service.call(self.principal, 'show_event_overview', {'event_id': theirs.pk})['is_error'])
        html = service.read_resource(service.OVERVIEW_URI)
        self.assertEqual(html['mimeType'], 'text/html;profile=mcp-app')
        self.assertIn('ui/initialize', html['text'])
        self.assertIn('https://tile.openstreetmap.org', service.read_resource(service.CARD_URI)['_meta']['ui']['csp']
                      ['resourceDomains'])

    def test_cards_work_inside_a_sandboxed_iframe(self):
        # Nov 25, 2026 (tested from a Claude chat on dev): the QR images and links were relative console addresses
        # (no Attnd origin and no cookies in the client's iframe), the map stayed blank and times were in UTC.
        import re
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from events.models import Location
        chicago = ZoneInfo('America/Chicago')
        Event.objects.filter(pk=self.event.pk).update(
            timezone='America/Chicago', start_time=datetime(2026, 11, 25, 19, 15, tzinfo=chicago),
            end_time=datetime(2026, 11, 25, 23, 0, tzinfo=chicago), latitude=32.75, longitude=-97.33)
        Location.objects.create(event=self.event, name='Main Stage', latitude=32.751, longitude=-97.331)
        Location.objects.create(event=self.event, name='Taco Row', latitude=32.752, longitude=-97.329)
        html = service.call(self.principal, 'show_event_overview', {'event_id': self.event.pk})['structured']['html']
        self.assertNotRegex(html, r'(src|href|action)="/(?!/)')  # every address absolute
        self.assertNotIn('qr.png"', html.replace('qr.png?download', ''))  # the QR is inline…
        self.assertIn('src="data:image/png;base64,', html)
        self.assertIn('target="_blank"', html)
        self.assertNotIn('data-event-map', html)  # …and the map a static picture with its two pins
        self.assertEqual(len(re.findall(r'<title>(Main Stage|Taco Row)</title>', html)), 2)
        self.assertIn('tile.openstreetmap.org/', html)
        self.assertNotIn('Thu Nov 26', html)  # 7:15 PM Central, not 1:15 AM UTC
        out = service.call(self.principal, 'show_component', {'component': 'event_tile', 'items': [{}]})
        self.assertFalse(out['is_error'], out['text'])
        self.assertIn('component--event_tile', out['structured']['html'])
        self.assertIn('Wed Nov 25, 2026', out['structured']['html'])
        self.assertTrue(service.call(self.principal, 'show_component', {'component': 'nope', 'items': [{}]})['is_error'])

    def test_connected_apps_page(self):
        client = TwoFactorClient()
        client.force_login(self.staff)
        page = client.get(reverse('platform:connected_apps'), secure=True)
        self.assertContains(page, 'Claude')
        self.assertContains(page, settings.MCP_RESOURCE_URL)
        grant = McpGrant.objects.get()
        client.post(reverse('platform:connected_app_revoke'), {'grant_id': grant.pk}, secure=True)
        grant.refresh_from_db()
        self.assertIsNotNone(grant.revoked_at)
        self.assertIsNone(grants.principal(self.tokens['access_token']))
        organizer = TwoFactorClient()
        organizer.force_login(self.organizer)
        self.assertEqual(organizer.get(reverse('platform:connected_apps'), secure=True).status_code, 404)


class ProtocolTests(McpTestCase):
    """The ASGI app at /mcp, as Claude talks to it."""

    def setUp(self):
        super().setUp()
        self.token = self.connect()['access_token']

    def exchange(self, *messages, token=None):
        import httpx

        from mcp_server.server import McpApp, Router

        async def fallback(scope, receive, send):
            raise AssertionError('only /mcp in these tests')

        async def go():
            app = McpApp()
            transport = httpx.ASGITransport(app=Router(fallback, app))
            headers = {'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-11-25'}
            if token is not False:
                headers['Authorization'] = f'Bearer {token or self.token}'
            out = []
            async with httpx.AsyncClient(transport=transport, base_url='http://testserver') as http:
                for message in messages:
                    out.append(await http.post('/mcp', json=message, headers=headers))
            if getattr(app, '_task', None):
                app._task.cancel()
            return out
        return async_to_sync(go)()

    def test_without_a_token_the_client_is_told_where_to_sign_in(self):
        (response,) = self.exchange({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, token=False)
        self.assertEqual(response.status_code, 401)
        challenge = response.headers['www-authenticate']
        self.assertIn(f'resource_metadata="{settings.SITE_URL}/.well-known/oauth-protected-resource/mcp"', challenge)
        self.assertIn('scope="mcp"', challenge)
        (response,) = self.exchange({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, token='nope')
        self.assertEqual(response.status_code, 401)
        self.assertIn('error="invalid_token"', response.headers['www-authenticate'])

    def test_initialize_list_call_and_the_app_resource(self):
        init = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}}
        listing = {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}}
        call = {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                'params': {'name': 'list_events', 'arguments': {}}}
        read = {'jsonrpc': '2.0', 'id': 4, 'method': 'resources/read', 'params': {'uri': service.OVERVIEW_URI}}
        responses = self.exchange(init, listing, call, read)
        for response in responses:
            self.assertEqual(response.status_code, 200, response.text[:500])
        first, tools, called, resource = (r.json() for r in responses)
        self.assertEqual(first['result']['serverInfo']['name'], 'attnd')
        self.assertIn('tools', first['result']['capabilities'])
        names = {t['name'] for t in tools['result']['tools']}
        self.assertIn('show_event_overview', names)
        self.assertIn('Prairie Fest', json.dumps(called['result']))
        self.assertFalse(called['result'].get('isError'))
        self.assertEqual(resource['result']['contents'][0]['mimeType'], 'text/html;profile=mcp-app')


class McpToolsRoundTwoTests(McpTestCase):
    """Owner, Nov 26, 2026: editing, usage, billing and prospect pitch drafts over MCP."""

    def setUp(self):
        super().setUp()
        self.principal = grants.principal(self.connect()['access_token'])

    def test_editing_runs_and_sending_waits_for_a_confirmation(self):
        from events.models import Location
        out = service.call(self.principal, 'add_location', {'event_id': self.event.pk, 'name': 'Taco Row'})
        self.assertFalse(out['is_error'], out['text'])
        self.assertTrue(Location.objects.filter(event=self.event, name='Taco Row').exists())
        out = service.call(self.principal, 'send_notification',
                           {'event_id': self.event.pk, 'title': 'Gates open', 'body': 'Come on in', 'when': 'now'})
        self.assertFalse(out['is_error'], out['text'])
        self.assertEqual(out['structured']['status'], 'waiting_for_confirmation')
        self.assertIn('/console/mcp/confirm/', out['structured']['confirm_url'])
        self.assertFalse(self.event.notification_set.filter(sent=True).exists())

    def test_usage_and_billing_cards(self):
        usage = service.call(self.principal, 'show_event_usage', {'event_id': self.event.pk})
        self.assertFalse(usage['is_error'], usage['text'])
        self.assertIn('component--event_metrics', usage['structured']['html'])
        self.assertIn('component--usage_answer', usage['structured']['html'])
        billing = service.call(self.principal, 'show_event_billing', {'event_id': self.event.pk})
        self.assertFalse(billing['is_error'], billing['text'])
        self.assertIn('component--event_billing', billing['structured']['html'])
        names = {t['name'] for t in service.tools(self.staff)}
        self.assertNotIn('show_billing_queue', names)  # platform admins only
        self.staff.is_superuser = True
        self.staff.save()
        self.assertIn('show_billing_queue', {t['name'] for t in service.tools(self.staff)})

    def test_pitch_drafts_follow_can_spam_and_never_reach_do_not_contact(self):
        from prospects.models import PitchDraft, Prospect, ProspectContact
        prospect = Prospect.objects.create(name='Riverside Fest', organizer='River Org', urls=['https://river.example/'])
        blocked = ProspectContact.objects.create(prospect=prospect, name='Dee', email='dee@river.example',
                                                 source_url='https://river.example/contact', do_not_contact=True)
        out = service.call(self.principal, 'draft_prospect_pitch', {'pk': prospect.pk})
        self.assertIn('test event first', out['text'])  # no event yet
        prospect.event = self.event
        prospect.save()
        out = service.call(self.principal, 'draft_prospect_pitch', {'pk': prospect.pk})
        self.assertIn('do not contact', out['text'])
        out = service.call(self.principal, 'draft_prospect_pitch', {'pk': prospect.pk, 'contact': blocked.pk})
        self.assertIn('do not contact', out['text'])
        self.assertFalse(PitchDraft.objects.exists())
        ok = ProspectContact.objects.create(prospect=prospect, name='Sam Lee', email='sam@river.example',
                                            source_url='https://river.example/team')
        out = service.call(self.principal, 'draft_prospect_pitch', {'pk': prospect.pk, 'sender_name': 'Andrew'})
        self.assertFalse(out['is_error'], out['text'])
        pitch = PitchDraft.objects.get()
        self.assertEqual((pitch.contact, pitch.to), (ok, 'sam@river.example'))
        for needed in ('Hi Sam,', f'/join/{self.event.event_code}/qr.svg', 'Andrew', 'promotional message',
                       'unsubscribe', 'mailing address', 'https://river.example/team'):
            self.assertIn(needed, pitch.body)
        self.assertIn('not sent', prospect.activity.first().text)
