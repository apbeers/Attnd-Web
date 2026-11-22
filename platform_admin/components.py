"""The Platform admin's health glance as a component (superusers only): the page's Overview tab and the canvas's
Platform tab."""
from django.http import Http404
from django.urls import reverse

from console.components import component


@component('platform_health', 'Platform health', 'Superusers only: AI spend this month against the alert, events live '
           'now, sign-ups this week, failed AI jobs and push failures in the last day, Google API calls today.', ids=(),
           audience='superuser', permission='platform_admin.change_platformsettings', page=lambda ids: reverse('platform:home'))
def platform_health(user):
    if not (getattr(user, 'is_authenticated', False) and user.has_perm('platform_admin.change_platformsettings')):
        raise Http404
    from platform_admin import data
    h = data.health()
    return {'name': 'Platform health', 'ai_month': f"{h['ai_month']:,.2f}", 'ai_alert': f"{h['ai_alert']:,.0f}",
            'ai_percent': h['ai_percent'], 'events_live': h['events_live'], 'signups_week': h['signups_week'],
            'failed_jobs': h['failed_jobs'], 'push_failures': h['push_failures'],
            'google': [{'api': k, 'counts': str(v)} for k, v in (h['google'] or {}).items()],
            'url': reverse('platform:home')}
