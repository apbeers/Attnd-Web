"""Signed in until midnight in the person's own time zone (console/session_expiry.py)."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from unittest import mock

from console import session_expiry
from console import tests_passkeys as passkey_tests

UTC = ZoneInfo('UTC')


class NextMidnightTests(SimpleTestCase):
    def at(self, zone, *parts):
        return datetime(*parts, tzinfo=ZoneInfo(zone))

    def test_chicago_afternoon(self):
        now = self.at('America/Chicago', 2026, 11, 21, 15, 0)
        self.assertEqual(session_expiry.next_midnight('America/Chicago', now),
                         self.at('America/Chicago', 2026, 11, 22, 0, 0).astimezone(UTC))

    def test_tokyo(self):
        now = self.at('Asia/Tokyo', 2026, 11, 21, 9, 0)
        self.assertEqual(session_expiry.next_midnight('Asia/Tokyo', now), datetime(2026, 11, 21, 15, 0, tzinfo=UTC))

    def test_late_evening_gets_the_midnight_after(self):
        now = self.at('America/Chicago', 2026, 11, 21, 23, 30)
        self.assertEqual(session_expiry.next_midnight('America/Chicago', now),
                         self.at('America/Chicago', 2026, 11, 23, 0, 0).astimezone(UTC))
        # an hour or more before midnight: that midnight
        now = self.at('America/Chicago', 2026, 11, 21, 22, 59)
        self.assertEqual(session_expiry.next_midnight('America/Chicago', now),
                         self.at('America/Chicago', 2026, 11, 22, 0, 0).astimezone(UTC))

    def test_invalid_zone_falls_back_to_chicago(self):
        now = self.at('America/Chicago', 2026, 11, 21, 12, 0)
        expected = self.at('America/Chicago', 2026, 11, 22, 0, 0).astimezone(UTC)
        for bad in ('', 'Mars/Olympus', '../../etc/passwd', '/etc/localtime', 'x' * 80):
            self.assertEqual(session_expiry.next_midnight(bad, now), expected, bad)

    def test_dst_change(self):
        now = self.at('America/Chicago', 2026, 11, 1, 0, 30)  # the night clocks go back
        self.assertEqual(session_expiry.next_midnight('America/Chicago', now),
                         self.at('America/Chicago', 2026, 11, 2, 0, 0).astimezone(UTC))

    def test_settings_never_cut_it_short(self):
        self.assertFalse(settings.SESSION_EXPIRE_AT_BROWSER_CLOSE)
        self.assertGreaterEqual(settings.SESSION_COOKIE_AGE, 2 * 24 * 3600)


class SignInExpiryTests(TestCase):
    """Every sign-in path ends at the next local midnight (the zone from the attnd_tz cookie)."""
    # The passkey tests' helpers (a software authenticator), without their test methods.
    setUp = passkey_tests.PasskeyFlowTests.setUp
    post_json, sign_in, add_passkey = passkey_tests.PasskeyFlowTests.post_json, passkey_tests.PasskeyFlowTests.sign_in, passkey_tests.PasskeyFlowTests.add_passkey
    enroll_passkey, passkey_sign_in = passkey_tests.PasskeyFlowTests.enroll_passkey, passkey_tests.PasskeyFlowTests.passkey_sign_in
    totp_code, enroll_totp = passkey_tests.PasskeyFlowTests.totp_code, passkey_tests.PasskeyFlowTests.enroll_totp

    def expect_midnight(self, client, zone):
        expiry = client.session.get_expiry_date()
        self.assertEqual(expiry, session_expiry.next_midnight(zone))
        self.assertFalse(client.session.get_expire_at_browser_close())
        self.assertLessEqual(expiry - datetime.now(UTC), timedelta(hours=25))

    def expect_sliding(self, client):
        session = client.session
        self.assertFalse(session.get_expire_at_browser_close())
        self.assertEqual(session.get_expiry_age(), int(session_expiry.IDLE.total_seconds()))
        self.assertIn(session_expiry.START_KEY, session)

    def test_organizer_password_sign_in_is_sliding(self):
        self.client.cookies['attnd_tz'] = 'Asia/Tokyo'
        self.sign_in('org@example.com')
        self.expect_sliding(self.client)
        response = self.client.get(reverse('console:dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_staff_url_encoded_zone_cookie(self):
        self.client.cookies['attnd_tz'] = 'America%2FNew_York'
        self.sign_in('staff@example.com')
        self.expect_midnight(self.client, 'America/New_York')

    def test_staff_no_cookie_uses_chicago(self):
        self.sign_in('staff@example.com')
        self.expect_midnight(self.client, 'America/Chicago')

    def test_staff_password_then_authenticator_code(self):
        device = self.enroll_totp(self.staff)
        self.client.cookies['attnd_tz'] = 'Europe/Berlin'
        self.sign_in('staff@example.com')
        self.client.post(reverse('two_factor_verify'), {'code': self.totp_code(device)})
        self.assertEqual(self.client.get(reverse('console:dashboard')).status_code, 200)
        self.expect_midnight(self.client, 'Europe/Berlin')  # the new session id keeps it

    def test_organizer_password_then_authenticator_code(self):
        device = self.enroll_totp(self.organizer)
        self.sign_in('org@example.com')
        self.client.post(reverse('two_factor_verify'), {'code': self.totp_code(device)})
        self.assertEqual(self.client.get(reverse('console:dashboard')).status_code, 200)
        self.expect_sliding(self.client)

    def test_staff_passkey_sign_in(self):
        auth = self.enroll_passkey(self.staff)
        client = Client()
        client.cookies['attnd_tz'] = 'Australia/Sydney'
        response = self.passkey_sign_in(auth, client=client)
        self.assertEqual(response.status_code, 200, response.content)
        self.expect_midnight(client, 'Australia/Sydney')

    def test_new_account(self):
        from events.models import Event, EventInvite
        owner = User.objects.create_user('own', 'own@example.com', 'pw')
        from django.utils import timezone
        event = Event.objects.create(name='E', event_code='EEXP', user=owner, start_time=timezone.now())
        EventInvite.objects.create(event=event, email='new@example.com', role='contributor', invited_by=owner)
        self.client.cookies['attnd_tz'] = 'America/Denver'
        self.client.post(reverse('console:signup'), {'email': 'new@example.com', 'password': 'a-long-Passw0rd!'})
        self.assertIn('_auth_user_id', self.client.session)
        self.expect_sliding(self.client)

    def test_tz_script_on_sign_in_pages_and_the_console(self):
        self.assertContains(self.client.get(reverse('login')), 'console/tz.js')
        self.assertContains(self.client.get(reverse('console:signup')), 'console/tz.js')
        self.assertContains(self.client.get('/'), 'console/tz.js')
        self.sign_in('org@example.com')
        self.assertContains(self.client.get(reverse('console:dashboard')), 'console/tz.js')



class SlidingSessionTests(TestCase):
    """Organizers: 14 days at most, renewed on use (hourly), 7 days idle; staff untouched."""

    def setUp(self):
        self.organizer = User.objects.create_user('o1', 'org@example.com', 'pw')
        self.client.post(reverse('login'), {'username': 'org@example.com', 'password': 'pw'})

    def at(self, seconds_after_sign_in):
        start = self.client.session[session_expiry.START_KEY]
        return mock.patch('console.session_expiry._time.time', return_value=start + seconds_after_sign_in)

    def test_idle_seconds(self):
        day = 86400
        self.assertEqual(session_expiry.idle_seconds(0, 0), 7 * day)
        self.assertEqual(session_expiry.idle_seconds(0, 10 * day), 4 * day)
        self.assertEqual(session_expiry.idle_seconds(0, 14 * day), 1)

    def test_use_renews_at_most_hourly(self):
        renewed = self.client.session[session_expiry.RENEWED_KEY]
        with self.at(600):
            self.client.get(reverse('console:dashboard'))
        self.assertEqual(self.client.session[session_expiry.RENEWED_KEY], renewed)  # not yet: under an hour
        with self.at(2 * 3600):
            self.client.get(reverse('console:dashboard'))
        self.assertGreater(self.client.session[session_expiry.RENEWED_KEY], renewed)
        self.assertEqual(self.client.session.get_expiry_age(), int(session_expiry.IDLE.total_seconds()))

    def test_renewal_never_passes_fourteen_days(self):
        with self.at(10 * 86400):
            self.client.get(reverse('console:dashboard'))
        self.assertEqual(self.client.session.get_expiry_age(), 4 * 86400)

    def test_signed_out_after_fourteen_days(self):
        with self.at(14 * 86400 + 5):
            response = self.client.get(reverse('console:dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_staff_keep_their_midnight(self):
        staff = User.objects.create_user('s1', 'staff@example.com', 'pw', is_staff=True)
        client = Client()
        client.post(reverse('login'), {'username': 'staff@example.com', 'password': 'pw'})
        expiry = client.session.get_expiry_date()
        with mock.patch('console.session_expiry._time.time', return_value=client.session[session_expiry.START_KEY] + 20 * 86400):
            client.get(reverse('console:account'))
        self.assertEqual(client.session.get_expiry_date(), expiry)
        self.assertTrue(staff.is_staff)
