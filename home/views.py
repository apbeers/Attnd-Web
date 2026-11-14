import os
from django.conf import settings
from django.shortcuts import render


def _build_number() -> str:
    path = os.path.join(settings.BASE_DIR, "version.txt")
    try:
        return open(path).read().strip()
    except OSError:
        return "?"


def home(request):
    return render(request, template_name='home/home.html', context={"build": _build_number()})


def privacy(request):
    from django.conf import settings
    return render(request, template_name='home/privacy.html',
                  context={'contact_email': getattr(settings, 'CONTACT_EMAIL', '')})
