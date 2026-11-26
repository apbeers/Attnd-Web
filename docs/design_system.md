# Design system (CSS)

One set of styles for everything attnd-web serves: the console, Ask Attnd, the Platform pages, the sign-in pages,
the public site (home, join, privacy, brand) and the MCP App cards. Studio's hosted sites are separate (their own
`site_static/attnd.js` styles).

## How a page gets its CSS

```django
{% load design %}
{% stylesheets "console" %}                 {# the console bundle #}
{% stylesheets "console" "agent" %}         {# + Ask Attnd's area file #}
{% stylesheets "site" "onboarding" %}       {# the public site + the home page's hero and gallery #}
```

A **bundle** (`design/bundles.py`) is the shared layers followed by one area file, served as one stylesheet:

| Layer | File | What |
|-------|------|------|
| Tokens | `design/static/design/tokens.css` | Every colour (light and dark), space, radius, type size, shadow, the reading width, `--density`. The only file that sets tokens on `:root`. |
| Base | `design/static/design/base.css` | Elements (body, headings, links, form controls) and text helpers (`muted`, `small`, `eyebrow`, `center`, `visually-hidden`…). |
| Layout | `design/static/design/layout.css` | The layout primitives and the breakpoints (below). |
| Components | `design/static/design/components.css` | Button, panel, notice, badge, chip, list, field, tabs, table, meter, segmented control, swatch, spinner, the wordmark, and the shared phone rules (44 px targets, 16 px fields). |
| Motion | `design/static/design/motion.css` | Keyframes and reduced-motion rules. |

**Area files** add only what is theirs: `console/console.css` (console chrome and features), `home/site.css` (the
public site's type, nav, footer, bands, join card, text pages), plus the extras `agent/agent.css` (Ask Attnd),
`home/onboarding.css` (hero, composer, gallery; also the console's first screen) and
`platform_admin/architecture.css`. `design.finders.BundleFinder` builds the bundles into `.design-build/` (ignored by
git); collectstatic hashes, minifies and compresses them like any static file. Tests and the MCP App read a bundle with
`design.bundles.text('console')`.

## Layout: use the whole screen

Pages use the full width at every size. Extra width becomes **more columns, never wider lines**:

| Primitive | Use |
|-----------|-----|
| `page` | The page: full width, side padding `--gutter`. `gutter` is that padding alone (full-bleed bands). |
| `page--centered` | One card in the middle of the screen (sign-in, password reset). |
| `region` | An event page's content beside the side menu, or a canvas pane: a container components query (`@container region (…)`). |
| `stack`, `cluster` | Vertical rhythm; a wrapping row. |
| `grid` (`--cols`, `--min`) | Equal columns; up to `--cols × --density` (more on ultra-wide). `grid--fixed` keeps the count. |
| `dashboard` (`--tile`) | Tiles of their natural size packed into as many columns as fit; `span-2`, `span-all`. |
| `lanes` (`--lane`) | Whole sections side by side (a schedule's days, notifications). |
| `flow` (`--flow-min`) | A long list flowing into columns. |
| `split` | A main column and an aside. |
| `form-fields`, `form-columns` | A form's fields / groups in columns when there is room. |
| `measure`, `prose` | Running text at a readable width (`--read-width`); never a whole page. |
| `narrow` | One short form or message inside a page (consent, a claim). |

`--density` (tokens) is 1 below 1920 px, 1.5 from 1920 px, 2 from 2560 px. Inside a `.component` it is always 1:
components size themselves from their own container.

**Breakpoints** (the only `@media` widths anywhere; `@media` can't read custom properties):
phone `(max-width: 600px)`, medium `(min-width: 601px)` / `(max-width: 1023px)`, wide `(min-width: 1024px)`,
ultra `(min-width: 1920px)` and `(min-width: 2560px)`. JavaScript asks `window.attndScreen.is('phone' | 'medium' |
'wide' | 'ultra')`. Components use container queries.

## Rules (checked by tests)

- Colours are tokens. No `#hex`, `rgb()` or `hsl()` outside `tokens.css` (CSS masks excepted); a new colour is a new
  token with its dark value. Mock-ups of the app (`--mock-*`) and QR plates (`--color-qr-plate`) are tokens too.
- Every `var(--x)` is defined somewhere (a token, a local custom property, or one a template / Python / JS sets).
- `style=""` only sets custom properties (`style="--value: 40%"`, `--swatch`, `--x`); the CSS reads them. Emails are
  exempt (mail clients only read inline styles).
- Shared components are defined once, in the layers. An area may adjust one inside its own scope (`.site .button`),
  never redefine it at the top level.
- No fixed page widths (`.wrap`, `.column`, `--page-width`).
- Pages load CSS through `{% stylesheets %}`, never a direct `<link>` to an area file.
- Dark mode follows the system everywhere (`:root:not([data-theme="light"])` in the dark media query, and
  `[data-theme="dark"]`). An event's theme (`events/theme.py` `css`) overrides tokens in the same three places.

Tests: `design/tests.py` (the rules above), `console/tests_breakpoints.py` (breakpoints, width checks in JS),
`console/tests_contrast.py`, `console/tests_phone.py` (with `ATTND_BROWSER_CHECKS=1`: every console page and the
public site at 360, 768, 1280, 2560 and 3440 px through `scripts/ci/phone_check.cjs`: no sideways scroll, 44 px
targets and 16 px fields on phones, and at ≥ 1920 px content that spans at least 85 % of the screen; centred card
pages and pages marked `data-reading` are exempt).
