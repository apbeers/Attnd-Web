"""The collectstatic CSS minifier (attnd/static_storage.py)."""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from attnd.static_storage import minify_css


class MinifyCssTests(SimpleTestCase):
    def test_comments_whitespace_and_strings(self):
        css = ('/* a comment */\n.a  >  .b ,\n.c { color : red ;  content: "  /* kept */  " ; }\n'
               '/*! licence */ a :hover { width: calc(100% - 2px); }\n')
        out = minify_css(css)
        self.assertNotIn('a comment', out)
        self.assertIn('"  /* kept */  "', out)
        self.assertIn('/*! licence */', out)
        self.assertIn('.a>.b,.c{color : red;content: "  /* kept */  "}', out)
        self.assertIn('a :hover{', out)  # the descendant space before :hover stays
        self.assertIn('calc(100% - 2px)', out)

    def test_our_stylesheets_keep_their_structure(self):
        for name in ('console/static/console/console.css', 'agent/static/agent/agent.css', 'home/static/home/site.css',
                     'home/static/home/onboarding.css'):
            text = (Path(settings.BASE_DIR) / name).read_text()
            out = minify_css(text)
            self.assertLess(len(out), len(text) * 0.9, name)
            self.assertEqual(out.count('{'), out.count('}'), name)
            self.assertEqual(text.count('var(--'), out.count('var(--'), name)
