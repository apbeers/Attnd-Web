"""Invite-only organizer side (SIGNUPS_OPEN off, the default): sign-up gate and homepage."""
import re

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from django.utils.html import escape

from console.signups import INVITE_ONLY_MESSAGE, is_invited

INVITE_ONLY_HTML = escape(INVITE_ONLY_MESSAGE)  # as the template renders it (apostrophes escaped)
from events.models import Event, EventInvite, EventMember

PASSWORD = 'a-strong-password-123'


def _normalized(html, *emails):
    """The page with per-request noise removed (CSRF tokens, the typed email), to compare two answers."""
    html = re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', 'csrf', html)
    for email in emails:
        html = html.replace(email, 'EMAIL')
    return html


@override_settings(SIGNUPS_OPEN=False, STUDIO_BETA_EMAILS=['beta@example.com'])
class SignupGateTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user('owner', 'owner@example.com', 'pw')
        self.event = Event.objects.create(name='Art Walk', event_code='ARTWALK1', timezone='America/Chicago',
                                          start_time=timezone.now(), user=self.owner)
        self.url = reverse('console:signup')

    def post(self, email, **extra):
        return self.client.post(self.url, {'email': email, 'password': PASSWORD, **extra}, secure=True)

    def test_page_says_invite_only_with_the_form_folded(self):
        response = self.client.get(self.url, secure=True)
        self.assertContains(response, 'Invite only')
        self.assertContains(response, INVITE_ONLY_HTML)
        self.assertContains(response, 'Have an invite? Sign up with the email it was sent to')
        self.assertNotContains(response, '<details class="stack" open')  # folded until they ask for it
        self.assertNotContains(response, 'Create an account')

    def test_uninvited_email_is_refused(self):
        response = self.post('stranger@example.com')
        self.assertContains(response, INVITE_ONLY_HTML, status_code=403)
        self.assertFalse(User.objects.filter(email='stranger@example.com').exists())
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_refusal_is_the_same_for_existing_accounts_and_unknown_emails(self):
        User.objects.create_user('taken', 'taken@example.com', 'pw')
        known = self.post('taken@example.com')
        unknown = self.post('nobody@example.com')
        self.assertEqual(known.status_code, unknown.status_code)
        self.assertNotContains(known, 'already exists', status_code=403)
        self.assertEqual(_normalized(known.content.decode(), 'taken@example.com'),
                         _normalized(unknown.content.decode(), 'nobody@example.com'))

    def test_team_invite_can_sign_up_and_joins_the_team(self):
        EventInvite.objects.create(event=self.event, email='helper@example.com', role='admin')
        response = self.post('Helper@Example.com')
        self.assertRedirects(response, reverse('console:dashboard'), fetch_redirect_response=False)
        helper = User.objects.get(email='helper@example.com')
        self.assertEqual(EventMember.objects.get(event=self.event, user=helper).role, 'admin')

    def test_studio_access_is_not_an_invite(self):
        """Only a pending team invite counts: STUDIO_BETA_EMAILS doesn't."""
        for email in ('STUDIO@example.com', 'beta@example.com'):
            self.assertContains(self.post(email), INVITE_ONLY_HTML, status_code=403)
        self.assertFalse(User.objects.filter(email__in=['studio@example.com', 'beta@example.com']).exists())

    def test_invited_email_with_an_account_gets_the_usual_message(self):
        EventInvite.objects.create(event=self.event, email='owner@example.com')
        self.assertContains(self.post('owner@example.com'), 'An account with this email already exists.')

    def test_is_invited(self):
        EventInvite.objects.create(event=self.event, email='a@example.com')
        self.assertTrue(is_invited('A@example.com'))
        for email in ('b@example.com', ' Beta@Example.com ', 'c@example.com', '', None):
            self.assertFalse(is_invited(email), email)

    def test_sign_in_page_stays_and_says_invite_only(self):
        response = self.client.get(reverse('login'), secure=True)
        self.assertContains(response, 'Attnd is invite-only right now.')
        self.assertNotContains(response, 'New here?')


@override_settings(SIGNUPS_OPEN=False)
class InviteOnlyHomepageTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_homepage_shows_the_notice_instead_of_the_form(self):
        # The onboarding home page (Nov 21, 2026) keeps its composer; with sign-ups closed it says so, and the
        # sign-up page then takes invited emails only.
        html = self.client.get('/', secure=True).content.decode()
        self.assertIn('data-composer', html)
        self.assertIn('invite-only right now', html)
        self.assertNotIn('id="scrape-form"', html)


@override_settings(SIGNUPS_OPEN=True)
class SignupsOpenTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_signup_page_and_any_email(self):
        response = self.client.get(reverse('console:signup'), secure=True)
        self.assertContains(response, 'Create an account')
        self.assertNotContains(response, INVITE_ONLY_HTML)
        response = self.client.post(reverse('console:signup'), {'email': 'anyone@example.com', 'password': PASSWORD},
                                    secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(email='anyone@example.com').exists())

    def test_homepage_open(self):
        html = self.client.get('/', secure=True).content.decode()
        self.assertIn('data-composer', html)
        self.assertNotIn('invite-only right now', html)
        self.assertContains(self.client.get(reverse('login'), secure=True), 'New here?')
