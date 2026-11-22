"""Confirm-card texts (``summary=``) and POST adapters (``build=``) for registered console actions.

A summary says exactly what will happen, with names and counts from the database ("Send “Gates open late” now to
1,240 attendees"). It must never fail: on any problem it falls back to a plain description.
"""

from django.utils import timezone


def _event(ctx):
    return ctx.event


def _name(ctx):
    event = ctx.event
    return f'“{event.name}”' if event else 'this event'


def _count(n, word):
    return f'{n:,} {word}{"" if n == 1 else "s"}'


def publish_changes(ctx):
    from events import drafts
    event = ctx.event
    n, phones = len(drafts.changes(event)), drafts.phones(event)
    return (f'Publish {_count(n, "change")} of {_name(ctx)} to {_count(phones, "phone")}? Attendees see them '
            'within seconds.')


def discard_changes(ctx):
    from events import drafts
    n = len(drafts.changes(ctx.event))
    return f'Discard {_count(n, "unpublished change")} of {_name(ctx)}? The event goes back to what attendees see.'


def safe(fn):
    def wrapper(ctx):
        try:
            return fn(ctx)
        except Exception:
            return default(ctx)
    wrapper.__name__ = fn.__name__
    return wrapper


def default(ctx):
    shown = ', '.join(f'{k.replace("_", " ")}: {str(v)[:80]}' for k, v in ctx.args.items()
                      if v not in (None, '', []) and not k.endswith('_id'))
    text = ctx.action.description.rstrip('.')
    return f'{text} ({_name(ctx)}){": " + shown if shown else ""}.'


# --- notifications -----------------------------------------------------------

def _audience(event, urgent):
    from events.models import Device
    devices = Device.objects.real().filter(event=event).exclude(push_token='')
    return devices.count() if urgent else devices.filter(subscribed=True).count()


@safe
def send_notification(ctx):
    event, args = ctx.event, ctx.args
    title = (args.get('title') or '').strip() or '(no title)'
    urgent = bool(args.get('urgent'))
    kind = 'urgent notification' if urgent else 'notification'
    if args.get('when') == 'later':
        return (f'Schedule the {kind} “{title}” for {args.get("send_time") or "(no time)"} ({event.timezone}) in '
                f'{_name(ctx)}. It goes to the attendees who allow notifications then and shows in the Updates tab.')
    if event.status == event.STATUS_DRAFT:
        return f'Save the {kind} “{title}” in {_name(ctx)}. The event is a draft, so it sends once you publish.'
    n = _audience(event, urgent)
    extra = (' as a time-sensitive alert, even to people who turned notifications off, and on Lock Screen Live '
             'Activities' if urgent else '')
    return f'Send the {kind} “{title}” now to {_count(n, "attendee")}{extra}. It also shows in the app’s Updates tab.'


@safe
def send_scheduled_notification(ctx):
    from events.models import Notification
    n = Notification.objects.get(pk=ctx.args['notification_id'], event_id=ctx.args['event_id'])
    count = _audience(n.event, n.is_urgent)
    return f'Send “{n.title}” now to {_count(count, "attendee")} of {_name(ctx)}.'


@safe
def delete_notification(ctx):
    from events.models import Notification
    n = Notification.objects.get(pk=ctx.args['notification_id'], event_id=ctx.args['event_id'])
    return f'Delete the scheduled notification “{n.title}” of {_name(ctx)}. It will not be sent.'


# --- event lifecycle ---------------------------------------------------------

@safe
def publish_event(ctx):
    return f'Publish {_name(ctx)}: attendees can join with the code {ctx.event.event_code} or the QR code.'


@safe
def unpublish_event(ctx):
    return f'Move {_name(ctx)} back to draft: attendees can no longer open it in the app.'


@safe
def archive_event(ctx):
    return f'Archive {_name(ctx)}: attendees see it as ended.'


@safe
def mark_paid(ctx):
    if ctx.args.get('paid'):
        return f'Mark {_name(ctx)} paid: the 20-phone free limit goes and it can be listed nearby.'
    return f'Mark {_name(ctx)} unpaid: the 20-phone free limit applies again.'


@safe
def list_event(ctx):
    return f'List {_name(ctx)} publicly: people near it can find it in the app’s “Find nearby events”.'


@safe
def delete_event(ctx):
    event = ctx.event
    return (f'Permanently delete {_name(ctx)} ({event.event_code}) with its {_count(event.location_set.count(), "location")}, '
            f'{_count(event.scheduleditem_set.count(), "schedule item")} and updates. This can’t be undone.')


def build_delete_event(args, ctx):
    return {'confirm_code': ctx.event.event_code if ctx.event else ''}


# --- content -------------------------------------------------------------------

@safe
def delete_location(ctx):
    from events.models import Location
    loc = Location.objects.get(pk=ctx.args['location_id'], event_id=ctx.args['event_id'])
    n = loc.scheduleditem_set.count()
    return (f'Delete the location “{loc.name}” from {_name(ctx)}'
            + (f'; its {_count(n, "schedule item")} will have no location.' if n else '.'))


@safe
def delete_item(ctx):
    from events.models import ScheduledItem
    item = ScheduledItem.objects.get(pk=ctx.args['item_id'], event_id=ctx.args['event_id'])
    when = timezone.localtime(item.start_time).strftime('%a %b %-d, %-I:%M %p')
    return f'Delete the schedule item “{item.name}” ({when}) from {_name(ctx)}.'


@safe
def restore_version(ctx):
    from events.models import EventRevision
    r = EventRevision.objects.get(pk=ctx.args['revision'], event_id=ctx.args['event_id'])
    return (f'Restore {_name(ctx)} to the version from {timezone.localtime(r.created_at):%b %-d, %-I:%M %p} '
            f'(“{r.summary}”). The current content is snapshotted first.')


def _changeset(user, changeset_id, missing_ok=False):
    """An AI review on one of the person's events only (a confirm card never names another event's review)."""
    from events.models import Event
    from scrape.models import ChangeSet
    found = ChangeSet.objects.filter(pk=changeset_id, event__in=Event.objects.for_user(user)).first() \
        if str(changeset_id or '').isdigit() else None
    if found is None and not missing_ok:
        raise ChangeSet.DoesNotExist
    return found


@safe
def discard_changes(ctx):
    from scrape.models import Change, ChangeSet
    cs = _changeset(ctx.user, ctx.args['changeset_id'])
    n = cs.changes.filter(status=Change.PENDING).count()
    return f'Discard all {_count(n, "AI suggestion")} for “{cs.event.name}”.'


def build_apply_changes(args, ctx):
    from scrape.models import Change, ChangeSet
    cs = _changeset(ctx.user, args.get('changeset_id'), missing_ok=True)
    data = {}
    if cs is None:
        return data
    wanted = {int(i) for i in (args.get('accept_change_ids') or []) if str(i).isdigit()}
    # Fields a person edited by hand (the change's conflict_fields) are never taken unless they were ticked on the
    # card (accept_fields) or the person asked to replace their own edits (replace_my_edits, P0 #7).
    raw = args.get('accept_fields')
    ticked = {str(k): set(v or []) for k, v in raw.items()} if isinstance(raw, dict) else {}
    replace = bool(args.get('replace_my_edits'))
    for ch in cs.changes.filter(status=Change.PENDING, pk__in=wanted):
        if ch.op != Change.OP_UPDATE:
            data[f'accept_{ch.id}'] = 'on'
            continue
        if str(ch.id) in ticked:
            fields = [f for f in ch.data if f in ticked[str(ch.id)]]
        else:
            fields = [f for f in ch.data if replace or f not in (ch.conflict_fields or [])]
        if fields:
            data[f'accept_{ch.id}'] = 'on'
            for f in fields:
                data[f'field_{ch.id}_{f}'] = 'on'
    if args.get('notify_attendees'):
        data['notify_attendees'] = 'on'
    return data


@safe
def apply_changes(ctx):
    from scrape.models import ChangeSet
    cs = _changeset(ctx.user, ctx.args['changeset_id'])
    n = len(ctx.args.get('accept_change_ids') or [])
    extra = ' and tell attendees their app was updated' if ctx.args.get('notify_attendees') else ''
    return f'Apply {_count(n, "AI suggestion")} to “{cs.event.name}”{extra}.'


# --- team ------------------------------------------------------------------------

def _member(ctx):
    from events.models import EventMember
    return EventMember.objects.select_related('user').get(pk=ctx.args['member'], event_id=ctx.args['event_id'])


@safe
def invite(ctx):
    role = ctx.args.get('role') or 'contributor'
    return (f'Give {ctx.args.get("email")} access to {_name(ctx)} as {role}. If they have no account yet they get an '
            'invitation email.')


@safe
def remove_member(ctx):
    return f'Remove {_member(ctx).user.email} from the team of {_name(ctx)}.'


@safe
def change_role(ctx):
    return f'Make {_member(ctx).user.email} {ctx.args.get("role")} of {_name(ctx)}.'


@safe
def transfer(ctx):
    return f'Make {_member(ctx).user.email} the owner of {_name(ctx)}. You stay on the team as an admin.'


@safe
def leave(ctx):
    return f'Leave the team of {_name(ctx)}. You lose access to it.'


@safe
def assign_owner(ctx):
    return f'Make {ctx.args.get("email")} the owner of {_name(ctx)}.'


@safe
def cancel_invite(ctx):
    from events.models import EventInvite
    inv = EventInvite.objects.get(pk=ctx.args['invite'], event_id=ctx.args['event_id'])
    return f'Cancel the invitation for {inv.email} to {_name(ctx)}.'


# --- Studio ----------------------------------------------------------------------

@safe
def studio_publish(ctx):
    site = getattr(ctx.event, 'studio_site', None)
    where = site.public_url() if site else 'its address'
    text = f'Publish the Studio website of {_name(ctx)} at {where}, and apply its app look and flyer to the app.'
    if str(ctx.args.get('confirm_rights')).lower() in ('1', 'true', 'on'):
        from studio.safety import RIGHTS_TEXT
        text += f' You confirm: “{RIGHTS_TEXT}”'
    return text


@safe
def studio_unpublish(ctx):
    return f'Take the Studio website of {_name(ctx)} offline: visitors will see “Not found”.'


@safe
def studio_settings(ctx):
    action = ctx.args.get('action')
    if action == 'reset_previews':
        return f'Stop every private preview link shared for the Studio website of {_name(ctx)}.'
    if ctx.args.get('slug'):
        return (f'Change the website address of {_name(ctx)} to “{ctx.args["slug"]}” '
                '(the old address stops working).')
    return default(ctx)


# --- prospects / diagnostics -----------------------------------------------------

@safe
def create_test_events(ctx):
    n = len(ctx.args.get('ids') or [])
    return (f'Import {_count(n, "prospect")} as testing-only events (AI import from their websites, about '
            '$0.05–0.20 each). Nobody is invited or emailed.')


@safe
def create_test_event(ctx):
    from prospects.models import Prospect
    p = Prospect.objects.get(pk=ctx.args['pk'])
    return f'Import “{p.name}” as a testing-only event (AI import from its website). Nobody is invited or emailed.'


@safe
def studio_restore(ctx):
    return (f'Make a copy of website version {ctx.args.get("version")} the draft of {_name(ctx)}'
            ' (the live site stays as it is until you publish).')


@safe
def studio_asset_delete(ctx):
    return f'Delete a file from the website library of {_name(ctx)}.'


@safe
def studio_entry_delete(ctx):
    return f'Delete one website form entry of {_name(ctx)}.'


@safe
def studio_review(ctx):
    from studio.models import Site
    site = Site.objects.filter(pk=ctx.args.get('site') or 0).select_related('event').first()
    name = f'“{site.event.name}” ({site.slug})' if site else 'this site'
    if ctx.args.get('action') == 'approve':
        return f'Approve the held website {name}: its version goes live now.'
    return f'Reject the held website {name}: it stays offline and its owner is emailed.'


@safe
def send_receipt(ctx):
    return f'Email {ctx.args.get("invoice") or "the receipt"} of {_name(ctx)} (with its PDF) to {ctx.args.get("email") or "that address"}.'
