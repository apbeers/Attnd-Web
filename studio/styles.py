"""Studio's site styles (batch 7 Track D): the documented set of five looks the best generated sites settled into.
"From scratch" always lands on one of them: `pick(event)` chooses by what the event is, `prompt_text(style, event)`
goes into the first design's instruction (studio.views.starter_instruction) and the designer follows it. Each style
says its layout (sections in order, with the data library elements from attnd.js), its type scale, how it uses the
event theme's three colours (API-023) and its do's and don'ts. Platform → Site styles draws every style as a preview
(`preview_html`, static: no scripts, sample data in place of the live elements) with the event's colours."""

from django.utils.html import escape

SYSTEM_FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif'
SERIF_FONT = '"Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif'
CONDENSED_FONT = '"Arial Narrow", "Roboto Condensed", "Helvetica Neue", Arial, sans-serif'

STYLES = [
    {
        'key': 'poster', 'name': 'Festival poster',
        'best_for': 'Festivals, concerts, art walks, fairs: big crowds, many stages, a headliner.',
        'pages': ['index.html', 'lineup.html', 'map.html', 'faq.html'],
        'layout': [
            ('Dark top bar', 'logo left, a hamburger that opens a full-screen menu'),
            ('Full-bleed hero', 'flyer art or a photo, the name in huge condensed type, <attnd-event field="dates">, '
                                '<attnd-countdown>'),
            ('Pill buttons', 'a stack of big → pills: Lineup, Map, Food, Parking, FAQ, Get the app'),
            ('Lineup', '<attnd-lineup limit="12"> as cards on a colour block'),
            ('Map', '<attnd-map height="420"> with "Get here" (parking, shuttle, accessibility)'),
            ('Get the app', '<attnd-app-badge qr code> on a dark block'),
            ('Footer', 'dates, address, sponsors, socials'),
        ],
        'type': {'display': CONDENSED_FONT, 'body': SYSTEM_FONT, 'scale': 'h1 clamp(2.8rem, 11vw, 6rem) / 0.95, '
                 'uppercase, 800; h2 clamp(1.8rem, 6vw, 3rem); body 1.0625rem / 1.55'},
        'colours': 'primary = hero overlay, pills and alternating blocks (white text); secondary = the lineup block; '
                   'tertiary = highlights (the countdown, underlines); the top bar and app block near-black.',
        'components': ['attnd-event', 'attnd-countdown', 'attnd-lineup', 'attnd-map', 'attnd-app-badge'],
        'do': ['Huge, confident headlines that say when ("THIS SATURDAY")', 'Alternate dark and colour blocks',
               'Pills at least 56px tall, full width on phones'],
        'dont': ['Body text on the flyer art without a dark overlay', 'More than one display font'],
    },
    {
        'key': 'program', 'name': 'Program',
        'best_for': 'Conferences, talks, theatre and film series: sessions, speakers, rooms.',
        'pages': ['index.html', 'schedule.html', 'venue.html'],
        'layout': [
            ('Light header', 'the name and dates, a plain nav (Schedule · Speakers · Venue · App)'),
            ('Intro', 'two sentences on what it is, <attnd-event field="address">'),
            ('Schedule', '<attnd-schedule group-by="day"> in a readable column, day headings sticky'),
            ('Speakers / performers', '<attnd-lineup> as a two-column list, names in the serif'),
            ('Venue', '<attnd-map height="320"> + rooms (<attnd-locations>)'),
            ('Get the app', '<attnd-app-badge qr code> in a quiet card'),
        ],
        'type': {'display': SERIF_FONT, 'body': SYSTEM_FONT, 'scale': 'h1 clamp(2.2rem, 6vw, 3.6rem) / 1.1, 600; '
                 'h2 1.6rem; body 1.0625rem / 1.6; measure 68ch'},
        'colours': 'mostly white and ink; primary = links, the day headings and buttons; secondary = a soft tint '
                   'behind the current day; tertiary = the time column.',
        'components': ['attnd-event', 'attnd-schedule', 'attnd-lineup', 'attnd-map', 'attnd-locations',
                       'attnd-app-badge'],
        'do': ['A calm, readable column', 'Times aligned in their own column', 'Rooms named exactly as in Attnd'],
        'dont': ['Full-bleed colour blocks', 'Text wider than 70 characters'],
    },
    {
        'key': 'market', 'name': 'Market day',
        'best_for': "Farmers' markets, holiday markets, food truck rallies, craft fairs: vendors and hours.",
        'pages': ['index.html', 'vendors.html', 'map.html'],
        'layout': [
            ('Warm header', 'the name, "Saturdays 8 AM – 1 PM" (<attnd-event field="time">), the address'),
            ('Map first', '<attnd-map height="360"> with the entrances and parking'),
            ('Vendors', '<attnd-locations images> as rounded cards grouped by category'),
            ("What's on", '<attnd-schedule limit="8"> (demos, music, kids)'),
            ('Get updates', 'a newsletter form (data-attnd-form="newsletter")'),
            ('Get the app', '<attnd-app-badge qr code>'),
        ],
        'type': {'display': SYSTEM_FONT, 'body': SYSTEM_FONT, 'scale': 'h1 clamp(2.2rem, 8vw, 4rem) / 1.05, 800; '
                 'h2 1.5rem, 700; body 1.0625rem / 1.55'},
        'colours': 'secondary = the page wash (a warm tint); primary = buttons and category chips; tertiary = '
                   'prices and "open now"; cards white with soft shadows.',
        'components': ['attnd-event', 'attnd-map', 'attnd-locations', 'attnd-schedule', 'attnd-app-badge'],
        'do': ['Big rounded cards with the vendor photo', 'Hours and the address above the fold',
               'Category chips that jump to their group'],
        'dont': ['Dark backgrounds', 'A lineup section (markets have vendors, not headliners)'],
    },
    {
        'key': 'course', 'name': 'Course & countdown',
        'best_for': 'Races, rides, walks and tournaments: a start time, a course, packet pickup.',
        'pages': ['index.html', 'course.html', 'faq.html'],
        'layout': [
            ('Countdown hero', '<attnd-countdown> huge, the start time and place under it'),
            ('Key times', '<attnd-schedule group-by="none"> (packet pickup, start, awards) as a timeline'),
            ('Course', '<attnd-map height="440"> with water stations and the finish (<attnd-locations>)'),
            ('FAQ', '<details> on parking, bags, timing, accessibility'),
            ('Get the app', '<attnd-app-badge qr code>: "Live updates on race day"'),
        ],
        'type': {'display': CONDENSED_FONT, 'body': SYSTEM_FONT, 'scale': 'countdown clamp(3rem, 14vw, 7rem), '
                 'tabular numbers, 800; h2 1.75rem uppercase; body 1.0625rem / 1.55'},
        'colours': 'primary = the hero and the timeline line; tertiary = the countdown digits; secondary = the FAQ '
                   'background; white elsewhere.',
        'components': ['attnd-countdown', 'attnd-schedule', 'attnd-map', 'attnd-locations', 'attnd-app-badge'],
        'do': ['The start time impossible to miss', 'A timeline with big times', 'Urgent updates near the top '
               '(<attnd-updates limit="1">)'],
        'dont': ['Sponsor walls above the course', 'Small type on the course map'],
    },
    {
        'key': 'single', 'name': 'One page',
        'best_for': 'Small events: one place, a handful of items, a launch or a party.',
        'pages': ['index.html'],
        'layout': [
            ('Hero', 'the name, <attnd-event field="dates">, <attnd-event field="address">'),
            ('About', 'two or three sentences'),
            ('Schedule', '<attnd-schedule group-by="none">'),
            ('Map', '<attnd-map height="300">'),
            ('Get the app', '<attnd-app-badge qr code>'),
        ],
        'type': {'display': SYSTEM_FONT, 'body': SYSTEM_FONT, 'scale': 'h1 clamp(2.2rem, 8vw, 3.6rem) / 1.05, 750; '
                 'h2 1.4rem; body 1.0625rem / 1.6'},
        'colours': 'primary = the hero band and buttons; secondary = section dividers; tertiary = links on hover.',
        'components': ['attnd-event', 'attnd-schedule', 'attnd-map', 'attnd-app-badge'],
        'do': ['Everything on one scrolling page with in-page links', 'Short sections'],
        'dont': ['Extra pages with one sentence each'],
    },
]
BY_KEY = {s['key']: s for s in STYLES}

WORDS = {
    'market': ('market', 'farmers', 'vendor', 'food truck', 'bazaar', 'craft fair', 'flea', 'makers'),
    'course': ('5k', '10k', 'half marathon', 'marathon', 'race', 'fun run', 'run ', 'ride', 'tournament', 'triathlon'),
    'program': ('conference', 'summit', 'keynote', 'session', 'workshop', 'symposium', 'talk', 'film series',
                'theatre', 'theater', 'expo'),
}


def _text(event):
    from django.db.models import Q
    bits = [event.name or '', event.description or '']
    locs = event.location_set.filter(hidden=False)
    bits += [c for c in locs.values_list('category', flat=True) if c]
    for tags in (list(locs.values_list('tags', flat=True)[:60])
                 + list(event.scheduleditem_set.filter(~Q(hidden=True)).values_list('tags', flat=True)[:120])):
        bits += list(tags or [])
    return ' ' + ' '.join(bits).lower() + ' '


def pick(event):
    """The style for a first design: by what the event is (its name, description, categories and tags), else by its
    size (small → One page), else Festival poster."""
    text = _text(event)
    for key in ('course', 'market', 'program'):
        if any(word in text for word in WORDS[key]):
            return BY_KEY[key]
    places = event.location_set.filter(hidden=False).count()
    items = event.scheduleditem_set.filter(hidden=False).count()
    if places <= 2 and items <= 6:
        return BY_KEY['single']
    return BY_KEY['poster']


def prompt_text(style):
    """The style as the designer's instruction (goes into the first design; the system prompt stays the same)."""
    layout = '; '.join(f'{i}. {name}: {what}' for i, (name, what) in enumerate(style['layout'], 1))
    t = style['type']
    return (f'SITE STYLE "{style["name"]}" (follow it: it wins over the general design brief). Pages: '
            f'{", ".join(style["pages"])}. Layout of the home page: {layout}. Type: display {t["display"]}; body '
            f'{t["body"]}; scale {t["scale"]}. Colours (the event theme): {style["colours"]} Set --attnd-accent and '
            f'the other --attnd-* properties from them. Do: {"; ".join(style["do"])}. Don\'t: '
            f'{"; ".join(style["dont"])}.')


def colours(event=None):
    """{'primary', 'secondary', 'tertiary', 'on', 'text', 'soft', 'mid'} for the light mode of the event's theme
    (Attnd's purple without one), contrast-adjusted like the console."""
    from events import theme as T
    data = T.normalize(getattr(event, 'theme', None) or {}) or T.default_theme()
    tok = T.tokens(data['light'], 'light')
    return {'primary': tok['--color-brand'], 'on': tok['--color-on-brand'], 'text': tok['--color-brand-text'],
            'soft': tok['--color-brand-soft'], 'mid': tok['--color-brand-mid'],
            'secondary': tok['--color-secondary'], 'tertiary': tok['--color-tertiary']}


def sample(event=None):
    """What the previews show: the event's own name, dates, places and items, else a made-up festival."""
    if event is None:
        return {'name': 'Prairie Lights Festival', 'dates': 'Sat Nov 14 – Sun Nov 15', 'address': 'Panther Island, Fort Worth',
                'places': ['Main Stage', 'Taco Row', 'Kids Corner', 'Art Alley'],
                'items': [('11:00 AM', 'Gates open', 'Main gate'), ('1:30 PM', 'The Prairie Lights', 'Main Stage'),
                          ('4:00 PM', 'Neon Armadillo', 'Main Stage'), ('6:00 PM', 'Lantern walk', 'Art Alley')]}
    from zoneinfo import ZoneInfo

    from events.content import models_q_visible_location
    try:
        tz = ZoneInfo(event.timezone or 'UTC')
    except Exception:
        tz = None
    items = []
    for item in (event.scheduleditem_set.filter(hidden=False).filter(models_q_visible_location())
                 .select_related('location').order_by('start_time')[:4]):
        when = item.start_time.astimezone(tz) if tz and item.start_time else item.start_time
        items.append((when.strftime('%-I:%M %p') if when else '', item.name, item.location.name if item.location else 'General'))
    start = event.start_time.astimezone(tz) if tz and event.start_time else event.start_time
    return {'name': event.name, 'dates': start.strftime('%a %b %-d') if start else 'Dates to come',
            'address': event.address or '', 'items': items,
            'places': list(event.location_set.filter(hidden=False).values_list('name', flat=True)[:4])}


def preview_html(style, event=None):
    """A static, script-free preview of the style's home page with the event's colours (for the gallery's sandboxed
    iframes). The live elements are drawn as sample blocks labelled with the element they stand for."""
    c, s = colours(event), sample(event)
    t = style['type']
    e = escape
    rows = ''.join(f'<li><span class="t">{e(a)}</span><b>{e(b)}</b><span class="m">{e(p)}</span></li>' for a, b, p in s['items'])
    places = ''.join(f'<li>{e(p)}</li>' for p in s['places'])
    tag = lambda name: f'<span class="el">&lt;{name}&gt;</span>'  # noqa: E731
    blocks = {
        'poster': f'''<header class="bar dark"><b>{e(s["name"])}</b><span>☰</span></header>
<section class="hero full"><p class="eyebrow">{e(s["dates"])}</p><h1>{e(s["name"])}</h1><p>{e(s["address"])}</p>{tag("attnd-countdown")}</section>
<nav class="pills"><a>Lineup →</a><a>Map →</a><a>Food →</a><a>FAQ →</a></nav>
<section class="block soft">{tag("attnd-lineup")}<ul class="cards">{rows}</ul></section>
<section class="block">{tag("attnd-map")}<div class="map"></div></section>
<section class="block dark app">{tag("attnd-app-badge")}<p>Get the Attnd app</p></section>''',
        'program': f'''<header class="bar light"><b>{e(s["name"])}</b><span>Schedule · Speakers · Venue</span></header>
<section class="intro col"><h1>{e(s["name"])}</h1><p class="lead">{e(s["dates"])} · {e(s["address"])}</p></section>
<section class="col">{tag("attnd-schedule")}<h2 class="day">{e(s["dates"])}</h2><ul class="times">{rows}</ul></section>
<section class="col">{tag("attnd-map")}<div class="map short"></div></section>''',
        'market': f'''<header class="hero warm"><h1>{e(s["name"])}</h1><p>{e(s["dates"])} · {e(s["address"])}</p></header>
<section class="block">{tag("attnd-map")}<div class="map"></div></section>
<section class="block">{tag("attnd-locations")}<ul class="vendor">{places}</ul></section>
<section class="block">{tag("attnd-schedule")}<ul class="times">{rows}</ul></section>''',
        'course': f'''<section class="hero full"><p class="eyebrow">{e(s["dates"])}</p><p class="count">02<small>d</small> 14<small>h</small> 30<small>m</small></p><h1>{e(s["name"])}</h1>{tag("attnd-countdown")}</section>
<section class="block">{tag("attnd-schedule")}<ol class="line">{rows}</ol></section>
<section class="block">{tag("attnd-map")}<div class="map tall"></div></section>
<section class="block soft"><h2>FAQ</h2><p>Parking · Bags · Timing</p></section>''',
        'single': f'''<section class="hero band"><h1>{e(s["name"])}</h1><p>{e(s["dates"])} · {e(s["address"])}</p></section>
<section class="col"><h2>Schedule</h2>{tag("attnd-schedule")}<ul class="times">{rows}</ul></section>
<section class="col">{tag("attnd-map")}<div class="map short"></div></section>
<section class="col">{tag("attnd-app-badge")}<p>Get the Attnd app</p></section>''',
    }[style['key']]
    css = f'''*{{box-sizing:border-box}}body{{margin:0;font:1.0625rem/1.55 {t["body"]};color:#1b1830;background:{"#fff" if style["key"] != "market" else c["soft"]}}}
h1,h2{{font-family:{t["display"]};margin:.2em 0;line-height:1;overflow-wrap:anywhere}}h1{{font-size:clamp(2.2rem,9vw,4.4rem);font-weight:800}}
.bar{{display:flex;justify-content:space-between;align-items:center;padding:14px 18px}}.bar.dark{{background:#111;color:#fff}}.bar.light{{border-bottom:1px solid #e6e1ef}}
.hero{{padding:48px 20px}}.hero.full{{background:linear-gradient(160deg,{c["primary"]},#111);color:{c["on"]}}}.hero.full h1{{text-transform:uppercase;font-size:clamp(2.8rem,12vw,5.4rem)}}
.hero.warm{{background:{c["soft"]};color:#1b1830}}.hero.band{{background:{c["primary"]};color:{c["on"]}}}
.eyebrow{{text-transform:uppercase;letter-spacing:.12em;font-weight:700;color:{c["tertiary"]};margin:0}}.hero.full .eyebrow{{color:{c["on"]};opacity:.85}}
.count{{font:800 clamp(3rem,14vw,6rem)/1 {t["display"]};font-variant-numeric:tabular-nums;margin:0}}.count small{{font-size:.35em;margin-right:.3em}}
.pills{{display:grid;gap:10px;padding:18px}}.pills a{{display:block;padding:16px 20px;border-radius:999px;background:{c["primary"]};color:{c["on"]};font-weight:700}}
.block{{padding:28px 20px}}.block.soft{{background:{c["soft"]}}}.block.dark{{background:#111;color:#fff}}
.col{{max-width:68ch;margin:0 auto;padding:24px 20px}}.intro h1{{font-weight:600}}.lead{{color:#555}}.day{{color:{c["text"]};font-size:1.3rem}}
.cards{{list-style:none;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}}.cards li{{background:#fff;border-radius:14px;padding:12px;display:grid}}
.times,.line,.vendor{{list-style:none;padding:0;margin:0}}.times li,.line li{{display:grid;grid-template-columns:6em 1fr;gap:2px 12px;padding:10px 0;border-bottom:1px solid #eee}}
.times .m,.line .m,.cards .m{{grid-column:2;color:#666;font-size:.9em}}.t{{color:{c["text"]};font-weight:700;font-variant-numeric:tabular-nums}}
.line li{{border-left:4px solid {c["primary"]};padding-left:12px;border-bottom:0}}
.vendor{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}}.vendor li{{background:#fff;border-radius:18px;padding:40px 14px 14px;box-shadow:0 4px 14px rgba(0,0,0,.08);font-weight:700}}
.map{{height:180px;border-radius:14px;background:repeating-linear-gradient(45deg,{c["soft"]},{c["soft"]} 12px,#fff 12px,#fff 24px)}}.map.short{{height:130px}}.map.tall{{height:220px}}
.el{{display:inline-block;margin:4px 0 8px;padding:2px 8px;border-radius:6px;font:12px ui-monospace,monospace;background:rgba(0,0,0,.06);color:inherit}}
.app p{{font-weight:700;font-size:1.3rem}}'''
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, '
            f'initial-scale=1"><title>{e(style["name"])}</title><style>{css}</style></head><body><main>{blocks}</main></body></html>')
