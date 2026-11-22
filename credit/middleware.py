"""Every request runs inside the AI spend context of the person making it (a visitor: a lazy visitor key), so an AI
call made while answering it is attributed without each view having to say so."""
from credit import ledger

# URL names whose AI calls have a feature of their own (else 'other').
FEATURES = {'location_fill': 'fill', 'event_theme_generate': 'theme', 'event_import': 'paste', 'event_new': 'import',
            'event_refresh': 'refresh', 'start': 'import'}


class SpendContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            ctx = {'user': user}
        else:
            ctx = {'visitor': lambda: _visitor(request)}
        with ledger.spend_context(**ctx):
            return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = getattr(request, 'resolver_match', None)
        name = getattr(match, 'url_name', '') or ''
        namespace = getattr(match, 'namespace', '') or ''
        feature = 'studio' if namespace == 'studio' else FEATURES.get(name)
        if feature:
            ledger.current()['feature'] = feature
        return None


def _visitor(request):
    key = ledger.visitor_key(request)
    request.session['ai_visitor'] = key  # carried into the account at sign-in (credit.signals)
    return key
