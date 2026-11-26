"""Lighthouse (Nov 22, 2026): text on the console's solid status fills (badges, success / danger buttons) reads at
4.5:1 in light and dark mode. The info badge was white on #0b8bd6 (3.7:1), and in dark mode white sat on the light
success / danger / info fills (2.3–3.3:1)."""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from studio.quality import contrast, _rgb


PAIRS = (('--color-success', '--color-on-success'), ('--color-danger', '--color-on-danger'),
         ('--color-info', '--color-on-info'), ('--color-success-text', '--color-on-success'))


def _blocks(css):
    """{'light': {...}, 'dark-media': {...}, 'dark': {...}}: the custom properties of the three theme blocks."""
    def props(text):
        return dict(re.findall(r'(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{3,6})\b', text))
    light = css[css.index(':root {'):css.index('}', css.index(':root {'))]
    media_start = css.index('@media (prefers-color-scheme: dark)')
    media = css[media_start:css.index(':root[data-theme="dark"]')]
    dark_start = css.index(':root[data-theme="dark"]')
    dark = css[dark_start:css.index('}', dark_start)]
    return {'light': props(light), 'dark-media': {**props(light), **props(media)},
            'dark': {**props(light), **props(dark)}}


class FillContrastTests(SimpleTestCase):
    def test_text_on_status_fills_reads(self):
        from design.bundles import text
        css = text('console')
        for mode, tokens in _blocks(css).items():
            for fill, on in PAIRS:
                ratio = contrast(_rgb(tokens[fill]), _rgb(tokens[on]))
                self.assertGreaterEqual(ratio, 4.5, f'{mode}: {on} on {fill} is {ratio:.2f}:1')

    def test_badges_and_buttons_use_the_on_tokens(self):
        from design.bundles import text
        css = text('console')
        for selector in ('.badge--success', '.badge--danger', '.badge--info', '.button--success'):
            rule = re.search(re.escape(selector) + r'\s*\{([^}]*)\}', css).group(1)
            self.assertNotIn('#fff', rule, selector)
            self.assertIn('var(--color-on-', rule, selector)
