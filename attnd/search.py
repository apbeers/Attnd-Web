"""Postgres search for the console and Ask Attnd (F3): full-text (English stemming, weighted fields) plus pg_trgm
word similarity for typos and partial words, in one query. pg_trgm is created by diagnostics' migration 0001.

    ranked = search(Event.objects.all(), 'taco stage', {'name': 'A', 'event_code': 'A', 'description': 'C'})
"""

from django.contrib.postgres.search import (
    SearchQuery,
    SearchRank,
    SearchVector,
    TrigramWordSimilarity,
)
from django.db.models import F, FloatField, Q, TextField, Value
from django.db.models.functions import Coalesce, Greatest

MIN_SIMILARITY = 0.3


def search(qs, query, fields, similarity=MIN_SIMILARITY):
    """`qs` filtered to rows matching `query` (full text on every field, or a trigram word similarity of at least
    `similarity` on any field), best first. `fields` = {lookup: weight 'A'…'D'}; lookups may cross relations (rows
    are made distinct)."""
    query = (query or '').strip()
    if not query:
        return qs
    vector = None
    for field, weight in fields.items():
        part = SearchVector(Coalesce(F(field), Value(''), output_field=TextField()), weight=weight, config='english')
        vector = part if vector is None else vector + part
    terms = SearchQuery(query, search_type='websearch', config='english')
    similarities = [TrigramWordSimilarity(query, Coalesce(F(field), Value(''), output_field=TextField())) for field in fields]
    best = similarities[0] if len(similarities) == 1 else Greatest(*similarities)
    annotated = qs.annotate(_vector=vector, _rank=SearchRank(vector, terms), _similar=best)
    matched = annotated.filter(Q(_vector=terms) | Q(_similar__gte=similarity))  # ts_rank is never 0
    scored = matched.annotate(_score=Coalesce(F('_rank'), 0.0, output_field=FloatField()) + F('_similar'))
    ids = list(dict.fromkeys(scored.order_by('-_score').values_list('pk', flat=True)))
    order = {pk: i for i, pk in enumerate(ids)}
    return sorted(qs.model.objects.filter(pk__in=ids), key=lambda row: order[row.pk]) if ids else []


def ids(qs, query, fields, similarity=MIN_SIMILARITY):
    """Primary keys of the matches, best first."""
    if not (query or "").strip():
        return []
    return [row.pk for row in search(qs, query, fields, similarity)]
