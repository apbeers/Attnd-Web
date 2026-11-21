"""Recent sign-in check (console/recent_auth.py): risky actions need a sign-in from the last 20 minutes; the Confirm
it's you page (password + second step, or a passkey), its rate limits, and Ask Attnd's confirm gate."""

import time
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from agent.tests import AgentTestCase
from console import recent_auth, totp, twofactor
from events.models import Event, EventMember, Notification

OLD = 25 * 60  # seconds: older than the 20-minute window


@override_settings(SECURE_SSL_REDIRECT=False)
class RecentAuthTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user('owner', 'owner@example.com', 'pw')
        self.helper = User.objects.create_user('helper', 'helper@example.com', 'pw')
        start = timezone.now() + timedelta(days=3)
        self.event = Event.objects.create(name='Prairie Fest', event_code='PRAIRIE', user=self.owner,
                                          timezone='America/Chicago', start_time=start, status=Event.STATUS_PUBLISHED)
        self.member = EventMember.objects.create(event=self.event, user=self.helper, role=EventMember.ROLE_ADMIN)
        self.client.force_login(self.owner)

    def age(self, seconds=OLD, client=None):
        session = (client or self.client).session
        session[recent_auth.KEY] = time.time() - seconds
        session.save()


class SessionMarkTests(RecentAuthTestCase):
    def test_a_sign_in_is_recent_and_ages_out(self):
        self.assertIn(recent_auth.KEY, self.client.session)
        response = self.client.post(reverse('console:account'), {'action': 'email', 'email-email': 'new@example.com',
                                                                 'email-password': 'pw'})
        self.assertEqual(response.status_code, 302)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.email, 'new@example.com')

    def test_password_alone_is_not_recent_with_a_second_factor(self):
        device = twofactor.start_setup(self.owner)
        twofactor.confirm_setup(device, totp.totp(totp._key(device.secret), time.time() - 30))
        client = Client()
        client.post(reverse('login'), {'username': 'owner@example.com', 'password': 'pw'})
        self.assertNotIn(recent_auth.KEY, client.session)


class RiskyActionTests(RecentAuthTestCase):
    def test_email_change_waits_for_the_check(self):
        self.age()
        response = self.client.post(reverse('console:account'), {'action': 'email', 'email-email': 'x@example.com'},
                                    HTTP_REFERER='http://testserver/console/account/')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith(reverse('confirm_identity')))
        self.assertIn('next=%2Fconsole%2Faccount%2F', response['Location'])
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.email, 'owner@example.com')

    def test_password_change_and_json_answer(self):
        self.age()
        response = self.client.post(reverse('console:account'), {'action': 'password'}, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertIn(reverse('confirm_identity'), response.json()['reauth'])

    def test_renaming_a_passkey_and_preferences_need_no_check(self):
        self.age()
        response = self.client.post(reverse('console:account'), {'action': 'notify'})
        self.assertEqual(response['Location'], reverse('console:account'))

    def test_adding_a_passkey_or_authenticator_needs_it(self):
        self.age()
        response = self.client.post(reverse('passkey_register_options'), '{}', content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertIn('reauth', response.json())
        response = self.client.get(reverse('two_factor_setup'))
        self.assertTrue(response['Location'].startswith(reverse('confirm_identity')))

    def test_delete_event(self):
        self.age()
        url = reverse('console:event_delete', args=[self.event.pk])
        response = self.client.post(url, {'confirm_code': 'PRAIRIE'})
        self.assertTrue(response['Location'].startswith(reverse('confirm_identity')))
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())
        self.age(0)
        self.client.post(url, {'confirm_code': 'PRAIRIE'})
        self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())

    def test_remove_member_and_transfer(self):
        self.age()
        url = reverse('console:team', args=[self.event.pk])
        for action in ('remove', 'transfer'):
            response = self.client.post(url, {'action': action, 'member': self.member.pk})
            self.assertTrue(response['Location'].startswith(reverse('confirm_identity')), action)
        self.assertTrue(EventMember.objects.filter(pk=self.member.pk).exists())
        self.assertEqual(Event.objects.get(pk=self.event.pk).user, self.owner)
        # inviting and roles don't need it
        self.client.post(url, {'action': 'role', 'member': self.member.pk, 'role': 'contributor'})
        self.assertEqual(EventMember.objects.get(pk=self.member.pk).role, 'contributor')

    def test_urgent_notification_works_with_an_old_sign_in(self):
        self.age(3 * 3600)
        response = self.client.post(reverse('console:notifications', args=[self.event.pk]), {
            'title': 'Gates closed', 'body': 'Storm: shelter in the hall', 'when': 'now', 'urgent': 'on'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('console:notifications', args=[self.event.pk]))
        n = Notification.objects.get(event=self.event)
        self.assertTrue(n.is_urgent)
        self.assertTrue(n.sent)


class ConfirmPageTests(RecentAuthTestCase):
    def test_password_confirms_and_goes_back(self):
        self.age()
        url = reverse('confirm_identity') + '?next=/console/account/'
        self.assertContains(self.client.get(url), 'Confirm it’s you')
        bad = self.client.post(url, {'password': 'nope', 'next': '/console/account/'})
        self.assertContains(bad, 'That password isn’t right')
        self.assertFalse(recent_auth.is_recent(bad.wsgi_request))
        ok = self.client.post(url, {'password': 'pw', 'next': '/console/account/'})
        self.assertEqual(ok['Location'], '/console/account/')
        self.assertTrue(recent_auth.is_recent(ok.wsgi_request))

    def test_unsafe_next_goes_to_the_console(self):
        response = self.client.post(reverse('confirm_identity'), {'password': 'pw', 'next': 'https://evil.example/'})
        self.assertNotIn('evil', response['Location'])

    def test_second_factor_needs_its_code_too(self):
        device = twofactor.start_setup(self.owner)
        twofactor.confirm_setup(device, totp.totp(totp._key(device.secret), time.time() - 30))
        session = self.client.session
        session[twofactor.SESSION_KEY] = str(self.owner.pk)  # signed in fully earlier
        session.save()
        self.age()
        url = reverse('confirm_identity')
        page = self.client.get(url)
        self.assertContains(page, 'name="code"')
        self.assertContains(self.client.post(url, {'password': 'pw', 'code': '000000'}), 'That code didn’t work')
        code = totp.totp(totp._key(device.secret), time.time())
        ok = self.client.post(url, {'password': 'pw', 'code': code, 'next': '/console/'})
        self.assertEqual(ok.status_code, 302)
        self.assertTrue(recent_auth.is_recent(ok.wsgi_request))

    def test_wrong_passwords_are_rate_limited_like_sign_in(self):
        url = reverse('confirm_identity')
        for _ in range(5):
            self.client.post(url, {'password': 'nope'})
        limited = self.client.post(url, {'password': 'pw'})
        self.assertEqual(limited.status_code, 429)
        self.assertFalse(recent_auth.is_recent(limited.wsgi_request) and limited.status_code == 302)

    def test_needs_a_sign_in(self):
        response = Client().get(reverse('confirm_identity'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])


class AgentRecentAuthTests(AgentTestCase):
    def test_guarded_confirm_shows_the_check_and_runs_nothing(self):
        run, _ = self.run_with([[('delete_event', {'event_id': self.event.pk})]])
        pending = self.client.get(reverse('agent:run', args=[run.pk]), secure=True).json()['run']['pending']
        session = self.client.session
        session[recent_auth.KEY] = time.time() - OLD
        session.save()
        response = self.client.post(reverse('agent:confirm', args=[run.pk]),
                                    {'step': pending['step'], 'hash': pending['hash']}, secure=True)
        self.assertEqual(response.status_code, 403)
        self.assertIn(reverse('confirm_identity'), response.json()['reauth'])
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())
        run.refresh_from_db()
        self.assertEqual(run.status, 'waiting_confirmation')  # still waiting: confirm again after the check
        session = self.client.session
        session[recent_auth.KEY] = time.time()
        session.save()
        with self.captureOnCommitCallbacks(execute=False):
            ok = self.client.post(reverse('agent:confirm', args=[run.pk]),
                                  {'step': pending['step'], 'hash': pending['hash']}, secure=True)
        self.assertEqual(ok.status_code, 200)

    def test_the_internal_request_checks_the_same_session(self):
        from agent import internal, registry
        session = self.client.session
        session[recent_auth.KEY] = time.time() - OLD
        session.save()
        run = self.make_run()
        out = internal.execute(run, registry.registry()['writes']['delete_event'], {'event_id': self.event.pk})
        self.assertFalse(out['ok'])
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())

    def test_urgent_notification_is_not_guarded(self):
        self.assertNotIn('send_notification', recent_auth.GUARDED_ACTIONS)
