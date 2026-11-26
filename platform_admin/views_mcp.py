"""Connected apps (Platform sub-tab, docs/mcp_plan.md): the MCP connections of Claude and other assistants.

Staff see and revoke their own; platform admins (`mcp_server.manage_mcp`) see and revoke everyone's. The page also
says how to connect, which tools an assistant gets, and what MCP clients did recently (their runs).
"""

from django.conf import settings
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from agent.registry import agent_action
from agent.registry import param as P
from attnd.permissions import requires
from mcp_server import grants
from mcp_server.models import McpGrant


def _visible(user):
    rows = McpGrant.objects.select_related('user', 'application')
    return rows if user.has_perm('mcp_server.manage_mcp') else rows.filter(user=user)


@agent_action('platform_connected_apps', 'Connected apps (staff): Claude and other assistants connected to Attnd '
              'through MCP, who they act for, when they were last used, the tools they get and what they did '
              'recently.', kind='read')
@requires(grants.PERMISSION)
def connected_apps(request):
    from agent.models import AgentRun
    from agent.tools import mcp_tools_for
    manage = request.user.has_perm('mcp_server.manage_mcp')
    rows = _visible(request.user)
    runs = (AgentRun.objects.filter(source=AgentRun.SOURCE_MCP).select_related('user').order_by('-created_at'))
    if not manage:
        runs = runs.filter(user=request.user)
    return render(request, 'platform_admin/connected_apps.html', {
        'active': list(rows.filter(revoked_at__isnull=True)[:100]),
        'revoked': list(rows.filter(revoked_at__isnull=False)[:20]),
        'runs': list(runs[:30]), 'manage': manage, 'enabled': settings.MCP_ENABLED,
        'server': settings.MCP_RESOURCE_URL, 'dev': not settings.SITE_URL.endswith('//attnd.app'),
        'tools': [t['name'] for t in mcp_tools_for(request.user)], 'max_days': settings.MCP_GRANT_MAX_DAYS,
        'idle_days': settings.MCP_GRANT_IDLE_DAYS})


def _revoke_summary(ctx):
    grant = McpGrant.objects.select_related('user').filter(pk=ctx.args.get('grant_id')).first()
    if grant is None:
        return 'Revoke a connected app'
    return f'Revoke {grant.client_name or "this assistant"}’s connection for {grant.user.email}'


@agent_action('revoke_connected_app', 'Revoke an MCP connection (Claude or another assistant): its tokens stop '
              'working at once and it has to connect again.', confirm=True, summary=_revoke_summary,
              params={'grant_id': P('integer', 'The connection id (from the Connected apps page)')},
              title='Revoked a connected app')
@requires(grants.PERMISSION)
@require_POST
def revoke_connected_app(request):
    grant = get_object_or_404(_visible(request.user), pk=request.POST.get('grant_id'), revoked_at__isnull=True)
    grants.revoke(grant, f'revoked by {request.user.email}')
    messages.success(request, f'Revoked {grant.client_name or "the assistant"}’s connection for {grant.user.email}.')
    return redirect('platform:connected_apps')
