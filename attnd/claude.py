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


def adapt_schema(schema):
    """A JSON schema as Anthropic's structured output accepts it (agent.providers.claude_schema: nullable enums as
    anyOf, no numeric / length / pattern keywords)."""
    from agent.providers import claude_schema
    return claude_schema(schema)


def document_block(data, media_type='application/pdf', name=''):
    import base64
    block = {'type': 'document', 'source': {'type': 'base64', 'media_type': media_type,
                                            'data': base64.standard_b64encode(data).decode('ascii')}}
    if name:
        block['title'] = name[:200]
    return block


def _create(api, kwargs):
    """messages.create, streamed for long answers (the SDK wants streaming when max_tokens is large)."""
    if kwargs.get('max_tokens', 0) > 16000:
        with api.messages.stream(**kwargs) as stream:
            return stream.get_final_message()
    return api.messages.create(**kwargs)


# Schemas the API refused as structured output in this process (too large a grammar): go straight to the tool.
_TOO_LARGE = set()
MAX_OUTPUT = 64000


def _schema_key(schema):
    import hashlib
    return hashlib.sha256(json.dumps(schema, sort_keys=True, default=str).encode()).hexdigest()


def parse_json(text):
    """The first JSON object in a model's text (``` fences, prose around it), or raises ValueError."""
    text = (text or '').strip()
    start = text.find('{')
    if start < 0:
        raise ValueError('no object')
    obj, _end = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(obj, dict):
        raise ValueError('not an object')
    return obj


def _request_id(response):
    return getattr(response, '_request_id', None) or getattr(response, 'id', '') or ''


def structured(*, system, content, schema, model=None, effort='low', max_tokens=4096, timeout=90.0, api=None, meta=None):
    """(parsed JSON, cost in USD). `content` is the user message: text or a list of content blocks.

    1. Structured output (`output_config.format` json_schema): the answer is guaranteed to match.
    2. When the API refuses the schema itself (e.g. "the compiled grammar is too large" for the import's event schema),
       the schema becomes the input schema of one `submit` tool the model must call: tool input is always a JSON
       object (no fences, no prose around it). Remembered per process, so later calls skip step 1.
    `stop_reason`: max_tokens → once more with twice the room (≤ 64K); refusal → a clear error. Every failure is logged
    with the request id."""
    import anthropic
    if api is None and not available():
        raise ClaudeError('No ANTHROPIC_API_KEY.')
    model = model or fast_model()
    api = api or client(timeout)
    key = _schema_key(schema)
    base = dict(model=model, max_tokens=max_tokens, messages=[{'role': 'user', 'content': content}])
    cost = Decimal(0)

    def with_format():
        return dict(base, system=[{'type': 'text', 'text': system, 'cache_control': {'type': 'ephemeral'}}],
                    output_config={'effort': effort, 'format': {'type': 'json_schema', 'schema': adapt_schema(schema)}})

    def with_tool():
        return dict(base, system=[{'type': 'text', 'cache_control': {'type': 'ephemeral'},
                                   'text': system + '\n\nGive your answer by calling the submit tool exactly once with '
                                                    'the complete result. Don\'t answer in text.'}],
                    output_config={'effort': effort},
                    tools=[{'name': 'submit', 'description': 'Submit the complete answer.',
                            'input_schema': adapt_schema(schema)}],
                    tool_choice={'type': 'auto'})

    def call(kwargs):
        nonlocal cost
        try:
            response = _create(api, kwargs)
        except anthropic.APIError as exc:
            raise exc
        cost += cost_of(model, response.usage)
        if meta is not None:  # (callers that log usage themselves)
            meta.update(model=model, input=(getattr(response.usage, 'input_tokens', 0) or 0)
                        + (getattr(response.usage, 'cache_read_input_tokens', 0) or 0),
                        output=getattr(response.usage, 'output_tokens', 0) or 0,
                        cached=getattr(response.usage, 'cache_read_input_tokens', 0) or 0, cost=cost)
        return response

    def run(build):
        kwargs = build()
        response = call(kwargs)
        if response.stop_reason == 'max_tokens' and kwargs['max_tokens'] < MAX_OUTPUT:
            logger.warning('Claude ran out of room (%s output tokens, request %s): once more with twice the room',
                           kwargs['max_tokens'], _request_id(response))
            kwargs['max_tokens'] = min(MAX_OUTPUT, kwargs['max_tokens'] * 2)
            response = call(kwargs)
        return response

    try:
        if key in _TOO_LARGE:
            response = run(with_tool)
        else:
            try:
                response = run(with_format)
            except anthropic.BadRequestError as exc:
                text = str(getattr(exc, 'message', exc)).lower()
                if not any(w in text for w in ('schema', 'grammar', 'output_config', 'format')):
                    raise
                logger.warning('Structured output refused (%s): answering through a submit tool instead', text[:120])
                _TOO_LARGE.add(key)
                response = run(with_tool)
    except anthropic.APIError as exc:
        logger.warning('Claude call failed: %s (request %s)', exc.__class__.__name__, getattr(exc, 'request_id', ''))
        raise ClaudeError(f'Claude: {exc.__class__.__name__}: {getattr(exc, "message", exc)}'[:300]) from exc

    rid = _request_id(response)
    if response.stop_reason == 'refusal':
        logger.warning('Claude declined (request %s)', rid)
        raise ClaudeError('Claude declined to read this')
    if response.stop_reason == 'max_tokens':
        logger.warning('Claude ran out of room twice (request %s)', rid)
        raise ClaudeError('the answer was too long for one reply; try fewer sources')
    for block in response.content:
        if block.type == 'tool_use' and block.name == 'submit' and isinstance(block.input, dict):
            return block.input, cost
    text = ''.join(b.text for b in response.content if b.type == 'text')
    try:
        return parse_json(text), cost
    except ValueError as exc:
        logger.warning('Claude returned no JSON (stop %s, request %s): %r', response.stop_reason, rid, text[:200])
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
