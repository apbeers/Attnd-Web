"""Tags for locations and schedule items (API-010).

Tags are short lower-case labels ("live music", "country", "bbq", "kid-friendly", "free") that attendees filter
and search by, so one concept must get one spelling across an event. The AI is shown the event's existing
vocabulary and asked to reuse it; ``Vocabulary`` then folds near-duplicates the AI still produces ("Kids
friendly", "kid friendly", "BBQ", "bbqs") onto the spelling the event already uses.
"""
from collections import Counter

from events.models import normalize_tags

AI_MIN_TAGS, AI_MAX_TAGS = 2, 6
VOCABULARY_HINT = 40  # how many existing tags are shown to the AI


def tag_key(tag):
    """Spelling-insensitive identity of a tag: '&' = 'and', hyphens/underscores/slashes = spaces, simple plurals."""
    text = ' '.join(str(tag).lower().replace('&', ' and ').replace('-', ' ').replace('_', ' ')
                    .replace('/', ' ').split())
    words = []
    for word in text.split():
        if len(word) > 3 and word.endswith('ies'):
            word = word[:-3] + 'y'
        elif len(word) > 3 and word.endswith('s') and not word.endswith(('ss', 'us', 'is')):
            word = word[:-1]
        words.append(word)
    return ' '.join(words)


def event_vocabulary(event):
    """The event's tags, most used first (locations and schedule items together)."""
    counts = Counter()
    for tags in list(event.location_set.values_list('tags', flat=True)) + \
            list(event.scheduleditem_set.values_list('tags', flat=True)):
        counts.update(normalize_tags(tags))
    return [tag for tag, _ in counts.most_common()]


class Vocabulary:
    """Maps tags onto one spelling per concept: the first spelling seen wins (seed it with the event's own)."""

    def __init__(self, seed=()):
        self._by_key = {}
        for raw in seed:
            for tag in normalize_tags([raw]):
                self._by_key.setdefault(tag_key(tag), tag)

    def canonical(self, tags, limit=AI_MAX_TAGS):
        out = []
        for tag in normalize_tags(tags):
            tag = self._by_key.setdefault(tag_key(tag), tag)
            if tag not in out:
                out.append(tag)
        return out[:limit]

    @property
    def tags(self):
        return list(self._by_key.values())


def canonicalize_extraction(data, seed=()):
    """Normalise every tag in an AI extraction (locations and schedule) against one vocabulary, in place."""
    vocab = Vocabulary(list(seed) + list(data.get('tag_vocabulary') or []))
    for entry in list(data.get('locations') or []) + list(data.get('schedule') or []):
        entry['tags'] = vocab.canonical(entry.get('tags') or [])
    data['tag_vocabulary'] = vocab.tags
    return data
