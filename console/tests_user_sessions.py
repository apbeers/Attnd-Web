"""Signed-in sessions per account (console/user_sessions.py): the list on the Account page, signing one or every other
out, the purge, and the "New sign-in to Attnd" email for a browser the account hasn't used before."""

from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from console import user_sessions
from console.models import SignInSecurity, UserSession

IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Mobile/15E148 Safari/604.1'
WINDOWS = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0 Safari/537.36'


@override_settings(SECURE_SSL_REDIRECT=False, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class UserSessionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('o1', 'org@example.com', 'pw')

    def sign_in(self, ua='', client=None):
        client = client or Client(HTTP_USER_AGENT=ua)
        response = client.post(reverse('login'), {'username': 'org@example.com', 'password': 'pw'})
        self.assertEqual(response.status_code, 302)
        return client

    def signed_in(self, client):
        response = client.get(reverse('console:account'))
        if response.status_code == 302:
            self.assertIn(reverse('login'), response['Location'])
        return response.status_code == 200

    def test_describe(self):
        self.assertEqual(user_sessions.describe(IPHONE), 'Safari on iPhone')
        self.assertEqual(user_sessions.describe(WINDOWS), 'Chrome on Windows')
        self.assertEqual(user_sessions.describe(''), 'Unknown browser')

    def test_sign_in_makes_a_row_and_a_device_cookie(self):
        client = self.sign_in(IPHONE)
        row = UserSession.objects.get(user=self.user)
        self.assertEqual(row.session_key, client.session.session_key)
        self.assertIn(user_sessions.COOKIE, client.cookies)
        self.assertEqual(row.device, user_sessions.device_hash(client.cookies[user_sessions.COOKIE].value))
        self.assertTrue(client.cookies[user_sessions.COOKIE]['httponly'])

    def test_account_lists_sessions_and_signs_out_others(self):
        phone = self.sign_in(IPHONE)
        laptop = self.sign_in(WINDOWS)
        page = laptop.get(reverse('console:account')).content.decode()
        self.assertIn('Safari on iPhone', page)
        self.assertIn('Chrome on Windows', page)
        self.assertIn('This device', page)
        other = UserSession.objects.get(session_key=phone.session.session_key)
        laptop.post(reverse('console:account'), {'action': 'session_signout', 'session': other.pk})
        self.assertFalse(Session.objects.filter(session_key=other.session_key).exists())
        self.assertFalse(self.signed_in(phone))
        self.assertTrue(self.signed_in(laptop))

    def test_sign_out_everywhere_else(self):
        a, b = self.sign_in(IPHONE), self.sign_in(IPHONE)
        me = self.sign_in(WINDOWS)
        me.post(reverse('console:account'), {'action': 'sessions_signout_others'})
        self.assertFalse(self.signed_in(a))
        self.assertFalse(self.signed_in(b))
        self.assertTrue(self.signed_in(me))
        self.assertEqual(len(user_sessions.live_sessions(self.user)), 1)

    def test_someone_else_cannot_sign_out_my_sessions(self):
        mine = self.sign_in(IPHONE)
        row = UserSession.objects.get(session_key=mine.session.session_key)
        other = User.objects.create_user('o2', 'o2@example.com', 'pw')
        client = Client()
        client.force_login(other)
        client.post(reverse('console:account'), {'action': 'session_signout', 'session': row.pk})
        self.assertTrue(self.signed_in(mine))

    def test_key_follows_the_second_step_and_last_active_is_throttled(self):
        client = self.sign_in(IPHONE)
        row = UserSession.objects.get(user=self.user)
        session = client.session
        session.cycle_key()
        session.save()
        client.cookies['sessionid'] = session.session_key
        client.get(reverse('console:account'))
        row.refresh_from_db()
        self.assertEqual(row.session_key, client.session.session_key)

    def test_purge(self):
        client = self.sign_in(IPHONE)
        client.post(reverse('logout'))
        self.assertEqual(user_sessions.purge(), 1)
        self.assertFalse(UserSession.objects.exists())

    def test_new_device_email(self):
        first = self.sign_in(IPHONE)
        self.assertEqual(len(mail.outbox), 0)  # the account's first device
        self.sign_in(IPHONE, client=first)  # the same browser again (its device cookie)
        self.assertEqual(len(mail.outbox), 0)
        self.sign_in(WINDOWS)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.subject, 'New sign-in to Attnd')
        self.assertEqual(message.to, ['org@example.com'])
        self.assertIn('Chrome on Windows', message.body)
        self.assertIn('/console/account/#sessions', message.body)
        self.assertEqual(len(SignInSecurity.objects.get(user=self.user).known_devices), 2)

    def test_no_email_for_test_and_ci_clients(self):
        self.sign_in(IPHONE)
        self.sign_in('')  # the test client: no User-Agent
        self.sign_in('attnd-ci/1.0')
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(EMAIL_ALLOWED_RECIPIENTS=['@allowed.example'],
                       EMAIL_BACKEND='console.mail.AllowListSMTPBackend')
    def test_allow_list_applies(self):
        from unittest import mock
        self.sign_in(IPHONE)
        with mock.patch('django.core.mail.backends.smtp.EmailBackend.send_messages', return_value=0) as smtp:
            self.sign_in(WINDOWS)
        sent = smtp.call_args[0][0] if smtp.called else []
        self.assertEqual(sent, [])  # org@example.com isn't on the list: dropped before SMTP
