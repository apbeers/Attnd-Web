"""The privacy policy draft (batch 7, docs/privacy_policy_draft.md): ready to publish, but not published until the
owner approves (then render it from home.views.privacy and drop the "not published" check)."""
from django.template.loader import render_to_string
from django.test import TestCase


class PrivacyDraftTests(TestCase):
    def test_draft_renders_and_names_every_provider(self):
        html = render_to_string('home/privacy_draft.html', {'contact_email': 'privacy@attnd.app',
                                                           'site_url': 'https://attnd.app'})
        for name in ('Anthropic', 'OpenAI', 'Heroku', 'Cloudinary', 'Mailgun', 'Stripe', 'Apple Music',
                     'Wikimedia Commons', 'Live Activity', 'App Clip', 'Prospects', 'mailto:privacy@attnd.app'):
            self.assertIn(name, html)
        self.assertIn('(not published yet)', html)

    def test_not_published_yet(self):
        resp = self.client.get('/privacy/', secure=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'home/privacy.html')
        self.assertTemplateNotUsed(resp, 'home/privacy_draft.html')
