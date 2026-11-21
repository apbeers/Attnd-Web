"""Stage B: signed-in people land in the console; the logo always reaches the home page; no sign-in prompt during
a session."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class SignInFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('u1', 'org@example.com', 'pw-12345-long')

    def test_signed_out(self):
        home = self.client.get('/', secure=True)
        self.assertEqual(home.status_code, 200)
        self.assertContains(home, 'data-passkey-immediate')
        self.assertContains(home, 'Sign in')
        self.assertEqual(self.client.get(reverse('login'), secure=True).status_code, 200)

    def test_signed_in(self):
        self.client.force_login(self.user)
        root = self.client.get('/', secure=True)
        self.assertRedirects(root, reverse('console:dashboard'), fetch_redirect_response=False)
        page = self.client.get(reverse('home:home_page'), secure=True)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '>Console</a>')
        self.assertNotContains(page, 'data-passkey-immediate')
        for name in ('login', 'console:signup', 'two_factor_verify'):
            response = self.client.get(reverse(name), secure=True)
            self.assertEqual(response.status_code, 302, name)
            self.assertTrue(response['Location'].startswith('/console/'), (name, response['Location']))

    def test_the_logo_goes_home(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse('console:dashboard'), secure=True)
        self.assertContains(page, f'class="brand" href="{reverse("home:home_page")}"')
