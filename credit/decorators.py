"""``@ai_credit_required``: an AI feature a person starts (imports, website checks, colours, Fill from website, Studio)
answers with the friendly limit message once this month's AI credit is used up; manual editing keeps working."""
from functools import wraps

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect

from credit import ledger


def blocked(request, event=None):
    """The response for a person over their limit, or None."""
    user = getattr(request, 'user', None)
    if request.method != 'POST' or user is None or not user.is_authenticated:
        return None
    if ledger.allowed(user, event=event):
        return None
    text = ledger.limit_message(user)
    if 'application/json' in request.headers.get('Accept', '') or request.headers.get('X-Attnd-Component'):
        return JsonResponse({'error': text, 'ai_credit': True}, status=429)
    messages.error(request, text)
    back = request.META.get('HTTP_REFERER') or '/console/'
    return redirect(back if back.startswith(('/', request.build_absolute_uri('/'))) else '/console/')


def ai_credit_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        event = kwargs.get('event') or next((a for a in args if hasattr(a, 'event_code')), None)
        return blocked(request, event) or view(request, *args, **kwargs)
    return wrapper
