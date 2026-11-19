"""Fill in tags for existing events with the AI (API-010).

    python manage.py backfill_tags DEMO 42            # events by code or id
    python manage.py backfill_tags --all              # every event that isn't a draft
    python manage.py backfill_tags --all --dry-run    # show what would change, save nothing
    heroku run -a attnd-dev python manage.py backfill_tags --all

Safe to re-run: only locations and schedule items with fewer than 2 tags are sent to the AI, and their existing
tags are kept (the AI's are added after them). Anything whose tags someone edited by hand (``tags`` in
``locked_fields``) is never touched. Every stored tag list is also normalised (lower case). Each changed event
gets one history snapshot and one content_version bump, so open apps refresh once.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from events.content import batch_changes, save_revision
from events.models import Event, Location, ScheduledItem, normalize_tags
from scrape.tags import AI_MAX_TAGS, AI_MIN_TAGS, VOCABULARY_HINT, Vocabulary, event_vocabulary

BATCH = 120  # things per AI request


def _locked(obj):
    return 'tags' in (obj.locked_fields or [])


class Command(BaseCommand):
    help = 'Add AI tags to existing locations and schedule items (skips hand-edited tags; safe to re-run).'

    def add_arguments(self, parser):
        parser.add_argument('events', nargs='*', help='Event codes or ids')
        parser.add_argument('--all', action='store_true', help='Every event except drafts')
        parser.add_argument('--include-drafts', action='store_true')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, events, all, include_drafts, dry_run, **options):
        if not events and not all:
            raise CommandError('Name events (code or id) or pass --all.')
        self.verbosity = options.get('verbosity', 1)
        qs = Event.objects.all()
        if events:
            ids = [int(e) for e in events if e.isdigit()]
            codes = [e.upper() for e in events if not e.isdigit()]
            qs = qs.filter(Q(pk__in=ids) | Q(event_code__in=codes))
        elif not include_drafts:
            qs = qs.exclude(status=Event.STATUS_DRAFT)
        total = failed = 0
        for event in qs.order_by('pk'):
            try:
                total += self.backfill(event, dry_run)
            except Exception as exc:  # one event failing must not stop the rest
                failed += 1
                self.stderr.write(f'{event.event_code}: failed: {exc}')
        self.stdout.write(self.style.SUCCESS(f'{"Would change" if dry_run else "Changed"} {total} tag lists.'))
        if failed:
            raise CommandError(f'{failed} event(s) failed; re-run to retry them (finished events are skipped).')

    def backfill(self, event, dry_run):
        from scrape.service import suggest_tags

        locations = list(event.location_set.all())
        items = list(event.scheduleditem_set.select_related('location'))
        objs = {('location', o.pk): o for o in locations} | {('item', o.pk): o for o in items}
        new_tags = {}
        for key, obj in objs.items():  # normalising alone (e.g. "Music" -> "music")
            if not _locked(obj) and normalize_tags(obj.tags) != (obj.tags or []):
                new_tags[key] = normalize_tags(obj.tags)

        todo = [key for key, obj in objs.items()
                if not _locked(obj) and len(normalize_tags(obj.tags)) < AI_MIN_TAGS]
        vocab_seed = event_vocabulary(event)
        vocab = Vocabulary(vocab_seed)
        info = {'name': event.name, 'description': event.description}
        for start in range(0, len(todo), BATCH):
            chunk = todo[start:start + BATCH]
            entries = []
            for kind, pk in chunk:
                obj = objs[(kind, pk)]
                entry = {'kind': kind, 'id': pk, 'name': obj.name, 'description': obj.description[:300],
                         'current_tags': normalize_tags(obj.tags)}
                if kind == 'location':
                    entry['category'] = obj.category
                    entry['snippet'] = obj.snippet
                else:
                    entry['location'] = obj.location.name if obj.location_id else None
                entries.append(entry)
            result = suggest_tags(info, entries, vocab.tags[:VOCABULARY_HINT] or vocab_seed[:VOCABULARY_HINT])
            vocab = Vocabulary(vocab.tags + list(result['tag_vocabulary']))
            for key in chunk:
                if key not in result['tags']:
                    continue
                merged = vocab.canonical(normalize_tags(objs[key].tags) + list(result['tags'][key]),
                                         limit=AI_MAX_TAGS)
                if merged != (objs[key].tags or []):
                    new_tags[key] = merged

        if not new_tags:
            self.stdout.write(f'{event.event_code}: nothing to change.')
            return 0
        if self.verbosity > 1 or dry_run:
            for (kind, pk), tags in sorted(new_tags.items()):
                self.stdout.write(f'  {kind} {objs[(kind, pk)].name!r}: {objs[(kind, pk)].tags} -> {tags}')
        if not dry_run:
            with transaction.atomic():
                save_revision(event, 'Before adding AI tags')
                with batch_changes(event):
                    for (kind, pk), tags in new_tags.items():
                        model = Location if kind == 'location' else ScheduledItem
                        # Re-check the lock inside the transaction: someone may have edited it meanwhile.
                        obj = model.objects.select_for_update().filter(pk=pk, event=event).first()
                        if obj is None or _locked(obj):
                            continue
                        obj.tags = tags
                        obj.save(update_fields=['tags'])
        self.stdout.write(f'{event.event_code}: {"would change" if dry_run else "changed"} {len(new_tags)} tag lists.')
        return len(new_tags)
