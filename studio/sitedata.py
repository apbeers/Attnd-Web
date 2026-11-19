"""Live event data for hosted sites (/<slug>/_attnd/data.json), from the same serializers as the apps' bundle."""

from django.conf import settings

from events import content
from events.models import Event


def join_url(event):
    return f'https://attnd.app/join/{event.event_code}/'


def site_data(site, base_url):
    """`base_url` = the page base the site is served under (published or preview), for absolute asset links."""
    event = site.event
    ev = content.event_v2(event)
    flyer = ev.get('flyer_url')
    if flyer and not flyer.startswith(('http://', 'https://')):
        flyer = None  # a local media path would point at the console's host
    data = {
        'schema': 1,
        'site': {'slug': site.slug, 'url': site.public_url()},
        'event': {
            'name': event.name, 'code': event.event_code, 'description': event.description, 'address': event.address,
            'latitude': ev['latitude'], 'longitude': ev['longitude'], 'start_time': ev['start_time'],
            'end_time': ev['end_time'], 'timezone': event.timezone or 'UTC', 'status': event.status,
            'accent_color': event.accent_color or None, 'logo_url': event.logo_url or None, 'flyer_url': flyer,
            'join_url': join_url(event), 'website_url': site.public_url() if site.is_live else None,
        },
        'app': {'ios': settings.IOS_APP_STORE_URL, 'android': settings.ANDROID_PLAY_STORE_URL,
                'join_url': join_url(event), 'qr_url': f'{base_url}_attnd/qr.svg', 'code': event.event_code},
        'categories': [], 'locations': [], 'schedule': [], 'updates': [],
        'tickets': None,  # <attnd-tickets> (tickets.content.site_tickets)
    }
    if event.status == Event.STATUS_DRAFT:
        return data  # unpublished content never leaves the console
    bundle = content.bundle(event)
    data['categories'] = bundle['categories']
    data['locations'] = [{k: v for k, v in loc.items() if k != 'sort_order'} for loc in bundle['locations']]
    for loc in data['locations']:
        if loc.get('image_url') and not loc['image_url'].startswith(('http://', 'https://')):
            loc['image_url'] = None
    data['schedule'] = bundle['schedule']
    if settings.TICKETS_ENABLED:  # parked: no ticket data, so <attnd-tickets> renders nothing
        from tickets.content import site_tickets
        data['tickets'] = site_tickets(event)
    data['updates'] = [{'id': n['id'], 'title': n['title'], 'body': n['body'], 'sent_at': n['send_time'],
                        'priority': n['priority']} for n in bundle['notifications']]
    return data
