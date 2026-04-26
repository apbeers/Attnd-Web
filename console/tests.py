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
            'username': 'newuser',
            'password1': 'Str0ng!Pass99',
            'password2': 'Str0ng!Pass99',
        }

    def test_get_returns_200(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_valid_post_redirects_to_admin(self):
        response = self.client.post(self.url, self.valid_data)
        self.assertRedirects(response, '/admin/', fetch_redirect_response=False)

    def test_valid_post_creates_staff_user(self):
        self.client.post(self.url, self.valid_data)
        user = User.objects.get(username='newuser')
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_valid_post_creates_demo_event_linked_to_user(self):
        self.client.post(self.url, self.valid_data)
        user = User.objects.get(username='newuser')
        events = Event.objects.filter(user=user)
        self.assertEqual(events.count(), 1)
        self.assertIn('newuser', events.first().name)

    def test_mismatched_passwords_returns_200_with_form_errors(self):
        data = {**self.valid_data, 'password2': 'DifferentPass99!'}
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='newuser').exists())
        self.assertFormError(response.context['form'], 'password2', "The two password fields didn’t match.")

    def test_duplicate_username_returns_200_with_form_errors(self):
        User.objects.create_user(username='newuser', password='irrelevant')
        response = self.client.post(self.url, self.valid_data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(username='newuser').count(), 1)
        self.assertFormError(response.context['form'], 'username', 'A user with that username already exists.')
