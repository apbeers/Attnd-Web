from django.test import SimpleTestCase, override_settings

from attnd import links


class AppLinkTests(SimpleTestCase):
    def test_dev_server_links_use_dev_attnd_app(self):
        # Nov 20, 2026: dev events get dev.attnd.app links (iOS opens both domains; a browser open hits dev).
        with override_settings(SITE_URL='https://dev.attnd.app'):
            self.assertEqual(links.join_url('DEMO'), 'https://dev.attnd.app/join/DEMO/')
            self.assertEqual(links.shared_plan_url('DEMO', 'eyJ2IjoxfQ=='), 'https://dev.attnd.app/p/DEMO/eyJ2IjoxfQ')

    def test_everything_else_uses_attnd_app(self):
        for site in ('https://attnd.app', 'http://localhost:8000', 'https://example.herokuapp.com', ''):
            with override_settings(SITE_URL=site):
                self.assertEqual(links.join_url('DEMO'), 'https://attnd.app/join/DEMO/')

    def test_qr_helpers(self):
        self.assertTrue(links.qr_png('x').startswith(b'\x89PNG'))
        self.assertTrue(links.qr_png_data_url('x').startswith('data:image/png;base64,'))
        self.assertIn('<svg', links.qr_svg('x'))
