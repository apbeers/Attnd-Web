from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse

from events.models import Event


class SignUpViewTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.url = reverse('console:signup')
        self.valid_data = {
            'email': 'new@example.com',
            'password': 'Str0ng!Pass99',
        }

    def test_get_returns_200(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_valid_post_redirects_to_admin(self):
        response = self.client.post(self.url, self.valid_data)
        self.assertRedirects(response, '/admin/', fetch_redirect_response=False)

    def test_valid_post_creates_staff_user(self):
        self.client.post(self.url, self.valid_data)
        user = User.objects.get(username='new@example.com')
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.email, 'new@example.com')

    def test_valid_post_creates_demo_event_linked_to_user(self):
        self.client.post(self.url, self.valid_data)
        user = User.objects.get(username='new@example.com')
        events = Event.objects.filter(user=user)
        self.assertEqual(events.count(), 1)

    def test_duplicate_email_returns_200_with_form_error(self):
        User.objects.create_user(username='new@example.com', email='new@example.com', password='x')
        response = self.client.post(self.url, self.valid_data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(username='new@example.com').count(), 1)
        self.assertFormError(response.context['form'], 'email', 'An account with this email already exists.')

    def test_invalid_email_returns_200_with_form_errors(self):
        response = self.client.post(self.url, {'email': 'not-an-email', 'password': 'Str0ng!Pass99'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email='not-an-email').exists())

    def test_missing_password_returns_200_with_form_errors(self):
        response = self.client.post(self.url, {'email': 'new@example.com', 'password': ''})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email='new@example.com').exists())
