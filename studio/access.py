"""Who may use Attnd Studio and what they may do on an event (D1, owner Nov 21, 2026): every organizer designs,
edits and previews websites for their events; publishing one needs the event's Studio plan (or the owner's
override in the Platform admin)."""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from events.models import Event


def is_staff(user):
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))


def has_studio_access(user):
    """Every signed-in organizer (D1): building and previewing a website is open to all; AI spend counts toward their
    monthly AI credit and Studio's per-event daily cap. Publishing is gated per event (`can_publish`)."""
    return bool(user and user.is_authenticated)


def cached_access(request):
    """has_studio_access, memoised on the request (templates ask more than once)."""
    if not hasattr(request, '_studio_access'):
        request._studio_access = has_studio_access(getattr(request, 'user', None))
    return request._studio_access


def can_switch_plan(event, user):
    """Plans change through an approved upgrade (the Platform admin); staff can still switch by hand."""
    return is_staff(user)


def can_publish(event, user):
    """The owner and event admins, on an event whose plan lets the site go live (Studio, or the owner's override)."""
    from platform_admin.plans import can_go_live
    return event.can_manage_team(user) and can_go_live(event)


def studio_required(view):
    """login + Studio access, else 404 (no Studio entry points for anyone else)."""
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not cached_access(request):
            raise Http404
        return view(request, *args, **kwargs)
    return wrapper


def studio_event_view(view):
    """Studio access + a team member of the event (any role); runs in the event's time zone."""
    @studio_required
    @wraps(view)
    def wrapper(request, event_id, *args, **kwargs):
        event = get_object_or_404(Event, pk=event_id)
        if not event.can_edit(request.user):
            raise Http404
        from console.views_events import _tz
        with timezone.override(_tz(event)):
            return view(request, event, *args, **kwargs)
    return wrapper


def staff_required(view):
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not is_staff(request.user):
            raise Http404
        return view(request, *args, **kwargs)
    return wrapper
