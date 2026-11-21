"""Limit changes in Django's History (LogEntry), like every other change in the console."""
import json

from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.contenttypes.models import ContentType


def log(user, obj, before=None, after=None, flag=CHANGE):
    """One LogEntry: the changed fields' labels, with old → new in the message's own words."""
    changed = [k for k in (after or {}) if (before or {}).get(k) != (after or {}).get(k)]
    message = []
    if flag == ADDITION:
        message = [{'added': {}}]
    elif flag == CHANGE and changed:
        message = [{'changed': {'fields': [obj._meta.get_field(k).verbose_name for k in changed
                                           if _has_field(obj, k)]}}]
    try:
        LogEntry.objects.create(
            user_id=user.pk, content_type_id=ContentType.objects.get_for_model(obj).pk,
            object_id=str(obj.pk), object_repr=str(obj)[:200], action_flag=flag,
            change_message=json.dumps(message) if flag != DELETION else '')
    except Exception:  # never fails the save
        pass
    return {k: [str((before or {}).get(k)), str((after or {}).get(k))] for k in changed}


def _has_field(obj, name):
    try:
        obj._meta.get_field(name)
        return True
    except Exception:
        return False


def snapshot(obj, fields):
    return {f: getattr(obj, f) for f in fields}
