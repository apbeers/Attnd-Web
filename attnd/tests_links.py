from django.test import SimpleTestCase, override_settings

from attnd import links


class AppLinkTests(SimpleTestCase):
    @override_settings(SITE_URL='https://dev.attnd.app')
    def test_app_links_always_use_attnd_app(self):
        # The apps verify and scan attnd.app links only, so dev's SITE_URL never leaks into QR codes.
        self.assertEqual(links.join_url('DEMO'), 'https://attnd.app/join/DEMO/')
        self.assertEqual(links.shared_plan_url('DEMO', 'eyJ2IjoxfQ=='), 'https://attnd.app/p/DEMO/eyJ2IjoxfQ')

    def test_qr_helpers(self):
        self.assertTrue(links.qr_png('x').startswith(b'\x89PNG'))
        self.assertTrue(links.qr_png_data_url('x').startswith('data:image/png;base64,'))
        self.assertIn('<svg', links.qr_svg('x'))
