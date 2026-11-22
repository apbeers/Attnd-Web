"""Claims of prospect test events (owner, Nov 21, 2026). The home page shows published test events near the visitor
("Happening near you"); "Is this your event? Claim it" asks a signed-in person for their email and role at the event.
Staff approve or decline in Prospects → Claims; approving hands the event over (owner = the claimant, the prospect →
"Claimed"). Never automatic: it gives an event away. The free limit (API-025) stays until an admin marks it paid."""

from urllib.parse import urlsplit

from django import forms
from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from console import mail


class ClaimForm(forms.Form):
    email = forms.EmailField(label='Your work email', help_text='We use it to check you’re with the event.')
    role = forms.CharField(label='Your role at the event', max_length=200,
                           widget=forms.TextInput(attrs={'placeholder': 'Organizer, marketing lead, volunteer…'}))
    note = forms.CharField(label='Anything else?', required=False, max_length=2000,
                           widget=forms.Textarea(attrs={'rows': 3}))


def claimable(event_code):
    """A published prospect test event nobody has been given yet, by code (any case)."""
    from events.models import Event
    return (Event.objects.filter(event_code__iexact=event_code, testing_only=True, status=Event.STATUS_PUBLISHED,
                                 prospects__isnull=False)
            .exclude(claim_requests__status='approved').distinct().first())


def _host(url):
    host = (urlsplit(url or '').hostname or '').lower()
    return host[4:] if host.startswith('www.') else host


def domain_hint(email, event, prospect):
    """'' or why the email looks right: its domain is the event's website or a contact's email domain."""
    domain = email.rsplit('@', 1)[-1].lower() if '@' in email else ''
    if not domain:
        return ''
    sites = {_host(u) for u in (event.source_urls or [])} | {_host(u) for u in (prospect.urls if prospect else [])}
    if any(s and (s == domain or s.endswith('.' + domain)) for s in sites):
        return f'{domain} is the event’s website'
    if prospect and any((c.email or '').lower().endswith('@' + domain) for c in prospect.contacts.all()):
        return f'{domain} matches a contact on the event’s pages'
    return ''


def request_claim(user, event, data):
    from attnd.ratelimit import RateLimit
    from prospects.models import ClaimRequest
    limit = RateLimit('claim_user')
    if limit.exceeded(user.pk):
        return False, 'You’ve sent a few claims today; we’ll look at them first.'
    if ClaimRequest.objects.filter(event=event, user=user, status=ClaimRequest.PENDING).exists():
        return False, 'You already asked for this event. We’ll be in touch.'
    limit.hit(user.pk)
    prospect = event.prospects.order_by('-pk').first()
    claim = ClaimRequest.objects.create(event=event, prospect=prospect, user=user, email=data['email'],
                                        role=data['role'], note=data.get('note', ''),
                                        domain_match=domain_hint(data['email'], event, prospect))
    admins = [email for _, email in getattr(settings, 'ADMINS', [])]
    if admins:
        mail.send('claim_request', admins, f'Claim: {event.name}',
                  paragraphs=[f'{data["email"]} ({data["role"]}) asks for “{event.name}” ({event.event_code}).',
                              claim.domain_match or 'No domain match.'],
                  button={'label': 'Review claims', 'url': f'{settings.SITE_URL}{reverse("prospects:claims")}'})
    return True, 'Thanks! We check every claim by hand and email you when the event is yours.'


@transaction.atomic
def approve(claim, staff):
    """Hand the event over: owner = the claimant, no longer a staff test copy (still unpaid: the free limit stays),
    the prospect → Claimed, every other pending claim of it declined."""
    from prospects.models import ClaimRequest, Prospect, ProspectActivity
    event = claim.event
    event.user = claim.user
    event.testing_only = False
    event.save(update_fields=['user', 'testing_only'])
    claim.status, claim.decided_by, claim.decided_at = ClaimRequest.APPROVED, staff, timezone.now()
    claim.save(update_fields=['status', 'decided_by', 'decided_at'])
    ClaimRequest.objects.filter(event=event, status=ClaimRequest.PENDING).exclude(pk=claim.pk).update(
        status=ClaimRequest.DECLINED, decided_by=staff, decided_at=timezone.now())
    if claim.prospect_id:
        Prospect.objects.filter(pk=claim.prospect_id).update(status=Prospect.CLAIMED)
        ProspectActivity.objects.create(prospect_id=claim.prospect_id, user=staff,
                                        text=f'Claimed by {claim.email} ({claim.role}); event handed over.')
    mail.send('claim_approved', [claim.user.email], f'{event.name} is yours on Attnd',
              paragraphs=['Your claim was approved.'],
              button={'label': 'Open your event', 'url': f'{settings.SITE_URL}{reverse("console:event", args=[event.pk])}'})


def decline(claim, staff):
    from prospects.models import ClaimRequest
    claim.status, claim.decided_by, claim.decided_at = ClaimRequest.DECLINED, staff, timezone.now()
    claim.save(update_fields=['status', 'decided_by', 'decided_at'])
