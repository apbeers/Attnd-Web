"""The organizer's preview phone (API-026, batch 6 §3).

The console's "Preview on my phone" QR is the join link plus a short signed token for that person and event
(~7 days). An app that joins with it becomes a preview device: not an attendee (no free spot, not counted), no random
wait on content pushes, and while it is on screen it may hold ``GET events/<id>/changes/?since=<version>``, answered
the moment the content version moves or after ~25 s.

Server safety (owner, Nov 21, 2026: attendees never hold connections): waiting requests give their database
connection back while they sleep, at most ``longpoll.MAX_WAITING`` wait per web process (shared with Ask Attnd's
long-poll, so most of gunicorn's threads stay free), at most ``MAX_DEVICES`` preview phones per event, and anything
over a cap is answered at once with "poll again in 15 s".
"""
import threading
import time
from datetime import timedelta

from django.core import signing
from django.utils import timezone

SALT = 'attnd.preview.v1'
TOKEN_DAYS = 7
MAX_DEVICES = 3  # active preview phones per event (batch 7: a fourth joins as an ordinary attendee)
WAIT_SECONDS = 25  # under Heroku's 30 s router timeout
RETRY_SECONDS = 15
CHECK_EVERY = 1.0


def make_token(user, event):
    return signing.dumps({'u': user.pk, 'e': event.pk}, salt=SALT, compress=True)


def read_token(token, event):
    """The user a valid token for this event was made for (still on its team), else None."""
    if not isinstance(token, str) or not token or len(token) > 400:
        return None
    try:
        data = signing.loads(token, salt=SALT, max_age=timedelta(days=TOKEN_DAYS))
    except signing.BadSignature:
        return None
    if not isinstance(data, dict) or data.get('e') != event.pk:
        return None
    from django.contrib.auth import get_user_model
    user = get_user_model().objects.filter(pk=data.get('u'), is_active=True).first()
    return user if user is not None and event.can_edit(user) else None


def link(event, user):
    """The join link with the preview token (what the "Preview on my phone" QR encodes)."""
    from urllib.parse import quote

    from attnd.links import join_url
    return f'{join_url(event)}?preview={quote(make_token(user, event), safe="")}'


def open_links(event, user):
    """ "Open as preview" on the organizer's phone (they can't scan their own screen): like attnd.links.open_in_app_links
    with the token in the query."""
    from urllib.parse import quote, urlsplit

    from django.conf import settings

    from attnd.links import ios_open_url, join_url
    token = quote(make_token(user, event), safe='')
    code = quote(event.event_code, safe='')
    fallback = f'{join_url(event)}?preview={token}'
    host = urlsplit(fallback).netloc
    package = getattr(settings, 'ANDROID_PACKAGE_NAME', '') or 'com.arcu.attnd'
    return {'ios': ios_open_url(f'/join/{code}/?preview={token}'),
            'android': (f'intent://{host}/join/{code}/?preview={token}#Intent;scheme=https;package={package};'
                        f'S.browser_fallback_url={quote(fallback, safe="")};end'),
            'fallback': fallback}


def has_room(event, device_id, now=None):
    """May this device become (or stay) a live preview phone? At most MAX_DEVICES per event; one that already is keeps
    its place. Over the cap the phone joins as an ordinary attendee (batch 7, the join answer says preview: false)."""
    from events.models import Device
    others = Device.objects.previews(now or timezone.now()).filter(event=event).exclude(pk=str(device_id)).count()
    return others < MAX_DEVICES


def mark(device, event, user, now=None):
    """Make a joined device a preview phone of this event for TOKEN_DAYS (the caller checked has_room)."""
    from events.models import Device
    now = now or timezone.now()
    from events.models import EventJoin
    Device.objects.filter(pk=device.pk).update(preview_until=now + timedelta(days=TOKEN_DAYS), preview_by=user)
    EventJoin.objects.filter(event=event, device_id=device.pk).delete()  # an attendee before: its free spot back


def remove(event, device_id):
    """The console's Remove: the phone leaves the event and stops being a preview phone."""
    from events.models import Device
    return Device.objects.filter(pk=device_id, event=event).exclude(preview_by__isnull=True, preview_until__isnull=True) \
        .update(event=None, preview_until=None, live_activity_token='')


# ---------------------------------------------------------------------------
# The changes long-poll
# ---------------------------------------------------------------------------

_per_event = {}
_lock = threading.Lock()


def _enter(event_id):
    with _lock:
        if _per_event.get(event_id, 0) >= MAX_DEVICES:
            return False
        _per_event[event_id] = _per_event.get(event_id, 0) + 1
        return True


def _leave(event_id):
    with _lock:
        _per_event[event_id] = max(0, _per_event.get(event_id, 1) - 1)
        if not _per_event[event_id]:
            _per_event.pop(event_id, None)


def wait_for_change(event_id, since, seconds=WAIT_SECONDS):
    """(content_version, changed, waited): returns as soon as the version is past `since`, else after `seconds`.
    Over a cap it doesn't wait (waited False → the caller says "poll again in 15 s")."""
    from django.db import connection

    from events import longpoll
    from events.models import Event

    def version():
        return Event.objects.filter(pk=event_id).values_list('content_version', flat=True).first()

    current = version()
    if current is None or current > since or connection.in_atomic_block:
        return current, current is not None and current > since, True
    if not _enter(event_id):
        return current, False, False
    try:
        if not longpoll.acquire():
            return current, False, False
        try:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                connection.close()  # back to the pool while sleeping
                time.sleep(CHECK_EVERY)
                current = version()
                if current is None or current > since:
                    break
            return current, current is not None and current > since, True
        finally:
            longpoll.release()
    finally:
        _leave(event_id)


# ---------------------------------------------------------------------------
# The console's live line ("Updated on 31 of 37 phones") and the preview phones
# ---------------------------------------------------------------------------

ACTIVE_WINDOW = timedelta(hours=24)  # attendee phones counted: seen in the last day, with a version reported


def seen_ago(delta):
    """"10 s ago", "3 min ago", "2 h ago", "4 days ago" (the preview phones' status line)."""
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 60:
        return f'{max(seconds, 1)} s ago'
    if seconds < 3600:
        return f'{seconds // 60} min ago'
    if seconds < 86400:
        return f'{seconds // 3600} h ago'
    days = seconds // 86400
    return f'{days} day{"s" if days != 1 else ""} ago'


def phones(event, now=None):
    """{'updated', 'total', 'text', 'previews': [...]} from the version each device last fetched."""
    from django.db.models import Count, Q

    from events.models import Device
    now = now or timezone.now()
    version = event.content_version
    attendee = Q(preview_by__isnull=True, preview_until__isnull=True)
    counts = (Device.objects.real().filter(event=event)  # one query: the attendee phones of the last day + previews
              # phones that reported a version (a device from before API-026 counts once it fetches again)
              .aggregate(total=Count('pk', filter=attendee & Q(last_seen__gte=now - ACTIVE_WINDOW,
                                                              last_version__isnull=False)),
                         updated=Count('pk', filter=attendee & Q(last_seen__gte=now - ACTIVE_WINDOW,
                                                                 last_version__gte=version)),
                         previews=Count('pk', filter=~attendee)))
    total, updated = counts['total'], counts['updated']
    if not total:
        text = 'No attendee phones have checked in yet.'
    elif updated == total:
        text = f'Up to date on {"the" if total == 1 else "all"} {total} phone{"" if total == 1 else "s"}.'
    else:
        text = f'Updated on {updated} of {total} phone{"" if total == 1 else "s"}.'
    previews = []
    rows = [] if not counts['previews'] else (Device.objects.filter(event=event).exclude(preview_by__isnull=True, preview_until__isnull=True)
            .select_related('preview_by').order_by('-joined_at')[:10])
    for d in rows:
        live = bool(d.preview_until and d.preview_until > now)
        current = d.last_version is not None and d.last_version >= version
        seen = d.last_seen
        state = ('Live' if current else 'Catching up') + (f' · seen {seen_ago(now - seen)}' if seen else ' · not seen yet')
        previews.append({'id': d.pk, 'label': f'{"iPhone" if d.platform == "ios" else "Android" if d.platform == "android" else "Phone"}'
                                             f'{" · " + d.preview_by.email if d.preview_by_id else ""}',
                         'state': state if live else 'preview ended', 'live': live, 'current': current})
    return {'updated': updated, 'total': total, 'text': text, 'version': version, 'previews': previews}
