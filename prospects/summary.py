"""Prospects as plain data for tools (MCP `list_prospects`, owner Nov 26, 2026): each prospect's id (the pk the
prospect tools take), name, status, fit, test event and contacts with their do-not-contact flag. Contact details of a
do-not-contact entry are never included (as on the prospect page and in the CSV)."""

from django.db.models import Q

from prospects.models import Prospect

STATUSES = {value for value, _ in Prospect.STATUS_CHOICES}


def rows(user, q='', status='', limit=50):
    if not user.has_perm('prospects.view_prospect'):
        raise PermissionError('Prospects are for staff only.')
    qs = Prospect.objects.select_related('event').prefetch_related('contacts', 'pitch_drafts').order_by('-fit_score', 'name')
    if status == 'open':
        qs = qs.exclude(status__in=['lost', 'dismissed'])
    elif status in STATUSES:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(organizer__icontains=q) | Q(city__icontains=q) |
                       Q(contacts__name__icontains=q) | Q(contacts__organization__icontains=q)).distinct()
    total = qs.count()
    out = []
    for p in qs[:max(1, min(limit, 200))]:
        contacts = []
        for c in p.contacts.all():
            row = {'id': c.pk, 'name': c.name or c.organization or 'Contact', 'role': c.role,
                   'do_not_contact': c.do_not_contact}
            if not c.do_not_contact:
                row['email'] = c.email
            contacts.append(row)
        pitches = [{'id': d.pk, 'to': d.to, 'status': d.status,
                    'date': (d.status_at or d.created_at).date().isoformat()} for d in p.pitch_drafts.all()[:5]]
        out.append({'pitches': pitches, 'pk': p.pk, 'name': p.name, 'organizer': p.organizer, 'city': p.city, 'status': p.status,
                    'fit': p.fit_score, 'test_event': p.event.event_code if p.event_id else None,
                    'contacts': contacts,
                    'can_pitch': bool(p.event_id and p.status not in ('lost', 'dismissed') and
                                      any(c.email and not c.do_not_contact for c in p.contacts.all()))})
    return {'total': total, 'shown': len(out), 'prospects': out}


def block_email(email, user=None):
    """Mark every contact with this address do not contact; returns how many changed."""
    from prospects.models import ProspectActivity, ProspectContact
    changed = 0
    for c in ProspectContact.objects.filter(email__iexact=email.strip(), do_not_contact=False).select_related('prospect'):
        c.do_not_contact = True
        c.save(update_fields=['do_not_contact'])
        ProspectActivity.objects.create(prospect=c.prospect, user=user, text=f'{c} marked do not contact'[:512])
        changed += 1
    return changed


def do_not_contact(user, email=''):
    """The one list to check before any send: every do-not-contact address (staff only), or whether one is on it."""
    from prospects.models import ProspectContact
    if not user.has_perm('prospects.view_prospect'):
        raise PermissionError('Prospects are for staff only.')
    blocked = ProspectContact.objects.filter(do_not_contact=True).exclude(email='')
    if email:
        hit = blocked.filter(email__iexact=email.strip()).exists()
        return {'email': email, 'do_not_contact': hit,
                'text': f'{email} is on the do-not-contact list: don\'t send.' if hit else
                        f'{email} is not on the do-not-contact list.'}
    rows = sorted({c.email.lower() for c in blocked})
    return {'count': len(rows), 'emails': rows}
