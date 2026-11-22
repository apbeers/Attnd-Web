"""Ask Attnd's platform help (agent/help.md): every link resolves to a real page, and the model gets it."""
import re

from django.test import SimpleTestCase
from django.urls import Resolver404, resolve

from agent.prompts import HELP, SYSTEM_PROMPT

LINK = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')


class HelpTests(SimpleTestCase):
    def test_every_link_resolves(self):
        links = LINK.findall(HELP)
        self.assertGreater(len(links), 15)
        for text, href in links:
            self.assertTrue(href.startswith('/') and not href.startswith('//'), href)
            path = href.replace('{event_id}', '1')
            try:
                resolve(path)
            except Resolver404:
                self.fail(f'help link “{text}” → {href} does not resolve')

    def test_help_is_a_tool_not_prompt_text(self):
        from agent.loop import FIXED_TOOLS
        self.assertIn('platform_help', {t['name'] for t in FIXED_TOOLS})
        self.assertIn('call platform_help', SYSTEM_PROMPT)
        self.assertNotIn('## Account and sign-in', SYSTEM_PROMPT)
        self.assertIn('[Account](/console/account/)', HELP)

    def test_account_pages_stay_off_limits_to_the_tools(self):
        from agent.registry import off_limits
        self.assertTrue(off_limits('/console/account/'))
        self.assertTrue(off_limits('/accounts/password_reset/'))
