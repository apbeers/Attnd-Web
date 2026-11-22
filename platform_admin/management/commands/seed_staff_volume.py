"""Realistic data volume for measuring the staff pages (Lighthouse, query counts), dev only.

    python manage.py seed_staff_volume            # add the rows (idempotent: cleans earlier seed rows first)
    python manage.py seed_staff_volume --clean    # remove them again

Every row it makes is marked so `--clean` finds exactly those: diagnostics sessions and reports have
model `lighthouse-seed`, prospects a `lighthouse-seed` url_key prefix, emails and claims an
`@lighthouse-seed.example` address, AI spend the feature `lighthouse-seed`. Rows go in with bulk_create, so no
signal sends an email or a push. Refuses to run on production (SITE_URL https://attnd.app without DEBUG).
"""
import random
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

MARK = 'lighthouse-seed'
DOMAIN = 'lighthouse-seed.example'
CROCKFORD = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'


class Command(BaseCommand):
    help = 'Seed (or --clean) marked rows that give the staff pages realistic volume. Dev only.'

    def add_arguments(self, parser):
        parser.add_argument('--clean', action='store_true', help='Only remove earlier seed rows')
        parser.add_argument('--entries', type=int, default=20000, help='Entries in the one big log session')

    def handle(self, *args, clean=False, entries=20000, **options):
        if getattr(settings, 'SITE_URL', '').rstrip('/') == 'https://attnd.app' and not (settings.DEBUG or settings.TESTING):
            raise CommandError('Not on production.')
        removed = self.clean()
        self.stdout.write(f'Removed {removed}')
        if clean:
            return
        rng = random.Random(42)
        with transaction.atomic():
            made = {
                'sessions+entries': self.diagnostics(rng, entries),
                'reports': self.reports(rng),
                'prospects': self.prospects(rng),
                'emails': self.emails(rng),
                'claims': self.claims(rng),
                'ai spend': self.ai_spend(rng),
            }
        self.stdout.write(f'Seeded {made}')

    # --- clean -------------------------------------------------------------------------------------------------

    def clean(self):
        from credit.models import AiSpend
        from diagnostics.models import DiagnosticReport, DiagnosticSession
        from platform_admin.models import EmailLog
        from prospects.models import ClaimRequest, Prospect
        out = {}
        out['sessions'] = DiagnosticSession.objects.filter(model=MARK).delete()[0]
        out['reports'] = DiagnosticReport.objects.filter(model=MARK).delete()[0]
        out['claims'] = ClaimRequest.objects.filter(email__endswith='@' + DOMAIN).delete()[0]
        out['prospects'] = Prospect.objects.filter(url_key__startswith=MARK).delete()[0]
        out['emails'] = EmailLog.objects.filter(to__endswith='@' + DOMAIN).delete()[0]
        out['ai spend'] = AiSpend.objects.filter(feature=MARK).delete()[0]
        out['users'] = User.objects.filter(email__endswith='@' + DOMAIN).delete()[0]
        return out

    # --- seeds -------------------------------------------------------------------------------------------------

    def _code(self, rng, used):
        while True:
            code = 'D-' + ''.join(rng.choice(CROCKFORD) for _ in range(5))
            if code not in used:
                used.add(code)
                return code

    def diagnostics(self, rng, big):
        from diagnostics.models import APP_CATEGORIES, DiagnosticEntry, DiagnosticSession
        now = timezone.now()
        used = set(DiagnosticSession.objects.values_list('code', flat=True))
        names = ['net.request.started', 'net.request.finished', 'net.request.failed', 'lifecycle.foreground',
                 'map.render', 'push.received', 'search.query', 'planner.run', 'perf.metric', 'error.caught']
        sessions = []
        sizes = [big] + [rng.randint(5, 60) for _ in range(149)]
        for n, size in enumerate(sizes):
            # within the last 3 days: older debug sessions are purged (dev builds after 7 days, purge.py)
            start = now - timedelta(minutes=rng.randint(30, 60 * 24 * 3) if n else 120)
            sessions.append(DiagnosticSession(
                code=self._code(rng, used), platform=rng.choice(['ios', 'android']),
                reason='debug_mode' if n == 0 else rng.choice(['dev', 'debug_mode']), app_version=f'2.0.{rng.randint(150, 190)}',
                build=str(rng.randint(150, 190)), os=rng.choice(['iOS 27.1', 'Android 17']), model=MARK,
                last_entry_at=start + timedelta(minutes=10), first_ts=start, last_ts=start + timedelta(minutes=10),
                entry_count=size, error_count=0, bytes=size * 300))
        DiagnosticSession.objects.bulk_create(sessions)
        total = 0
        for session, size in zip(sessions, sizes):
            rows, errors = [], 0
            for i in range(size):
                level = rng.choices([1, 2, 4, 5], [40, 45, 10, 5])[0]
                errors += level >= 5
                name = 'error.caught' if level >= 5 else rng.choice(names[:-1])
                ts = session.first_ts + timedelta(milliseconds=i * 600_000 // max(1, size))
                rows.append(DiagnosticEntry(
                    session=session, received_at=ts, ts=ts, mono_ms=i * 30, seq=i, level=level,
                    cat=rng.choice(APP_CATEGORIES), name=name, msg=f'{name} {i}',
                    fields={'ms': rng.randint(5, 900), 'status': rng.choice([200, 200, 304, 404, 500]),
                            'req_id': f'{rng.getrandbits(64):016x}'},
                    state=rng.choice(['fg', 'bg']), net=rng.choice(['wifi', 'cell']), app_version=session.app_version))
            DiagnosticEntry.objects.bulk_create(rows, batch_size=2000)
            DiagnosticSession.objects.filter(pk=session.pk).update(error_count=errors)
            total += size
        return f'{len(sessions)}+{total}'

    def reports(self, rng):
        from diagnostics.models import DiagnosticReport
        now = timezone.now()
        rows = []
        for i in range(300):
            sig = f'{rng.randint(0, 24):040x}'
            platform = rng.choice(['ios', 'android'])
            rows.append(DiagnosticReport(
                occurred_at=now - timedelta(hours=rng.randint(1, 24 * 6)), platform=platform,
                app_version=f'2.0.{rng.randint(150, 190)}', build=str(rng.randint(150, 190)),
                os='iOS 27.1' if platform == 'ios' else 'Android 17', model=MARK,
                kind=rng.choice(['crash', 'hang', 'anr', 'exit']), reason='seed',
                summary=f'java.lang.IllegalStateException: seeded problem {sig[-2:]}', signature=sig,
                stack='\n'.join(f'  at a.b.c{j}(SourceFile:{j})' for j in range(30)), bytes=2000))
        DiagnosticReport.objects.bulk_create(rows)
        # received_at is auto_now_add: spread it over the week like real reports
        for row in rows:
            DiagnosticReport.objects.filter(pk=row.pk).update(received_at=row.occurred_at)
        return len(rows)

    def prospects(self, rng):
        from prospects.models import Prospect, ProspectContact
        today = timezone.localdate()
        cities = ['Fort Worth', 'Dallas', 'Austin', 'Denton', 'Waco', 'Arlington', 'Plano', 'San Marcos']
        kinds = ['Music Festival', 'Farmers Market', 'Art Walk', 'Food Truck Rally', 'County Fair', '5K Run',
                 'Holiday Market', 'Craft Fair', 'Car Show', 'Beer Festival']
        statuses = [s for s, _ in Prospect.STATUS_CHOICES]
        rows = []
        for i in range(120):
            name = f'{rng.choice(cities)} {rng.choice(kinds)} {i + 1}'
            start = today + timedelta(days=rng.randint(5, 200))
            rows.append(Prospect(
                name=name, organizer=f'{name} Committee', summary='Outdoor event across several stops. ' * 3,
                start_date=start, end_date=start + timedelta(days=rng.randint(0, 2)), city=rng.choice(cities),
                venue='Downtown', urls=[f'https://{DOMAIN}/{i}/'], fit_score=rng.randint(20, 98),
                fit_reasons=['Outdoor', 'Several stages'], traits=['outdoor', 'walking'],
                status=rng.choice(statuses), url_key=f'{MARK}/{i}', name_key=f'{MARK} {i}'))
        Prospect.objects.bulk_create(rows)
        contacts = [ProspectContact(prospect=p, name=f'Person {j}', role='Organizer', email=f'p{p.pk}-{j}@{DOMAIN}',
                                    source_url=f'https://{DOMAIN}/{p.pk}/contact', do_not_contact=j == 2)
                    for p in rows for j in range(rng.randint(1, 3))]
        ProspectContact.objects.bulk_create(contacts)
        return f'{len(rows)}+{len(contacts)} contacts'

    def emails(self, rng):
        from platform_admin.models import EmailLog
        now = timezone.now()
        kinds = ['import_done', 'review_waiting', 'notification_sent', 'invite', 'sign_in_code', 'new_sign_in',
                 'password_reset', 'upgrade_request']
        statuses = ['sent', 'delivered', 'delivered', 'delivered', 'logged', 'blocked', 'bounced', 'failed']
        rows = [EmailLog(created_at=now - timedelta(minutes=rng.randint(1, 60 * 24 * 60)), kind=rng.choice(kinds),
                         to=f'person{i % 90}@{DOMAIN}', subject=f'Seeded email {i}', status=rng.choice(statuses),
                         message_id=f'<{MARK}-{i}@{DOMAIN}>')
                for i in range(600)]
        EmailLog.objects.bulk_create(rows)
        return len(rows)

    def claims(self, rng):
        from events.models import Event
        from prospects.models import ClaimRequest
        events = list(Event.objects.filter(showcase=False).order_by('pk')[:10])
        if not events:
            return 0
        users = User.objects.bulk_create([User(username=f'lhseed{i:03d}', email=f'claimer{i}@{DOMAIN}',
                                               first_name='Seed', last_name=f'Claimer {i}') for i in range(30)])
        rows = [ClaimRequest(event=rng.choice(events), user=u, email=u.email, role='Organizer',
                             note='I run this event.', status=rng.choice(['pending', 'pending', 'approved', 'declined']))
                for u in users]
        ClaimRequest.objects.bulk_create(rows)
        return len(rows)

    def ai_spend(self, rng):
        from credit.models import AiSpend
        fields = {f.name for f in AiSpend._meta.get_fields()}
        if 'feature' not in fields:
            return 0
        now = timezone.now()
        users = list(User.objects.filter(email__endswith='@' + DOMAIN)[:30])
        rows = []
        for i in range(500):
            row = AiSpend(feature=MARK, user=rng.choice(users) if users else None)
            for name, value in (('model', 'claude-haiku-5-5'), ('provider', 'anthropic'),
                                ('cost_usd', round(rng.uniform(0.001, 0.2), 4)),
                                ('input_tokens', rng.randint(500, 20000)), ('output_tokens', rng.randint(50, 2000)),
                                ('created_at', now - timedelta(minutes=rng.randint(1, 60 * 24 * 20)))):
                if name in fields:
                    setattr(row, name, value)
            rows.append(row)
        AiSpend.objects.bulk_create(rows)
        return len(rows)
