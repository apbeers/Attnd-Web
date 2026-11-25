"""The MCP server's web pages and OAuth endpoints (docs/mcp_plan.md).

* ``/oauth/authorize/``: the consent page (sign-in first, so the second step applies; staff only).
* ``/oauth/token/``, ``/oauth/revoke/`` and the two ``/.well-known/`` metadata documents: django-oauth-toolkit's
  own views, public (clients call them), rate limited, and only while ``MCP_ENABLED``.
* ``/console/mcp/confirm/<step>/``: where a person confirms (or cancels) a write an MCP client asked for. It runs
  through the chat's own gate (agent.loop.confirm), as the browser's session.
"""

from functools import wraps
from urllib.parse import urlsplit

from django.conf import settings
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from oauth2_provider import views as oauth_views

from attnd.permissions import declare, signed_in
from attnd.public import public
from attnd.ratelimit import RateLimit, client_ip
from mcp_server import grants


def enabled(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not settings.MCP_ENABLED:
            raise Http404
        return view(request, *args, **kwargs)
    return wrapper


def limited(name, key):
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            limit = RateLimit(name)
            if limit.hit(key(request)) > limit.limit:
                return JsonResponse({'error': 'slow_down', 'error_description': 'Too many requests. Try again soon.'},
                                    status=429)
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


class Authorize(oauth_views.AuthorizationView):
    """django-oauth-toolkit's consent flow on our page: staff only, this server's resource only, and the redirect's
    host shown plainly (MCP spec 2025-11-25: a loopback-only client gets a warning)."""
    template_name = 'mcp_server/authorize.html'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.has_perm(grants.PERMISSION):
            raise Http404  # staff only: no entry point for anyone else (attnd/permissions.py)
        if request.method == 'GET' and not request.GET.get('client_id'):  # opened by hand, not by an assistant
            return render(request, 'mcp_server/authorize.html', {'start_here': True,
                                                                 'server': settings.MCP_RESOURCE_URL})
        wanted = settings.MCP_RESOURCE_URL.rstrip('/')
        resources = request.GET.getlist('resource') or request.POST.getlist('resource')
        if any(r.rstrip('/') != wanted for r in resources):
            return render(request, 'mcp_server/authorize.html', {'error': {
                'error': 'invalid_target', 'description': f'This sign-in is only for {wanted}.'}}, status=400)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        redirect_uri = self.request.GET.get('redirect_uri') or self.request.POST.get('redirect_uri') or ''
        host = urlsplit(redirect_uri).hostname or ''
        context.update(redirect_host=host, loopback=host in ('localhost', '127.0.0.1', '::1'),
                       max_days=settings.MCP_GRANT_MAX_DAYS,
                       server=settings.MCP_RESOURCE_URL, dev=not settings.SITE_URL.endswith('//attnd.app'))
        return context


authorize = enabled(declare(grants.PERMISSION)(Authorize.as_view()))
token = public(enabled(csrf_exempt(limited('mcp_token_ip', client_ip)(oauth_views.TokenView.as_view()))))
revoke_token = public(enabled(csrf_exempt(oauth_views.RevokeTokenView.as_view())))
server_metadata = public(enabled(oauth_views.OAuthServerMetadataView.as_view()))
resource_metadata = public(enabled(oauth_views.OAuthProtectedResourceMetadataView.as_view()))


@signed_in
@enabled
def confirm(request, step_id):
    """Confirm or cancel a write an MCP client asked for. Only the person it acts for; single use."""
    from agent import loop
    from agent.models import AgentRun, AgentStep
    step = get_object_or_404(AgentStep.objects.select_related('run'), pk=step_id, run__user=request.user,
                             run__source=AgentRun.SOURCE_MCP, kind=AgentStep.KIND_BUTTON)
    run = step.run
    error = ''
    if request.method == 'POST' and step.confirm_state == AgentStep.CONFIRM_PENDING:
        if not request.session.session_key:
            request.session.save()
        # It runs as this browser's session (signed in now, with any recent sign-in a guarded action needs).
        AgentRun.objects.filter(pk=run.pk).update(session_key=request.session.session_key,
                                                  host=request.get_host(), secure=request.is_secure())
        run.refresh_from_db()
        try:
            if request.POST.get('decision') == 'confirm':
                loop.confirm(run, step.pk, request.POST.get('hash', ''))
            else:
                loop.cancel(run, step.pk)
        except loop.GateError as exc:
            error = str(exc)
        else:
            AgentRun.objects.filter(pk=run.pk).update(status=AgentRun.DONE)
            return redirect('mcp:confirm', step_id=step.pk)
    step.refresh_from_db()
    return render(request, 'mcp_server/confirm.html', {
        'step': step, 'run': run, 'error': error, 'pending': step.confirm_state == AgentStep.CONFIRM_PENDING})
