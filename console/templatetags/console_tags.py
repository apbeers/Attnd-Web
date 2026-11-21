"""Inline SVG icons for the console — no icon font or third-party icon set."""

from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()

# 24×24 stroke icons drawn for Attnd.
_PATHS = {
    'arrow-left': '<path d="M19 12H5"/><path d="M12 19l-7-7 7-7"/>',
    'expand': '<path d="M15 3h6v6"/><path d="M9 21H3v-6"/><path d="M21 3l-7 7"/><path d="M3 21l7-7"/>',
    'collapse': '<path d="M4 14h6v6"/><path d="M20 10h-6V4"/><path d="M14 10l7-7"/><path d="M3 21l7-7"/>',
    'chevron-right': '<path d="M9 18l6-6-6-6"/>',
    'stars': '<path d="M12 3l1.8 4.2L18 9l-4.2 1.8L12 15l-1.8-4.2L6 9l4.2-1.8z"/><path d="M19 15l.8 1.7 1.7.8-1.7.8L19 20l-.8-1.7-1.7-.8 1.7-.8z"/>',
    'lock': '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    'unlock': '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 7.5-2"/>',
    'plus': '<path d="M12 5v14M5 12h14"/>',
    'pin': '<path d="M12 21s-7-6.5-7-12a7 7 0 0 1 14 0c0 5.5-7 12-7 12z"/><circle cx="12" cy="9" r="2.5"/>',
    'pin-fill': '<path fill="currentColor" d="M12 21s-7-6.5-7-12a7 7 0 0 1 14 0c0 5.5-7 12-7 12z"/><circle cx="12" cy="9" r="2.5" fill="#fff"/>',
    'calendar': '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    'phone': '<rect x="7" y="2" width="10" height="20" rx="2"/><path d="M11 18h2"/>',
    'info': '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    'grip': '<circle cx="9" cy="6" r="1"/><circle cx="15" cy="6" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="9" cy="18" r="1"/><circle cx="15" cy="18" r="1"/>',
    'bell': '<path d="M6 16v-5a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
    'gauge': '<path d="M4 18a8 8 0 1 1 16 0"/><path d="M12 18l4-6"/>',
    'send': '<path d="M4 12l16-8-6 16-2-7z"/>',
    'printer': '<path d="M6 9V3h12v6"/><rect x="4" y="9" width="16" height="8" rx="1"/><rect x="7" y="14" width="10" height="7"/>',
    'people': '<circle cx="9" cy="8" r="3"/><path d="M3 20a6 6 0 0 1 12 0"/><circle cx="17" cy="9" r="2.5"/><path d="M15.5 14.2A5 5 0 0 1 21 19"/>',
    'map': '<path d="M3 6l6-3 6 3 6-3v15l-6 3-6-3-6 3z"/><path d="M9 3v15M15 6v15"/>',
    'list': '<path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01"/>',
    'link': '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    'image': '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5L5 21"/>',
    'warning': '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18h.01"/>',
    'download': '<path d="M12 3v12M7 10l5 5 5-5M4 21h16"/>',
    'history': '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 3"/>',
    'check': '<path d="M5 12l5 5 9-10"/>',
    'check-circle': '<circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/>',
    'chat': '<path d="M4 4h16v12H8l-4 4z"/>',
    'broadcast': '<circle cx="12" cy="12" r="2"/><path d="M8.5 8.5a5 5 0 0 0 0 7M15.5 8.5a5 5 0 0 1 0 7M5.5 5.5a9 9 0 0 0 0 13M18.5 5.5a9 9 0 0 1 0 13"/>',
    'refresh': '<path d="M20 11a8 8 0 0 0-14.9-3M4 4v4h4M4 13a8 8 0 0 0 14.9 3M20 20v-4h-4"/>',
    'chart': '<path d="M4 20V4M4 20h16"/><path d="M8 16v-5M12 16V8M16 16v-3M20 16V6"/>',
    'support': '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14M12 17h.01"/>',
    'close': '<path d="M6 6l12 12M18 6L6 18"/>',
    'paperclip': '<path d="M20 11.5l-8 8a5 5 0 0 1-7-7l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.4 8.4a1.7 1.7 0 0 1-2.4-2.4L15 7"/>',
    'stop': '<rect x="6" y="6" width="12" height="12" rx="1.5"/>',
    'copy': '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
    'mic': '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
    'key': '<circle cx="8" cy="15" r="4"/><path d="M10.8 12.2L20 3M16 7l3 3M14 9l2 2"/>',
    'heart': '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>',
    'tag': '<path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="8.5" r="1.5"/>',
    'mail': '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
    'code': '<path d="M8 7l-5 5 5 5M16 7l5 5-5 5M14 4l-4 16"/>',
    'globe': '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    'upload': '<path d="M12 16V4M7 9l5-5 5 5M4 20h16"/>',
    'undo': '<path d="M9 14L4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>',
    'cursor': '<path d="M5 3l14 7-6 2-2 6z"/>',
    'target': '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    'brush': '<path d="M14 4l6 6-8 8H6v-6z"/><path d="M6 18l-3 3"/>',
}


@register.simple_tag
def icon(name, css_class=''):
    paths = _PATHS.get(name, _PATHS['info'])
    return format_html(
        '<svg class="icon {}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{}</svg>',
        css_class, mark_safe(paths))


@register.simple_tag(takes_context=True)
def passkey_prompt(context):
    """True once after a password sign-in: base.html offers "Sign in faster with a passkey"."""
    request = context.get('request')
    if request is None:
        return False
    from console.views_passkeys import take_prompt
    return take_prompt(request)


# ---------------------------------------------------------------------------
# Components (console/components): {% component "event_tile" event=e %}
# ---------------------------------------------------------------------------

@register.simple_tag(takes_context=True)
def component(context, name, part=None, data=None, **objects):
    """Render a console component from its data function's JSON.

    Pages pass objects their view already loaded for this person (event=…, location=…, form=…) or ids; a composite
    passes each part's `data`. `part` renders one template partial (where a page's layout splits a component).
    Inside the Ask Attnd chat (surface 'chat') nested components inherit the surface and the id prefix.
    A request with the header X-Attnd-Component naming this component (the chat submitting one of its forms) also
    gets the chat rendering stored on the request; console.middleware returns it instead of the page.
    """
    from console import components as C
    request = context.get('request')
    surface = context.get('surface') or 'page'
    uid = context.get('uid') or ''
    if data is None:
        user = getattr(request, 'user', None)
        cacheable = 'form' not in objects and not any(isinstance(v, (dict, list)) for v in objects.values())
        key = (name, tuple(sorted((k, getattr(v, 'pk', v)) for k, v in objects.items()))) if cacheable else None
        cache = getattr(request, '_components', None) if request is not None else None
        if cacheable and cache is not None and key in cache:
            data = cache[key]
        else:
            comp = C.get(name)
            ids = _ids_of(comp, objects)
            data = C.load(user, name, ids, {k: v for k, v in objects.items() if k not in ids})  # (+ audience check)
            if cacheable and request is not None:
                if cache is None:
                    cache = request._components = {}
                cache[key] = data
    if (request is not None and surface == 'page' and part in (None, 'form')
            and request.headers.get('X-Attnd-Component') == name and request.method == 'POST'):
        fragment_uid = C.new_uid()
        request._component_fragment = {'component': name, 'ids': data.get('ids', {}), 'uid': fragment_uid,
                                       'html': C.render(name, data, request, surface='chat', uid=fragment_uid)}
    return mark_safe(C.render(name, data, request, surface=surface, part=part, uid=uid))


def _ids_of(comp, objects):
    """The component's ids from the objects or ids a page passed."""
    ids = {}
    for key in comp.all_ids:
        if objects.get(key):
            ids[key] = int(objects[key])
            continue
        obj = objects.get(key[:-3])
        if obj is not None and getattr(obj, 'pk', None):
            ids[key] = obj.pk
    if 'event_id' in comp.all_ids and 'event_id' not in ids:
        for obj in objects.values():
            event_id = getattr(obj, 'event_id', None) or getattr(getattr(obj, 'instance', None), 'event_id', None)
            if event_id:
                ids['event_id'] = event_id
                break
    form = objects.get('form')
    if form is not None and getattr(form, 'instance', None) is not None and form.instance.pk:
        for key in comp.optional_ids:
            ids.setdefault(key, form.instance.pk)
    return ids


@register.filter
def html_attrs(attrs, uid=''):
    """A widget's attributes as HTML (Django's attrs.html); `uid` prefixes ids that other attributes point to."""
    parts = []
    for key, value in (attrs or {}).items():
        if value is False or value is None:
            continue
        if uid and key in ('aria-describedby', 'list'):
            value = ' '.join(uid + part for part in str(value).split())
        parts.append(format_html(' {}', key) if value is True else format_html(' {}="{}"', key, value))
    return mark_safe(''.join(parts))


@register.simple_tag
def status_badges(event):
    """An event's status badges (console.components.catalog.status_badges), for templates outside components."""
    from console.components.catalog import status_badges as badges
    return badges(event)


@register.filter
def tag_id(extra_tags, prefix):
    """The number in a message tag like "undo-12" ({{ message.extra_tags|tag_id:'undo' }}), or ''."""
    for tag in (extra_tags or '').split():
        if tag.startswith(prefix + '-') and tag[len(prefix) + 1:].isdigit():
            return int(tag[len(prefix) + 1:])
    return ''


@register.filter
def history_message(entry):
    """A LogEntry's message for the admin's History page (console/history.py)."""
    from console.history import admin_message
    return admin_message(entry)


@register.simple_tag(takes_context=True)
def chat_default(context):
    """Is "Chat as my default view" on for the signed-in person (the top bar's switch)?"""
    from console.models import ConsolePreference
    request = context.get('request')
    return ConsolePreference.chat_first(getattr(request, 'user', None))


@register.filter
def named(actions, names):
    """A component's actions picked by name ("save_snapshot" or "apply,discard"), never by position."""
    wanted = {n.strip() for n in (names or '').split(',') if n.strip()}
    return [a for a in actions or [] if a.get('name') in wanted]
