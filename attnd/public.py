"""Pages anyone may open (stage F2: Django's LoginRequiredMiddleware makes every other view need a sign-in).

    path('api/v2/', public_include('events.urls_v2')),   # every view in that URLconf is public
    path('health/', public(views.health)),               # one view

`attnd/tests_public.py` lists every public page, so a new one is a deliberate choice.
"""
from importlib import import_module

from django.contrib.auth.decorators import login_not_required
from django.urls import URLPattern, URLResolver, include


def public(view):
    return login_not_required(view)


def mark(patterns):
    for p in patterns:
        if isinstance(p, URLPattern):
            p.callback.login_required = False  # what login_not_required sets
        elif isinstance(p, URLResolver):
            mark(p.url_patterns)
    return patterns


def public_include(module_name):
    module = import_module(module_name)
    mark(module.urlpatterns)
    return include(module_name)
