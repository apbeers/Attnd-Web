"""Discovery (staff only): find upcoming events in the region that suit the app, read their pages, keep the
organizer contacts published there.

A run has two steps:

A. Search: Claude Sonnet with Anthropic's web search tool (``web_search_20260209``; Haiku can't search) lists upcoming
   events in the region within the horizon that match the themes (a few themes per call), as JSON candidates.
B. Verify + enrich each candidate we don't know yet (dedupe keys, and not already an Attnd event's source): fetch its
   page with Studio's SSRF-guarded fetch (``studio.importer.fetch``: public hosts only, every redirect checked,
   size cap) plus up to two "about / contact / sponsors / vendors" pages on the same site, then one Claude Haiku
   structured-output call scores the fit (0–100) and lists the organizer contacts found on those pages.

Contacts are checked here, never trusted from the model: each needs a ``source_url`` that is one of the pages we
fetched, and an email or phone number is kept only when it appears on that page. Nothing is ever guessed. Attnd
never emails prospects itself.

"Run now" runs in a daemon thread of the web dyno (like AI imports); the scheduled path (``prospects.schedule``)
runs inline. Runs never overlap (``claim`` locks the settings row), stop at ``PROSPECT_RUN_MAX_MINUTES``, at the
run's candidate cap and at the daily spend cap ``PROSPECT_DAILY_SPEND_CAP_USD`` (over every run that UTC day).
"""

import json
import logging
import re
import threading
import time
import unicodedata
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from django.conf import settings
from django.db import close_old_connections, connection, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from prospects.models import (Prospect, ProspectActivity, ProspectContact, ProspectRun, ProspectSettings)

logger = logging.getLogger(__name__)

THEMES_PER_SEARCH = 5
MAX_PAGE_CHARS = 15_000
MAX_SOURCE_CHARS = 40_000
MAX_EXTRA_PAGES = 2
MAX_URLS = 10
MAX_CONTACTS = 20

TRAITS = ['outdoor', 'mostly_outdoor', 'multiple_stops', 'walking', 'free_entry', 'ticketed', 'recurring',
          'family_friendly', 'food_drink', 'music', 'art', 'market', 'evening', 'multi_day']

CONTACT_LINK_RE = re.compile(r'about|contact|sponsor|vendor|exhibitor|organi[sz]er|team|staff|who-we-are|'
                             r'our-story|partners?|committee|board|press|media', re.I)


class StopRun(Exception):
    """A cap was reached: stop reading, keep what we have."""


# ---------------------------------------------------------------------------
# Keys (dedupe)
# ---------------------------------------------------------------------------

def url_key(url):
    """host without www + path, lower case, no trailing slash, query or fragment."""
    parts = urlparse((url or '').strip())
    host = (parts.hostname or '').lower().removeprefix('www.')
    if not host:
        return ''
    path = re.sub(r'/(?:index\.(?:html?|php))?$', '', parts.path or '').lower()
    return f'{host}{path}'[:512]


_FILLER = {'the', 'annual', 'a', 'an', 'of', 'and', 'presents', 'presented', 'by'}


def name_key(name, year=None):
    text = unicodedata.normalize('NFKD', name or '').encode('ascii', 'ignore').decode().lower()
    text = re.sub(r'&', ' and ', text)
    words = [w for w in re.findall(r'[a-z0-9]+', text)
             if w not in _FILLER and not re.fullmatch(r'(19|20)\d\d|\d+(st|nd|rd|th)', w)]
    if not words:
        return ''
    return (' '.join(words)[:280] + f'|{year or ""}')


def _date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _http_url(value):
    value = (value or '').strip()
    parts = urlparse(value)
    return value if parts.scheme in ('http', 'https') and parts.hostname else ''


# ---------------------------------------------------------------------------
# Claude (search: Sonnet with web search; scoring: Haiku with structured output) + cost
# ---------------------------------------------------------------------------

def spent_today():
    start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    total = ProspectRun.objects.filter(created_at__gte=start).aggregate(t=Sum('cost_usd'))['t']
    return total or Decimal('0')


def budget_left():
    return _daily_cap() - spent_today()


def search_cost(searches):
    return Decimal(str(settings.PROSPECT_WEB_SEARCH_CALL_USD)) * searches


def parse_json(text):
    """The reply's JSON object (the first { to the last }), or {}."""
    text = text or ''
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def respond(instructions, user_input, schema, name, web_search=None):
    """One Claude call. With `web_search` (a web_search_20260209 tool): Claude Sonnet searches the web (Haiku can't)
    and answers with JSON matching `schema` (in words: the reply follows the searches); pause_turn is resumed. Without:
    Claude Haiku with structured output. Returns (data, cost, model)."""
    from attnd import claude
    if not web_search:
        try:
            data, cost = claude.structured(system=instructions, content=user_input, schema=schema,
                                           model=settings.PROSPECT_SCORE_MODEL, effort='low', max_tokens=8000)
        except claude.ClaudeError as exc:
            raise RuntimeError(str(exc)) from exc
        return data, cost, settings.PROSPECT_SCORE_MODEL
    model = settings.PROSPECT_MODEL
    system = (instructions + '\n\nWhen you are done searching, reply with ONE JSON object only (no prose), matching '
              'this JSON schema:\n' + json.dumps(schema, separators=(',', ':')))
    messages = [{'role': 'user', 'content': user_input}]
    cost, searches, response = Decimal('0'), 0, None
    for _ in range(4):  # the server-side search loop may pause (pause_turn): resend to continue
        try:
            response, call = claude.message(system=system, messages=messages, model=model, effort='medium',
                                            max_tokens=16000, tools=[web_search], timeout=600.0)
        except claude.ClaudeError as exc:
            raise RuntimeError(str(exc)) from exc
        cost += call
        used = getattr(getattr(response.usage, 'server_tool_use', None), 'web_search_requests', 0) or 0
        searches += used
        if response.stop_reason != 'pause_turn':
            break
        messages = [messages[0], {'role': 'assistant', 'content': [b.model_dump(mode='json', exclude_none=True)
                                                                   if hasattr(b, 'model_dump') else b
                                                                   for b in response.content]}]
    if response is None or response.stop_reason == 'refusal':
        raise RuntimeError('The AI declined the search.')
    text = '\n'.join(b.text for b in response.content if getattr(b, 'type', '') == 'text')
    return parse_json(text), cost + search_cost(searches), model


# ---------------------------------------------------------------------------
# Schemas and prompts
# ---------------------------------------------------------------------------

def _obj(props):
    return {'type': 'object', 'properties': props, 'required': list(props), 'additionalProperties': False}


_S = {'type': 'string'}
_NS = {'type': ['string', 'null']}

SEARCH_SCHEMA = _obj({'candidates': {'type': 'array', 'items': _obj({
    'name': _S,
    'urls': {'type': 'array', 'items': _S, 'description': "The event's own page first (organizer site), then "
                                                          'listings (max 4). Only URLs you saw in search results'},
    'start_date': {**_NS, 'description': 'YYYY-MM-DD of the next date, or null'},
    'end_date': {**_NS, 'description': 'YYYY-MM-DD, or null'},
    'city': _S,
    'venue': {**_S, 'description': 'Venue, district or area; may be empty'},
    'organizer': {**_S, 'description': 'Organizing organization if the results say; may be empty'},
    'why': {**_S, 'description': 'One short sentence: why it fits'},
})}})

SEARCH_PROMPT = """You find upcoming events for Attnd, a free app attendees use at events where people walk around
between several stops: a map of the stops, the schedule, live updates. Use web search.

A good fit is outdoor or mostly outdoor, and people walk between multiple stops, addresses, booths, galleries,
venues or stages (art walks, street festivals, markets, crawls, tours, parades, fairs, open streets). Poor fits:
single-room indoor shows, conferences in one hall, private events, virtual events, sports games.

Rules: only real events you found in search results; only URLs you actually saw; prefer the organizer's own site
over ticket or listing sites; dates as YYYY-MM-DD (null when unknown); events inside the region and within the
date range only; no duplicates; an empty list is fine."""

CONTACT = _obj({
    'name': {**_S, 'description': 'Person name, or empty'},
    'role': {**_S, 'description': 'Role or title, or empty'},
    'organization': {**_S, 'description': 'Organization, or empty'},
    'email': {**_S, 'description': 'Exactly as written on the page, or empty. Never guess one'},
    'phone': {**_S, 'description': 'Exactly as written on the page, or empty'},
    'website': {**_S, 'description': 'Organization website URL, or empty'},
    'profile_url': {**_S, 'description': 'Social or profile page URL, or empty'},
    'source_url': {**_S, 'description': 'The SOURCE url (from the page headers) where this contact appears'},
})

ENRICH_SCHEMA = _obj({
    'is_event': {'type': 'boolean', 'description': 'False when the pages are not about one real public event'},
    'name': _S,
    'organizer': {**_S, 'description': 'The organizing organization or person, or empty'},
    'summary': {**_S, 'description': 'Two sentences for a sales person: what it is, how big, what people do'},
    'start_date': _NS,
    'end_date': _NS,
    'city': _S,
    'venue': _S,
    'fit_score': {'type': 'integer', 'description': '0-100 against the fit rule'},
    'fit_reasons': {'type': 'array', 'items': _S, 'description': 'Up to 5 short reasons, good and bad'},
    'traits': {'type': 'array', 'items': {'type': 'string', 'enum': TRAITS}},
    'contacts': {'type': 'array', 'items': CONTACT, 'description': 'Organizer contacts on these pages (max 10)'},
})

ENRICH_PROMPT = """You qualify an event as a sales prospect for Attnd, a free app for attendees of events where people
walk between multiple stops (map of the stops, schedule, live updates), and Attnd Studio (event websites).

Fit rule (score 0-100): outdoor or mostly outdoor; people walk between multiple stops, addresses, booths, galleries,
venues or stages; has a schedule, a list of participants or a map worth putting in an app; inside the region;
upcoming within the date range. Lower the score for each part that's missing; under 20 for indoor single-venue,
past, private or out-of-region events.

Contacts: the people or organizations who organize or run the event, from these pages only (organizer, director,
coordinator, committee, the organizing nonprofit or business, its press / sponsorship / vendor contact). Copy emails
and phone numbers exactly as written; never guess or construct one. Business and organizer contact details only:
no attendees, vendors' customers or private individuals' home details. Give each contact the SOURCE url it appeared
on. Dates YYYY-MM-DD or null. Use only facts from the pages."""


# ---------------------------------------------------------------------------
# Fetching (Studio's SSRF-guarded fetch, scrape's text extraction)
# ---------------------------------------------------------------------------

def _decode(data, ctype):
    match = re.search(rb'charset=["\']?([\w-]+)', data[:2000])
    for enc in ([match.group(1).decode()] if match else []) + ['utf-8', 'latin-1']:
        try:
            return data.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode('utf-8', 'replace')


def fetch_page(url):
    """(final_url, html, text) or None. Text = visible text + JSON-LD + the page's mailto/tel links."""
    from scrape.service import _page_text
    from studio.importer import MAX_HTML, SiteImportError, fetch
    try:
        data, ctype, final = fetch(url, MAX_HTML)
    except SiteImportError as exc:
        logger.info('Prospect fetch refused or failed: %s', exc)
        return None
    if 'html' not in ctype and not data.lstrip()[:15].lower().startswith((b'<!doctype', b'<html')):
        return None
    html = _decode(data, ctype)
    soup = BeautifulSoup(html, 'html.parser')
    links = sorted({a['href'].strip() for a in soup.find_all('a', href=True)
                    if a['href'].strip().lower().startswith(('mailto:', 'tel:'))})
    text = _page_text(html)[:MAX_PAGE_CHARS]
    if links:
        text += '\n[Contact links] ' + ' '.join(links)[:2000]
    return final, html, text


def contact_links(html, base_url, limit=MAX_EXTRA_PAGES):
    """Up to `limit` same-site about / contact / sponsor / vendor pages."""
    host = (urlparse(base_url).hostname or '').lower().removeprefix('www.')
    soup = BeautifulSoup(html, 'html.parser')
    found = []
    for a in soup.find_all('a', href=True):
        url = urljoin(base_url, a['href']).split('#')[0]
        parts = urlparse(url)
        if parts.scheme not in ('http', 'https') or (parts.hostname or '').lower().removeprefix('www.') != host:
            continue
        if url_key(url) == url_key(base_url) or url in found:
            continue
        if CONTACT_LINK_RE.search(parts.path) or CONTACT_LINK_RE.search(a.get_text(' ', strip=True)[:60]):
            found.append(url)
        if len(found) >= limit:
            break
    return found


# ---------------------------------------------------------------------------
# Contacts: the source rule
# ---------------------------------------------------------------------------

def _digits(text):
    return re.sub(r'\D', '', text or '')


def clean_contacts(contacts, pages):
    """Keep only contacts the pages back up. `pages` = {url: raw page html + text}. An email or phone is kept only
    when it is written on its source page; a contact without a fetched source_url is dropped."""
    out, seen = [], set()
    for c in contacts or []:
        if not isinstance(c, dict):
            continue
        source = (c.get('source_url') or '').strip()
        page = pages.get(source)
        if page is None:  # also try without a trailing slash difference
            match = [u for u in pages if url_key(u) == url_key(source)]
            source = match[0] if match else ''
            page = pages.get(source)
        if page is None:
            continue
        lower = page.lower()
        email = (c.get('email') or '').strip().removeprefix('mailto:')
        if email and (not re.fullmatch(r'[^@\s]+@[^@\s]+\.[A-Za-z]{2,}', email) or email.lower() not in lower):
            email = ''
        phone = (c.get('phone') or '').strip()
        digits = _digits(phone)
        if phone and (len(digits) < 7 or digits not in _digits(page)):
            phone = ''
        row = {
            'name': (c.get('name') or '').strip()[:256],
            'role': (c.get('role') or '').strip()[:256],
            'organization': (c.get('organization') or '').strip()[:256],
            'email': email[:254],
            'phone': phone[:64],
            'website': _http_url(c.get('website'))[:1024],
            'profile_url': _http_url(c.get('profile_url'))[:1024],
            'source_url': source[:1024],
        }
        if not (row['name'] or row['organization']) or not (row['name'] or row['email'] or row['phone']
                                                             or row['profile_url'] or row['website']):
            continue
        key = row['email'].lower() or f"{row['name'].lower()}|{row['organization'].lower()}"
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out[:MAX_CONTACTS]


def add_contacts(prospect, rows):
    """Add contacts not already on the prospect (same email, or same name + organization). Returns the count."""
    existing = set()
    for c in prospect.contacts.all():
        if c.email:
            existing.add(c.email.lower())
        existing.add(f'{c.name.lower()}|{c.organization.lower()}')
    added = 0
    for row in rows:
        keys = {f"{row['name'].lower()}|{row['organization'].lower()}"} | ({row['email'].lower()} if row['email'] else set())
        if keys & existing:
            continue
        ProspectContact.objects.create(prospect=prospect, **row)
        existing |= keys
        added += 1
    return added


# ---------------------------------------------------------------------------
# A run
# ---------------------------------------------------------------------------

class Runner:
    def __init__(self, run, conf=None, max_new=None, max_minutes=None):
        self.run = run
        self.conf = conf or ProspectSettings.load()
        self.max_new = max(1, max_new) if max_new else self.conf.max_candidates
        self.max_minutes = min(max_minutes or settings.PROSPECT_RUN_MAX_MINUTES, settings.PROSPECT_RUN_MAX_MINUTES)
        self.deadline = time.monotonic() + self.max_minutes * 60
        self.today = timezone.localdate()
        self.until = self.today + timedelta(days=self.conf.horizon_days)
        self.lines = []
        self.known_sources = self._event_source_keys()
        self.seen_keys = set()

    # -- bookkeeping --
    def log(self, text):
        stamp = timezone.now().strftime('%H:%M:%S')
        self.lines.append(f'{stamp} {text}')
        self.run.log = '\n'.join(self.lines)[-20000:]
        self.run.save(update_fields=['log', 'found', 'new', 'updated', 'skipped', 'cost_usd'])

    def spend(self, cost):
        self.run.cost_usd += cost
        self.run.save(update_fields=['cost_usd'])

    def check(self):
        if time.monotonic() > self.deadline:
            raise StopRun(f'Stopped: the run reached its time limit ({self.max_minutes} min).')
        if budget_left() <= 0:
            raise StopRun(f'Stopped: today\'s prospect AI budget (${_daily_cap():.2f}) '
                          'is used up.')

    @staticmethod
    def _event_source_keys():
        from events.models import Event
        keys = set()
        for urls in Event.objects.exclude(source_urls=[]).values_list('source_urls', flat=True):
            keys |= {url_key(u) for u in urls or [] if isinstance(u, str)}
        keys.discard('')
        return keys

    # -- steps --
    def search(self, themes):
        self.check()
        region = self.conf.region
        user = (f'Region: {region}\nDate range: {self.today.isoformat()} to {self.until.isoformat()}\n'
                f'Kinds of events: {", ".join(themes)}\n'
                f'Find up to {min(15, self.max_new)} upcoming events.')
        tool = {'type': 'web_search_20260209', 'name': 'web_search', 'max_uses': 8,
                'user_location': {'type': 'approximate', 'country': 'US', 'timezone': self.conf.timezone}}
        data, cost, model = respond(SEARCH_PROMPT, user, SEARCH_SCHEMA, 'event_candidates', web_search=tool)
        self.spend(cost)
        found = [c for c in data.get('candidates') or [] if isinstance(c, dict) and c.get('name')]
        self.log(f'Search ({", ".join(themes)}): {len(found)} candidates, ${cost:.4f} ({model})')
        return found

    def find_existing(self, urls, nkey):
        keys = [url_key(u) for u in urls if url_key(u)]
        q = Q(url_key__in=keys) if keys else Q(pk__in=[])
        if nkey:
            q |= Q(name_key=nkey)
        return Prospect.objects.filter(q).order_by('pk').first()

    def merge_seen(self, prospect, cand):
        """A known prospect seen again: newer dates, more URLs, blanks filled; status and notes untouched."""
        start, end = _date(cand.get('start_date')), _date(cand.get('end_date'))
        changed = []
        if start and start != prospect.start_date:
            prospect.start_date, prospect.end_date = start, end or prospect.end_date
            changed.append('dates')
        urls = list(dict.fromkeys(prospect.urls + [u for u in (_http_url(x) for x in cand.get('urls') or []) if u]))
        if len(urls) != len(prospect.urls):
            prospect.urls = urls[:MAX_URLS]
            changed.append('links')
        for field in ('city', 'venue', 'organizer'):
            if not getattr(prospect, field) and cand.get(field):
                setattr(prospect, field, str(cand[field])[:256])
                changed.append(field)
        prospect.last_seen_run = self.run
        prospect.save()
        if changed:
            ProspectActivity.objects.create(prospect=prospect, text=f'Seen again in run {self.run.pk}: '
                                                                    f'{", ".join(changed)} updated')
        return bool(changed)

    def enrich(self, cand, urls):
        """Fetch the pages and qualify the candidate. Returns (fields, contacts) or None."""
        pages, texts = {}, []
        first = None
        for url in urls[:2]:
            self.check()
            got = fetch_page(url)
            if got:
                first = first or got
                final, html, text = got
                pages[final] = html + '\n' + text
                texts.append(f'## SOURCE: {final}\n\n{text}')
                break  # the event's own page is enough; the other URLs are listings
        if first is None:
            return None
        for extra in contact_links(first[1], first[0]):
            self.check()
            got = fetch_page(extra)
            if got and got[0] not in pages:
                pages[got[0]] = got[1] + '\n' + got[2]
                texts.append(f'## SOURCE: {got[0]}\n\n{got[2]}')
        self.check()
        user = (f'Region: {self.conf.region}\nToday: {self.today.isoformat()}; date range up to '
                f'{self.until.isoformat()}\nCandidate from web search: {json.dumps(cand, ensure_ascii=False)}\n\n'
                + '\n\n---\n\n'.join(texts)[:MAX_SOURCE_CHARS])
        data, cost, model = respond(ENRICH_PROMPT, user, ENRICH_SCHEMA, 'prospect')
        self.spend(cost)
        if not data:
            return None
        data['contacts'] = clean_contacts(data.get('contacts'), pages)
        data['_cost'], data['_model'] = cost, model
        data['_pages'] = list(pages)
        return data

    def save_new(self, cand, urls, data):
        start = _date(data.get('start_date')) or _date(cand.get('start_date'))
        end = _date(data.get('end_date')) or _date(cand.get('end_date'))
        name = (data.get('name') or cand.get('name') or '').strip()[:256]
        score = max(0, min(100, int(data.get('fit_score') or 0)))
        reasons = [str(r)[:200] for r in (data.get('fit_reasons') or [])][:5]
        if (end or start) and (end or start) < self.today:
            score = min(score, 10)
            reasons.append('Already over')
        nkey = name_key(name, start.year if start else None)
        existing = self.find_existing(urls, nkey)
        traits = [t for t in data.get('traits') or [] if t in TRAITS]
        if existing:  # the same event under another URL: merge
            self.merge_seen(existing, {**cand, 'urls': urls})
            if not existing.fit_reasons:
                existing.fit_score, existing.fit_reasons, existing.traits = score, reasons, traits
                existing.save()
            added = add_contacts(existing, data['contacts'])
            if added:
                ProspectActivity.objects.create(prospect=existing, text=f'{added} contact(s) added by run {self.run.pk}')
            return existing, False
        prospect = Prospect.objects.create(
            name=name or 'Untitled event', organizer=(data.get('organizer') or cand.get('organizer') or '')[:256],
            summary=(data.get('summary') or cand.get('why') or '')[:2000],
            start_date=start, end_date=end, city=(data.get('city') or cand.get('city') or '')[:128],
            venue=(data.get('venue') or cand.get('venue') or '')[:256], urls=urls[:MAX_URLS], fit_score=score,
            fit_reasons=reasons, traits=traits, url_key=url_key(urls[0]), name_key=nkey,
            first_seen_run=self.run, last_seen_run=self.run)
        add_contacts(prospect, data['contacts'])
        ProspectActivity.objects.create(prospect=prospect, text=f'Found by run {self.run.pk} '
                                                                f'(fit {score}, ${data["_cost"]:.4f})')
        return prospect, True

    def handle(self, cand):
        urls = list(dict.fromkeys(u for u in (_http_url(x) for x in cand.get('urls') or []) if u))
        if not urls:
            self.run.skipped += 1
            self.log(f'Skipped {cand.get("name")!r}: no link')
            return
        keys = {url_key(u) for u in urls}
        if keys & self.seen_keys:
            return  # the same event from another search in this run
        self.seen_keys |= keys
        self.run.found += 1
        if keys & self.known_sources:
            self.run.skipped += 1
            self.log(f'Skipped {cand["name"]!r}: already an Attnd event')
            return
        start = _date(cand.get('start_date'))
        existing = self.find_existing(urls, name_key(cand['name'], start.year if start else None))
        if existing:
            if self.merge_seen(existing, cand):
                self.run.updated += 1
            else:
                self.run.skipped += 1
            self.log(f'Known: {existing.name!r}')
            return
        if self.run.new >= self.max_new:
            raise StopRun(f'Stopped: {self.max_new} new events read (the run\'s cap).')
        data = self.enrich(cand, urls)
        if data is None or not data.get('is_event', True):
            self.run.skipped += 1
            self.log(f'Skipped {cand["name"]!r}: page not readable or not an event')
            return
        prospect, created = self.save_new(cand, urls, data)
        if created:
            self.run.new += 1
            self.log(f'New: {prospect.name!r}, fit {prospect.fit_score}, {len(data["contacts"])} contact(s)')
        else:
            self.run.updated += 1
            self.log(f'Merged into {prospect.name!r}')

    def execute(self):
        themes = self.conf.theme_list
        if not themes:
            raise ValueError('No search themes are set (Settings).')
        try:
            for i in range(0, len(themes), THEMES_PER_SEARCH):
                for cand in self.search(themes[i:i + THEMES_PER_SEARCH]):
                    try:
                        self.handle(cand)  # no transaction: it waits on the network
                    except StopRun:
                        raise
                    except Exception as exc:  # one bad candidate never sinks the run
                        logger.exception('Prospect candidate failed')
                        self.run.skipped += 1
                        self.log(f'Skipped {cand.get("name")!r}: {exc}')
                if self.run.new >= self.max_new:
                    raise StopRun(f'Stopped: {self.max_new} new events read (the run\'s cap).')
        except StopRun as stop:
            self.log(str(stop))
        self.log(f'Done: {self.run.found} found, {self.run.new} new, {self.run.updated} updated, '
                 f'{self.run.skipped} skipped, ${self.run.cost_usd:.4f}')


# ---------------------------------------------------------------------------
# Starting, claiming (no overlap), running
# ---------------------------------------------------------------------------

def stale_after():
    return timedelta(minutes=settings.PROSPECT_RUN_MAX_MINUTES + 10)


def claim(run_id):
    """Queued → running, unless another run is running (then this one is skipped). The settings row is locked, so
    two dynos can never both start one. Returns the run when it may go ahead."""
    with transaction.atomic():
        conf = ProspectSettings.load()
        ProspectSettings.objects.select_for_update().get(pk=conf.pk)
        run = ProspectRun.objects.select_for_update().get(pk=run_id)
        if run.status != ProspectRun.QUEUED:
            return None
        now = timezone.now()
        for old in ProspectRun.objects.filter(status=ProspectRun.RUNNING).exclude(pk=run.pk):
            if old.started_at and now - old.started_at > stale_after():
                old.status, old.error, old.finished_at = ProspectRun.FAILED, 'Interrupted (restart).', now
                old.save(update_fields=['status', 'error', 'finished_at'])
            else:
                run.status, run.finished_at = ProspectRun.SKIPPED, now
                run.error = f'Run {old.pk} is still running.'
                run.save(update_fields=['status', 'finished_at', 'error'])
                return None
        run.status, run.started_at = ProspectRun.RUNNING, now
        run.save(update_fields=['status', 'started_at'])
        ProspectSettings.objects.filter(pk=conf.pk).update(last_run_at=now)
        return run


def _daily_cap():
    from credit.ledger import limits
    return Decimal(limits().prospects_daily_usd)


def execute(run_id, max_new=None, max_minutes=None):
    """(AI credit: staff-only Prospects is exempt from personal limits; its own daily cap applies.)"""
    from credit import ledger
    with ledger.spend_context(feature='prospects', exempt=True):
        return _execute(run_id, max_new, max_minutes)


def _execute(run_id, max_new=None, max_minutes=None):
    run = claim(run_id)
    if run is None:
        return ProspectRun.objects.get(pk=run_id)
    runner = Runner(run, max_new=max_new, max_minutes=max_minutes)
    try:
        if budget_left() <= 0:
            runner.log(f'Not started: today\'s prospect AI budget (${_daily_cap():.2f}) '
                       'is used up.')
        else:
            runner.execute()
        run.status = ProspectRun.DONE
    except Exception as exc:
        logger.exception('Prospect run %s failed', run.pk)
        run.status, run.error = ProspectRun.FAILED, str(exc)[:2000]
    run.finished_at = timezone.now()
    run.save()
    return run


def create_run(trigger, user=None):
    return ProspectRun.objects.create(trigger=trigger, started_by=user if user and user.is_authenticated else None)


def start_background(user):
    """Run now: a queued run, started in a daemon thread once the transaction commits."""
    run = create_run(ProspectRun.MANUAL, user)
    transaction.on_commit(lambda: threading.Thread(target=_thread_main, args=(run.pk,), daemon=True,
                                                   name=f'prospects-{run.pk}').start())
    return run


def _thread_main(run_id):
    close_old_connections()
    try:
        execute(run_id)
    finally:
        connection.close()


def running_run():
    return ProspectRun.objects.filter(status__in=[ProspectRun.QUEUED, ProspectRun.RUNNING],
                                      created_at__gte=timezone.now() - stale_after()).first()
