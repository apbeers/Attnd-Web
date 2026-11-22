"""What the Platform admin page shows: customers with their plans, spend and what they used; the health glance."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone


def _count(qs, field):
    rows = qs.filter(**{field: OuterRef('pk')}).order_by().values(field).annotate(n=Count('pk')).values('n')
    return Coalesce(Subquery(rows, output_field=IntegerField()), 0)


def customers(query='', sort='-date_joined', limit=500):
    """Every account with events (and every organizer that signed up), newest first unless `sort`."""
    from agent.models import AgentRun
    from events.models import Device, Event, Notification
    from prospects.models import ClaimRequest
    from scrape.models import IngestJob
    from studio.models import Site
    User = get_user_model()
    qs = User.objects.all()
    if query:
        qs = qs.filter(Q(email__icontains=query) | Q(event__name__icontains=query) | Q(event__event_code__iexact=query))
    qs = qs.annotate(
        n_events=_count(Event.objects.all(), 'user'),
        n_imports=_count(IngestJob.objects.filter(kind=IngestJob.KIND_CREATE), 'created_by'),
        n_runs=_count(AgentRun.objects.all(), 'user'),
        n_sites=_count(Site.objects.exclude(draft=None), 'event__user'),
        n_live_sites=_count(Site.objects.exclude(published=None), 'event__user'),
        n_notifications=_count(Notification.objects.filter(sent=True), 'event__user'),
        n_themes=_count(Event.objects.exclude(theme={}), 'user'),
        n_screens=_count(Event.objects.exclude(layout_config={}), 'user'),
        n_claims=_count(ClaimRequest.objects.all(), 'user'),
        n_phones=_count(Device.objects.attendees(), 'event__user'),
    ).distinct()
    allowed = {'-date_joined', 'date_joined', 'email', '-n_events', '-n_phones', '-last_login'}
    qs = qs.order_by(sort if sort in allowed else '-date_joined')
    return list(qs[:limit])


def customer_rows(users):
    from credit import ledger
    from credit.models import AiSpend
    from platform_admin import plans
    from platform_admin.models import PaymentRecord
    ids = [u.pk for u in users]
    month = dict(AiSpend.objects.filter(user_id__in=ids, counted=True, created_at__gte=ledger.month_start())
                 .values_list('user').annotate(t=Sum('cost_usd')))
    total = dict(AiSpend.objects.filter(user_id__in=ids).values_list('user').annotate(t=Sum('cost_usd')))
    paid = dict(PaymentRecord.objects.filter(event__user_id__in=ids).values_list('event__user')
                .annotate(t=Sum('amount_usd')))
    from credit.models import AiLimitOverride
    overrides = {o.user_id: o for o in AiLimitOverride.objects.filter(user_id__in=ids)}
    settings_row = ledger.limits()
    from events.models import Event
    event_plans = {}
    for e in Event.objects.filter(user_id__in=ids).only('id', 'user_id', 'plan', 'paid', 'name'):
        event_plans.setdefault(e.user_id, []).append(plans.LABELS[plans.plan_of(e)])
    rows = []
    for u in users:
        limit = ledger.monthly_limit(u, overrides.get(u.pk), settings_row)
        rows.append({
            'id': u.pk, 'email': u.email, 'joined': u.date_joined, 'last_active': u.last_login,
            'events': u.n_events, 'plans': ', '.join(sorted(set(event_plans.get(u.pk, [])))) or '—',
            'paid_usd': paid.get(u.pk) or Decimal('0'),
            'ai_month': month.get(u.pk) or Decimal('0'), 'ai_total': total.get(u.pk) or Decimal('0'),
            'ai_limit': 'No limit' if limit is None else f'${limit:,.0f}',
            'features': {'AI imports': u.n_imports, 'Ask Attnd runs': u.n_runs, 'Sites built': u.n_sites,
                         'Sites live': u.n_live_sites, 'Notifications sent': u.n_notifications,
                         'Themes': u.n_themes, 'Screens edited': u.n_screens, 'Claims': u.n_claims,
                         'Phones joined': u.n_phones},
        })
    return rows


def health():
    from credit import ledger
    from events.models import Event
    from scrape.models import IngestJob
    now = timezone.now()
    try:
        from attnd import google
        google_usage = google.usage()
    except Exception:
        google_usage = {}
    alert = Decimal(ledger.limits().platform_alert_usd or 0)
    spent = ledger.platform_total()
    live = sum(1 for e in Event.objects.filter(status=Event.STATUS_PUBLISHED, start_time__lte=now)
               .only('start_time', 'end_time') if e.timing == 'now')
    from django.contrib.auth import get_user_model
    from events.models import Notification
    return {
        'ai_month': spent, 'ai_alert': alert, 'ai_percent': int(min(100, spent / alert * 100)) if alert else 0,
        'google': google_usage,
        'failed_jobs': IngestJob.objects.filter(status=IngestJob.FAILED, created_at__gte=now - timedelta(hours=24)).count(),
        'push_failures': Notification.objects.filter(sent_at__gte=now - timedelta(days=1))
        .aggregate(f=Sum('failed_count'))['f'] or 0,
        'signups_week': get_user_model().objects.filter(date_joined__gte=now - timedelta(days=7)).count(),
        'events_live': live,
    }
