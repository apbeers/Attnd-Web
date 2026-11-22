"""Platform → Site styles (batch 7 Track D): Studio's five site styles with a live preview each (phone and wide), in
an event's colours, and which style a first design of that event gets."""

from django.shortcuts import render

from agent.registry import agent_action
from attnd.permissions import requires
from events.models import Event
from studio import styles


@agent_action('platform_site_styles', "Studio's site styles (staff): the five looks a first website design lands on "
              '(layout, type, colour use, data library elements, do / don\'t) with previews in an event\'s colours. '
              '?event=<id>.', kind='read')
@requires('platform_admin.view_platformsettings')
def site_styles(request):
    events = Event.objects.for_user(request.user, 'events.change_event').order_by('-start_time')[:50]
    chosen = request.GET.get('event', '')
    event = next((e for e in events if str(e.pk) == chosen), None)
    picked = styles.pick(event)['key'] if event is not None else ''
    return render(request, 'platform_admin/site_styles.html', {
        'events': events, 'event': event, 'picked': picked,
        'styles': [{**s, 'preview': styles.preview_html(s, event), 'prompt': styles.prompt_text(s)}
                   for s in styles.STYLES]})
