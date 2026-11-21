"""Per-event colour themes (API-023): three colours per mode, made readable before anything draws them.

    {"light": {"primary": "#431F86", "secondary": "#B79CF2", "tertiary": "#7B4FD6"},
     "dark":  {"primary": "#9D7BEA", "secondary": "#6F47D6", "tertiary": "#C4B0FF"},
     "source": "ai" | "site" | "manual" | "default", "made_at": "…", "reason": "…"}

Roles: **primary** fills buttons and active chips and colours links; **secondary** tints soft surfaces (selected rows,
chips, the page's glow); **tertiary** is the highlight (tab underlines, hovers, gradients). `normalize` validates a
theme (upper-case `#RRGGBB`, a missing mode derived from the other); `tokens` turns one mode into the console's and
the public site's CSS tokens with guaranteed contrast (WCAG 2.2: text ≥ 4.5:1); `checks` explains, per colour, what
was adjusted. Nothing is ever refused for contrast: the adjusted value is what renders and what the apps get.
Pure (no Django models), unit-tested in events/tests_theme.py.
"""

import colorsys
import re

ROLES = ('primary', 'secondary', 'tertiary')
MODES = ('light', 'dark')
SOURCES = ('ai', 'site', 'manual', 'default')  # site = from the website's colours without the AI
HEX_RE = re.compile(r'^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$')

WHITE = '#FFFFFF'
INK = '#120F22'  # the console's darkest text: text on light fills
TEXT_MIN = 4.5  # WCAG AA for normal text
UI_MIN = 3.0  # WCAG non-text contrast (hover colours, underlines)

# The pages a theme is drawn on (console.css / site.css): surfaces text must be readable against.
PAGE = {
    'light': {'bg': '#F7F5FC', 'surface': '#FFFFFF', 'text': '#1B1830'},
    'dark': {'bg': '#110E1A', 'surface': '#1B1728', 'text': '#ECE9F6'},
}

DEFAULT = {
    'light': {'primary': '#431F86', 'secondary': '#B79CF2', 'tertiary': '#7B4FD6'},
    'dark': {'primary': '#9D7BEA', 'secondary': '#6F47D6', 'tertiary': '#C4B0FF'},
}


# ---------------------------------------------------------------------------
# Colour maths
# ---------------------------------------------------------------------------

def clean_hex(value):
    """'#RRGGBB' upper case, or '' when it isn't a colour ('#abc' and 'abc123' are accepted)."""
    m = HEX_RE.match(str(value or '').strip())
    if not m:
        return ''
    h = m.group(1)
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return '#' + h.upper()


def rgb(hex_value):
    h = clean_hex(hex_value)
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def to_hex(r, g, b):
    return '#' + ''.join(f'{max(0, min(255, round(v))):02X}' for v in (r, g, b))


def hls(hex_value):
    r, g, b = rgb(hex_value)
    return colorsys.rgb_to_hls(r / 255, g / 255, b / 255)


def from_hls(h, l, s):
    r, g, b = colorsys.hls_to_rgb(h % 1.0, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
    return to_hex(r * 255, g * 255, b * 255)


def relative_luminance(hex_value):
    """WCAG 2.2 relative luminance (sRGB)."""
    def channel(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb(hex_value)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a, b):
    """WCAG contrast ratio, 1 … 21."""
    la, lb = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def mix(a, b, weight):
    """`weight` of colour a over colour b (0 = b, 1 = a)."""
    ra, rb = rgb(a), rgb(b)
    return to_hex(*(x * weight + y * (1 - weight) for x, y in zip(ra, rb)))


def shift_lightness(hex_value, delta):
    h, l, s = hls(hex_value)
    return from_hls(h, l + delta, s)


def saturation(hex_value):
    return hls(hex_value)[2]


def until_contrast(color, against, minimum, direction):
    """Move `color` in lightness (direction -1 darker, +1 lighter) until it reaches `minimum` against every colour
    in `against`. Always ends (black and white are the extremes)."""
    def ok(c):
        return all(contrast(c, other) >= minimum for other in against)
    current = color
    for _ in range(60):
        if ok(current):
            return current
        nxt = shift_lightness(current, 0.02 * direction)
        if nxt == current:
            break
        current = nxt
    return INK if direction < 0 else WHITE


# ---------------------------------------------------------------------------
# Themes
# ---------------------------------------------------------------------------

def default_theme():
    return {'light': dict(DEFAULT['light']), 'dark': dict(DEFAULT['dark']), 'source': 'default'}


def _derive(colors, to_mode):
    """A mode from the other one: dark fills and highlights get lighter, light ones darker; the soft secondary keeps
    its hue."""
    out = {}
    for role in ROLES:
        h, l, s = hls(colors[role])
        if role == 'secondary':
            out[role] = from_hls(h, min(l, 0.55) if to_mode == 'dark' else max(l, 0.7), s)
        elif to_mode == 'dark':
            out[role] = from_hls(h, max(l, 0.66), s)
        else:
            out[role] = from_hls(h, min(l, 0.38), s)
    return out


def _mode(raw):
    if not isinstance(raw, dict):
        return None
    colors = {r: clean_hex(raw.get(r)) for r in ROLES}
    if not colors['primary']:
        return None
    h, l, s = hls(colors['primary'])
    colors['secondary'] = colors['secondary'] or from_hls(h, max(l, 0.75), s * 0.8)
    colors['tertiary'] = colors['tertiary'] or from_hls(h + 0.08, l, s)
    return colors


def normalize(theme):
    """A clean theme, or {} when it has no usable colours. Bad entries are dropped; a missing role comes from the
    mode's primary, a missing mode from the other mode."""
    if not isinstance(theme, dict):
        return {}
    modes = {m: _mode(theme.get(m)) for m in MODES}
    if not modes['light'] and not modes['dark']:
        return {}
    modes['light'] = modes['light'] or _derive(modes['dark'], 'light')
    modes['dark'] = modes['dark'] or _derive(modes['light'], 'dark')
    out = {'light': modes['light'], 'dark': modes['dark']}
    source = theme.get('source')
    out['source'] = source if source in SOURCES else 'manual'
    for key in ('made_at', 'reason', 'cost_usd'):
        value = theme.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            out[key] = str(value).strip()[:300]
    raw = theme.get('page_colors')
    page_colors = [c for c in (clean_hex(v) for v in raw) if c] if isinstance(raw, list) else []
    if page_colors:
        out['page_colors'] = page_colors[:12]  # the website's colours the AI started from (Generate again uses them)
    return out


def tokens(colors, mode):
    """CSS custom properties for one mode, with contrast guaranteed:
    - primary → --color-brand (fill; white or near-black text on it ≥ 4.5:1, nudged in lightness until it is),
      --color-on-brand, --color-brand-dark (hover), --color-brand-text (≥ 4.5:1 on the page, its surfaces and the
      soft tint), --color-focus;
    - secondary → --color-brand-soft / --color-brand-line (tints over the surface) and --color-secondary;
    - tertiary → --color-brand-mid (≥ 3:1 on the page: hovers, underlines) and --color-tertiary.
    """
    page = PAGE[mode]
    dark = mode == 'dark'
    primary, secondary, tertiary = (clean_hex(colors[r]) for r in ROLES)

    fill = primary
    if contrast(fill, WHITE) >= TEXT_MIN:
        on = WHITE
    elif contrast(fill, INK) >= TEXT_MIN:
        on = INK
    elif contrast(fill, WHITE) >= contrast(fill, INK):
        fill, on = until_contrast(fill, [WHITE], TEXT_MIN, -1), WHITE
    else:
        fill, on = until_contrast(fill, [INK], TEXT_MIN, +1), INK

    soft = mix(secondary, page['surface'], 0.24 if dark else 0.14)
    line = mix(secondary, page['surface'], 0.45 if dark else 0.34)
    glow = mix(secondary, page['bg'], 0.22 if dark else 0.18)
    surfaces = [page['bg'], page['surface'], soft]
    text = until_contrast(primary, surfaces, TEXT_MIN, +1 if dark else -1)
    mid = until_contrast(tertiary, [page['bg'], page['surface']], UI_MIN, +1 if dark else -1)
    r, g, b = rgb(text if dark else fill)
    return {
        '--color-brand': fill,
        '--color-brand-dark': shift_lightness(fill, -0.07 if on == WHITE else 0.07),
        '--color-brand-mid': mid,
        '--color-brand-text': text,
        '--color-brand-soft': soft,
        '--color-brand-line': line,
        '--color-on-brand': on,
        '--color-secondary': secondary,
        '--color-tertiary': tertiary,
        '--color-focus': f'rgba({r}, {g}, {b}, {".5" if dark else ".38"})',
        '--color-bg-tint': f'radial-gradient(1200px 600px at 100% -10%, {glow} 0%, rgba({",".join(str(v) for v in rgb(glow))}, 0) 60%), {page["bg"]}',
        '--brand-gradient': f'linear-gradient(135deg, {fill} 0%, {mid} 100%)',
    }


def adjusted(theme):
    """The colours as they render (contrast-adjusted), per mode: what the API sends."""
    theme = normalize(theme)
    if not theme:
        return None
    out = {}
    for mode in MODES:
        t = tokens(theme[mode], mode)
        out[mode] = {'primary': t['--color-brand'], 'secondary': theme[mode]['secondary'],
                     'tertiary': t['--color-brand-mid']}
    return out


def checks(theme):
    """{mode: {role: {'value', 'used', 'contrast', 'note'}}}: what each colour does and any adjustment, in plain words."""
    theme = normalize(theme) or default_theme()
    out = {}
    for mode in MODES:
        colors = theme[mode]
        t = tokens(colors, mode)
        page = PAGE[mode]
        rows = {}
        p = colors['primary']
        notes = []
        if t['--color-brand'] != p:
            notes.append('Text on this primary is hard to read; buttons use a slightly '
                         + ('darker' if relative_luminance(t['--color-brand']) < relative_luminance(p) else 'lighter')
                         + ' shade.')
        if t['--color-brand-text'] != p:
            notes.append('As text on the page it is too ' + ('dark' if mode == 'dark' else 'light')
                         + '; links use ' + ('a lighter' if mode == 'dark' else 'a darker') + ' shade.')
        rows['primary'] = {'value': p, 'used': t['--color-brand'],
                           'contrast': round(contrast(t['--color-brand'], t['--color-on-brand']), 1),
                           'note': ' '.join(notes)}
        s = colors['secondary']
        rows['secondary'] = {'value': s, 'used': s, 'contrast': round(contrast(s, page['surface']), 1),
                             'note': ('Close to the page colour: it shows only as a faint tint.'
                                      if contrast(s, page['surface']) < 1.25 else '')}
        tt = colors['tertiary']
        rows['tertiary'] = {'value': tt, 'used': t['--color-brand-mid'],
                            'contrast': round(contrast(t['--color-brand-mid'], page['surface']), 1),
                            'note': ('Too faint on the page for highlights; we use a '
                                     + ('lighter' if mode == 'dark' else 'darker') + ' shade.'
                                     if t['--color-brand-mid'] != tt else '')}
        out[mode] = rows
    return out


def has_theme(event):
    return bool(normalize(getattr(event, 'theme', None)))


def for_bundle(event):
    """API-023: the event's adjusted colours, or None (no theme, or the organizer turned it off for the apps)."""
    if not getattr(event, 'theme_apps', False):
        return None
    return adjusted(event.theme)


# ---------------------------------------------------------------------------
# CSS for the website
# ---------------------------------------------------------------------------

def _block(selector, values):
    return selector + '{' + ';'.join(f'{k}:{v}' for k, v in values.items()) + '}'


def css(theme, light_selector=':root', dark_selector=':root:not([data-theme="light"])',
        forced_dark=':root[data-theme="dark"]', aliases=None):
    """The token overrides for a page, in the same three places the stylesheet defines its tokens (light, the dark
    media query, a pinned dark theme). `aliases` adds page tokens that take a theme token's value
    ({'--color-join-qr': '--color-brand-soft'}). '' without a theme."""
    theme = normalize(theme)
    if not theme:
        return ''
    light = tokens(theme['light'], 'light')
    dark = tokens(theme['dark'], 'dark')
    for name, source in (aliases or {}).items():
        light[name], dark[name] = light[source], dark[source]
    parts = [_block(light_selector, light), '@media (prefers-color-scheme: dark){' + _block(dark_selector, dark) + '}']
    if forced_dark:
        parts.append(_block(forced_dark, dark))
    return '\n'.join(parts)


# ---------------------------------------------------------------------------
# A theme from colours found on the event's pages (the AI's fallback)
# ---------------------------------------------------------------------------

def from_page_colors(colors):
    """A deterministic theme from the most used colourful page colour, or None when there is none."""
    for c in colors or ():
        c = clean_hex(c)
        if not c:
            continue
        h, l, s = hls(c)
        if s >= 0.25 and 0.12 <= l <= 0.88:
            light = {'primary': from_hls(h, min(l, 0.45), s),
                     'secondary': from_hls(h + 0.05, 0.75, s * 0.75),
                     'tertiary': from_hls(h - 0.08, 0.5, s)}
            return normalize({'light': light, 'source': 'site', 'reason': f'From the colour {c} on the event’s website.'})
    return None


def site_css(event):
    """The public join / shared-plan pages (home/site.css, `<html data-scheme="auto">`): '' unless the event has a
    theme and "Use custom theme on the website" is on."""
    if not getattr(event, 'theme_web', False):
        return ''
    return css(event.theme, dark_selector=':root[data-scheme="auto"]', forced_dark='',
               aliases={'--color-join-qr': '--color-brand-soft'})
