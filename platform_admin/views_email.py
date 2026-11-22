"""Emails (batch 7 Track B): the staff Emails log (Platform sub-tab), the one-click unsubscribe page (RFC 8058) and
Mailgun's event webhook, which moves a log row on to delivered / delayed / bounced / marked as spam."""

import hashlib
import hmac
import json
import time

from django.conf import settings
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from agent.registry import agent_action
from attnd.permissions import requires
from attnd.public import public
from console import mail
from platform_admin.models import EmailLog

PAGE = 200


@agent_action('platform_emails', 'The Emails log (staff): every email the platform sent, to whom, its kind, subject, '
              'status and Mailgun events (never the body). Filter with ?kind=, ?status=, ?q= (address).', kind='read')
@requires('platform_admin.view_emaillog')
def emails(request):
    rows = EmailLog.objects.all()
    kind, status, q = request.GET.get('kind', ''), request.GET.get('status', ''), (request.GET.get('q') or '').strip()
    if kind in mail.KINDS:
        rows = rows.filter(kind=kind)
    if status in dict(EmailLog.STATUSES):
        rows = rows.filter(status=status)
    if q:
        rows = rows.filter(Q(to__icontains=q) | Q(subject__icontains=q))
    since = timezone.now() - timezone.timedelta(hours=24)
    counts = dict(EmailLog.objects.filter(created_at__gte=since).values_list('status').annotate(n=Count('pk')))
    labels = dict(EmailLog.STATUSES)
    return render(request, 'platform_admin/emails.html', {
        'rows': [{'row': r, 'kind_label': mail.KINDS.get(r.kind, (r.kind, ''))[0],
                  'category': mail.KINDS.get(r.kind, ('', ''))[1]} for r in rows[:PAGE]],
        'kinds': [{'key': k, 'label': label, 'category': cat} for k, (label, cat) in mail.KINDS.items()],
        'statuses': EmailLog.STATUSES, 'kind': kind, 'status': status, 'q': q,
        'counts': [{'label': labels.get(s, s), 'n': n, 'status': s} for s, n in sorted(counts.items())],
        'total_24h': sum(counts.values()), 'retention_days': getattr(settings, 'EMAIL_LOG_DAYS', 90),
        'webhook_on': bool(getattr(settings, 'MAILGUN_WEBHOOK_SIGNING_KEY', '')),
        'allow_list': bool(settings.EMAIL_ALLOWED_RECIPIENTS), 'enabled': mail.email_enabled()})


@public
@csrf_exempt
def unsubscribe(request, token):
    """GET: a page that asks (link scanners never unsubscribe anyone). POST: one click (RFC 8058: the mail app
    posts `List-Unsubscribe=One-Click` without cookies), or the page's buttons (`action=resubscribe` undoes it)."""
    user, kind = mail.read_unsubscribe_token(token)
    if user is None:
        raise Http404
    label = mail.KINDS[kind][0]
    done = undone = False
    if request.method == 'POST':
        on = request.POST.get('action') == 'resubscribe'
        mail.set_wanted(user, kind, on)
        done, undone = not on, on
        if request.POST.get('List-Unsubscribe') == 'One-Click':
            return HttpResponse('Unsubscribed.', content_type='text/plain')
    wanted = mail.wants(user, kind)
    return render(request, 'platform_admin/unsubscribe.html', {
        'label': label, 'email': user.email, 'wanted': wanted, 'done': done, 'undone': undone,
        'account_url': '/console/account/#emails'})


MAILGUN_STATUS = {'delivered': EmailLog.DELIVERED, 'complained': EmailLog.COMPLAINED,
                  'unsubscribed': EmailLog.UNSUBSCRIBED}


def _verified(signature):
    key = getattr(settings, 'MAILGUN_WEBHOOK_SIGNING_KEY', '')
    if not key or not isinstance(signature, dict):
        return False
    timestamp, token, given = (str(signature.get(k) or '') for k in ('timestamp', 'token', 'signature'))
    if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > 900:
        return False
    expected = hmac.new(key.encode(), (timestamp + token).encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, given)


@public
@csrf_exempt
@require_POST
def mailgun_webhook(request):
    """Mailgun's webhooks (delivered, failed, complained, unsubscribed): signed with the webhook signing key
    (MAILGUN_WEBHOOK_SIGNING_KEY); unknown messages are answered 200 and ignored. Off (404) without the key."""
    if not getattr(settings, 'MAILGUN_WEBHOOK_SIGNING_KEY', ''):
        raise Http404
    try:
        payload = json.loads(request.body or b'{}')
    except ValueError:
        return HttpResponse(status=400)
    if not _verified(payload.get('signature')):
        return HttpResponse(status=406)  # Mailgun stops retrying on 406
    data = payload.get('event-data') or {}
    event = str(data.get('event') or '')
    message_id = str(((data.get('message') or {}).get('headers') or {}).get('message-id') or '').strip('<>')
    if not message_id:
        return JsonResponse({'ok': True})
    if event == 'failed':
        status = EmailLog.BOUNCED if data.get('severity') == 'permanent' else EmailLog.DEFERRED
    else:
        status = MAILGUN_STATUS.get(event)
    recipient = str(data.get('recipient') or '').lower()
    rows = EmailLog.objects.filter(message_id=message_id)
    if recipient:
        rows = rows.filter(to__iexact=recipient) if rows.filter(to__iexact=recipient).exists() else rows
    note = {'event': event, 'at': timezone.now().isoformat(timespec='seconds'),
            'reason': str(data.get('reason') or (data.get('delivery-status') or {}).get('description') or '')[:120]}
    for row in rows[:20]:
        row.events = (list(row.events or []) + [note])[-10:]
        fields = ['events']
        if status:
            row.status = status
            fields.append('status')
        row.save(update_fields=fields)
        if event == 'unsubscribed' and row.user_id and mail.is_optional(row.kind):
            mail.set_wanted(row.user, row.kind, False)
    return JsonResponse({'ok': True})
