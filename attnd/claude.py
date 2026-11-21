"""One small helper for Claude calls outside Ask Attnd's own loop (official `anthropic` SDK).

    data, cost = claude.structured(system=PROMPT, content='…', schema=SCHEMA)

Structured output (`output_config.format` = a JSON schema), a cached system prompt, one effort for every call of a
feature, and the cost in USD from `studio.ai.token_cost` (cache reads at a tenth, writes ×1.25). Raises
`ClaudeError` for anything that isn't a usable answer (no key, API errors, refusals, cut-off output), so callers
fall back to their non-AI path.
"""

import json
import logging
from decimal import Decimal

from django.conf import settings

logger = logging.getLogger(__name__)


class ClaudeError(RuntimeError):
    pass


def available():
    return bool(getattr(settings, 'ANTHROPIC_API_KEY', ''))


def fast_model():
    """Claude Haiku: imports, website checks, tags, vision, form fills, scores."""
    return getattr(settings, 'AI_MODEL', '') or 'claude-haiku-5-5'


def smart_model():
    """Claude Sonnet: where Haiku can't (web search) or as the stronger fallback."""
    return getattr(settings, 'AI_SMART_MODEL', '') or 'claude-sonnet-5-5'


def client(timeout=60.0):
    import anthropic
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=timeout, max_retries=2)


def cost_of(model, usage):
    from studio.ai import token_cost
    cached = getattr(usage, 'cache_read_input_tokens', 0) or 0
    written = getattr(usage, 'cache_creation_input_tokens', 0) or 0
    return token_cost(model, (getattr(usage, 'input_tokens', 0) or 0) + cached + written,
                      getattr(usage, 'output_tokens', 0) or 0, cached_tokens=cached, cache_write_tokens=written)


def image_block(data, media_type):
    import base64
    return {'type': 'image', 'source': {'type': 'base64', 'media_type': media_type,
                                        'data': base64.standard_b64encode(data).decode('ascii')}}


def structured(*, system, content, schema, model=None, effort='low', max_tokens=4096, timeout=90.0, api=None):
    """(parsed JSON, cost in USD). `content` is the user message: text or a list of content blocks."""
    import anthropic
    if api is None and not available():
        raise ClaudeError('No ANTHROPIC_API_KEY.')
    model = model or fast_model()
    api = api or client(timeout)
    try:
        response = api.messages.create(
            model=model, max_tokens=max_tokens,
            system=[{'type': 'text', 'text': system, 'cache_control': {'type': 'ephemeral'}}],
            messages=[{'role': 'user', 'content': content}],
            output_config={'effort': effort, 'format': {'type': 'json_schema', 'schema': schema}},
        )
    except anthropic.APIError as exc:
        raise ClaudeError(f'Claude: {exc.__class__.__name__}: {getattr(exc, "message", exc)}'[:300]) from exc
    cost = cost_of(model, response.usage)
    if response.stop_reason != 'end_turn':
        raise ClaudeError(f'Claude stopped: {response.stop_reason}')
    text = next((b.text for b in response.content if b.type == 'text'), '')
    try:
        return json.loads(text), cost
    except ValueError as exc:
        raise ClaudeError('Claude returned no JSON.') from exc


def message(*, system, messages, model=None, effort='low', max_tokens=4096, tools=None, timeout=120.0, api=None,
            **extra):
    """A plain Messages call (tools such as web search): (response, cost in USD)."""
    import anthropic
    if api is None and not available():
        raise ClaudeError('No ANTHROPIC_API_KEY.')
    model = model or fast_model()
    api = api or client(timeout)
    kwargs = dict(model=model, max_tokens=max_tokens, messages=messages,
                  system=[{'type': 'text', 'text': system, 'cache_control': {'type': 'ephemeral'}}],
                  output_config={'effort': effort, **extra.pop('output_config', {})}, **extra)
    if tools:
        kwargs['tools'] = tools
    try:
        response = api.messages.create(**kwargs)
    except anthropic.APIError as exc:
        raise ClaudeError(f'Claude: {exc.__class__.__name__}: {getattr(exc, "message", exc)}'[:300]) from exc
    return response, cost_of(model, response.usage)


def nullable(schema):
    """A JSON schema that also allows null (structured output: anyOf)."""
    return {'anyOf': [schema, {'type': 'null'}]}


ZERO = Decimal('0')
