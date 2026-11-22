"""One permission policy for the whole site (owner, Nov 22, 2026: deny by default, Django's permission model).

Everything that decides "may this person do this" asks `user.has_perm(...)`:

* **Platform roles** are Django Groups with named permissions (`ROLES` below; synced into the database after every
  migrate by `sync_roles`). `is_staff` accounts get the Staff role without being in the group; superusers get every
  permission (Django's rule). Putting someone in the "Staff" group in the admin gives them the same.
* **Event roles** (owner / admin / contributor, `Event.role_for`) are object permissions answered by
  `PermissionMixin` (on the site's one auth backend): `user.has_perm('events.change_location', event)` or with the location itself.
* **Views declare their permission** (`declare`, `requires`, `signed_in`, `console.views_events.event_view(perm)`).
  `DeclaredPermissionMiddleware` refuses every view that doesn't (404 when the URL names an object, 403 otherwise) and
  `attnd/tests_permissions.py` walks every URL pattern, component and Ask Attnd action so CI fails on a new
  undeclared one. Public pages stay `attnd.public.public()`.

See CLAUDE.md → "Declaring permissions for new views" and docs/security_review_permissions.md.
"""

import logging
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseForbidden, HttpResponseNotFound

logger = logging.getLogger('attnd.permissions')

# --- what a view can declare -------------------------------------------------------------------------------------

PUBLIC = 'public'  # anyone (attnd.public.public marks these; the middleware never asks)
SIGNED_IN = 'signed_in'  # any signed-in person; the view only ever reads or writes that person's own rows

ATTR = 'attnd_permission'  # the attribute a declaration sets on the view function
SCOPE_ATTR = 'attnd_permission_scope'  # 'event' when the permission is checked against the URL's event

# --- event roles (object permissions) ----------------------------------------------------------------------------

CONTRIBUTOR = frozenset({
    'events.view_event', 'events.change_event',
    'events.add_location', 'events.change_location', 'events.delete_location', 'events.view_location',
    'events.add_scheduleditem', 'events.change_scheduleditem', 'events.delete_scheduleditem',
    'events.view_scheduleditem',
    'events.add_notification', 'events.change_notification', 'events.delete_notification',
    'events.view_notification',
    'studio.view_site', 'studio.change_site',  # design, edit and preview the website (publishing: publish_site)
})
EVENT_ADMIN = CONTRIBUTOR | {
    'events.manage_event',  # status, listing, sources, team, upgrade / limit requests, discard, publish changes…
    'studio.publish_site',  # still needs the event's plan (studio.access.can_publish)
}
OWNER = EVENT_ADMIN | {'events.delete_event', 'events.transfer_event'}
EVENT_ROLES = {'contributor': CONTRIBUTOR, 'admin': EVENT_ADMIN, 'owner': OWNER}
EVENT_PERMS = OWNER  # every permission that is answered per event

# --- platform roles (Django Groups) ------------------------------------------------------------------------------

STAFF_GROUP = 'Staff'
PLATFORM_ADMIN_GROUP = 'Platform admins'
STAFF = frozenset({
    'prospects.view_prospect', 'prospects.change_prospect',  # the sales pipeline, claims, test events
    'diagnostics.view_diagnosticsession', 'diagnostics.delete_diagnosticsession',  # app logs, reports, symbols
    'credit.view_aispend', 'credit.change_ailimits',  # AI spend, limits, per-person overrides
    'studio.view_sitereport', 'studio.change_sitereport',  # Studio abuse reports, take down / restore
    'events.mark_event_paid',  # going live (no checkout yet)
    'events.view_showcase',  # the "Showcase & demo" section of Your events
})
PLATFORM_ADMIN = STAFF | {
    'platform_admin.change_platformsettings',  # the Platform admin: prices, customers, upgrades, overrides
    'events.assign_event_owner',  # hand an unassigned event to someone
    'events.view_all_usage',  # usage across every event
}
ROLES = {STAFF_GROUP: STAFF, PLATFORM_ADMIN_GROUP: PLATFORM_ADMIN}
PLATFORM_PERMS = PLATFORM_ADMIN


def event_of(obj):
    """The event an object belongs to (an Event, or anything with .event / .site.event), else None."""
    from events.models import Event
    if isinstance(obj, Event):
        return obj
    event = getattr(obj, 'event', None)
    if isinstance(event, Event):
        return event
    site = getattr(obj, 'site', None)
    if site is not None and isinstance(getattr(site, 'event', None), Event):
        return site.event
    return None


def role_grants(user_obj, perm, obj=None):
    """The event roles (object permissions) and the Staff role of `is_staff` accounts."""
    if not user_obj or not user_obj.is_active or not user_obj.is_authenticated:
        return False
    if obj is None:
        return perm in STAFF and bool(user_obj.is_staff)
    event = event_of(obj)
    if event is None or perm not in EVENT_PERMS:
        return False
    return perm in EVENT_ROLES.get(event.role_for(user_obj), ())


class PermissionMixin:
    """For the site's authentication backend (console.auth_backends.EmailOrUsernameBackend, a ModelBackend): role
    grants first, then the group and per-user permissions in the database. One backend, so login() needs no
    `backend=` argument."""

    def has_perm(self, user_obj, perm, obj=None):
        return role_grants(user_obj, perm, obj) or super().has_perm(user_obj, perm, obj)


def sync_roles(**kwargs):
    """post_migrate: make sure every permission exists, then give each role group exactly its permissions."""
    from django.apps import apps
    from django.contrib.auth.management import create_permissions
    from django.contrib.auth.models import Group, Permission
    config = kwargs.get('app_config')
    if config is not None and config.label != 'console':
        return  # once per migrate (post_migrate is sent for every app)
    for config in apps.get_app_configs():
        create_permissions(config, verbosity=0)
    for name, perms in ROLES.items():
        group, _ = Group.objects.get_or_create(name=name)
        wanted = []
        for perm in sorted(perms):
            app_label, codename = perm.split('.', 1)
            found = Permission.objects.filter(content_type__app_label=app_label, codename=codename).first()
            if found is None:
                logger.warning('permission %s does not exist', perm)
                continue
            wanted.append(found)
        group.permissions.set(wanted)


# --- declarations ------------------------------------------------------------------------------------------------

def declare(permission, scope=None):
    """Mark a view with the permission it needs (the check itself is the view's or its decorator's)."""
    def decorator(view):
        setattr(view, ATTR, permission)
        if scope:
            setattr(view, SCOPE_ATTR, scope)
        return view
    return decorator


def declared(view):
    """The permission a view declared (function views, class views through as_view), or None."""
    for candidate in (view, getattr(view, 'view_class', None), getattr(view, 'cls', None)):
        if candidate is not None and getattr(candidate, ATTR, None):
            return getattr(candidate, ATTR)
    if getattr(view, 'login_required', True) is False:
        return PUBLIC
    return None


def signed_in(view):
    """Any signed-in person; the view keeps to that person's own rows."""
    return declare(SIGNED_IN)(login_required(view))


def requires(permission, deny=404):
    """A platform permission (staff / platform admin areas). Denied → 404 (no entry point), or 403 with deny=403."""
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.has_perm(permission):
                if deny == 403:
                    raise PermissionDenied
                raise Http404
            return view(request, *args, **kwargs)
        return declare(permission)(wrapper)
    return decorator


def is_platform(permission):
    return permission in PLATFORM_PERMS


# --- refusing undeclared views ------------------------------------------------------------------------------------

UNCHECKED_NAMESPACES = {'admin'}  # Django's admin checks its own model permissions


class DeclaredPermissionMiddleware:
    """Refuse any view that didn't declare a permission (deny by default)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = getattr(request, 'resolver_match', None)
        if match is not None and UNCHECKED_NAMESPACES & set(match.namespaces):
            return None
        if declared(view_func) is not None:
            return None
        name = match.view_name if match is not None else getattr(view_func, '__name__', '?')
        logger.error('view %s declares no permission: refused', name)
        if any(k.endswith('_id') or k in ('pk', 'code') for k in view_kwargs):
            return HttpResponseNotFound('Not found')
        return HttpResponseForbidden('Forbidden')
