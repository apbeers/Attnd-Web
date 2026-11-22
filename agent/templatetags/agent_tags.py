from django import template

from agent.registry import off_limits

register = template.Library()


@register.simple_tag(takes_context=True)
def agent_panel(context):
    """Show the Ask Attnd panel? Signed-in console pages only, never the account / sign-in pages or the admin."""
    request = context.get('request')
    user = getattr(request, 'user', None)
    if request is None or not (user and user.is_authenticated):
        return False
    path = request.path
    return path.startswith('/console/') and not off_limits(path)
