"""Who may use Attnd Studio (invite-only beta) and what they may do on an event."""

from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from events.models import Event


def is_staff(user):
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))


def has_studio_access(user):
    """Staff, an email listed in STUDIO_BETA_EMAILS, or an invited email (StudioAccess); case-insensitive."""
    if not user or not user.is_authenticated:
        return False
    if is_staff(user):
        return True
    email = (user.email or '').strip().lower()
    if not email:
        return False
    if email in settings.STUDIO_BETA_EMAILS:
        return True
    from studio.models import StudioAccess
    return StudioAccess.objects.filter(email__iexact=email).exists()


def cached_access(request):
    """has_studio_access, memoised on the request (templates ask more than once)."""
    if not hasattr(request, '_studio_access'):
        request._studio_access = has_studio_access(getattr(request, 'user', None))
    return request._studio_access


def can_switch_plan(event, user):
    return is_staff(user) or (has_studio_access(user) and event.can_manage_team(user))


def can_publish(event, user):
    return event.can_manage_team(user) and has_studio_access(user)


def studio_required(view):
    """login + Studio access, else 404 (no Studio entry points for people outside the beta)."""
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
