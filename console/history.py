"""History of everything, the Django way: every create / update / delete made through the console, Ask Attnd's
internal requests and the chat's cards becomes a `django.contrib.admin.models.LogEntry` (ADDITION / CHANGE /
DELETION, `change_message` in Django's JSON format listing the changed fields), exactly what the admin's History
pages show.

It reuses the Undo layer's capture (console/undo.py: the `Recorder` the `UndoMiddleware` fills from the model signals),
so History and Undo always agree: `undo.finalize` calls `record()` with the same operations. A `HistoryRecord` next to
each LogEntry keeps the event it belongs to, how the change was made (console, Ask Attnd, a chat card, an undo), the
values before → after and the UndoEntry that can still reverse it. `timeline()` turns them into the readable rows the
console's History page and the item details show. `EventRevision` snapshots (rollback) are separate and unchanged.
"""

import json

from django.apps import apps
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

FLAGS = {'create': ADDITION, 'update': CHANGE, 'delete': DELETION}
VIA_TEXT = {'ask_attnd': 'Ask Attnd', 'chat': 'Ask Attnd', 'undo': 'Undo'}
MAX_TEXT = 160
# Fields the timeline never shows as "old → new" (long JSON, internal lists): still listed as changed.
OPAQUE = {'context', 'display_config', 'layout_config', 'locked_fields', 'source_urls', 'snapshot', 'operations'}
# Bookkeeping fields never listed as changed (Undo still restores them): "Locked fields" isn't a change people made.
HIDDEN = {'locked_fields'}


def _verbose(cls, name):
    try:
        text = str(cls._meta.get_field(name).verbose_name)
    except Exception:
        text = name.replace('_', ' ')
    return text[:1].upper() + text[1:]


def change_message(op):
    """Django's JSON change message (what `LogEntry.get_change_message` reads; deletions have none, like the admin)."""
    if op['op'] == 'create':
        return json.dumps([{'added': {}}])
    if op['op'] == 'update':
        cls = apps.get_model(op['model'])
        return json.dumps([{'changed': {'fields': [_verbose(cls, f) for f in op.get('fields') or [] if f not in HIDDEN]}}])
    return ''


def _short(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (list, dict)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 1] + '…'


def _values(op):
    if op['op'] != 'update':
        return {}
    before = (op.get('before') or {}).get('fields') or {}
    after = op.get('after') or {}
    return {f: [_short(before.get(f)), _short(after.get(f))] for f in op.get('fields') or [] if f not in OPAQUE}


def _repr(op):
    fields = op.get('after') if op['op'] == 'create' else (op.get('before') or {}).get('fields')
    cls = apps.get_model(op['model'])
    if op['op'] != 'delete':
        obj = cls._base_manager.filter(pk=op['pk']).first()
        if obj is not None:
            return str(obj)[:200]
    fields = fields or {}
    return str(fields.get('name') or fields.get('title') or fields.get('email') or f'{cls._meta.verbose_name} {op["pk"]}')[:200]


def record(user, operations, undo_entry=None, via=''):
    """One LogEntry (+ HistoryRecord) per operation. Never raises into the request that made the change."""
    from console.models import HistoryRecord
    if not getattr(user, 'is_authenticated', False) or not operations:
        return []
    now = timezone.now()
    out = []
    for op in operations:
        cls = apps.get_model(op['model'])
        entry = LogEntry.objects.create(
            action_time=now, user_id=user.pk, content_type=ContentType.objects.get_for_model(cls, for_concrete_model=False),
            object_id=str(op['pk']), object_repr=_repr(op), action_flag=FLAGS[op['op']], change_message=change_message(op))
        HistoryRecord.objects.create(log_entry=entry, event_id=op.get('event'), via=via, values=_values(op),
                                     undo=undo_entry)
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# The readable timeline (console History page, item details, the chat's object_history card)
# ---------------------------------------------------------------------------

MODELS = {'event': 'events.event', 'location': 'events.location', 'item': 'events.scheduleditem',
          'notification': 'events.notification'}


def _fmt(value):
    if value in (None, '', [], {}):
        return '(empty)'
    if value is True:
        return 'yes'
    if value is False:
        return 'no'
    return f'“{value}”'


def entries(event_id, model=None, object_id=None, limit=50):
    qs = (LogEntry.objects.filter(attnd__event_id=event_id).select_related('user', 'content_type', 'attnd',
                                                                            'attnd__undo')
          .order_by('-action_time', '-pk'))
    if model:
        app_label, name = model.split('.')
        qs = qs.filter(content_type__app_label=app_label, content_type__model=name)
    if object_id:
        qs = qs.filter(object_id=str(object_id))
    return list(qs[:limit])


def row(entry, user=None):
    """One timeline row: who, when, what, the field changes old → new, Undo when it can still be undone."""
    from console import undo
    rec = getattr(entry, 'attnd', None)
    cls = entry.content_type.model_class() if entry.content_type_id else None
    kind = str(cls._meta.verbose_name) if cls else 'item'
    verb = {ADDITION: 'Added', CHANGE: 'Changed', DELETION: 'Deleted'}[entry.action_flag]
    changes = []
    if rec and entry.action_flag == CHANGE:
        for field, pair in rec.values.items():
            if not (isinstance(pair, list) and len(pair) == 2):
                continue
            changes.append({'field': _verbose(cls, field) if cls else field, 'old': _fmt(pair[0]), 'new': _fmt(pair[1])})
    if entry.action_flag == CHANGE and not changes:
        message = entry.get_change_message().replace(' and Locked fields', '').replace('Locked fields and ', '')
        if message in ('Changed Locked fields.', 'Changed.'):
            message = ''
    else:
        message = ''
    who = entry.user.email if entry.user_id and entry.user.email else 'Someone'
    if user is not None and entry.user_id == user.pk:
        who = 'You'
    via = VIA_TEXT.get(rec.via, '') if rec else ''
    can_undo = bool(rec and rec.undo_id and user is not None and undo.available(rec.undo, user))
    return {
        'id': entry.pk, 'when': timezone.localtime(entry.action_time).strftime('%b %-d, %-I:%M %p'),
        'iso': entry.action_time.isoformat(), 'who': who, 'via': via, 'flag': {ADDITION: 'added', CHANGE: 'changed',
                                                                                DELETION: 'deleted'}[entry.action_flag],
        'title': f'{verb} {kind} “{entry.object_repr}”', 'kind': kind, 'name': entry.object_repr,
        'message': message, 'changes': changes[:8], 'more': max(0, len(changes) - 8),
        'undo_id': rec.undo_id if can_undo else None,
    }


def timeline(event_id, user=None, model=None, object_id=None, limit=50):
    return [row(e, user) for e in entries(event_id, model, object_id, limit)]


def admin_message(entry):
    """The admin History page's Action column: Django's own message plus how it was made and old → new."""
    text = entry.get_change_message() or {ADDITION: 'Added.', CHANGE: 'Changed.', DELETION: 'Deleted.'}[entry.action_flag]
    rec = getattr(entry, 'attnd', None) if hasattr(entry, 'attnd') else None
    if rec is None:
        return text
    via = VIA_TEXT.get(rec.via, '')
    if via:
        text = f'{text} ({via})'
    cls = entry.content_type.model_class() if entry.content_type_id else None
    details = '; '.join(f'{_verbose(cls, field) if cls else field}: {_fmt(pair[0])} → {_fmt(pair[1])}'
                        for field, pair in list(rec.values.items())[:5] if isinstance(pair, list) and len(pair) == 2)
    return f'{text} {details}'.strip()
