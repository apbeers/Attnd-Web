"""Undo for every change: per change, not a whole-event rollback, so other people's later edits are never reverted.

While a console POST runs (`UndoMiddleware`; Ask Attnd's internal requests and the chat's component forms go
through it too), a `Recorder` in a context variable collects what the request does to the tracked models through
the `pre_save` / `post_save` / `pre_delete` signals: the row as it was before (serialized), whether it was created,
updated or deleted. At the end the changed fields are worked out against the database and saved as one `UndoEntry`
(24 hours, single use, only for the person who made it). The success message gets an Undo button (`extra_tags`
`undo-<id>`), the response an `X-Attnd-Undo: <id>` header.

`undo(entry, user)` checks every operation first and refuses cleanly when it can't:
- an update that went out to attendees (a sent notification) can't be unsent;
- a field someone (or the person) changed again later is never overwritten: it says who;
- something it refers to that is gone, or a deleted row that is back.
Then it restores everything in one transaction with the normal `save()` / `delete()`, so `content_version` bumps
and pushes run as usual, and records that as a new entry (`reverses`), which is the Redo.

Not covered: `QuerySet.update()` writes (e.g. drag-to-reorder) and deleting a whole event.
"""

import contextvars
import json
import logging
from datetime import timedelta

from django.apps import apps
from django.contrib import messages
from django.core import serializers
from django.db import models, transaction
from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver
from django.utils import timezone

logger = logging.getLogger(__name__)

LIFETIME = timedelta(hours=24)
MAX_OPERATIONS = 500
TRACKED = ('events.event', 'events.location', 'events.scheduleditem', 'events.notification', 'events.eventmember',
           'events.eventinvite')
# Fields the system keeps up to date by itself: never restored and never a conflict.
BOOKKEEPING = {'plan_suggestions', 'content_version', 'content_updated_at', 'last_refreshed_at', 'sources_checked_at', 'updated_at',
               'last_seen'}

_current = contextvars.ContextVar('attnd_undo_recorder', default=None)


def _label(obj):
    return obj._meta.label_lower


def serialize(obj):
    return json.loads(serializers.serialize('json', [obj]))[0]


def deserialize(row):
    return next(serializers.deserialize('json', json.dumps([row]))).object


class Recorder:
    def __init__(self, user):
        self.user = user
        self.ops = {}  # (model, pk) -> {'op', 'before', 'related'}
        self.unsupported = False
        self.deleted_events = []  # serialized events deleted in this request (History only: not undoable)

    def key(self, obj):
        return (_label(obj), str(obj.pk))


class recording:
    """Record the changes made inside the block (a context manager; returns the Recorder)."""

    def __init__(self, user):
        self.recorder = Recorder(user)

    def __enter__(self):
        self.token = _current.set(self.recorder)
        return self.recorder

    def __exit__(self, *exc):
        _current.reset(self.token)
        return False


def _active(sender, raw=False):
    rec = _current.get()
    if rec is None or raw:
        return None
    return rec if sender._meta.label_lower in TRACKED else None


@receiver(pre_save)
def _pre_save(sender, instance, raw=False, **kwargs):
    rec = _active(sender, raw)
    if rec is None or instance.pk is None:
        return
    key = rec.key(instance)
    if key in rec.ops:
        return
    row = sender._base_manager.filter(pk=instance.pk).first()
    if row is not None:
        rec.ops[key] = {'op': 'update', 'before': serialize(row)}


@receiver(post_save)
def _post_save(sender, instance, created, raw=False, **kwargs):
    rec = _active(sender, raw)
    if rec is None:
        return
    key = rec.key(instance)
    if created and key not in rec.ops:
        rec.ops[key] = {'op': 'create', 'before': None}


@receiver(pre_delete)
def _pre_delete(sender, instance, **kwargs):
    rec = _current.get()
    if rec is None:
        return
    if _label(instance) == 'events.event':
        rec.unsupported = True  # deleting a whole event isn't undoable (History still records it)
        rec.deleted_events.append(serialize(instance))
        return
    if _label(instance) not in TRACKED:
        return
    key = rec.key(instance)
    earlier = rec.ops.pop(key, None)
    if earlier and earlier['op'] == 'create':
        return  # created and deleted in the same request: nothing to undo
    before = earlier['before'] if earlier else serialize(instance)
    related = {}
    if _label(instance) == 'events.location':  # items at this location lose it (SET_NULL, no signals)
        from events.models import ScheduledItem
        related['items'] = list(ScheduledItem.objects.filter(location_id=instance.pk).values_list('pk', flat=True))
    rec.ops[key] = {'op': 'delete', 'before': before, 'related': related}


def same(a, b):
    """Equal values, treating numbers written with a different scale ("-97.3320272" / "-97.33202720000") as equal."""
    if a == b:
        return True
    if isinstance(a, (str, int, float)) and isinstance(b, (str, int, float)) and not isinstance(a, bool):
        from decimal import Decimal, InvalidOperation
        try:
            return Decimal(str(a)) == Decimal(str(b))
        except (InvalidOperation, ValueError):
            return False
    return False


def _numeric(value):
    from decimal import Decimal, InvalidOperation
    try:
        Decimal(str(value))
        return True
    except (InvalidOperation, ValueError):
        return False


def _event_id(model, pk, fields):
    if model == 'events.event':
        return int(pk)
    return (fields or {}).get('event')


def finalize(rec, label, reverses=None, via=''):
    """The UndoEntry for what the recorder saw (None when nothing undoable changed). Everything it saw also goes into
    the history of everything (console/history.py: a LogEntry per row, with the same operations), undoable or not."""
    from console import history
    from console.models import HistoryRecord
    if rec is None or not getattr(rec.user, 'is_authenticated', False) or not (rec.ops or rec.deleted_events):
        return None
    operations = _operations(rec)
    via = HistoryRecord.VIA_UNDO if reverses is not None else via
    entry = None
    if not rec.unsupported and operations and len(operations) <= MAX_OPERATIONS:
        entry = _undo_entry(rec, operations, label, reverses)
    deleted = [{'model': 'events.event', 'pk': row['pk'], 'op': 'delete', 'before': row, 'event': row['pk']}
               for row in rec.deleted_events]
    history.record(rec.user, operations + deleted, entry, via)
    return entry


def _operations(rec):
    operations = []
    for (model, pk), op in rec.ops.items():
        cls = apps.get_model(model)
        out = {'model': model, 'pk': pk, 'op': op['op'], 'before': op['before']}
        if op['op'] in ('create', 'update'):
            obj = cls._base_manager.filter(pk=pk).first()
            if obj is None:
                continue
            after = serialize(obj)['fields']
            if op['op'] == 'update':
                before = op['before']['fields']
                changed = [f for f, v in after.items() if f not in BOOKKEEPING and not same(before.get(f), v)]
                if not changed:
                    continue
            else:
                changed = [f for f in after if f not in BOOKKEEPING]
            out['fields'] = changed
            out['after'] = {f: after[f] for f in changed}
            out['event'] = _event_id(model, pk, after)
        else:
            out['related'] = op.get('related') or {}
            out['event'] = _event_id(model, pk, op['before']['fields'])
        operations.append(out)
    return operations


def _undo_entry(rec, operations, label, reverses):
    from console.models import UndoEntry
    event_id = next((o['event'] for o in operations if o.get('event')), None)
    from events.models import Event
    if event_id and not Event.objects.filter(pk=event_id).exists():
        event_id = None
    return UndoEntry.objects.create(user=rec.user, event_id=event_id, label=(label or describe(operations))[:300],
                                    operations=operations, expires_at=timezone.now() + LIFETIME, reverses=reverses)


def _name(model, fields):
    cls = apps.get_model(model)
    name = (fields or {}).get('name') or (fields or {}).get('title') or (fields or {}).get('email')
    kind = cls._meta.verbose_name
    return f'{kind} “{name}”' if name else kind


def _cap(text):
    return text[:1].upper() + text[1:]


def describe(operations):
    op = operations[0]
    fields = op.get('after') or (op.get('before') or {}).get('fields')
    verb = {'create': 'Added', 'update': 'Changed', 'delete': 'Deleted'}[op['op']]
    more = f' (+{len(operations) - 1} more)' if len(operations) > 1 else ''
    return f'{verb} {_name(op["model"], fields)}{more}'


# ---------------------------------------------------------------------------
# Undoing
# ---------------------------------------------------------------------------

class CannotUndo(Exception):
    pass


def available(entry, user):
    return (entry is not None and entry.user_id == user.pk and entry.used_at is None
            and entry.expires_at > timezone.now())


def _newer_by(entry, model, pk, user):
    from console.models import UndoEntry
    newer = (UndoEntry.objects.filter(created_at__gt=entry.created_at,
                                      operations__contains=[{'model': model, 'pk': pk}])
             .exclude(pk=entry.pk).exclude(reverses=entry).select_related('user').order_by('-created_at').first())
    if newer is None:
        return 'someone'
    return 'you' if newer.user_id == user.pk else (newer.user.email or 'someone else')


def _check(entry, op, user):
    """Raise CannotUndo with a plain message when this operation can't be reversed now."""
    cls = apps.get_model(op['model'])
    current = cls._base_manager.filter(pk=op['pk']).first()
    before = (op.get('before') or {}).get('fields') or {}
    name = _name(op['model'], op.get('after') or before)
    if op['model'] == 'events.notification':
        sent = (current is not None and current.sent) or (op.get('after') or {}).get('sent') is True
        if sent and op['op'] != 'delete':
            raise CannotUndo('This update was already sent — it can’t be unsent.')
    if op['op'] == 'delete':
        if current is not None:
            raise CannotUndo(f'{_cap(name)} is already back.')
        restore = before
    else:
        if current is None:
            if op['op'] == 'create':
                return None  # already gone
            raise CannotUndo(f'{_cap(name)} was deleted since, so this can’t be undone.')
        now = serialize(current)['fields']
        moved = [f for f in op['fields'] if not same(now.get(f), op['after'].get(f))]
        if moved:
            who = _newer_by(entry, op['model'], op['pk'], user)
            what = ', '.join(cls._meta.get_field(f).verbose_name for f in moved[:3])
            raise CannotUndo(f'{_cap(name)} was changed again since ({what}, by {who}), so this can’t be '
                             'undone without overwriting that newer change.')
        restore = {f: before.get(f) for f in op['fields']} if op['op'] == 'update' else {}
    for fname, value in restore.items():  # whatever it points to must still exist
        field = cls._meta.get_field(fname)
        if isinstance(field, models.ForeignKey) and value is not None and field.related_model is not None:
            if not field.related_model._base_manager.filter(pk=value).exists():
                raise CannotUndo(f'Something {name} refers to was deleted since, so this can’t be undone.')
    return current


def undo(entry, user):
    """Reverse one entry. Returns (message, redo entry or None). Raises CannotUndo."""
    from console.models import UndoEntry
    from events.models import Event, ScheduledItem
    with transaction.atomic():
        entry = UndoEntry.objects.select_for_update().get(pk=entry.pk)
        if not available(entry, user):
            raise CannotUndo('This can no longer be undone (already undone, or more than a day old).')
        if entry.event_id:
            event = Event.objects.filter(pk=entry.event_id).first()
            if event is None or not event.can_edit(user):
                raise CannotUndo('You no longer have access to this event.')
        ops = list(reversed(entry.operations))
        currents = [_check(entry, op, user) for op in ops]
        said = []
        with recording(user) as rec:
            for op, current in zip(ops, currents):
                cls = apps.get_model(op['model'])
                before = op.get('before')
                name = _name(op['model'], (before or {}).get('fields') or op.get('after'))
                if op['op'] == 'create':
                    if current is not None:
                        current.delete()
                        said.append(f'{name} is removed again')
                elif op['op'] == 'update':
                    old = deserialize(before)
                    names = []
                    for fname in op['fields']:
                        field = cls._meta.get_field(fname)
                        setattr(current, field.attname, getattr(old, field.attname))
                        names.append(fname)
                    current.save(update_fields=names)
                    texts = [f for f in names if isinstance(before['fields'].get(f), str) and not same(before['fields'][f], 0)
                             and not _numeric(before['fields'][f])]
                    texts.sort(key=lambda f: f not in ('name', 'title'))
                    shown = [f'{cls._meta.get_field(f).verbose_name} “{before["fields"][f][:40]}”' for f in texts][:2]
                    said.append(f'{name}: ' + (', '.join(shown) if shown else 'restored') + ' again')
                else:
                    obj = deserialize(before)
                    obj.save(force_insert=True)
                    items = (op.get('related') or {}).get('items') or []
                    if items:
                        ScheduledItem.objects.filter(pk__in=items, location__isnull=True).update(location=obj)
                    said.append(f'{name} is back')
        entry.used_at = timezone.now()
        entry.save(update_fields=['used_at'])
        redo = finalize(rec, f'Undo: {entry.label}', reverses=entry)
    message = 'Undone: ' + '; '.join(s[0].upper() + s[1:] for s in said[:3]) + ('.' if said else '')
    if len(said) > 3:
        message += f' (+{len(said) - 3} more)'
    return message, redo


def latest_for(user):
    from console.models import UndoEntry
    return (UndoEntry.objects.filter(user=user, used_at__isnull=True, expires_at__gt=timezone.now())
            .order_by('-created_at', '-pk').first())


def purge():
    from console.models import UndoEntry
    return UndoEntry.objects.filter(expires_at__lt=timezone.now() - timedelta(days=1)).delete()[0]


# ---------------------------------------------------------------------------
# The middleware: every console POST
# ---------------------------------------------------------------------------

SKIP = ('/console/undo/', '/console/agent/')


def tag_message(request, entry, response):
    """Put the Undo button on the request's success message (or add one when the view said nothing)."""
    storage = getattr(request, '_messages', None)
    queued = list(getattr(storage, '_queued_messages', []) or [])
    target = next((m for m in reversed(queued) if m.level == messages.SUCCESS), None)
    target = target or next((m for m in reversed(queued) if m.level == messages.INFO), None)
    if target is None:
        if storage is None or not (300 <= response.status_code < 400):
            return
        messages.success(request, entry.label + '.', extra_tags=f'undo-{entry.pk}')
        return
    target.extra_tags = f'{target.extra_tags or ""} undo-{entry.pk}'.strip()
    if not entry.label or entry.label == describe(entry.operations):
        entry.label = str(target.message)[:300]
        entry.save(update_fields=['label'])


def _label_from_messages(request):
    storage = getattr(request, '_messages', None)
    queued = list(getattr(storage, '_queued_messages', []) or [])
    for m in reversed(queued):
        if m.level == messages.SUCCESS:
            return str(m.message)
    return ''


def _via(request):
    from agent.context import current
    from console.models import HistoryRecord
    if current() is not None:
        return HistoryRecord.VIA_AGENT  # Ask Attnd's internal request (also a confirmed chat button)
    if request.headers.get('X-Attnd-Component'):
        return HistoryRecord.VIA_CHAT  # a form or button in a chat card, posted by the browser
    return HistoryRecord.VIA_CONSOLE


class UndoMiddleware:
    """Records each console POST's changes (console/undo.py) and offers Undo for them."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (request.method != 'POST' or not request.path.startswith('/console/') or request.path.startswith(SKIP)
                or not request.user.is_authenticated):
            return self.get_response(request)
        with recording(request.user) as rec:
            response = self.get_response(request)
        if not (rec.ops or rec.deleted_events) or response.status_code >= 400:
            return response
        try:
            entry = finalize(rec, _label_from_messages(request), via=_via(request))
        except Exception:  # never let bookkeeping break a save that worked
            logger.exception('Recording undo failed')
            return response
        if entry is not None:
            response['X-Attnd-Undo'] = str(entry.pk)
            tag_message(request, entry, response)
        return response
