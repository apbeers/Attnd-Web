from django.conf import settings


def studio(request):
    """`studio_access` (Studio: admins only) and `studio_staff` for the console chrome, evaluated only when a template
    asks, and `TICKETS_ENABLED` (Attnd Tickets is parked unless it is on: no ticket links)."""
    from studio.access import cached_access, is_staff
    return {'studio_access': lambda: cached_access(request),
            'studio_staff': lambda: is_staff(getattr(request, 'user', None)),
            'TICKETS_ENABLED': settings.TICKETS_ENABLED}
