"""Ticket APIs for the apps (API-017), under /api/v2/. The apps never sell: "Get tickets" opens the web page.

Every call also needs the normal app authentication (attestation JWT / static key / CI key). On top of that:

    My tickets (buyers)                       header X-Attnd-Buyer: <buyer token>
      POST tickets/code/    {"email"}                          → {"status": "sent"}  (same answer for any address)
      POST tickets/verify/  {"email", "code", "device_name"?}  → {"buyer_token", "email", "expires_at"}
      GET  tickets/mine/    [?event_id=]                       → {"email", "tickets": [...], "fetched_at"} (ETag)
      POST tickets/signout/

    Door mode (door staff and the event's team)  header X-Attnd-Door: <door token>
      POST door/code/  {"email"}  ·  POST door/verify/ {"email", "code", "device_name"?} → {"door_token", "events"}
      GET  door/events/
      GET  door/events/<id>/manifest/                 keys + ticket types + tickets (ETag)
      GET  door/events/<id>/revocations/?since=<cursor>
      POST door/events/<id>/checkins/ {"device_id", "gate", "checkins": [{"client_id", "token"|"ticket_id", "at",
                                       "kind": "in"|"out", "device_result"?}]}  (idempotent, ≤ 500 per call)
      GET  door/events/<id>/counts/
      POST door/signout/

    CI only (dev tools): GET tickets/test-inbox/?email=<…@TICKETS_TEST_EMAIL_DOMAIN>  → {"code"}

CI keys (API-008) may use these only for DEMO and the ticket demo event and only with test-domain emails.
"""

import hashlib
import json

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from attnd.ratelimit import RateLimit, client_ip
from events.apis import ApiKeyRequired, is_ci_request
from tickets import buyers, checkin, signing, wallet
from tickets.models import AccessToken, Ticket, VerificationCode, normalize_email


def _body(request):
    try:
        data = json.loads(request.body or b'{}')
        return data if isinstance(data, dict) else {}
    except (ValueError, UnicodeDecodeError):
        return {}


def _error(message, status, code):
    return JsonResponse({'error': code, 'message': message}, status=status)


def ci_event_codes():
    return {c.upper() for c in (settings.DEMO_EVENT_CODE, settings.TICKETS_DEMO_EVENT_CODE) if c}


def _ci_refuses_email(request, email):
    return is_ci_request(request) and not buyers.is_test_address(email)


def _json_etag(request, data):
    body = json.dumps(data, separators=(',', ':'), sort_keys=True, ensure_ascii=False).encode()
    etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
    if request.headers.get('If-None-Match') == etag:
        resp = HttpResponse(status=304)
    else:
        resp = HttpResponse(body, content_type='application/json')
    resp['ETag'] = etag
    resp['Cache-Control'] = 'no-cache'
    return resp


class TicketApi(ApiKeyRequired, View):
    ci_scope = 'tickets'


@method_decorator(csrf_exempt, name='dispatch')
class SendCode(TicketApi):
    purpose = VerificationCode.PURPOSE_BUYER

    def post(self, request):
        email = normalize_email(_body(request).get('email'))
        if _ci_refuses_email(request, email):
            return _error('CI keys may only use test addresses', 403, 'forbidden')
        try:
            buyers.send_code(email, self.purpose, client_ip(request))
        except buyers.CodeError as exc:
            return _error(str(exc), exc.status, exc.code)
        return JsonResponse({'status': 'sent', 'expires_in': buyers.CODE_MINUTES * 60})


@method_decorator(csrf_exempt, name='dispatch')
class Verify(TicketApi):
    purpose = VerificationCode.PURPOSE_BUYER
    kind = AccessToken.KIND_BUYER

    def post(self, request):
        body = _body(request)
        email = normalize_email(body.get('email'))
        if _ci_refuses_email(request, email):
            return _error('CI keys may only use test addresses', 403, 'forbidden')
        try:
            buyers.check_code(email, body.get('code'), self.purpose, client_ip(request))
        except buyers.CodeError as exc:
            return _error(str(exc), exc.status, exc.code)
        raw, token = buyers.issue_token(email, self.kind, str(body.get('device_name') or ''))
        return JsonResponse(self.answer(request, raw, token))

    def answer(self, request, raw, token):
        return {'buyer_token': raw, 'email': token.email, 'expires_at': token.expires_at.isoformat()}


def ticket_json(ticket, request=None):
    """One ticket for "My tickets" (stored by the app for offline use)."""
    event = ticket.event
    valid = ticket.status == Ticket.VALID
    base = settings.SITE_URL
    page = f'/tickets/ticket/{ticket.public_id}/{ticket.key}/'
    return {
        'id': ticket.id,
        'code': ticket.display_code,
        'status': ticket.status,
        'version': ticket.version,
        'qr': signing.token_for(ticket) if valid else None,
        'type': {'id': ticket.ticket_type_id, 'name': ticket.ticket_type.name},
        'holder_name': ticket.holder_name,
        'order_ref': ticket.order.ref,
        'checked_in_at': ticket.checked_in_at.isoformat() if ticket.checked_in_at else None,
        'transferable': valid and ticket.ticket_type.transferable and not ticket.checked_in_at,
        'ticket_url': base + page,
        'apple_wallet_url': base + page + 'wallet.pkpass' if valid and wallet.apple_enabled() else None,
        'google_wallet_url': base + page + 'google-wallet/' if valid and wallet.google_enabled() else None,
        'event': {'id': event.id, 'name': event.name, 'event_code': event.event_code, 'timezone': event.timezone,
                  'start_time': event.start_time.isoformat(),
                  'end_time': event.end_time.isoformat() if event.end_time else None, 'address': event.address,
                  'latitude': float(event.latitude) if event.latitude is not None else None,
                  'longitude': float(event.longitude) if event.longitude is not None else None,
                  'cancelled': bool(getattr(getattr(event, 'ticketing', None), 'cancelled_at', None))},
    }


def _buyer(request):
    return buyers.resolve_token(request.headers.get('X-Attnd-Buyer', ''), AccessToken.KIND_BUYER)


@method_decorator(csrf_exempt, name='dispatch')
class MyTickets(TicketApi):
    def get(self, request):
        token = _buyer(request)
        if token is None:
            return _error('Sign in again', 401, 'buyer_token_invalid')
        qs = (Ticket.objects.filter(holder_email=token.email).exclude(event__status='draft')
              .select_related('event', 'event__ticketing', 'ticket_type', 'order').order_by('event__start_time', 'id'))
        if request.GET.get('event_id', '').isdigit():
            qs = qs.filter(event_id=int(request.GET['event_id']))
        if is_ci_request(request):
            qs = qs.filter(event__event_code__in=ci_event_codes())
        data = {'email': token.email, 'tickets': [ticket_json(t, request) for t in qs[:500]]}
        return _json_etag(request, data)


@method_decorator(csrf_exempt, name='dispatch')
class SignOut(TicketApi):
    header = 'X-Attnd-Buyer'

    def post(self, request):
        buyers.revoke_token(request.headers.get(self.header, ''))
        return JsonResponse({'status': 'ok'})


@method_decorator(csrf_exempt, name='dispatch')
class TestInbox(TicketApi):
    """Dev/CI only: the latest code sent to a test-domain address (the apps' live suites sign in with it)."""

    def get(self, request):
        email = normalize_email(request.GET.get('email'))
        if not (settings.TICKETS_DEV_TOOLS and is_ci_request(request) and buyers.is_test_address(email)):
            return _error('Not found', 404, 'not_found')
        record = VerificationCode.objects.filter(email=email).exclude(test_code='').order_by('-created_at').first()
        if record is None:
            return _error('No code yet', 404, 'not_found')
        return JsonResponse({'email': email, 'code': record.test_code, 'purpose': record.purpose,
                             'sent_at': record.created_at.isoformat()})


# ---------------------------------------------------------------------------
# Door mode
# ---------------------------------------------------------------------------

class DoorSendCode(SendCode):
    purpose = VerificationCode.PURPOSE_DOOR


def _door_event_json(event):
    return {'id': event.id, 'name': event.name, 'event_code': event.event_code, 'timezone': event.timezone,
            'start_time': event.start_time.isoformat(), 'end_time': event.end_time.isoformat() if event.end_time else None}


def _door_events(request, email):
    events = checkin.door_events(email)
    if is_ci_request(request):
        events = events.filter(event_code__in=ci_event_codes())
    return events


@method_decorator(csrf_exempt, name='dispatch')
class DoorVerify(Verify):
    purpose = VerificationCode.PURPOSE_DOOR
    kind = AccessToken.KIND_DOOR

    # An address that works no doors still verifies (only its inbox's owner learns that "events" is empty).
    def answer(self, request, raw, token):
        return {'door_token': raw, 'email': token.email, 'expires_at': token.expires_at.isoformat(),
                'events': [_door_event_json(e) for e in _door_events(request, token.email)]}


class DoorApi(TicketApi):
    """Door token + access to the event in the URL (re-checked on every call, so removing staff works at once)."""

    def door(self, request, event_id=None):
        token = buyers.resolve_token(request.headers.get('X-Attnd-Door', ''), AccessToken.KIND_DOOR)
        if token is None:
            return None, None, _error('Sign in to door mode again', 401, 'door_token_invalid')
        limit = RateLimit('tickets_door_token')
        if limit.exceeded(token.pk):
            return None, None, _error('Too many requests', 429, 'rate_limited')
        limit.hit(token.pk)
        if event_id is None:
            return token, None, None
        event = _door_events(request, token.email).filter(pk=event_id).first()
        if event is None:
            return None, None, _error('Not found', 404, 'not_found')
        return token, event, None


@method_decorator(csrf_exempt, name='dispatch')
class DoorEvents(DoorApi):
    def get(self, request):
        token, _, refused = self.door(request)
        if refused:
            return refused
        return JsonResponse({'email': token.email, 'events': [_door_event_json(e)
                                                              for e in _door_events(request, token.email)]})


@method_decorator(csrf_exempt, name='dispatch')
class DoorManifest(DoorApi):
    def get(self, request, event_id):
        _, event, refused = self.door(request, event_id)
        if refused:
            return refused
        data = checkin.manifest(event)
        stable = {k: v for k, v in data.items() if k != 'generated_at'}
        resp = _json_etag(request, stable)
        if resp.status_code == 200:
            resp.content = json.dumps(data, separators=(',', ':'), ensure_ascii=False).encode()
        return resp


@method_decorator(csrf_exempt, name='dispatch')
class DoorRevocations(DoorApi):
    def get(self, request, event_id):
        _, event, refused = self.door(request, event_id)
        if refused:
            return refused
        since = request.GET.get('since', '0')
        return JsonResponse(checkin.revocations(event, int(since) if since.isdigit() else 0))


@method_decorator(csrf_exempt, name='dispatch')
class DoorCheckins(DoorApi):
    def post(self, request, event_id):
        token, event, refused = self.door(request, event_id)
        if refused:
            return refused
        body = _body(request)
        scans = body.get('checkins')
        if not isinstance(scans, list):
            return _error('checkins must be a list', 400, 'invalid')
        if len(scans) > checkin.MAX_BATCH:
            return _error(f'At most {checkin.MAX_BATCH} per call', 413, 'too_many')
        results = checkin.sync(event, scans, device_id=str(body.get('device_id') or ''),
                               gate=str(body.get('gate') or ''), staff_email=token.email)
        latest = event.ticket_revocations.order_by('-id').values_list('id', flat=True).first() or 0
        return JsonResponse({'results': results, 'counts': checkin.counts(event), 'revocations_cursor': latest})


@method_decorator(csrf_exempt, name='dispatch')
class DoorCounts(DoorApi):
    def get(self, request, event_id):
        _, event, refused = self.door(request, event_id)
        if refused:
            return refused
        return JsonResponse(checkin.counts(event))


class DoorSignOut(SignOut):
    header = 'X-Attnd-Door'


# ---------------------------------------------------------------------------
# Dev tools for the live suites (CI key + TICKETS_DEV_TOOLS + Stripe test mode, demo events only)
# ---------------------------------------------------------------------------

def _dev_ci(request):
    return settings.TICKETS_DEV_TOOLS and is_ci_request(request)


@method_decorator(csrf_exempt, name='dispatch')
class TestOrder(TicketApi):
    """Hold tickets like the web page does: {"event_id", "lines": [{"type_id", "quantity", "amount"?}], "email"}."""

    def post(self, request):
        from events.models import Event
        from tickets import inventory, payments
        if not _dev_ci(request):
            return _error('Not found', 404, 'not_found')
        body = _body(request)
        event = Event.objects.filter(pk=body.get('event_id'), event_code__in=ci_event_codes()).first()
        email = normalize_email(body.get('email'))
        if event is None or not buyers.is_test_address(email):
            return _error('Not found', 404, 'not_found')
        lines = [(row.get('type_id'), row.get('quantity', 1), row.get('amount')) for row in body.get('lines') or []
                 if isinstance(row, dict)]
        try:
            order = inventory.create_hold(event, lines, email=email, name='CI buyer', source='app',
                                          promo_code=str(body.get('promo_code') or ''),
                                          unlock_codes=[str(body.get('unlock_code') or '')])
            if order.total_cents == 0:
                payments.complete_free(order)
                order.refresh_from_db()
        except (inventory.CartError, payments.PaymentError) as exc:
            return _error(str(exc), 400, 'cart')
        return JsonResponse({'order_id': str(order.public_id), 'ref': order.ref, 'status': order.status,
                             'total_cents': order.total_cents, 'fee_cents': order.fee_cents})


@method_decorator(csrf_exempt, name='dispatch')
class TestPay(TicketApi):
    """Pay a held order with Stripe's test card (pm_card_visa) server-side; the webhook then issues the tickets."""

    def post(self, request):
        from tickets import inventory, stripe_api
        from tickets.models import Order
        if not (_dev_ci(request) and stripe_api.test_mode()):
            return _error('Not found', 404, 'not_found')
        order = Order.objects.filter(public_id=_body(request).get('order_id'),
                                     event__event_code__in=ci_event_codes()).select_related('event').first()
        if order is None or order.status not in Order.ACTIVE_HOLDS or order.total_cents <= 0:
            return _error('Not found', 404, 'not_found')
        account = inventory.ticketing(order.event).payout_account
        meta = {'order_id': str(order.public_id), 'event_id': order.event_id, 'order_ref': order.ref}
        pi = stripe_api.post('/v1/payment_intents', {
            'amount': order.total_cents, 'currency': order.currency, 'payment_method': 'pm_card_visa',
            'payment_method_types': ['card'], 'confirm': True, 'application_fee_amount': order.fee_cents,
            'transfer_data': {'destination': account.stripe_account_id}, 'on_behalf_of': account.stripe_account_id,
            'metadata': meta}, idempotency_key=f'test-pay-{order.public_id}')
        Order.objects.filter(pk=order.pk).update(status=Order.PENDING, stripe_payment_intent_id=pi.get('id', ''))
        return JsonResponse({'payment_intent': pi.get('id'), 'status': pi.get('status')})

