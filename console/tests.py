from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse

from events.models import Event


class SignUpViewTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.url = reverse('console:signup')

    def test_get_returns_200(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_valid_post_creates_staff_user_with_demo_event_and_redirects(self):
        response = self.client.post(self.url, {
            'email': 'new@example.com',
            'username': 'newuser',
            'password1': 'Str0ng!Pass99',
            'password2': 'Str0ng!Pass99',
        })
        self.assertRedirects(response, '/admin/', fetch_redirect_response=False)

        user = User.objects.get(username='newuser')
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(Event.objects.filter(user=user).exists())

    def test_mismatched_passwords_returns_200_with_form_errors(self):
        response = self.client.post(self.url, {
            'email': 'bad@example.com',
            'username': 'baduser',
            'password1': 'Str0ng!Pass99',
            'password2': 'DifferentPass99!',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='baduser').exists())
        self.assertFormError(response.context['form'], 'password2', "The two password fields didn’t match.")
