"""Attnd Tickets (API-017). Parked: only installed, routed and tested with TICKETS_ENABLED (attnd/settings.py)."""
import os


def load_tests(loader, tests, pattern):
    """Test discovery: this package's tests run only with TICKETS_ENABLED=1 (without it the app isn't installed and
    its models can't be imported), so `manage.py test` finds nothing here while ticketing is parked."""
    from django.conf import settings
    if not settings.TICKETS_ENABLED:
        return tests
    here = os.path.dirname(__file__)
    tests.addTests(loader.discover(start_dir=here, pattern=pattern or 'test*.py'))
    return tests
