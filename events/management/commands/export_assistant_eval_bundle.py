"""Export the DEMO event's bundle at a fixed moment, for the event assistant eval (docs/assistant_eval/).

    uv run python manage.py export_assistant_eval_bundle            # → docs/assistant_eval/demo_bundle.json
    uv run python manage.py export_assistant_eval_bundle --now 2026-11-14T22:00:00Z --out /tmp/demo.json

It runs `load_demo_event` with the clock frozen at `--now` inside a transaction that is rolled back, so nothing in
the database changes, then writes the attendee bundle (`content.bundle`, catalogue version 2) with deterministic ids:
locations 1… in their order, schedule items 1001… by start time, notifications 9001… by send time, so the file only
changes when the DEMO content does. The question catalogue (questions.json) names entities, never ids, and its
`fixture.now` must equal the `--now` used here. iOS and Android load this file as their eval fixture.
"""
import json
from datetime import datetime
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.test import RequestFactory

DEFAULT_NOW = '2026-11-14T22:00:00+00:00'  # Sat Nov 14, 2026, 4:00 PM in Fort Worth
DEFAULT_OUT = Path(settings.BASE_DIR) / 'docs' / 'assistant_eval' / 'demo_bundle.json'


class _Rollback(Exception):
    pass


def renumber(bundle):
    """Stable ids: what the apps and the catalogue see never depends on database sequences."""
    loc_ids = {loc['id']: n for n, loc in enumerate(bundle['locations'], start=1)}
    items = sorted(bundle['schedule'], key=lambda i: (i['start_time'], i.get('location_name') or '', i['name']))
    item_ids = {item['id']: 1001 + n for n, item in enumerate(items)}
    notes = sorted(bundle['notifications'], key=lambda n: (n.get('send_time') or '', n['title']))
    note_ids = {note['id']: 9001 + n for n, note in enumerate(notes)}
    for loc in bundle['locations']:
        loc['id'] = loc_ids[loc['id']]
    for item in items:
        item['id'] = item_ids[item['id']]
        if item.get('location_id') is not None:
            item['location_id'] = loc_ids.get(item['location_id'])
    bundle['schedule'] = items
    for note in notes:
        note['id'] = note_ids[note['id']]
    bundle['notifications'] = notes
    ctx = (bundle.get('event') or {}).get('context') or {}
    for amenity in ctx.get('amenities') or []:
        if amenity.get('location_id') is not None:
            amenity['location_id'] = loc_ids.get(amenity['location_id'])
    bundle['event']['id'] = 42
    return bundle


def strip_volatile(value):
    """Drop per-server values (hosts in URLs stay attnd.app; ETags and server times don't belong in a fixture)."""
    if isinstance(value, dict):
        return {k: strip_volatile(v) for k, v in value.items() if k not in ('generated_at', 'server_time')}
    if isinstance(value, list):
        return [strip_volatile(v) for v in value]
    return value


class Command(BaseCommand):
    help = 'Write the DEMO bundle at a fixed moment for the event assistant eval (nothing is saved).'

    def add_arguments(self, parser):
        parser.add_argument('--now', default=DEFAULT_NOW, help='ISO time the DEMO event is built around')
        parser.add_argument('--out', default=str(DEFAULT_OUT))

    def handle(self, *args, now, out, **options):
        from events import content
        from events.models import Event

        frozen = datetime.fromisoformat(now.replace('Z', '+00:00'))
        bundle = None
        with mock.patch('django.utils.timezone.now', return_value=frozen):
            try:
                with transaction.atomic():
                    call_command('load_demo_event', '--code', 'EVALDEMO', '--devices', '0', stdout=self.stdout)
                    event = Event.objects.get(event_code='EVALDEMO')
                    request = RequestFactory().get('/api/v2/', secure=True, HTTP_HOST='attnd.app')
                    raw = json.dumps(content.bundle(event, request, 2), cls=DjangoJSONEncoder)
                    bundle = json.loads(raw)
                    raise _Rollback
            except _Rollback:
                pass
        bundle['event']['event_code'] = 'DEMO'
        bundle['event']['join_url'] = 'https://attnd.app/join/DEMO/'
        bundle = strip_volatile(renumber(bundle))
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(bundle, indent=1, ensure_ascii=False, sort_keys=True) + '\n')
        self.stdout.write(self.style.SUCCESS(
            f'Wrote {out}: {len(bundle["locations"])} locations, {len(bundle["schedule"])} schedule items, '
            f'{len(bundle["notifications"])} notifications, now = {frozen.isoformat()}.'))
