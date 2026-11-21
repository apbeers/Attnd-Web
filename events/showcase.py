"""Showcase events (owner, Nov 21, 2026): the home page's "See it live" gallery. Eight small, finished demo events of
different kinds, each with places, a schedule, details for the assistant, a colour theme and two sent updates,
kept happening now like DEMO (``refresh`` from ``run_scheduled_tasks``).

- ``Event.showcase``: exempt from the free limit (API-025), never in nearby search, no usage counts, unlisted.
- Created on dev only for now: ``SHOWCASE_EVENTS`` (default on when ``SITE_URL`` is dev.attnd.app, or with DEBUG); the
  release phase runs ``load_showcase_events --if-enabled``. Production turns it on later with the setting.
- Cover images: ``load_showcase_events --images`` asks Studio's image model for one cover per event (OpenAI; costs a
  few cents each), stored as the event's flyer in our media; without it the cards draw the theme's colours.
- DEMO stays exactly as it is (the three repos' integration tests depend on its facts); the gallery shows it as one
  more card.
"""

from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.utils import timezone

# (kind, label) for the gallery's category pills; the event codes are fixed so reloading updates in place.
SHOWCASE = [
    {
        'code': 'SHOWFEST', 'kind': 'Festival', 'name': 'Riverbend Music Festival', 'tz': 'America/Chicago',
        'lat': 30.2621, 'lng': -97.7550, 'address': 'Riverbend Park, Austin, TX', 'days': 2,
        'theme': ('#7A2E8E', '#F3C4FF', '#F28C28'),
        'about': 'Two days of indie, soul and country on three stages by the river.',
        'places': [('North Stage', 'Stage', ['live music']), ('River Stage', 'Stage', ['live music', 'acoustic']),
                   ('Tent Stage', 'Stage', ['dj', 'late night']), ('Food Court', 'Food', ['food', 'vegetarian']),
                   ('Water Station', 'Services', ['water', 'free']), ('Kids Corner', 'Family', ['kid-friendly'])],
        'items': [('Gates open', None), ('The Paper Kites Tribute', 0), ('Delta Rose', 1), ('Lone Pine Trio', 0),
                  ('Sundown DJ set', 2), ('Marigold Choir', 1), ('Headliner: Neon Rodeo', 0), ('Late set: Velvet Echo', 2)],
        'faq': [('Can I bring a chair?', 'Low chairs are welcome on the lawn.'),
                ('Is there re-entry?', 'Yes, with your wristband.')],
    },
    {
        'code': 'SHOWMARKET', 'kind': 'Market', 'name': 'Saturday Farmers Market', 'tz': 'America/Los_Angeles',
        'lat': 45.5122, 'lng': -122.6587, 'address': 'Park Blocks, Portland, OR', 'days': 1,
        'theme': ('#2F6B2F', '#CDEBC0', '#E0A526'),
        'about': 'Forty local farms and makers, live music at noon and a cooking demo.',
        'places': [('Green Valley Farm', 'Produce', ['vegetables', 'organic']), ('Bread & Salt', 'Bakery', ['bread']),
                   ('Honey Hollow', 'Pantry', ['honey']), ('Demo Kitchen', 'Food', ['cooking demo']),
                   ('Music Corner', 'Stage', ['live music']), ('Info Tent', 'Services', ['info'])],
        'items': [('Market opens', None), ('Knife skills demo', 3), ('Fiddle duo', 4), ('Seasonal soups demo', 3),
                  ('Kids veggie tasting', 0), ('Market closes', None)],
        'faq': [('Are dogs allowed?', 'Leashed dogs are welcome.'), ('Do vendors take cards?', 'Most do.')],
    },
    {
        'code': 'SHOWCONF', 'kind': 'Conference', 'name': 'Northwind Tech Summit', 'tz': 'America/New_York',
        'lat': 42.3519, 'lng': -71.0446, 'address': 'Seaport Convention Center, Boston, MA', 'days': 2,
        'theme': ('#1F4E8C', '#CFE0F5', '#16A3A3'),
        'about': 'Two days, four rooms and forty talks about building software people love.',
        'places': [('Main Hall', 'Room', ['keynote']), ('Room A', 'Room', ['talks']), ('Room B', 'Room', ['talks']),
                   ('Workshop Lab', 'Room', ['workshop']), ('Expo Floor', 'Expo', ['sponsors']),
                   ('Coffee Bar', 'Food', ['coffee'])],
        'items': [('Registration', 4), ('Opening keynote', 0), ('Designing for offline', 1), ('Hands-on: accessible apps', 3),
                  ('Scaling small teams', 2), ('Lunch', 5), ('Lightning talks', 0), ('Closing panel', 0)],
        'faq': [('Is there Wi-Fi?', 'Yes: Northwind / summit2026.'), ('Are talks recorded?', 'Keynotes are.')],
    },
    {
        'code': 'SHOW5K', 'kind': 'Race', 'name': 'Harbor Lights 5K', 'tz': 'America/Los_Angeles',
        'lat': 32.7157, 'lng': -117.1700, 'address': 'Harbor Drive, San Diego, CA', 'days': 1,
        'theme': ('#C2410C', '#FED7AA', '#0E7490'),
        'about': 'An evening run along the harbor with a finish-line party.',
        'places': [('Start Line', 'Course', ['start']), ('Water Stop 1', 'Course', ['water']),
                   ('Water Stop 2', 'Course', ['water']), ('Finish Line', 'Course', ['finish']),
                   ('Bag Check', 'Services', ['services']), ('After Party', 'Stage', ['live music'])],
        'items': [('Packet pickup', 4), ('Warm-up', 0), ('Kids dash', 0), ('5K start', 0), ('Awards', 5),
                  ('After party band', 5)],
        'faq': [('Is there bag check?', 'Yes, by the start line.'), ('Strollers?', 'Start at the back of the pack.')],
    },
    {
        'code': 'SHOWART', 'kind': 'Art walk', 'name': 'Old Town Art Walk', 'tz': 'America/Denver',
        'lat': 40.5853, 'lng': -105.0844, 'address': 'Old Town, Fort Collins, CO', 'days': 1,
        'theme': ('#9D174D', '#FBCFE8', '#4338CA'),
        'about': 'Twelve galleries open late, with artists in their studios and street music.',
        'places': [('Gallery Nine', 'Gallery', ['painting']), ('Clay Works', 'Studio', ['ceramics']),
                   ('Print Shop', 'Studio', ['printmaking']), ('Mural Alley', 'Outdoor', ['murals']),
                   ('Plaza Stage', 'Stage', ['live music']), ('Taco Cart', 'Food', ['food'])],
        'items': [('Walk begins', None), ('Live wheel throwing', 1), ('Screen print a tote', 2), ('Mural talk', 3),
                  ('Street jazz', 4), ('Last call', None)],
        'faq': [('Is it free?', 'Yes, every gallery is free.'), ('Accessible route?', 'Ask at the plaza.')],
    },
    {
        'code': 'SHOWTRUCK', 'kind': 'Food', 'name': 'Food Truck Rally', 'tz': 'America/Chicago',
        'lat': 39.0997, 'lng': -94.5786, 'address': 'Crossroads, Kansas City, MO', 'days': 1,
        'theme': ('#B91C1C', '#FECACA', '#F59E0B'),
        'about': 'Twenty food trucks, a beer garden and a band on Main Street.',
        'places': [('Smoke Ring BBQ', 'Food', ['bbq', 'food']), ('Taco Loco', 'Food', ['tacos', 'food']),
                   ('Dumpling Bus', 'Food', ['dumplings', 'food']), ('Sweet Spot', 'Food', ['desserts', 'food']),
                   ('Beer Garden', 'Drinks', ['beer', '21+']), ('Main Street Stage', 'Stage', ['live music'])],
        'items': [('Trucks open', None), ('Hot sauce contest', 1), ('Band: The Crossroads', 5), ('Pie eating', 3),
                  ('Last orders', None)],
        'faq': [('Cash or card?', 'Every truck takes cards.'), ('Seating?', 'Tables by the stage.')],
        'menus': {'Smoke Ring BBQ': [('Brisket plate', '$16'), ('Pulled pork sandwich', '$11')],
                  'Taco Loco': [('Street tacos (3)', '$10'), ('Elote', '$5')]},
    },
    {
        'code': 'SHOWHOLIDAY', 'kind': 'Market', 'name': 'Winter Lights Holiday Market', 'tz': 'America/Chicago',
        'lat': 41.8837, 'lng': -87.6324, 'address': 'Daley Plaza, Chicago, IL', 'days': 3,
        'theme': ('#14532D', '#BBF7D0', '#B91C1C'),
        'about': 'Handmade gifts, mulled cider, carolers and a lantern parade.',
        'places': [('Gift Row', 'Shops', ['gifts']), ('Cider Hut', 'Drinks', ['cider']), ('Bake House', 'Food', ['food']),
                   ('Carol Stage', 'Stage', ['live music']), ('Santa’s Cabin', 'Family', ['kid-friendly']),
                   ('Lantern Start', 'Outdoor', ['parade'])],
        'items': [('Market opens', None), ('Carolers', 3), ('Photos with Santa', 4), ('Lantern parade', 5),
                  ('Brass quintet', 3)],
        'faq': [('Is it heated?', 'The huts are; dress warm.'), ('Strollers?', 'Yes, the paths are wide.')],
    },
    {
        'code': 'SHOWFAIR', 'kind': 'Fair', 'name': 'Cedar County Fair', 'tz': 'America/Chicago',
        'lat': 41.7653, 'lng': -91.1301, 'address': 'Fairgrounds, Tipton, IA', 'days': 3,
        'theme': ('#1D4ED8', '#DBEAFE', '#EAB308'),
        'about': 'Rides, livestock shows, a demolition derby and the best pie contest in the county.',
        'places': [('Grandstand', 'Arena', ['shows']), ('Livestock Barn', 'Barn', ['animals']),
                   ('Midway', 'Rides', ['rides', 'kid-friendly']), ('Pie Tent', 'Food', ['food', 'contest']),
                   ('4-H Hall', 'Hall', ['exhibits']), ('First Aid', 'Services', ['first aid'])],
        'items': [('Gates open', None), ('Goat show', 1), ('Pie judging', 3), ('Tractor pull', 0),
                  ('Demolition derby', 0), ('Fireworks', 0)],
        'faq': [('Parking?', 'Free in the north lot.'), ('Ride wristbands?', 'At the Midway booth.')],
    },
]

KINDS = {s['code']: s['kind'] for s in SHOWCASE}


def enabled():
    if getattr(settings, 'SHOWCASE_EVENTS', None) is not None:
        return bool(settings.SHOWCASE_EVENTS)
    host = urlsplit(getattr(settings, 'SITE_URL', '') or '').hostname or ''
    return bool(settings.DEBUG) or host == 'dev.attnd.app'


def kind_of(event):
    if event.event_code in KINDS:
        return KINDS[event.event_code]
    return 'Event'


def _times(now, days):
    """Happening now: began an hour ago, ends `days` later (at least 10 hours from now)."""
    start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    end = start + timedelta(days=max(days - 1, 0), hours=12)
    return start, end


@transaction.atomic
def load(spec, now=None, owner=None):
    """Create or refresh one showcase event in place (same code and id); returns it."""
    from events.models import Event, Location, Notification, ScheduledItem
    now = now or timezone.now()
    start, end = _times(now, spec['days'])
    light = dict(zip(('primary', 'secondary', 'tertiary'), spec['theme']))
    event, _ = Event.objects.update_or_create(event_code=spec['code'], defaults=dict(
        name=spec['name'], description=spec['about'], timezone=spec['tz'], start_time=start, end_time=end,
        latitude=spec['lat'], longitude=spec['lng'], address=spec['address'], status=Event.STATUS_PUBLISHED,
        public=False, paid=False, showcase=True, testing_only=False, user=owner,
        theme={'light': light, 'source': 'manual', 'reason': 'Showcase event'},
        context={'about': spec['about'], 'faq': [{'q': q, 'a': a} for q, a in spec['faq']],
                 'getting_there': f'{spec["address"]}. Free shuttles from downtown every 15 minutes.'}))
    event.location_set.all().delete()
    event.scheduleditem_set.all().delete()
    event.notification_set.all().delete()
    places = []
    for i, (name, category, tags) in enumerate(spec['places']):
        menu = [{'name': n, 'price': p} for n, p in (spec.get('menus') or {}).get(name, [])]
        places.append(Location.objects.create(
            event=event, name=name, category=category, tags=tags, sort_order=i,
            latitude=spec['lat'] + 0.0009 * ((i % 3) - 1), longitude=spec['lng'] + 0.0012 * ((i // 3) - 0.5),
            description=f'{name} at {spec["name"]}.', context={'menu': menu} if menu else {}))
    span = max((end - start).total_seconds() / 3600, 6)
    for i, (name, place) in enumerate(spec['items']):
        begins = start + timedelta(hours=round(i * span / max(len(spec['items']), 1)))
        ScheduledItem.objects.create(event=event, name=name, start_time=begins, end_time=begins + timedelta(minutes=50),
                                     location=places[place] if place is not None and place < len(places) else None,
                                     tags=list(places[place].tags[:2]) if place is not None and place < len(places) else ['general'])
    for title, body, ago in (('Welcome!', f'Welcome to {spec["name"]}. Tap a place for what’s on there.', 50),
                             ('Schedule tip', 'Like the sets you want and we’ll remind you 15 minutes before.', 20)):
        when = now - timedelta(minutes=ago)
        Notification.objects.create(event=event, title=title, body=body, send_time=when, sent=True, sent_at=when)
    return event


def load_all(now=None, owner=None):
    return [load(spec, now=now, owner=owner) for spec in SHOWCASE]


def refresh(now=None):
    """Keep them happening now (run_scheduled_tasks): reload one that ends within 12 hours."""
    from events.models import Event
    if not enabled():
        return 0
    now = now or timezone.now()
    done = 0
    for spec in SHOWCASE:
        event = Event.objects.filter(event_code=spec['code'], showcase=True).first()
        if event is None or not event.end_time or event.end_time - now < timedelta(hours=12) or event.start_time > now:
            load(spec, now=now, owner=event.user if event else None)
            done += 1
    return done


def generate_covers(events):
    """One AI cover per showcase event (Studio's image model), stored as its flyer. Returns how many were made."""
    from django.core.files.base import ContentFile

    from studio.ai import generate_image
    made = 0
    for event in events:
        if event.flyer:
            continue
        prompt = (f'A bright, modern poster-style photo illustration for "{event.name}", {event.description} '
                  'No text, no logos, no people’s faces in focus.')
        png, _model = generate_image(prompt, size='landscape')
        event.flyer.save(f'showcase-{event.event_code.lower()}.png', ContentFile(png), save=False)
        event.save(update_fields=['flyer'])
        made += 1
    return made
