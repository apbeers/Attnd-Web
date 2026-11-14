"""Load a fully populated demo event, for testing and for showing people what Attnd does.

    python manage.py load_demo_event                      # code DEMO, happening now
    python manage.py load_demo_event --owner you@example.com
    heroku run -a attnd-dev python manage.py load_demo_event --owner you@example.com

It is happening right now (times are relative to when you run it), so every "now" feature shows.
Running it again refreshes the same event (same id, so phones that joined it stay joined). It fills every feature:
locations with categories and pins (and one without coordinates), a three-day schedule, sent and
scheduled notifications, a display config, a team and a pending invite, watched website pages, AI
imports and updates (one applied, one waiting for review), history snapshots, joined devices and
two days of anonymous 5-minute usage. Nothing here sends a real push: the fake devices have no push
tokens.
"""
import math
import random
import uuid
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from events import usage
from events.content import batch_changes, save_revision
from events.models import (Device, Event, EventInvite, EventMember, Location, Notification, ScheduledItem,
                           UsageCount, next_send_slot)
from scrape.models import Change, ChangeSet, IngestJob, SourcePage

CENTER = (32.7495, -97.3316)  # Fort Worth, TX
SITE = 'https://example.org/fort-worth-food-and-music-festival'
DEMO_INSTALL_TAG = 'demo'  # app-wide install counts made by this command, replaced on each run

LOCATIONS = [
    # name, category, snippet, description, (north metres, east metres) from the centre
    ('Main Stage', 'Stage', 'Headliners every night', 'The big one: headliners each night from 7 pm, with a sound check at 5.', (60, -40)),
    ('Trinity Stage', 'Stage', 'Local bands all day', 'Acoustic sets and up-and-coming local bands, noon to 10 pm.', (-80, 120)),
    ('Panther Island Pavilion', 'Stage', 'DJ sets after dark', 'Waterfront DJ stage. Bring a blanket for the lawn.', (140, 200)),
    ('Chef Demo Kitchen', 'Food', 'Live cooking demos', 'Regional chefs cook live; free tastings while they last.', (10, 60)),
    ('Taco Row', 'Food', '12 taco trucks', 'Twelve trucks from across Tarrant County. Vegetarian options at trucks 3, 7 and 11.', (-30, -90)),
    ('BBQ Pit Lane', 'Food', 'Brisket, ribs, sausage', 'Smoked since 4 am. Usually sells out by 8 pm, so come early.', (-110, -20)),
    ('Sweet Spot', 'Food', 'Desserts and coffee', 'Kolaches, paletas, cold brew and a cupcake truck.', (35, 150)),
    ('Craft Beer Garden', 'Drinks', 'Texas breweries, 21+', 'Forty taps from Texas breweries. ID required; wristbands at the gate.', (90, 60)),
    ('Lemonade Stand', 'Drinks', 'Non-alcoholic drinks', 'Fresh lemonade, agua fresca and free water refills.', (-50, 30)),
    ('Makers Market', 'Vendor', '60 local makers', 'Art, ceramics, leather goods and vintage. Most vendors take cards.', (-140, 90)),
    ('Kids Zone', 'Family', 'Games, face painting', 'Face painting, a climbing wall and a quiet tent. Open 11 am to 7 pm.', (120, -110)),
    ('First Aid', 'Services', 'Medical tent', 'EMTs on site all day. Also lost children and sunscreen.', (0, -150)),
    ('Info Booth', 'Services', 'Maps, lost & found', 'Festival maps, schedules, lost and found, and phone charging.', (-10, 0)),
    ('Restrooms North', 'Services', 'Accessible restrooms', 'Accessible restrooms and baby changing stations.', (170, 20)),
    ('Restrooms South', 'Services', 'Restrooms', 'Restrooms and handwashing stations.', (-170, -40)),
    ('Rideshare Pickup', 'Parking', 'Uber / Lyft zone', 'Rideshare pickup and drop-off on Main St at 1st St.', (230, -160)),
    ('Overflow Parking', 'Parking', '$10 cash or card', 'Shuttles every 10 minutes to the main gate.', (-320, 260)),
]

ACTS = ['The Prairie Lights', 'Ana Morales Trio', 'Cowtown Brass', 'DJ Lone Star', 'Velvet Mesquite', 'Sundown Choir',
        'The Fort Worth Strings', 'Kid Rodeo', 'Neon Armadillo', 'Juniper Lane', 'Los Hermanos Flores', 'Big Sky Radio']


def _offset(north, east):
    lat = CENTER[0] + north / 111_320
    lng = CENTER[1] + east / (111_320 * math.cos(math.radians(CENTER[0])))
    return round(lat, 7), round(lng, 7)


class Command(BaseCommand):
    help = 'Create (or replace) a fully populated demo event that is happening now.'

    def add_arguments(self, parser):
        parser.add_argument('--code', default='DEMO')
        parser.add_argument('--owner', help='Email of the account that should own it (default: unassigned)')
        parser.add_argument('--devices', type=int, default=320)
        parser.add_argument('--seed', type=int, default=2026)

    @transaction.atomic
    def handle(self, *args, code, owner, devices, seed, **options):
        rng = random.Random(seed)
        now = timezone.now()
        owner_user = User.objects.filter(email__iexact=owner).first() if owner else None
        if owner and owner_user is None:
            self.stderr.write(f'No account uses {owner}; the demo event will be unassigned.')

        start = (now - timedelta(hours=6)).replace(minute=0, second=0, microsecond=0)
        fields = dict(
            name='Fort Worth Food & Music Festival', user=owner_user,
            description='Three days of Texas food, live music and local makers along the Trinity River. '
                        'Free entry; food and drink tickets at the gate.',
            address='Panther Island, Fort Worth, TX 76102', latitude=CENTER[0], longitude=CENTER[1],
            start_time=start, end_time=start + timedelta(days=2, hours=10), timezone='America/Chicago',
            status=Event.STATUS_PUBLISHED, paid=True, public=True, logo_url='', accent_color='',
            source_urls=[SITE], refresh_schedule=Event.REFRESH_WATCH, update_mode=Event.UPDATE_AUTO,
            display_config={'preset': 'festival', 'secondary_pane': 'now_next'}, locked_fields=[],
        )
        event = Event.objects.filter(event_code=code.upper()).first()
        if event is None:
            event = Event.objects.create(event_code=code.upper(), created_at=now - timedelta(days=9), **fields)
        else:
            # Keep the same event (same id) so phones that joined it stay joined; replace its contents.
            self.stdout.write(f'Refreshing the existing {event.event_code} demo event (id {event.id}).')
            for model in (Location, ScheduledItem, Notification, EventMember, EventInvite, SourcePage, UsageCount):
                model.objects.filter(event=event).delete()
            event.changesets.all().delete()
            event.ingest_jobs.all().delete()
            event.revisions.all().delete()
            Device.objects.filter(event=event, id__startswith='demo-').delete()
            Event.objects.filter(pk=event.pk).update(**fields)
            event.refresh_from_db()
        UsageCount.objects.filter(event=None, metric='install', detail=DEMO_INSTALL_TAG).delete()

        with batch_changes(event):
            locations = self._locations(event, owner_user)
            items = self._schedule(event, owner_user, locations, start, rng)
            notifications = self._notifications(event, owner_user, start, now)
        self._team(event, owner_user)
        self._history(event, owner_user, locations, items, now)
        self._watched_pages(event, now)
        joined = self._devices(event, devices, start, now, rng)
        self._usage(event, locations, items, notifications, start, now, rng, joined)

        event.refresh_from_db()
        self.stdout.write(self.style.SUCCESS(
            f'Demo event {event.event_code} (id {event.id}): {len(locations)} locations, {len(items)} schedule items, '
            f'{len(notifications)} notifications, {joined} devices, '
            f'{UsageCount.objects.filter(event=event).count()} usage buckets. '
            f'Owner: {owner_user.email if owner_user else "unassigned"}.'))

    # -- content -------------------------------------------------------------------------------

    def _locations(self, event, user):
        out = []
        for i, (name, category, snippet, description, (n, e)) in enumerate(LOCATIONS):
            lat, lng = _offset(n, e)
            out.append(Location.objects.create(
                event=event, user=user, name=name, category=category, snippet=snippet, description=description,
                latitude=lat, longitude=lng, sort_order=i, address='Panther Island, Fort Worth, TX'))
        # One without coordinates: shown in lists, never pinned on the map.
        out.append(Location.objects.create(
            event=event, user=user, name='Roaming Mariachi', category='Stage', sort_order=len(out),
            snippet='Somewhere near you', description='A roaming mariachi band; follow the trumpets.'))
        return out

    def _schedule(self, event, user, locations, start, rng):
        by_name = {loc.name: loc for loc in locations}
        stages = [by_name['Main Stage'], by_name['Trinity Stage'], by_name['Panther Island Pavilion']]
        items = []
        for day in range(3):
            day_start = start + timedelta(days=day)
            items.append(ScheduledItem.objects.create(
                event=event, user=user, location=None, name='Gates open' if day else 'Opening ceremony',
                description='Welcome to the festival!', start_time=day_start, end_time=day_start + timedelta(minutes=30),
                tags=['General']))
            for s, stage in enumerate(stages):
                for slot in range(4):
                    t = day_start + timedelta(hours=2 + slot * 2 + s * 0.5)
                    act = ACTS[(day * 7 + s * 3 + slot) % len(ACTS)]
                    items.append(ScheduledItem.objects.create(
                        event=event, user=user, location=stage, name=act, description=f'{act} live on the {stage.name}.',
                        start_time=t, end_time=t + timedelta(minutes=75), tags=['Music']))
            for d, dish in enumerate(['Brisket 101', 'Street tacos at home', 'Kolache masterclass']):
                t = day_start + timedelta(hours=3 + d * 3)
                items.append(ScheduledItem.objects.create(
                    event=event, user=user, location=by_name['Chef Demo Kitchen'], name=dish,
                    description='Live demo with tastings.', start_time=t, end_time=t + timedelta(minutes=45),
                    tags=['Food', 'Demo']))
            t = day_start + timedelta(hours=5)
            items.append(ScheduledItem.objects.create(
                event=event, user=user, location=by_name['Kids Zone'], name='Face painting & games',
                start_time=t, end_time=t + timedelta(hours=4), tags=['Family']))
        return items

    def _notifications(self, event, user, start, now):
        out = []
        sent = [
            (start + timedelta(minutes=5), 'Welcome!', 'Gates are open. Grab a map at the Info Booth by the main gate.'),
            (start + timedelta(hours=2), 'Taco Row is open', 'All twelve taco trucks are serving now.'),
            (start + timedelta(hours=4), 'Shade & water', 'Free water refills at the Lemonade Stand and First Aid.'),
            (now - timedelta(minutes=40), 'Set time change', 'Velvet Mesquite moves to 8:30 pm on the Main Stage.'),
        ]
        for when, title, body in sent:
            out.append(Notification.objects.create(event=event, user=user, title=title, body=body, send_time=when,
                                                   sent=True, sent_at=when, delivered_count=0))
        for minutes, title, body in [(90, 'Headliner soon', 'The Prairie Lights take the Main Stage in 30 minutes.'),
                                     (24 * 60, 'Day 2', 'Gates open at noon. Brisket 101 starts at 3 pm.')]:
            out.append(Notification.objects.create(event=event, user=user, title=title, body=body,
                                                   send_time=next_send_slot(now + timedelta(minutes=minutes))))
        return out

    def _team(self, event, owner):
        helpers = User.objects.exclude(pk=owner.pk if owner else None).filter(is_active=True).order_by('id')[:2]
        for user, role in zip(helpers, [EventMember.ROLE_ADMIN, EventMember.ROLE_CONTRIBUTOR]):
            EventMember.objects.get_or_create(event=event, user=user, defaults={'role': role})
        EventInvite.objects.get_or_create(event=event, email='volunteer.coordinator@example.org',
                                          defaults={'role': EventMember.ROLE_CONTRIBUTOR, 'invited_by': owner})

    def _history(self, event, user, locations, items, now):
        """An AI import, an applied AI update and one waiting for review, plus history snapshots."""
        create = IngestJob.objects.create(
            kind=IngestJob.KIND_CREATE, status=IngestJob.DONE, trigger=IngestJob.TRIGGER_CONSOLE, event=event,
            created_by=user, urls=[SITE], created_at=now - timedelta(days=9), finished_at=now - timedelta(days=9),
            result={'event_id': event.id, 'event_code': event.event_code, 'event_name': event.name,
                    'location_count': len(locations), 'item_count': len(items), 'notes': ''})
        save_revision(event, 'Imported with AI', user)

        applied_job = IngestJob.objects.create(
            kind=IngestJob.KIND_REFRESH, status=IngestJob.DONE, trigger=IngestJob.TRIGGER_WATCH, event=event,
            urls=[SITE + '/lineup'], created_at=now - timedelta(hours=2), finished_at=now - timedelta(hours=2))
        applied = ChangeSet.objects.create(
            event=event, job=applied_job, status=ChangeSet.APPLIED, summary='1 schedule change, 1 new location',
            base_version=1, created_at=now - timedelta(hours=2), applied_at=now - timedelta(hours=2))
        velvet = next((i for i in items if i.name == 'Velvet Mesquite'), items[1])
        Change.objects.create(changeset=applied, entity=Change.ENTITY_ITEM, op=Change.OP_UPDATE, target_id=velvet.id,
                              label=velvet.name, data={'start_time': velvet.start_time.isoformat()},
                              reason='The lineup page now lists Velvet Mesquite at 8:30 pm.', status=Change.ACCEPTED)
        Change.objects.create(changeset=applied, entity=Change.ENTITY_LOCATION, op=Change.OP_CREATE,
                              label='Sweet Spot', data={'name': 'Sweet Spot', 'category': 'Food'},
                              reason='New dessert area on the vendor page.', status=Change.ACCEPTED, sort=1)
        applied_job.result = {'changeset_id': applied.id, 'summary': applied.summary, 'pending': 0, 'auto_applied': 2}
        applied_job.save(update_fields=['result'])
        save_revision(event, 'Before applying AI changes: 1 schedule change, 1 new location', None)

        pending_job = IngestJob.objects.create(
            kind=IngestJob.KIND_REFRESH, status=IngestJob.DONE, trigger=IngestJob.TRIGGER_WATCH, event=event,
            urls=[SITE + '/vendors'], created_at=now - timedelta(minutes=25), finished_at=now - timedelta(minutes=24))
        pending = ChangeSet.objects.create(
            event=event, job=pending_job, status=ChangeSet.PENDING, base_version=event.content_version,
            summary='Removal suggested: Lemonade Stand', created_at=now - timedelta(minutes=24),
            notes='The vendor page no longer lists the Lemonade Stand. Removals always wait for a person.')
        lemonade = next(loc for loc in locations if loc.name == 'Lemonade Stand')
        Change.objects.create(changeset=pending, entity=Change.ENTITY_LOCATION, op=Change.OP_DELETE,
                              target_id=lemonade.id, label=lemonade.name, data={},
                              reason='"Lemonade Stand" is no longer on /vendors.')
        pending_job.result = {'changeset_id': pending.id, 'summary': pending.summary, 'pending': 1, 'auto_applied': 0}
        pending_job.save(update_fields=['result'])
        Event.objects.filter(pk=event.pk).update(last_refreshed_at=now - timedelta(minutes=24))

    def _watched_pages(self, event, now):
        for path, changed in [('', 26), ('/lineup', 2 * 60), ('/vendors', 25), ('/map', None)]:
            SourcePage.objects.create(
                event=event, url=SITE + path, content_hash=uuid.uuid4().hex, checked_at=now - timedelta(minutes=4),
                changed_at=now - timedelta(minutes=changed) if changed else None, last_status='200')
        Event.objects.filter(pk=event.pk).update(sources_checked_at=now - timedelta(minutes=4))

    # -- people and usage ----------------------------------------------------------------------

    def _devices(self, event, count, start, now, rng):
        rows = []
        for n in range(count):
            joined = start - timedelta(days=2) + timedelta(seconds=rng.betavariate(5, 2) * (now - start + timedelta(days=2)).total_seconds())
            ios = rng.random() < 0.58
            rows.append(Device(
                id=f'demo-{event.event_code.lower()}-{n:04d}', event=event, platform='ios' if ios else 'android',
                subscribed=rng.random() < 0.7, push_token='', api_version=2,
                app_version=rng.choice(['2.0.92', '2.0.92', '2.0.88']) if ios else rng.choice(['2.0.25', '2.0.24']),
                joined_at=joined, last_seen=min(now, joined + timedelta(minutes=rng.randint(5, 600)))))
        Device.objects.bulk_create(rows)
        return len(rows)

    def _usage(self, event, locations, items, notifications, start, now, rng, joined):
        """Two days of believable 5-minute counts: joins ramp up to the event, activity follows the day."""
        UsageCount.objects.filter(event=event).delete()
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(event.timezone)
        totals = {}

        def add(metric, bucket, n, detail='', platform=None):
            if n <= 0:
                return
            platform = platform or ('ios' if rng.random() < 0.58 else 'android')
            key = (metric, usage.bucket_start(bucket), str(detail), platform)
            totals[key] = totals.get(key, 0) + n

        begin = usage.bucket_start(start - timedelta(days=2))
        steps = int((now - begin) / timedelta(minutes=5))
        weights = [loc for loc in locations for _ in range(max(1, 6 - loc.sort_order // 3))]
        # Joins (and, for most people, installs) are exactly when the demo devices joined.
        for device in Device.objects.filter(event=event, id__startswith='demo-'):
            add('join', device.joined_at, 1, platform=device.platform)
            if rng.random() < 0.8:
                add('install', device.joined_at - timedelta(minutes=rng.randint(0, 3)), 1, DEMO_INSTALL_TAG, device.platform)
        for s in range(steps + 1):
            t = begin + timedelta(minutes=5 * s)
            local_hour = t.astimezone(tz).hour
            during = t >= start
            daylight = 0.15 + max(0.0, math.sin(math.pi * (local_hour - 9) / 15))  # busy 11 am – 10 pm
            if not during:
                continue
            activity = 6 + 40 * daylight
            add('app_open', t, int(rng.gauss(activity, activity / 4)))
            for tab, share in [('map', .35), ('schedule', .3), ('locations', .22), ('notifications', .06),
                               ('flyer', .02), ('options', .05)]:
                add('tab_view', t, int(rng.gauss(activity * share, 1.5)), tab)
            for _ in range(int(activity / 3)):
                add('location_view', t, 1, rng.choice(weights).id)
            for _ in range(int(activity / 5)):
                add('schedule_item_view', t, 1, rng.choice(items).id)
            add('search', t, int(rng.random() * activity / 10))
            add('refresh', t, int(rng.random() * 2))
        for note in notifications:
            if note.sent and note.sent_at and note.sent_at <= now:
                for k in range(4):
                    add('notification_open', note.sent_at + timedelta(minutes=5 * k), int(30 / (k + 1)), note.id)
        UsageCount.objects.bulk_create([
            UsageCount(event=None if metric == 'install' else event, bucket=bucket, metric=metric,
                       detail=detail, platform=platform, count=n)
            for (metric, bucket, detail, platform), n in totals.items()
        ], ignore_conflicts=True)
