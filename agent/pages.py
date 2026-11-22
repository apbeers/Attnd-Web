"""Console HTML → what the agent reads (stdlib html.parser only).

* ``page_text(html, path)``: compact structured text of the page's ``<main>``: headings, key/value pairs, tables as
  rows, lists, notices, form fields with their current values, buttons, and links to other console pages.
* ``forms(html)``: every form with the values a browser would submit (for prefilling writes).
* ``form_errors(html)``: validation errors by field name, plus non-field errors and error notices.
"""

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

SKIP_TAGS = {'script', 'style', 'svg', 'template', 'noscript', 'head', 'iframe', 'canvas'}
BLOCK_TAGS = {'p', 'div', 'section', 'article', 'header', 'footer', 'aside', 'nav', 'ul', 'ol', 'table', 'thead',
              'tbody', 'tr', 'form', 'fieldset', 'details', 'summary', 'dl', 'blockquote', 'pre', 'figure', 'label'}
VOID_TAGS = {'input', 'br', 'hr', 'img', 'meta', 'link', 'source', 'wbr', 'col', 'area', 'base', 'embed', 'track'}
SKIP_CLASSES = {'agent', 'task-tray', 'topbar', 'sidenav', 'back-link'}  # (list_pages has the event's pages)
INLINE_TAGS = {'span', 'strong', 'em', 'b', 'i', 'small', 'code', 'time', 'abbr', 'mark'}


def _attrs(attrs):
    return {k: (v if v is not None else '') for k, v in attrs}


def _squash(text):
    return re.sub(r'[ \t\r\f\v]+', ' ', text)


class _Labels(HTMLParser):
    """First pass: <label for=id> texts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.labels, self._current, self._skip = {}, None, 0

    def handle_starttag(self, tag, attrs):
        a = _attrs(attrs)
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == 'label' and a.get('for'):
            self._current = [a['for'], '']

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag == 'label' and self._current:
            self.labels[self._current[0]] = ' '.join(self._current[1].split())
            self._current = None

    def handle_data(self, data):
        if self._current and not self._skip:
            self._current[1] += data


class _Text(HTMLParser):
    def __init__(self, labels, path):
        super().__init__(convert_charrefs=True)
        self.labels, self.path = labels, path
        self.out = []
        self.stack = []  # (tag, skip, extra)
        self.skip = 0
        self.in_main = None  # None = no <main> seen yet
        self.title = ''
        self._in_title = False
        self.row = None
        self.cell = None
        self.select = None
        self.textarea = None
        self.link = None
        self.button = None
        self.mute = 0
        self.muted = []  # stack depths of muted <label for> elements

    # -- output ----------------------------------------------------------
    def emit(self, text):
        if self.link is not None:
            self.link['text'] += text
        elif self.button is not None:
            self.button['text'] += text
        elif self.cell is not None:
            self.cell.append(text)
        else:
            self.out.append(text)

    def newline(self):
        if self.cell is None and self.link is None:
            self.out.append('\n')

    @property
    def active(self):
        return not self.skip and self.in_main is not False

    # -- parser ----------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        a = _attrs(attrs)
        classes = set(a.get('class', '').split())
        skip = tag in SKIP_TAGS or bool(classes & SKIP_CLASSES) or 'hidden' in a or a.get('aria-hidden') == 'true' \
            or 'data-agent-skip' in a
        if tag == 'label' and a.get('for') in self.labels and not skip:
            self.mute += 1  # its text is shown with its field
            self.muted.append(len(self.stack))
        if tag in INLINE_TAGS and self.active and not skip:
            self.emit(' ')
        if tag == 'title':
            self._in_title = True
        if tag == 'main':
            self.in_main = True
        if tag not in VOID_TAGS:
            self.stack.append((tag, skip))
            if skip:
                self.skip += 1
        if skip or not self.active:
            return
        if tag in ('h1', 'h2', 'h3', 'h4', 'h5'):
            self.newline()
            self.emit('#' * int(tag[1]) + ' ')
        elif tag == 'li':
            self.newline()
            self.emit('- ')
        elif tag == 'tr':
            self.newline()
            self.row = []
        elif tag in ('td', 'th'):
            self.cell = []
        elif tag == 'dt':
            self.newline()
        elif tag == 'dd':
            self.emit(': ')
        elif tag == 'br':
            self.newline()
        elif tag in BLOCK_TAGS:
            self.newline()
            if 'notice' in classes:
                tone = next((c.split('--')[1] for c in classes if c.startswith('notice--') and c != 'notice--dismissible'),
                            'info')
                self.emit(f'[{tone}] ')
            if tag == 'form' and a.get('method', 'get').lower() == 'post':
                action = a.get('action') or self.path
                self.emit(f'[form → {urlsplit(urljoin(self.path, action)).path}] ')
        elif tag == 'a':
            href = a.get('href', '')
            target = urlsplit(urljoin(self.path, href))
            self.link = {'text': '', 'href': target.path + (f'?{target.query}' if target.query else '')
                         if href and not href.startswith(('#', 'mailto:', 'javascript:')) else ''}
        elif tag == 'button':
            self.button = {'text': '', 'name': a.get('name', ''), 'value': a.get('value', ''),
                           'disabled': 'disabled' in a}
        elif tag == 'input':
            self._input(a)
        elif tag == 'select':
            self.select = {'a': a, 'options': [], 'current': None}
        elif tag == 'option' and self.select is not None:
            self.select['current'] = {'value': a.get('value'), 'text': '', 'selected': 'selected' in a}
            self.select['options'].append(self.select['current'])
        elif tag == 'textarea':
            self.textarea = {'a': a, 'text': ''}
        elif tag == 'img' and a.get('alt'):
            self.emit(f'[image: {a["alt"]}]')

    def _label(self, a):
        return self.labels.get(a.get('id', '')) or a.get('aria-label') or a.get('placeholder') or a.get('name', '')

    def _input(self, a):
        kind = a.get('type', 'text').lower()
        name = a.get('name', '')
        if kind in ('hidden', 'submit', 'image', 'reset') or name == 'csrfmiddlewaretoken':
            if kind == 'submit' and a.get('value'):
                self.emit(f' [button: {a["value"]}]')
            return
        label = self._label(a)
        if kind == 'password':
            value = '(hidden)'
        elif kind in ('checkbox', 'radio'):
            value = 'checked' if 'checked' in a else 'not checked'
            if kind == 'radio' or a.get('value') not in (None, '', 'on'):
                label = f'{label} [{name}={a.get("value", "")}]'
                name = ''
        elif kind == 'file':
            value = '(file upload)'
        else:
            value = a.get('value', '')
        field = f' ({name})' if name and name != label else ''
        self.newline()
        self.emit(f'• {label}{field}: {value}' if value != '' else f'• {label}{field}: (empty)')
        self.newline()

    def handle_endtag(self, tag):
        if tag == 'title':
            self._in_title = False
        was_active = self.active
        if tag not in VOID_TAGS:
            # pop to the matching tag (tolerate unclosed tags)
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i][0] == tag:
                    for _, skip in self.stack[i:]:
                        if skip:
                            self.skip -= 1
                    del self.stack[i:]
                    while self.muted and self.muted[-1] >= i:
                        self.muted.pop()
                        self.mute -= 1
                    break
        if tag == 'main':
            self.in_main = False
            return
        if not was_active:
            return
        if tag in ('td', 'th') and self.cell is not None:
            if self.row is not None:
                self.row.append(' '.join(''.join(self.cell).split()))
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            cells = [c for c in self.row]
            if any(cells):
                self.emit('| ' + ' | '.join(cells) + ' |')
            self.row = None
            self.newline()
        elif tag == 'a' and self.link is not None:
            link, self.link = self.link, None
            text = ' '.join(link['text'].split())
            href = link['href']
            if href.startswith('/console/') and text:
                self.emit(f'{text} (→ {href})')
            elif text:
                self.emit(text)
        elif tag == 'button' and self.button is not None:
            b, self.button = self.button, None
            text = ' '.join(b['text'].split())
            if text and not b['disabled']:
                extra = f' ({b["name"]}={b["value"]})' if b['name'] else ''
                self.emit(f' [button: {text}{extra}]')
        elif tag == 'option' and self.select is not None and self.select['current'] is not None:
            self.select['current'] = None
        elif tag == 'select' and self.select is not None:
            s, self.select = self.select, None
            a = s['a']
            chosen = [o for o in s['options'] if o['selected']] or s['options'][:1]
            options = ', '.join(f'{(o["text"] or "").strip()}={o["value"]}' for o in s['options'][:30])
            more = f' … ({len(s["options"])} options)' if len(s['options']) > 30 else ''
            name = a.get('name', '')
            self.newline()
            self.emit(f'• {self._label(a)} ({name}): {", ".join((o["text"] or "").strip() for o in chosen) or "(none)"}'
                      f'  [options: {options}{more}]')
            self.newline()
        elif tag == 'textarea' and self.textarea is not None:
            t, self.textarea = self.textarea, None
            value = t['text'].strip()
            self.newline()
            self.emit(f'• {self._label(t["a"])} ({t["a"].get("name", "")}): ' + (value.replace('\n', '\n    ')
                                                                                  if value else '(empty)'))
            self.newline()
        elif tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'li', 'dd') or tag in BLOCK_TAGS:
            self.newline()

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if not self.active:
            return
        if self.select is not None:
            if self.select['current'] is not None:
                self.select['current']['text'] += data
            return
        if self.textarea is not None:
            self.textarea['text'] += data
            return
        if self.mute:
            return
        self.emit(_squash(data))


def _tidy(text):
    lines = []
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            if lines and lines[-1] != '':
                lines.append('')
            continue
        lines.append(line)
    return '\n'.join(lines).strip()


def page_text(html, path='/'):
    """(title, text) for a console page."""
    labels = _Labels()
    labels.feed(html)
    parser = _Text(labels.labels, path)
    if '<main' not in html:
        parser.in_main = True
    parser.feed(html)
    parser.close()
    title = ' '.join(parser.title.split()).removesuffix('— Attnd').strip(' —')
    return title, _tidy(''.join(parser.out))


# ---------------------------------------------------------------------------
# Forms (what a browser would submit) and errors
# ---------------------------------------------------------------------------

class _Forms(HTMLParser):
    def __init__(self, path):
        super().__init__(convert_charrefs=True)
        self.path = path
        self.forms = []
        self.form = None
        self.select = None
        self.option = None
        self.textarea = None
        # errors
        self.field_stack = []  # [{'names': [...], 'errors': [...]}] for .field containers
        self.depth = []  # tag stack with a marker for .field
        self.error = None  # [target name or None, text]
        self.errors = {}
        self.non_field = []
        self.notice = None

    def handle_starttag(self, tag, attrs):
        a = _attrs(attrs)
        classes = set(a.get('class', '').split())
        if tag not in VOID_TAGS:
            self.depth.append((tag, 'field' in classes, 'notice--error' in classes))
            if 'field' in classes:
                self.field_stack.append({'names': [], 'errors': []})
            if 'notice--error' in classes:
                self.notice = ''
        if tag == 'form':
            action = a.get('action') or self.path
            self.form = {'action': urlsplit(urljoin(self.path, action)).path, 'method': a.get('method', 'get').lower(),
                         'fields': [], 'buttons': [], 'files': [], 'names': set()}
            self.forms.append(self.form)
        elif tag in ('div', 'li', 'p', 'span') and ('field__error' in classes):
            self.error = [None, '']
        elif tag == 'ul' and 'errorlist' in classes:
            target = None
            m = re.match(r'id_(.+)_error$', a.get('id', ''))
            if m:
                target = m.group(1)
            elif 'nonfield' in classes:
                target = '__all__'
            self.error = [target, '']
        if self.form is None and tag not in ('input', 'select', 'textarea', 'button'):
            return
        name = a.get('name', '')
        if tag == 'input':
            kind = a.get('type', 'text').lower()
            self._remember(name)
            if self.form is None or not name or 'disabled' in a:
                return
            if kind in ('submit', 'image'):
                self.form['buttons'].append({'name': name, 'value': a.get('value', '')})
            elif kind == 'file':
                self.form['files'].append(name)
                self.form['names'].add(name)
            elif kind in ('checkbox', 'radio'):
                self.form['names'].add(name)
                self.form.setdefault('checkables', {}).setdefault(name, []).append(a.get('value', 'on'))
                if 'checked' in a:
                    self.form['fields'].append((name, a.get('value', 'on')))
            elif kind != 'reset':
                self.form['names'].add(name)
                self.form['fields'].append((name, a.get('value', '')))
        elif tag == 'button':
            if self.form is not None and name:
                self.form['buttons'].append({'name': name, 'value': a.get('value', '')})
        elif tag == 'select':
            self._remember(name)
            self.select = {'name': name, 'multiple': 'multiple' in a, 'options': [], 'disabled': 'disabled' in a}
        elif tag == 'option' and self.select is not None:
            self.option = {'value': a.get('value'), 'text': '', 'selected': 'selected' in a}
            self.select['options'].append(self.option)
        elif tag == 'textarea':
            self._remember(name)
            self.textarea = {'name': name, 'text': '', 'disabled': 'disabled' in a}

    def _remember(self, name):
        if name and self.field_stack:
            self.field_stack[-1]['names'].append(name)

    def handle_endtag(self, tag):
        if tag == 'form':
            self.form = None
        elif tag == 'option':
            self.option = None
        elif tag == 'select' and self.select is not None:
            s, self.select = self.select, None
            if self.form is not None and s['name'] and not s['disabled']:
                self.form['names'].add(s['name'])
                chosen = [o for o in s['options'] if o['selected']]
                if not chosen and not s['multiple'] and s['options']:
                    chosen = s['options'][:1]
                for o in chosen:
                    self.form['fields'].append((s['name'], o['value'] if o['value'] is not None else o['text'].strip()))
                self.form.setdefault('choices', {})[s['name']] = [
                    (o['value'] if o['value'] is not None else o['text'].strip()) for o in s['options']]
        elif tag == 'textarea' and self.textarea is not None:
            t, self.textarea = self.textarea, None
            if self.form is not None and t['name'] and not t['disabled']:
                self.form['names'].add(t['name'])
                text = t['text'][1:] if t['text'].startswith('\n') else t['text']
                self.form['fields'].append((t['name'], text.replace('\r\n', '\n')))
        if self.error is not None and tag in ('div', 'li', 'p', 'span', 'ul'):
            target, text = self.error
            text = ' '.join(text.split())
            if tag == 'ul' or tag in ('div', 'p', 'span') or (tag == 'li' and target is None):
                if text:
                    if target is None and self.field_stack:
                        self.field_stack[-1]['errors'].append(text)
                    elif target == '__all__' or target is None:
                        self.non_field.append(text)
                    else:
                        self.errors.setdefault(target, []).append(text)
                self.error = None
        if tag not in VOID_TAGS:
            for i in range(len(self.depth) - 1, -1, -1):
                if self.depth[i][0] == tag:
                    for _, is_field, is_notice in reversed(self.depth[i:]):
                        if is_field and self.field_stack:
                            box = self.field_stack.pop()
                            if box['errors']:
                                key = box['names'][0] if box['names'] else '__all__'
                                self.errors.setdefault(key, []).extend(box['errors'])
                        if is_notice and self.notice is not None:
                            text = ' '.join(self.notice.split()).rstrip('×').strip()
                            if text:
                                self.non_field.append(text)
                            self.notice = None
                    del self.depth[i:]
                    break

    def handle_data(self, data):
        if self.option is not None:
            self.option['text'] += data
        if self.textarea is not None:
            self.textarea['text'] += data
        if self.error is not None:
            self.error[1] += data
        if self.notice is not None:
            self.notice += data


def forms(html, path='/'):
    parser = _Forms(path)
    parser.feed(html)
    parser.close()
    return parser.forms


def form_errors(html, path='/'):
    parser = _Forms(path)
    parser.feed(html)
    parser.close()
    errors = {k.split('-', 1)[-1] if '-' in k else k: v for k, v in parser.errors.items()}
    if parser.non_field:
        errors.setdefault('__all__', []).extend(dict.fromkeys(parser.non_field))
    return errors
