"""
ASGI entry point of the web dyno (gunicorn's own `asgi` worker, see Procfile and SETUP.md).

Django's WSGI application runs on a fixed pool of threads (attnd.wsgi_bridge: the same middleware and views as
before), and attnd.longpoll_asgi in front of it lets long-polls wait on the event loop instead of in a thread.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'attnd.settings')

django_wsgi = get_wsgi_application()

from attnd.longpoll_asgi import LongPollMiddleware  # noqa: E402  (after Django is set up)
from attnd.wsgi_bridge import WsgiBridge  # noqa: E402

application = LongPollMiddleware(WsgiBridge(django_wsgi))
