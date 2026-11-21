"""Stage F2: LoginRequiredMiddleware. Every view needs a sign-in except this list of public pages; a new public page
must be added here on purpose."""
from django.test import TestCase
from django.urls import URLPattern, URLResolver, get_resolver, reverse

PUBLIC = {
    # site
    'health', 'django.contrib.sitemaps.views.sitemap', 'homepage', 'home_page', 'privacy', 'csp_report',
    # the apps' API (API keys / attestation, never a session)
    'nearby', 'lookup', 'bundle', 'join', 'changes', 'leave', 'usage', 'device', 'diagnostics_logs', 'diagnostics_reports',
    'app_status', 'symbols', 'symbolicate', 'attest-nonce', 'attest-ios', 'attest-android',
    # join links, shared plans, well-known files
    'join_event', 'join_qr', 'shared_plan_qr', 'shared_plan', 'aasa', 'android_asset_links',
    # onboarding (Nov 21, 2026): the composer, the page's anonymous counts, the approximate "near you" cards; Claim it asks for a sign-in itself
    # (login_required)
    'start', 'metric', 'near', 'claim',
    # signing in, signing up, passwords (the passkey registration endpoints answer their own JSON 403)
    'login', 'signup', 'passkey_auth_options', 'passkey_auth', 'passkey_register_options', 'passkey_register', 'password_reset', 'password_reset_done',
    'password_reset_confirm', 'password_reset_complete',
}


def walk(patterns, out, prefix=''):
    for p in patterns:
        if isinstance(p, URLResolver):
            walk(p.url_patterns, out, prefix + str(p.pattern))
        elif isinstance(p, URLPattern) and getattr(p.callback, 'login_required', True) is False:
            out.add(p.name or f'{prefix}{p.pattern}')
    return out


class PublicPagesTests(TestCase):
    def test_the_public_pages_are_exactly_these(self):
        found = walk(get_resolver().url_patterns, set())
        named = {n for n in found if not n.startswith(('^', 'manage-attnd', 'api/v', 'accounts/', 'p/'))}
        unnamed = found - named
        # (unnamed: static / media serving, the retired /api/vN/ catch-all, the admin's login redirect, the shared
        # plan link without its slash, Django's own auth URLs)
        self.assertEqual(named - {'logout', 'password_change', 'password_change_done'}, PUBLIC)
        self.assertTrue(all(u.startswith(('^', 'manage-attnd', 'api/v', 'accounts/', 'p/')) for u in unnamed), unnamed)

    def test_everything_else_needs_a_sign_in(self):
        for name in ('console:dashboard', 'console:assistant', 'agent:runs', 'console:account'):
            response = self.client.get(reverse(name), secure=True)
            self.assertEqual(response.status_code, 302, name)
            self.assertTrue(response['Location'].startswith(reverse('login')), name)

    def test_public_pages_open_signed_out(self):
        for url in ('/', '/home/', '/privacy/', '/health/', reverse('login'), reverse('console:signup'),
                    '/.well-known/assetlinks.json', reverse('password_reset')):
            self.assertEqual(self.client.get(url, secure=True).status_code, 200, url)
        self.assertNotEqual(self.client.get('/api/v2/events/nearby/', secure=True).status_code, 302)
