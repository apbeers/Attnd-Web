def studio(request):
    """`studio_access` (Studio: admins only) and `studio_staff` for the console chrome, evaluated only when a template
    asks."""
    from studio.access import cached_access, is_staff
    return {'studio_access': lambda: cached_access(request),
            'studio_staff': lambda: is_staff(getattr(request, 'user', None))}
