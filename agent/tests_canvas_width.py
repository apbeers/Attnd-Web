"""The full-screen canvas on desktop (owner, Nov 21, 2026): every canvas tab uses the whole canvas width, counts that
wait for the person are alert badges, and the page fills exactly the visible height."""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from agent import canvas

CSS = (Path(settings.BASE_DIR) / 'agent' / 'static' / 'agent' / 'agent.css').read_text()
from design.bundles import text as _bundle  # noqa: E402
CONSOLE_CSS = _bundle('console')
TEMPLATE = (Path(settings.BASE_DIR) / 'agent' / 'templates' / 'agent' / 'assistant.html').read_text()


def rules(css, needle):
    """Every rule whose selector mentions `needle`: [(selector, body)]."""
    return [(sel.strip(), body) for sel, body in re.findall(r'([^{}]+)\{([^{}]*)\}', css) if needle in sel]


class CanvasWidthTests(SimpleTestCase):
    def test_panes_grids_and_sections_are_full_width(self):
        self.assertRegex(CSS, r'\.canvas__pane, \.canvas__grid, \.canvas__section \{ width: 100%; max-width: none; \}')

    def test_no_narrow_wrapper_on_any_canvas_tab(self):
        for sel, body in rules(CSS, 'canvas') + rules(CONSOLE_CSS, 'canvas'):
            self.assertNotIn('read-width', body, f'{sel}: canvas tabs never cap their width')
            if re.search(r'canvas__(pane|grid|section)\b', sel):
                self.assertNotRegex(body, r'max-width:\s*(?!\s)(?!none|100%)', f'{sel}: no max-width on a canvas tab')

    def test_a_lone_card_spans_the_row_and_multi_section_tabs_use_columns(self):
        self.assertIn('.canvas__cards:has(> .agent__card:only-child) { grid-template-columns: minmax(0, 1fr); }', CSS)
        for tab in ('Notifications', 'Settings', 'Metrics', 'History', 'Website'):
            self.assertIn(f'[data-tab={tab}]', CSS, f'{tab}: its sections side by side on wide canvases')
        self.assertIn('.component--event_team { display: grid;', CONSOLE_CSS, 'Team: members beside the invite form')
        # Every tab the canvas draws is known to the CSS rules above or holds one full-width list.
        multi = {'Notifications', 'Settings', 'Metrics', 'History', 'Website'}
        for label in canvas.ORDER:
            sections = [name for name, tab in canvas.KINDS.items() if tab == label]
            if len(sections) > 1 and label not in {'Overview', 'Platform'}:
                self.assertIn(label, multi, f'{label} has several sections: give it columns in agent.css')

    def test_the_page_fills_the_visible_height(self):
        self.assertIn('height: var(--app-height, 100dvh);', CSS)  # every width: one app shell (one chat, Nov 22)
        self.assertIn('display: flex; flex-direction: column; overflow: hidden;', CSS)
        self.assertIn('body:has(.assistant) > .page > .assistant { flex: 1 1 auto; height: auto; min-height: 0; }', CSS)


class TabCountTests(SimpleTestCase):
    def test_alert_counts_are_badges(self):
        self.assertEqual(canvas.ALERT_COUNTS, {'Review'})
        self.assertIn('tab-count--alert', TEMPLATE)
        badge = dict(rules(CSS, '.tab-count--alert'))
        body = next(iter(badge.values()))
        self.assertIn('var(--color-on-danger)', body)
        self.assertIn('var(--color-danger)', body)
        # The on-colour is defined for light and both dark-mode blocks (tokens only).
        self.assertEqual(CONSOLE_CSS.count('--color-on-danger:'), 3)


class ChatTaskLinesTests(SimpleTestCase):
    def test_the_chat_column_has_the_task_lines_and_the_tray_sits_with_the_chat(self):
        panel = (Path(settings.BASE_DIR) / 'agent' / 'templates' / 'agent' / '_panel.html').read_text()
        self.assertIn('data-agent-tasks', panel)
        js = (Path(settings.BASE_DIR) / 'console' / 'static' / 'console' / 'console.js').read_text()
        self.assertIn('function renderChatLines', js)
        self.assertIn('window.attndRefreshTheme', js)
        self.assertIn('tray.hidden = tasks.length === 0 || onChatPage;', js)  # no tray on the chat page: its lines are in the chat
