"""Live Google Places photos for locations without one of their own (batch 7, owner: OK; API-031).

`GET /img/places/<location id>/` asks Google for the place's first photo every time (attnd.google.place_photo: never
stored or cached on the server, as Google's terms require) and redirects to it, so the browser or the app keeps the
image in its own HTTP cache. `?format=json` gives the photo's authors for the credit line. Only for locations flagged
`google_photo` (events/place_images.py sets it when every free source found nothing) of published events, or of any
event to its team; limited per IP and by GOOGLE_PHOTOS_DAILY_MAX loads a day.
"""
from django.http import Http404, HttpResponseRedirect, JsonResponse

from attnd import google
from attnd.ratelimit import RateLimit, client_ip

CACHE = 'public, max-age=3600'  # the browser / app cache only; Google's photo URL itself expires


def place_photo(request, location_id):
    from events.models import Event, Location
    location = (Location.objects.select_related('event')  # public: visibility checked right below
                .filter(pk=location_id, google_photo=True, hidden=False).first())
    if location is None or location.image or location.image_url or not location.google_place_id:
        raise Http404
    event = location.event
    if event.status != Event.STATUS_PUBLISHED and not (
            request.user.is_authenticated and request.user.has_perm('events.view_event', event)):
        raise Http404
    limit, ip = RateLimit('place_photo_ip'), client_ip(request)
    if limit.exceeded(ip):
        return JsonResponse({'error': 'Too many requests.'}, status=429)
    limit.hit(ip)
    width = request.GET.get('w', '')
    photo = google.place_photo(location.google_place_id, int(width) if width.isdigit() else 1200)
    if photo is None:
        raise Http404
    if request.GET.get('format') == 'json':
        response = JsonResponse({'url': photo['url'], 'attributions': photo['attributions'],
                                 'credit': google.photo_credit(photo['attributions'])})
    else:
        response = HttpResponseRedirect(photo['url'])
    response['Cache-Control'] = CACHE
    return response
