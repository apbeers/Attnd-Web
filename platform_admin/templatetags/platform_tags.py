from django import template

from platform_admin import areas

register = template.Library()


@register.inclusion_tag('platform_admin/_platform_nav.html', takes_context=True)
def platform_nav(context, current):
    user = context['request'].user
    return {'areas': areas.visible(user), 'current': current, 'actions': areas.actions(user)}


@register.simple_tag(takes_context=True)
def platform_entry(context):
    """The top bar's one Platform link ('' when the person has no platform permission)."""
    request = context.get('request')
    return areas.first_url(request.user) if request is not None else ''
