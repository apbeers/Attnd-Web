"""The design system's rules (docs/design_system.md), checked on every stylesheet and template the site serves.

One set of tokens, one set of layout primitives and components, and per area (console, chat, public site, onboarding,
the Architecture page) only what that area needs. These tests fail when something goes around that:

- a stylesheet other than design/tokens.css sets tokens on :root;
- a literal colour (#hex, rgb(), hsl()) in a stylesheet outside tokens.css (CSS masks, which use black only as
  "opaque", are allowed);
- a var(--name) that nothing defines (a typo, or a token that was never added);
- a style="" attribute that sets anything but custom properties (style="--value: 40%"); emails are exempt, because
  mail clients only read inline styles;
- an old fixed-width wrapper (.wrap, .column, --page-width) or a second definition of a shared component;
- a page that links an area stylesheet directly instead of through {% stylesheets %}.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from design import bundles

ROOT = Path(settings.BASE_DIR)
TOKENS = 'design/static/design/tokens.css'
SHEETS = sorted(set(bundles.LAYERS) | {name for b in bundles.BUNDLES.values() for name in b}
                | set(bundles.EXTRAS.values()))
TEMPLATE_DIRS = [p for p in ROOT.glob('*/templates') if '.venv' not in p.parts] + [ROOT / 'templates']
EMAIL_TEMPLATES = ('templates/emails/',)
COLOUR = re.compile(r'#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(')
# Shared components: defined once, in design/components.css (an area may adjust them inside its own scope).
SHARED = ('.button', '.panel', '.badge', '.chip', '.notice', '.meter', '.segmented', '.spinner', '.brand', '.live-dot',
          '.visually-hidden', '.grid', '.cluster', '.stack', '.page', '.section', '.split')


def _path(name):
    """A stylesheet's source file from its static name ('console/console.css')."""
    return ROOT / bundles.source_path(name)


def _css(name):
    return _path(name).read_text()


def _templates():
    for d in TEMPLATE_DIRS:
        for path in d.rglob('*.html'):
            yield path.relative_to(ROOT).as_posix(), path.read_text(errors='ignore')


class TokenTests(SimpleTestCase):
    def test_only_tokens_css_sets_root_tokens(self):
        offenders = [name for name in SHEETS if not str(_path(name)).endswith(TOKENS) and ':root' in _css(name)]
        self.assertEqual(offenders, [])

    def test_no_literal_colours_outside_tokens(self):
        found = {}
        for name in SHEETS:
            if str(_path(name)).endswith(TOKENS):
                continue
            for n, line in enumerate(_css(name).splitlines(), 1):
                if COLOUR.search(line) and 'mask' not in line:
                    found.setdefault(name, []).append(n)
        self.assertEqual(found, {}, 'Add a token to design/tokens.css and use it')

    def test_every_var_is_defined(self):
        defined, prefixes = set(), set()
        for name in SHEETS:
            defined |= set(re.findall(r'(--[a-z0-9-]+)\s*:', _css(name)))
        # Custom properties set from templates, Python (themes, previews) and JavaScript count as defined.
        for pattern in ('*.html', '*.py', '*.js'):
            for path in ROOT.rglob(pattern):
                if '.venv' in path.parts or 'node_modules' in path.parts:
                    continue
                text = path.read_text(errors='ignore')
                defined |= set(re.findall(r'(--[a-z0-9-]+)\s*[:\'"]', text))
                prefixes |= set(re.findall(r'(--[a-z0-9]+-)\{', text))  # f'--pv-{k}:…' sets a family
        missing = {}
        for name in SHEETS:
            for var in set(re.findall(r'var\((--[a-z0-9-]+)', _css(name))):
                if var not in defined and not var.startswith(tuple(prefixes)):
                    missing.setdefault(name, []).append(var)
        self.assertEqual(missing, {})


class StructureTests(SimpleTestCase):
    def test_style_attributes_only_set_custom_properties(self):
        found = []
        for rel, text in _templates():
            if rel.startswith(EMAIL_TEMPLATES):
                continue
            for value in re.findall(r'\sstyle="([^"]*)"', text):
                parts = [p.strip() for p in value.split(';') if p.strip()]
                if any(not p.startswith('--') and not p.startswith('{{') for p in parts):
                    found.append((rel, value))
        self.assertEqual(found, [])

    def test_no_fixed_width_wrappers(self):
        for name in SHEETS:
            text = _css(name)
            self.assertNotIn('--page-width', text, name)
            self.assertIsNone(re.search(r'^\.(wrap|column)\b', text, re.M), name)
        for rel, text in _templates():
            self.assertIsNone(re.search(r'class="(wrap|column)\b', text), rel)

    def test_shared_components_are_defined_once(self):
        shared = _css('design/components.css') + _css('design/layout.css') + _css('design/base.css')
        for cls in SHARED:
            self.assertRegex(shared, r'(^|\n)' + re.escape(cls) + r'[ {,.:\[]', cls)
        for name in SHEETS:
            if name.startswith('design/'):
                continue
            for cls in SHARED:
                # A bare top-level rule for a shared class in an area file is a second definition.
                self.assertIsNone(re.search(r'(^|\n)' + re.escape(cls) + r' \{', _css(name)), f'{cls} in {name}')

    def test_pages_load_styles_through_the_tag(self):
        direct = [rel for rel, text in _templates()
                  if re.search(r"""<link[^>]+href="\{% static '(console/console|home/site|home/onboarding|agent/agent)\.css'""", text)]
        self.assertEqual(direct, [])

    def test_bundles_are_the_layers_then_the_area(self):
        for name, parts in bundles.BUNDLES.items():
            self.assertEqual(parts[:len(bundles.LAYERS)], bundles.LAYERS, name)
            self.assertEqual(len(parts), len(bundles.LAYERS) + 1, name)
