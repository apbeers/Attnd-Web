"""Pitch drafts for prospects (staff; owner, Nov 26, 2026): an email offering the organizer the free test event that
Attnd already made from their website, with its join QR code. Drafts only: nothing here sends mail.

CAN-SPAM (FTC, 16 CFR 316) for a commercial email: accurate sender and subject, a clear statement that it is an
advertisement, a valid physical postal address of the sender, and a working way to opt out that is honoured. The
draft names the sender, says it is a promotional message, has an opt-out line and a mailing-address placeholder the
person must fill in, and states where the address was found. It is never drafted for a contact marked do not
contact, or when every contact with an email is."""

from django.conf import settings
from django.urls import reverse

from attnd.links import app_link_origin, join_url

ADDRESS_PLACEHOLDER = '[Your business mailing address: required before sending]'


class CannotDraft(Exception):
    pass


def _first_name(contact):
    name = (contact.name or '').strip() if contact else ''
    return name.split()[0] if name else 'there'


def pick_contact(prospect, contact_id=None):
    """The contact to write to: the given one, else the first with an email who isn't do-not-contact."""
    contacts = list(prospect.contacts.order_by('id'))
    if contact_id is not None:
        contact = next((c for c in contacts if c.pk == contact_id), None)
        if contact is None:
            raise CannotDraft('That contact isn’t on this prospect.')
        if contact.do_not_contact:
            raise CannotDraft(f'{contact} is marked do not contact. No draft was made.')
        if not contact.email:
            raise CannotDraft(f'{contact} has no email address.')
        return contact
    usable = [c for c in contacts if c.email and not c.do_not_contact]
    if not usable:
        if any(c.email and c.do_not_contact for c in contacts):
            raise CannotDraft('Every contact with an email is marked do not contact. No draft was made.')
        raise CannotDraft('This prospect has no contact with an email address.')
    return usable[0]


def draft(prospect, user, contact_id=None, sender_name=''):
    """Make and store a PitchDraft. Raises CannotDraft with the reason (shown to the person)."""
    from prospects.models import PitchDraft, ProspectActivity
    if prospect.status in ('dismissed', 'lost'):
        raise CannotDraft(f'This prospect is “{prospect.get_status_display()}”. Change its status first.')
    if not prospect.event_id:
        raise CannotDraft('Create its test event first: the pitch shows the organizer their event on a phone.')
    contact = pick_contact(prospect, contact_id)
    event = prospect.event
    link = join_url(event)
    qr = f'{app_link_origin()}{reverse("join:join_qr", args=[event.event_code])}'
    sender = (sender_name or user.get_full_name() or 'The Attnd team').strip()[:80]
    places = event.location_set.filter(hidden=False).count()
    items = event.scheduleditem_set.filter(hidden=False).count()
    found = []
    if places:
        found.append(f'{places} place{"s" if places != 1 else ""}')
    if items:
        found.append(f'{items} schedule item{"s" if items != 1 else ""}')
    what = f' ({" and ".join(found)})' if found else ''
    source = contact.source_url or (prospect.urls or [''])[0]
    subject = f'{prospect.name}: a free app for your attendees, already set up'[:200]
    body = '\n'.join([
        f'Hi {_first_name(contact)},',
        '',
        f'I put together a free, working version of the {prospect.name} app with Attnd, from the details on your '
        f'website{what}: a map of the grounds, the schedule, and updates you can send to everyone’s phone.',
        '',
        f'Scan the QR code to try it on your phone, or open {link} (nothing to sign up for).',
        f'QR code: {qr}',
        '',
        'If it’s useful, I’d be glad to hand it over to you to edit and publish for the event.',
        '',
        sender,
        f'Attnd · {settings.SITE_URL}',
        ADDRESS_PLACEHOLDER,
        '',
        '—',
        'This is a promotional message from Attnd.',
        f'I found your address at {source}.' if source else 'I found your address on your public website.',
        'If you’d rather not hear from us, reply “unsubscribe” and we won’t contact you again.',
    ])
    pitch = PitchDraft.objects.create(prospect=prospect, contact=contact, to=contact.email, subject=subject,
                                      body=body, created_by=user)
    ProspectActivity.objects.create(prospect=prospect, user=user,
                                    text=f'Pitch draft for {contact} (not sent)'[:512])
    return pitch, {'join_url': link, 'qr_url': qr}
