"""Console editing of the assistant context (API-018, ``events.context``).

The section "Details for the assistant" shows each context key as a textarea. Text keys are plain text; lists use
one simple, validated line format each (no JavaScript needed):

    FAQ           Q: Can I bring a chair?            (blocks separated by an empty line;
                  A: Yes, low beach chairs only.      the answer may continue on more lines)
    Amenities     kind | name | location | note      (location = one of the event's locations, may be empty)
    Menu          name | price | note
    Performers    name | genres, comma separated | short bio [| https://music.apple.com/… or "no link"]
    Links         title | https://…

Saving a changed context puts ``context`` in ``changed_data``, so the view locks it against AI refreshes.
"""

from django import forms

from events import context as ctx

HELP = {
    'faq': 'Blocks of “Q: question” and “A: answer” lines, separated by an empty line.',
    'amenities': 'One per line: kind | name | location | note. Kinds: ' + ', '.join(ctx.AMENITY_KINDS) + '.',
    'menu': 'One per line: name | price | note, e.g. “Brisket plate | $16 | gluten-free”.',
    'performers': 'One per line: name | genres (comma separated) | short bio. Attnd adds Apple Music links it is sure '
                  'about; end a line with | https://music.apple.com/… to set one yourself, or | no link for none.',
    'links': 'One per line: title | https://…',
}
ROWS = {'about': 5, 'faq': 6, 'amenities': 4, 'menu': 5, 'performers': 4, 'links': 2}


def _cells(line, count):
    cells = [c.strip() for c in line.split('|')]
    return (cells + [''] * count)[:count] if len(cells) <= count else cells[:count - 1] + [' | '.join(cells[count - 1:])]


def _flat(value):
    return ' '.join(str(value or '').replace('|', '/').split())


# ---------------------------------------------------------------------------
# Context → text
# ---------------------------------------------------------------------------

def to_text(key, value, names=None, music_links=None):
    if not value:
        return ''
    if key == 'faq':
        return '\n\n'.join(f'Q: {e["q"]}\nA: {e["a"]}' for e in value)
    if key == 'amenities':
        rows = []
        for e in value:
            location = e.get('location_name') or (names or {}).get(e.get('location_id')) or ''
            rows.append(' | '.join([e['kind'], _flat(e['name']), _flat(location), _flat(e.get('note'))]).rstrip(' |'))
        return '\n'.join(rows)
    if key == 'menu':
        return '\n'.join(' | '.join([_flat(e['name']), _flat(e.get('price')), _flat(e.get('note'))]).rstrip(' |')
                         for e in value)
    if key == 'performers':
        from events import music
        rows = []
        for e in value:
            row = ' | '.join([_flat(e['name']), ', '.join(e.get('genres') or []), _flat(e.get('bio'))]).rstrip(' |')
            entry = (music_links or {}).get(music.norm(e['name']))
            if entry is not None:  # API-030: the link (or the organizer's "no link")
                row = f'{row} | {entry or NO_LINK}'
            rows.append(row)
        return '\n'.join(rows)
    if key == 'links':
        return '\n'.join(f'{_flat(e["title"])} | {e["url"]}' for e in value)
    return value


# ---------------------------------------------------------------------------
# Text → context (raises ValidationError)
# ---------------------------------------------------------------------------

def _lines(text):
    return [(n, line.strip()) for n, line in enumerate((text or '').splitlines(), 1) if line.strip()]


def parse_faq(text):
    entries, current, part, gap = [], None, None, False
    for n, raw in enumerate((text or '').splitlines(), 1):
        line = raw.strip()
        lower = line.lower()
        if lower.startswith('q:'):
            current = {'q': line[2:].strip(), 'a': '', 'line': n}
            entries.append(current)
            part = 'q'
        elif lower.startswith('a:'):
            if current is None:
                raise forms.ValidationError(f'Line {n}: an answer needs a “Q:” line before it.')
            current['a'] = line[2:].strip()
            part = 'a'
        elif line:
            if current is None:
                raise forms.ValidationError(f'Line {n}: start each question with “Q:”.')
            joint = ' ' if part == 'q' else ('\n\n' if gap else '\n')
            current[part] = (current[part] + joint + line).strip()
        gap = not line
    for e in entries:
        if not e['q'] or not e['a']:
            raise forms.ValidationError(f'Line {e["line"]}: each question needs a “Q:” and an “A:”.')
    return [{'q': e['q'], 'a': e['a']} for e in entries]


def parse_amenities(text, find_location=None):
    out = []
    for n, line in _lines(text):
        kind, name, location, note = _cells(line, 4)
        if ctx.amenity_kind(kind) == 'other' and kind.strip().lower() != 'other':
            raise forms.ValidationError(f'Line {n}: “{kind}” isn’t a kind. Use one of: {", ".join(ctx.AMENITY_KINDS)}.')
        if not name:
            raise forms.ValidationError(f'Line {n}: add a name after the kind (kind | name | location | note).')
        location_id = None
        if location:
            location_id = find_location(location) if find_location else None
            if location_id is None:
                raise forms.ValidationError(f'Line {n}: there is no location called “{location}”.')
        out.append({'kind': kind, 'name': name, 'location_id': location_id, 'note': note})
    return out


def parse_menu(text):
    out = []
    for n, line in _lines(text):
        name, price, note = _cells(line, 3)
        if not name:
            raise forms.ValidationError(f'Line {n}: start with the name (name | price | note).')
        out.append({'name': name, 'price': price, 'note': note})
    return out


NO_LINK = 'no link'


def split_music(line):
    """(line without the music cell, url / '' for "no link" / None when the line has no music cell). API-030."""
    from events import music
    head, sep, last = line.rpartition('|')
    cell = last.strip()
    if sep and (music.valid_url(cell) or cell.lower() == NO_LINK):
        return head.rstrip(), '' if cell.lower() == NO_LINK else cell
    if sep and cell.lower().startswith(('https://music.apple', 'http://music.apple', 'music.apple')):
        raise forms.ValidationError(f'“{cell}” isn’t an Apple Music link (https://music.apple.com/…).')
    return line, None


def parse_performers(text, music_edits=None):
    out = []
    for n, line in _lines(text):
        try:
            line, link = split_music(line)
        except forms.ValidationError as exc:
            raise forms.ValidationError(f'Line {n}: {exc.messages[0]}') from None
        name, genres, bio = _cells(line, 3)
        if not name:
            raise forms.ValidationError(f'Line {n}: start with the name (name | genres | bio).')
        out.append({'name': name, 'genres': [g.strip() for g in genres.split(',') if g.strip()], 'bio': bio})
        if link is not None and music_edits is not None:
            music_edits[name] = link
    return out


def parse_links(text):
    out = []
    for n, line in _lines(text):
        cells = [c.strip() for c in line.split('|')]
        url = cells[-1]
        title = ' | '.join(cells[:-1]) if len(cells) > 1 else ''
        if not ctx.https_url(url):
            raise forms.ValidationError(f'Line {n}: “{url}” isn’t an https:// link (title | https://…).')
        out.append({'title': title, 'url': url})
    return out


class AssistantContextMixin:
    """Adds ``ctx_<key>`` fields for the model's ``context``. Set ``context_kind`` on the form class; call
    ``_init_context()`` in ``__init__`` and ``_save_context()`` in ``save``."""

    context_kind = None

    def _context_locations(self):
        event = self.instance if self.context_kind == ctx.EVENT else None
        if event is None or not event.pk:
            return {}
        return dict(event.location_set.values_list('id', 'name'))

    def _init_context(self):
        kind = self.context_kind
        self._location_names = self._context_locations()
        current = ctx.normalize(kind, self.instance.context)
        for key in ctx.KEYS[kind]:
            limit = ctx.TEXTS[kind].get(key)
            label = ctx.LABELS[key]
            self.fields[f'ctx_{key}'] = forms.CharField(
                label=label, required=False, strip=True,
                help_text=HELP.get(key, f'Up to {limit} characters.' if limit else ''),
                widget=forms.Textarea(attrs={'rows': ROWS.get(key, 2 if (limit or 0) <= 600 else 3)}))
            self.initial[f'ctx_{key}'] = to_text(key, current.get(key), self._location_names, self._music_shown())

    def _music_shown(self):
        """{normalised name: url, or '' for the organizer's "no link"} (API-030; only on schedule items)."""
        if self.context_kind != ctx.ITEM:
            return {}
        out = {}
        for key, entry in (getattr(self.instance, 'music', None) or {}).items():
            if isinstance(entry, dict) and (entry.get('url') or entry.get('locked')):
                out[key] = entry.get('url') or ''
        return out

    @property
    def context_fields(self):
        return [self[f'ctx_{key}'] for key in ctx.KEYS[self.context_kind]]

    @property
    def context_errors(self):
        return self.is_bound and any(self.errors.get(f'ctx_{key}') for key in ctx.KEYS[self.context_kind])

    def _parse_context(self):
        names = {v.lower().strip(): k for k, v in self._location_names.items()}

        def find(name):
            return names.get(' '.join(name.lower().split()))

        raw, errors = {}, False
        for key in ctx.KEYS[self.context_kind]:
            text = self.cleaned_data.get(f'ctx_{key}', '')
            try:
                if key == 'faq':
                    raw[key] = parse_faq(text)
                elif key == 'amenities':
                    raw[key] = parse_amenities(text, find)
                elif key == 'menu':
                    raw[key] = parse_menu(text)
                elif key == 'performers':
                    self.music_edits = {}
                    raw[key] = parse_performers(text, self.music_edits)
                elif key == 'links':
                    raw[key] = parse_links(text)
                else:
                    raw[key] = text
            except forms.ValidationError as exc:
                self.add_error(f'ctx_{key}', exc)
                errors = True
        return None if errors else ctx.normalize(self.context_kind, raw)

    def clean(self):
        data = super().clean()
        self.cleaned_context = self._parse_context()
        if self.cleaned_context is not None and self.instance.pk:
            kind, names = self.context_kind, self._location_names
            if (ctx.normalize_named(kind, self.cleaned_context, names)
                    != ctx.normalize_named(kind, self.instance.context, names)):
                if 'context' not in self.changed_data:
                    self.changed_data.append('context')  # the view locks it (mark_locked)
            else:
                self.cleaned_context = ctx.normalize(kind, self.instance.context)  # unchanged: keep it as stored
        return data

    def _save_context(self):
        if getattr(self, 'cleaned_context', None) is not None:
            self.instance.context = self.cleaned_context
        if self.context_kind == ctx.ITEM and getattr(self, 'music_edits', None):
            from events import music
            music.set_links(self.instance, self.music_edits)  # the organizer's own links (locked)
