from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
import datetime

from events.models import Event, Location, Notification

TRIAL_USERNAME = "trialuser"


def _make_trial_user():
    user, _ = User.objects.get_or_create(username=TRIAL_USERNAME, defaults={"email": "trial@attnd.app"})
    user.set_unusable_password()
    user.save()
    return user


class SignUpViewTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.url = reverse('console:signup')
        self.valid_data = {
            'email': 'new@example.com',
            'password': 'Str0ng!Pass99',
        }

    def _post(self, data):
        return self.client.post(self.url, data, secure=True)

    def test_get_returns_200(self):
        response = self.client.get(self.url, secure=True)
        self.assertEqual(response.status_code, 200)

    def test_valid_post_redirects_to_admin(self):
        response = self._post(self.valid_data)
        self.assertRedirects(response, '/admin/', fetch_redirect_response=False)

    def test_valid_post_creates_staff_user(self):
        self._post(self.valid_data)
        user = User.objects.get(username='new@example.com')
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.email, 'new@example.com')

    def test_valid_post_creates_demo_event_linked_to_user(self):
        self._post(self.valid_data)
        user = User.objects.get(username='new@example.com')
        events = Event.objects.filter(user=user)
        self.assertEqual(events.count(), 1)

    def test_claim_transfers_trial_event(self):
        """Signing up with ?claim=CODE transfers the trial event to the new user."""
        trial_user = _make_trial_user()
        event = Event.objects.create(
            name="Scraped Festival",
            event_code="TESTCODE",
            description="desc",
            latitude=0, longitude=0,
            start_time=timezone.now() + datetime.timedelta(days=7),
            address="123 Main St",
            user=trial_user,
        )
        Location.objects.create(
            name="Main Stage", snippet="s", description="d",
            latitude=0, longitude=0, address="", user=trial_user, event=event,
        )
        Notification.objects.create(
            title="Welcome!", body="Thanks for joining.",
            send_time=timezone.now() + datetime.timedelta(hours=1),
            user=trial_user, event=event,
        )

        data = {**self.valid_data, 'claim': 'TESTCODE'}
        self._post(data)

        new_user = User.objects.get(username='new@example.com')
        event.refresh_from_db()
        self.assertEqual(event.user, new_user)
        self.assertEqual(event.location_set.filter(user=new_user).count(), 1)
        self.assertEqual(event.notification_set.filter(user=new_user).count(), 1)
        # No extra demo event should be created
        self.assertEqual(Event.objects.filter(user=new_user).count(), 1)

    def test_claim_bad_code_falls_back_to_demo_event(self):
        """An invalid claim code silently falls back to creating the demo event."""
        data = {**self.valid_data, 'claim': 'BADCODE99'}
        self._post(data)
        user = User.objects.get(username='new@example.com')
        self.assertEqual(Event.objects.filter(user=user).count(), 1)

    def test_duplicate_email_returns_200_with_form_error(self):
        User.objects.create_user(username='new@example.com', email='new@example.com', password='x')
        response = self._post(self.valid_data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(username='new@example.com').count(), 1)
        self.assertFormError(response.context['form'], 'email', 'An account with this email already exists.')

    def test_invalid_email_returns_200_with_form_errors(self):
        response = self._post({'email': 'not-an-email', 'password': 'Str0ng!Pass99'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email='not-an-email').exists())

    def test_missing_password_returns_200_with_form_errors(self):
        response = self._post({'email': 'new@example.com', 'password': ''})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email='new@example.com').exists())
