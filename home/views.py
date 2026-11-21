import os

from django.shortcuts import render


def _release() -> str:
    """The deployed Heroku release ("v123"), set only with the runtime-dyno-metadata feature; else empty."""
    return os.getenv('HEROKU_RELEASE_VERSION', '')


def home(request):
    """/: signed-in people go straight to the console (owner, Nov 21, 2026); /home/ always shows the home page."""
    if request.user.is_authenticated and request.path == '/':
        from django.shortcuts import redirect
        return redirect('console:dashboard')
    return render(request, template_name='home/home.html', context={"release": _release()})


def privacy(request):
    from django.conf import settings
    return render(request, template_name='home/privacy.html',
                  context={'contact_email': getattr(settings, 'CONTACT_EMAIL', '')})
