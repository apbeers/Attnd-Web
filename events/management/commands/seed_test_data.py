"""
Management command: seed_test_data

Creates (or updates) a stable set of test events used by the iOS integration
test suite and Apple App Review. All events are set to public=True with
start_times rolling forward from now so that time-filtered endpoints
(nearby_events, etc.) always return results.

Run once after each deployment or call from a Heroku release phase:
    python manage.py seed_test_data

Safe to run multiple times — it upserts by event_code.
"""

from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from events.models import Event, Location, Notification, ScheduledItem


class Command(BaseCommand):
    help = 'Upsert test fixture events for iOS integration tests and App Review.'

    # The test user that owns all fixture data.
    # Created if it doesn't exist (password is unusable — login disabled).
    FIXTURE_USERNAME = 'fixtureuser'

    EVENTS = [
        {
            'event_code': 'FWFM2026',
            'name': 'Fort Worth Food & Music Festival',
            'description': 'Annual food and music festival in Fort Worth.',
            'latitude': '32.75550000000000000000',
            'longitude': '-97.33080000000000000000',
            'address': 'Sundance Square, 420 Main St, Fort Worth, TX 76102',
            'paid': False,
            'public': True,
            'hours_from_now': 4,   # starts 4 hours from now
            'locations': [
                {
                    'name': "Cattlemen's Steakhouse",
                    'snippet': 'Award-winning steakhouse since 1947.',
                    'description': 'One of Fort Worth\'s most iconic steakhouses, serving prime cuts since 1947.',
                    'latitude': '32.75500000000000000000',
                    'longitude': '-97.33200000000000000000',
                    'address': '2458 N Main St, Fort Worth, TX 76106',
                    'scheduled_items': [
                        {'name': 'Opening Ceremony', 'description': 'Welcome to the festival.', 'hours_delta': 0},
                        {'name': 'Headliner Performance', 'description': 'Main act of the evening.', 'hours_delta': 4},
                    ],
                },
                {'name': 'Sundance Square Plaza', 'snippet': 'Open-air entertainment district.', 'description': 'Heart of downtown Fort Worth.', 'latitude': '32.75550000000000000000', 'longitude': '-97.33080000000000000000', 'address': '420 Main St, Fort Worth, TX 76102', 'scheduled_items': []},
                {'name': "Billy Bob's Texas", 'snippet': 'World\'s largest honky-tonk.', 'description': 'Live music and dancing every weekend.', 'latitude': '32.75200000000000000000', 'longitude': '-97.33500000000000000000', 'address': '2520 Rodeo Plaza, Fort Worth, TX 76164', 'scheduled_items': []},
                {'name': 'Kimbell Art Museum', 'snippet': 'World-class art collection.', 'description': 'One of the finest art museums in the US.', 'latitude': '32.74900000000000000000', 'longitude': '-97.36400000000000000000', 'address': '3333 Camp Bowie Blvd, Fort Worth, TX 76107', 'scheduled_items': []},
                {'name': 'Amon Carter Museum', 'snippet': 'American art and photography.', 'description': 'Dedicated to American art.', 'latitude': '32.74950000000000000000', 'longitude': '-97.36300000000000000000', 'address': '3501 Camp Bowie Blvd, Fort Worth, TX 76107', 'scheduled_items': []},
                {'name': 'Fort Worth Botanic Garden', 'snippet': 'Texas oldest botanic garden.', 'description': 'Beautiful gardens open year-round.', 'latitude': '32.74600000000000000000', 'longitude': '-97.36600000000000000000', 'address': '3220 Botanic Garden Blvd, Fort Worth, TX 76107', 'scheduled_items': []},
                {'name': 'Stockyards National Historic District', 'snippet': 'Historic cowboy culture.', 'description': 'Experience the Old West in Fort Worth.', 'latitude': '32.75200000000000000000', 'longitude': '-97.34800000000000000000', 'address': '131 E Exchange Ave, Fort Worth, TX 76164', 'scheduled_items': []},
                {'name': 'Trinity Park', 'snippet': 'Riverside park and trails.', 'description': 'Miles of trails along the Trinity River.', 'latitude': '32.74500000000000000000', 'longitude': '-97.36000000000000000000', 'address': 'University Dr, Fort Worth, TX 76107', 'scheduled_items': []},
                {'name': 'Modern Art Museum of Fort Worth', 'snippet': 'Contemporary art landmark.', 'description': 'One of the oldest and largest modern art museums in the US.', 'latitude': '32.74800000000000000000', 'longitude': '-97.36500000000000000000', 'address': '3200 Darnell St, Fort Worth, TX 76107', 'scheduled_items': []},
                {'name': 'Water Gardens', 'snippet': 'Iconic urban water park.', 'description': 'Stunning architectural water feature in downtown.', 'latitude': '32.74900000000000000000', 'longitude': '-97.32900000000000000000', 'address': '1502 Commerce St, Fort Worth, TX 76102', 'scheduled_items': []},
            ],
            'notifications': [
                {'title': 'Festival kicks off tomorrow!', 'body': 'Get ready for an amazing time.', 'sent': True},
                {'title': 'Lineup announced for Saturday night', 'body': 'Check the schedule tab.', 'sent': True},
            ],
        },
        {
            'event_code': 'ATIS2026',
            'name': 'Austin Tech & Innovation Summit',
            'description': 'Annual tech and innovation summit in Austin.',
            'latitude': '30.26720000000000000000',
            'longitude': '-97.74310000000000000000',
            'address': 'Palmer Events Center, 900 Barton Springs Rd, Austin, TX 78704',
            'paid': False,
            'public': True,
            'hours_from_now': 6,
            'locations': [
                {
                    'name': 'Palmer Events Center',
                    'snippet': 'Premier Austin event venue.',
                    'description': 'State-of-the-art convention center on Lady Bird Lake.',
                    'latitude': '30.26720000000000000000',
                    'longitude': '-97.74310000000000000000',
                    'address': '900 Barton Springs Rd, Austin, TX 78704',
                    'scheduled_items': [
                        {'name': 'Food Tasting', 'description': 'Sample local Austin cuisine.', 'hours_delta': 0},
                    ],
                },
                {'name': 'Capital Factory', 'snippet': 'Austin\'s startup hub.', 'description': 'Premier coworking and accelerator space.', 'latitude': '30.26840000000000000000', 'longitude': '-97.74060000000000000000', 'address': '701 Brazos St, Austin, TX 78701', 'scheduled_items': []},
                {'name': "Stubb's Amphitheater", 'snippet': 'Iconic outdoor music venue.', 'description': 'Live music under the Texas sky.', 'latitude': '30.26980000000000000000', 'longitude': '-97.73840000000000000000', 'address': '801 Red River St, Austin, TX 78701', 'scheduled_items': []},
                {'name': 'Austin Convention Center', 'snippet': 'Large-scale event space.', 'description': 'One of the largest convention centers in Texas.', 'latitude': '30.26330000000000000000', 'longitude': '-97.74080000000000000000', 'address': '500 E Cesar Chavez St, Austin, TX 78701', 'scheduled_items': []},
                {'name': 'Lady Bird Lake', 'snippet': 'Scenic urban waterway.', 'description': 'Beautiful reservoir on the Colorado River.', 'latitude': '30.25810000000000000000', 'longitude': '-97.74850000000000000000', 'address': 'Lady Bird Lake, Austin, TX 78704', 'scheduled_items': []},
                {'name': 'Zilker Park', 'snippet': 'Austin\'s beloved park.', 'description': 'Home of Austin City Limits Music Festival.', 'latitude': '30.26350000000000000000', 'longitude': '-97.76850000000000000000', 'address': '2100 Barton Springs Rd, Austin, TX 78746', 'scheduled_items': []},
                {'name': 'South Congress Avenue', 'snippet': 'Austin\'s eclectic main street.', 'description': 'Lined with shops, restaurants, and galleries.', 'latitude': '30.24800000000000000000', 'longitude': '-97.75000000000000000000', 'address': 'South Congress Ave, Austin, TX 78704', 'scheduled_items': []},
                {'name': 'Sixth Street', 'snippet': 'Legendary entertainment district.', 'description': 'Austin\'s famous nightlife and music strip.', 'latitude': '30.26750000000000000000', 'longitude': '-97.73900000000000000000', 'address': '6th St, Austin, TX 78701', 'scheduled_items': []},
                {'name': 'Texas State Capitol', 'snippet': 'Historic state government building.', 'description': 'Renaissance Revival architecture completed in 1888.', 'latitude': '30.27467000000000000000', 'longitude': '-97.74028000000000000000', 'address': '1100 Congress Ave, Austin, TX 78701', 'scheduled_items': []},
                {'name': 'Barton Springs Pool', 'snippet': 'Natural spring-fed pool.', 'description': 'Three-acre natural swimming pool in Zilker Park.', 'latitude': '30.26120000000000000000', 'longitude': '-97.77000000000000000000', 'address': '2201 Barton Springs Rd, Austin, TX 78746', 'scheduled_items': []},
            ],
            'notifications': [
                {'title': 'Early bird registration closes tonight', 'body': 'Last chance to register at the early rate.', 'sent': True},
                {'title': 'Speaker spotlight: keynote revealed', 'body': 'See who\'s headlining this year\'s summit.', 'sent': True},
            ],
        },
        {
            'event_code': 'DACN2026',
            'name': 'Dallas Arts & Culture Night',
            'description': 'An evening celebrating Dallas arts and culture.',
            'latitude': '32.77670000000000000000',
            'longitude': '-96.79700000000000000000',
            'address': 'Dallas Arts District, 2403 Flora St, Dallas, TX 75201',
            'paid': False,
            'public': True,
            'hours_from_now': 8,
            'locations': [
                {'name': 'Dallas Museum of Art', 'snippet': 'World-class art collection.', 'description': 'One of the largest art museums in the US.', 'latitude': '32.78780000000000000000', 'longitude': '-96.80050000000000000000', 'address': '1717 N Harwood St, Dallas, TX 75201', 'scheduled_items': []},
                {'name': 'Deep Ellum', 'snippet': 'Dallas arts and entertainment district.', 'description': 'Historic district known for live music and street art.', 'latitude': '32.78360000000000000000', 'longitude': '-96.78670000000000000000', 'address': 'Deep Ellum, Dallas, TX 75226', 'scheduled_items': []},
                {'name': 'The Bomb Factory', 'snippet': 'Premier live music venue.', 'description': 'Dallas\'s largest indoor music venue.', 'latitude': '32.78430000000000000000', 'longitude': '-96.78590000000000000000', 'address': '2713 Canton St, Dallas, TX 75226', 'scheduled_items': []},
                {'name': 'Nasher Sculpture Center', 'snippet': 'World-renowned sculpture collection.', 'description': 'Permanent collection of modern and contemporary sculpture.', 'latitude': '32.78880000000000000000', 'longitude': '-96.80150000000000000000', 'address': '2001 Flora St, Dallas, TX 75201', 'scheduled_items': []},
                {'name': 'AT&T Performing Arts Center', 'snippet': 'Dallas performing arts campus.', 'description': 'Multi-venue performing arts campus in the Arts District.', 'latitude': '32.78990000000000000000', 'longitude': '-96.80360000000000000000', 'address': '2403 Flora St, Dallas, TX 75201', 'scheduled_items': []},
                {'name': 'Crow Museum of Asian Art', 'snippet': 'Asian art and culture.', 'description': 'Free museum showcasing Asian art and culture.', 'latitude': '32.78810000000000000000', 'longitude': '-96.80220000000000000000', 'address': '2010 Flora St, Dallas, TX 75201', 'scheduled_items': []},
                {'name': 'Klyde Warren Park', 'snippet': 'Urban park over a highway.', 'description': 'Dallas\' living room — built over a freeway.', 'latitude': '32.78920000000000000000', 'longitude': '-96.80610000000000000000', 'address': '2012 Woodall Rodgers Fwy, Dallas, TX 75201', 'scheduled_items': []},
                {'name': 'Dallas Arboretum', 'snippet': 'Stunning botanical garden.', 'description': '66 acres of seasonal gardens on White Rock Lake.', 'latitude': '32.82480000000000000000', 'longitude': '-96.71670000000000000000', 'address': '8525 Garland Rd, Dallas, TX 75218', 'scheduled_items': []},
                {'name': 'Perot Museum of Nature and Science', 'snippet': 'Interactive science museum.', 'description': 'Eleven permanent halls covering everything from energy to space.', 'latitude': '32.78700000000000000000', 'longitude': '-96.80680000000000000000', 'address': '2201 N Field St, Dallas, TX 75201', 'scheduled_items': []},
                {'name': 'Dallas Contemporary', 'snippet': 'Contemporary art gallery.', 'description': 'Non-collecting contemporary art museum.', 'latitude': '32.78350000000000000000', 'longitude': '-96.78800000000000000000', 'address': '161 Glass St, Dallas, TX 75207', 'scheduled_items': []},
            ],
            'notifications': [
                {'title': 'Venue map now available', 'body': 'See the Locations tab for the full map.', 'sent': True},
                {'title': 'Free parking at Klyde Warren Park', 'body': 'Park free all evening at Klyde Warren.', 'sent': True},
            ],
        },
    ]

    def handle(self, *args, **options):
        user = self._get_or_create_fixture_user()
        now = timezone.now()

        for event_data in self.EVENTS:
            start_time = now + timedelta(hours=event_data['hours_from_now'])
            event, created = Event.objects.update_or_create(
                event_code=event_data['event_code'],
                defaults={
                    'name': event_data['name'],
                    'description': event_data['description'],
                    'latitude': event_data['latitude'],
                    'longitude': event_data['longitude'],
                    'address': event_data['address'],
                    'start_time': start_time,
                    'paid': event_data['paid'],
                    'public': event_data['public'],
                    'user': user,
                },
            )
            verb = 'Created' if created else 'Updated'
            self.stdout.write(f'{verb} event: {event.name} ({event.event_code}) — starts at {start_time.strftime("%Y-%m-%d %H:%M UTC")}')

            # Rebuild locations (delete and recreate to keep IDs stable-ish)
            existing_location_names = {loc.name: loc for loc in event.location_set.all()}
            seen_names = set()

            for loc_data in event_data['locations']:
                seen_names.add(loc_data['name'])
                loc, loc_created = Location.objects.get_or_create(
                    event=event,
                    name=loc_data['name'],
                    defaults={
                        'snippet': loc_data['snippet'],
                        'description': loc_data['description'],
                        'latitude': loc_data['latitude'],
                        'longitude': loc_data['longitude'],
                        'address': loc_data['address'],
                        'user': user,
                    },
                )
                if not loc_created:
                    loc.snippet = loc_data['snippet']
                    loc.description = loc_data['description']
                    loc.latitude = loc_data['latitude']
                    loc.longitude = loc_data['longitude']
                    loc.address = loc_data['address']
                    loc.save()

                # Rebuild scheduled items for this location
                loc.scheduleditem_set.all().delete()
                for item_data in loc_data.get('scheduled_items', []):
                    ScheduledItem.objects.create(
                        name=item_data['name'],
                        description=item_data['description'],
                        start_time=start_time + timedelta(hours=item_data['hours_delta']),
                        location=loc,
                        event=event,
                        user=user,
                    )

            # Remove locations that are no longer in the fixture
            for name, loc in existing_location_names.items():
                if name not in seen_names:
                    loc.delete()

            # Rebuild notifications
            event.notification_set.all().delete()
            for notif_data in event_data['notifications']:
                Notification.objects.create(
                    title=notif_data['title'],
                    body=notif_data['body'],
                    send_time=now - timedelta(hours=1),
                    sent=notif_data['sent'],
                    user=user,
                    event=event,
                )

        from events import drafts  # §4: the App Review samples' refresh goes straight to the phones
        for code in [e['event_code'] for e in self.EVENTS]:
            ev = Event.objects.filter(event_code=code).first()
            if ev is not None and drafts.enabled(ev):
                drafts.publish(ev)
        self.stdout.write(self.style.SUCCESS('Test data seeded successfully.'))

    def _get_or_create_fixture_user(self):
        user, created = User.objects.get_or_create(username=self.FIXTURE_USERNAME)
        if created:
            user.set_unusable_password()
            user.save()
            self.stdout.write(f'Created fixture user: {self.FIXTURE_USERNAME}')
        return user
