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


@safe
def discard_changes(ctx):
    from scrape.models import Change, ChangeSet
    cs = ChangeSet.objects.get(pk=ctx.args['changeset_id'])
    n = cs.changes.filter(status=Change.PENDING).count()
    return f'Discard all {_count(n, "AI suggestion")} for “{cs.event.name}”.'


def build_apply_changes(args, ctx):
    from scrape.models import Change, ChangeSet
    cs = ChangeSet.objects.filter(pk=args.get('changeset_id')).first()
    data = {}
    if cs is None:
        return data
    wanted = {int(i) for i in (args.get('accept_change_ids') or []) if str(i).isdigit()}
    for ch in cs.changes.filter(status=Change.PENDING, pk__in=wanted):
        data[f'accept_{ch.id}'] = 'on'
        if ch.op == Change.OP_UPDATE:
            for f in ch.data:
                data[f'field_{ch.id}_{f}'] = 'on'
    if args.get('notify_attendees'):
        data['notify_attendees'] = 'on'
    return data


@safe
def apply_changes(ctx):
    from scrape.models import ChangeSet
    cs = ChangeSet.objects.get(pk=ctx.args['changeset_id'])
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
    return f'Publish the Studio website of {_name(ctx)} at {where}, and apply its app look and flyer to the app.'


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
