"""Studio's AI provider layer: OpenAI (the existing SDK) and Anthropic (the official SDK), images (OpenAI),
vision descriptions, cost logging and the per-event daily spend cap.

Both chat providers expose the same small interface for the agent loop (studio/agent.py):

    provider = get_provider()
    messages = provider.start(user_text)
    turn = provider.chat(system, messages, TOOLS)      # Turn(text, calls=[ToolCall(id, name, args)], usage)
    provider.add_turn(messages, turn)
    provider.add_results(messages, [(call, "result text"), ...])

OpenAI goes through the Responses API (function tools + reasoning; Chat Completions refuses that combination on
gpt-5.5), Anthropic through the Messages API.
"""

import base64
import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

logger = logging.getLogger(__name__)


class AIError(Exception):
    """Shown to the organizer as is."""


class BudgetExceeded(AIError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Turn:
    text: str = ''
    calls: list = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ''
    cached_tokens: int = 0  # part of input_tokens read from the provider's prompt cache (billed lower)
    raw: object = None  # provider-native assistant message, appended back as is


# ---------------------------------------------------------------------------
# Cost and the spend cap
# ---------------------------------------------------------------------------

def price(model):
    prices = settings.STUDIO_PRICES
    return prices.get(model) or prices['default']


CACHED_INPUT_FACTOR = 0.1  # cached input tokens cost a tenth of fresh ones (OpenAI and Anthropic cache reads)


CACHE_WRITE_FACTOR = 1.25  # Anthropic: 5-minute cache writes cost 1.25x fresh input


def token_cost(model, input_tokens, output_tokens, cached_tokens=0, cache_write_tokens=0):
    per_in, per_out = price(model)
    cached = min(max(cached_tokens, 0), input_tokens)
    written = min(max(cache_write_tokens, 0), input_tokens - cached)
    fresh = input_tokens - cached - written
    return Decimal(str(round((fresh + cached * CACHED_INPUT_FACTOR + written * CACHE_WRITE_FACTOR) * per_in / 1_000_000
                             + output_tokens * per_out / 1_000_000, 4)))


def spent_today(event):
    from studio.models import StudioUsage
    start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    total = StudioUsage.objects.filter(event=event, created_at__gte=start).aggregate(t=Sum('cost_usd'))['t']
    return total or Decimal('0')


def daily_cap(event=None):
    """Studio's AI per event per UTC day: the owner's per-event override, else the AI limits' value."""
    if event is not None:
        from platform_admin.models import EventOverride
        override = EventOverride.of(event)
        if override is not None and override.studio_daily_cap_usd is not None:
            return Decimal(override.studio_daily_cap_usd)
    from credit.ledger import limits
    return Decimal(limits().studio_daily_usd)


def budget_left(event):
    return daily_cap(event) - spent_today(event)


def check_budget(event):
    if budget_left(event) <= 0:
        raise BudgetExceeded(f'Today\'s AI budget for this event (${daily_cap(event):.2f}) is used up. '
                             f'It resets at midnight UTC; you can still edit the code by hand.')


def record_usage(event, run, kind, provider, model, input_tokens=0, output_tokens=0, images=0, cached_tokens=0):
    from studio.models import StudioUsage
    cost = token_cost(model, input_tokens, output_tokens, cached_tokens) + Decimal(str(settings.STUDIO_PRICES['image'])) * images
    StudioUsage.objects.create(event=event, run=run, kind=kind, provider=provider, model=model[:64],
                               input_tokens=input_tokens, output_tokens=output_tokens, images=images, cost_usd=cost)
    from credit import ledger  # (the images part is recorded by generate_image itself)
    if input_tokens or output_tokens:
        ledger.record(cost=token_cost(model, input_tokens, output_tokens, cached_tokens), model=model,
                      provider=provider, input_tokens=input_tokens, output_tokens=output_tokens, feature='studio',
                      user=getattr(run, 'created_by', None) if run is not None else None, event=event)
    return cost


def usage_summary(event, days=30):
    from studio.models import StudioUsage
    since = timezone.now() - timedelta(days=days)
    rows = StudioUsage.objects.filter(event=event, created_at__gte=since)
    agg = rows.aggregate(cost=Sum('cost_usd'), inp=Sum('input_tokens'), out=Sum('output_tokens'), img=Sum('images'))
    return {'today': spent_today(event), 'cap': daily_cap(event), 'month': agg['cost'] or 0,
            'input_tokens': agg['inp'] or 0, 'output_tokens': agg['out'] or 0, 'images': agg['img'] or 0}


# ---------------------------------------------------------------------------
# Chat providers
# ---------------------------------------------------------------------------

def provider_name():
    choice = settings.STUDIO_AI_PROVIDER
    if choice == 'auto':
        return 'anthropic' if settings.ANTHROPIC_API_KEY else 'openai'
    return choice


def get_provider(**options):
    """The chat provider. `options` (all optional, used by Ask Attnd): model, fallback_model, effort,
    parallel_tool_calls; Studio passes none and gets its own settings."""
    name = provider_name()
    if name == 'anthropic':
        if not settings.ANTHROPIC_API_KEY:
            raise AIError('STUDIO_AI_PROVIDER is anthropic but ANTHROPIC_API_KEY is not set.')
        return AnthropicProvider(effort=options.get('effort'))
    import os
    if not os.getenv('OPENAI_API_KEY'):
        raise AIError('No AI key is configured on the server (OPENAI_API_KEY or ANTHROPIC_API_KEY).')
    return OpenAIProvider(**options)


_unavailable_models = set()  # OpenAI models this account can't use (fell back once; remembered per process)

REASONING_PREFIXES = ('gpt-5', 'o1', 'o3', 'o4')


def model_unavailable(exc):
    """True when an OpenAI error says the model itself can't be used (not found / no access), so the fallback model
    is worth a try. Other 400s (bad parameters, too long) are real errors."""
    from openai import NotFoundError
    if isinstance(exc, NotFoundError):
        return True
    text = str(exc).lower()
    return getattr(exc, 'code', None) == 'model_not_found' or (
        'model' in text and any(w in text for w in ('does not exist', 'not found', 'do not have access',
                                                     'does not have access', 'invalid model', 'unsupported model')))


def _item_dict(item):
    """A Responses output item as a plain dict (SDK models, or anything with attributes in tests)."""
    if isinstance(item, dict):
        return item
    if hasattr(item, 'model_dump'):
        return item.model_dump(exclude_none=True, mode='json')
    return {k: v for k, v in vars(item).items() if v is not None}


class OpenAIProvider:
    """OpenAI through the Responses API (`client.responses.create`) with function tools and reasoning on.

    Chat Completions refuses function tools together with `reasoning_effort` on gpt-5.5, so Studio uses Responses.
    The conversation is chained with `previous_response_id` (responses are stored, the default): each call sends only
    the new input items (the request, or the `function_call_output`s for the last turn's `function_call`s); the
    reasoning items stay on OpenAI's side. `messages` keeps the full item list anyway (useful in logs and tests).
    `instructions` (the system prompt) is not carried over by previous_response_id, so it goes with every call.
    """
    name = 'openai'

    def __init__(self, model=None, fallback_model=None, effort='medium', parallel_tool_calls=True):
        self.model = model or settings.STUDIO_OPENAI_MODEL
        self.fallback_model = settings.STUDIO_OPENAI_FALLBACK_MODEL if fallback_model is None else fallback_model
        if self.model in _unavailable_models and self.fallback_model:
            self.model = self.fallback_model
        self.effort = effort or 'medium'  # reasoning effort; callers may change it between calls
        self.parallel_tool_calls = parallel_tool_calls
        self.previous_id = None
        self._sent = 0  # items of `messages` that OpenAI already has (through previous_response_id)

    @staticmethod
    def _tools(tools):
        return [{'type': 'function', 'name': t['name'], 'description': t['description'],
                 'parameters': t['parameters'], 'strict': bool(t.get('strict'))} for t in tools]

    def state(self):
        """What a paused conversation needs to continue later (in another thread): the chain position."""
        return {'previous_id': self.previous_id, 'sent': self._sent, 'model': self.model}

    def restore(self, state):
        self.previous_id, self._sent = state.get('previous_id'), int(state.get('sent') or 0)
        self.model = state.get('model') or self.model

    def start(self, user_text):
        self.previous_id, self._sent = None, 0
        return [{'role': 'user', 'content': user_text}]

    def chat(self, system, messages, tools):
        from openai import APIError, BadRequestError, NotFoundError, OpenAI
        client = OpenAI(timeout=900)
        kwargs = {'reasoning': {'effort': self.effort}} if self.model.startswith(REASONING_PREFIXES) else {}
        if self.previous_id:
            kwargs['previous_response_id'] = self.previous_id
        try:
            response = client.responses.create(
                model=self.model, instructions=system, input=list(messages[self._sent:]),
                tools=self._tools(tools), parallel_tool_calls=self.parallel_tool_calls, store=True, **kwargs)
        except (NotFoundError, BadRequestError) as exc:
            fallback = self.fallback_model
            if model_unavailable(exc) and fallback and self.model != fallback and not self.previous_id:
                logger.warning('AI model %s unavailable (%s); using %s', self.model, exc, fallback)
                _unavailable_models.add(self.model)
                self.model = fallback
                return self.chat(system, messages, tools)
            raise AIError(f'The AI request failed: {exc}') from exc
        except APIError as exc:
            raise AIError(f'The AI request failed: {exc}') from exc
        status = getattr(response, 'status', 'completed')
        if status == 'failed':
            error = getattr(response, 'error', None)
            raise AIError(f'The AI request failed: {getattr(error, "message", None) or "no details"}')
        output = list(getattr(response, 'output', None) or [])
        calls = []
        for item in output:
            if getattr(item, 'type', '') != 'function_call':
                continue
            try:
                args = json.loads(item.arguments or '{}')
            except json.JSONDecodeError:
                args = {'_invalid_json': (item.arguments or '')[:200]}
            calls.append(ToolCall(item.call_id, item.name, args if isinstance(args, dict) else {}))
        text = getattr(response, 'output_text', '') or ''
        if status == 'incomplete' and not calls and not text:
            reason = getattr(getattr(response, 'incomplete_details', None), 'reason', '') or 'incomplete'
            raise AIError(f'The AI stopped before answering ({reason}). Try a smaller request.')
        self.previous_id = response.id
        usage = getattr(response, 'usage', None)
        cached = getattr(getattr(usage, 'input_tokens_details', None), 'cached_tokens', 0) or 0
        return Turn(text=text, calls=calls, raw=[_item_dict(i) for i in output], model=self.model,
                    input_tokens=getattr(usage, 'input_tokens', 0) or 0,
                    output_tokens=getattr(usage, 'output_tokens', 0) or 0, cached_tokens=cached)

    def add_turn(self, messages, turn):
        messages.extend(turn.raw)  # function_call (and reasoning/message) items; OpenAI has them already
        self._sent = len(messages)

    def add_results(self, messages, results):
        for call, text in results:
            messages.append({'type': 'function_call_output', 'call_id': call.id, 'output': text})




class AnthropicProvider:
    """Claude through the official `anthropic` SDK (Messages API, streamed: long turns). Thinking is adaptive by
    default on the current models; effort high; refusals fall back server-side (`fallbacks: "default"`, beta)."""
    name = 'anthropic'

    def __init__(self, effort=None):
        self.model = settings.STUDIO_ANTHROPIC_MODEL
        self.effort = effort or 'high'

    def start(self, user_text):
        return [{'role': 'user', 'content': user_text}]

    def state(self):
        return {}  # the messages list holds the whole conversation

    def restore(self, state):
        pass

    def _post(self, body):
        """One streamed Messages call; returns the final message as a plain dict (blocks go back unchanged)."""
        import anthropic

        from attnd import claude
        try:
            api = claude.client(timeout=600.0)
            extra = {k: body.pop(k) for k in ('fallbacks',) if k in body}
            with api.messages.stream(**body, extra_body=extra,
                                     extra_headers={'anthropic-beta': 'server-side-fallback-2026-07-01'}) as stream:
                final = stream.get_final_message()
        except anthropic.APIStatusError as exc:
            raise AIError(f'The AI request failed ({exc.status_code}): {str(getattr(exc, "message", exc))[:500]}') from exc
        except anthropic.APIError as exc:
            raise AIError(f'Could not reach the AI service: {exc}') from exc
        return final.model_dump(mode='json', exclude_none=True)

    def chat(self, system, messages, tools):
        body = {
            'model': self.model, 'max_tokens': 32000, 'system': system, 'messages': self._cached(messages),
            'tools': [{'name': t['name'], 'description': t['description'], 'input_schema': t['parameters']}
                      for t in tools],
            'output_config': {'effort': self.effort}, 'fallbacks': 'default',
        }
        data = self._post(body)
        if data.get('stop_reason') == 'refusal':
            raise AIError('The AI declined this request. Try rephrasing it.')
        content = data.get('content') or []
        text = '\n'.join(b.get('text', '') for b in content if b.get('type') == 'text').strip()
        calls = [ToolCall(b['id'], b['name'], b.get('input') or {}) for b in content if b.get('type') == 'tool_use']
        usage = data.get('usage') or {}
        return Turn(text=text, calls=calls, model=data.get('model') or self.model,
                    raw={'role': 'assistant', 'content': content},
                    input_tokens=(usage.get('input_tokens') or 0) + (usage.get('cache_read_input_tokens') or 0)
                    + (usage.get('cache_creation_input_tokens') or 0),
                    cached_tokens=usage.get('cache_read_input_tokens') or 0,
                    output_tokens=usage.get('output_tokens') or 0)

    @staticmethod
    def _cached(messages):
        """The messages with a prompt-cache breakpoint on the newest block, so each step of the agent loop reads the
        conversation so far from the cache instead of paying for it again (the caller's list is not changed)."""
        if not messages:
            return messages
        last = dict(messages[-1])
        content = last.get('content')
        blocks = [{'type': 'text', 'text': content}] if isinstance(content, str) else [dict(b) for b in content or []]
        if not blocks:
            return messages
        blocks[-1]['cache_control'] = {'type': 'ephemeral'}
        last['content'] = blocks
        return [*messages[:-1], last]

    def add_turn(self, messages, turn):
        messages.append(turn.raw)  # every block back unchanged (thinking blocks included)

    def add_results(self, messages, results):
        messages.append({'role': 'user', 'content': [
            {'type': 'tool_result', 'tool_use_id': call.id, 'content': text} for call, text in results]})


# ---------------------------------------------------------------------------
# Images and vision (OpenAI)
# ---------------------------------------------------------------------------

IMAGE_SIZES = {'portrait': '1024x1536', 'landscape': '1536x1024', 'square': '1024x1024'}


def generate_image(prompt, size='portrait', references=()):
    """PNG bytes from the image model. `references` = [(name, bytes, mime)] (organizer assets to build on).
    Returns (png_bytes, model)."""
    from openai import BadRequestError, NotFoundError, OpenAI
    client = OpenAI(timeout=300)
    models = [settings.STUDIO_IMAGE_MODEL] + ([settings.STUDIO_IMAGE_FALLBACK_MODEL]
                                              if settings.STUDIO_IMAGE_FALLBACK_MODEL else [])
    last_error = None
    for model in dict.fromkeys(models):
        try:
            if references:
                response = client.images.edit(model=model, image=list(references), prompt=prompt[:4000],
                                              size=IMAGE_SIZES.get(size, IMAGE_SIZES['portrait']))
            else:
                response = client.images.generate(model=model, prompt=prompt[:4000], n=1, quality='high',
                                                  size=IMAGE_SIZES.get(size, IMAGE_SIZES['portrait']))
        except (NotFoundError, BadRequestError) as exc:
            last_error = exc
            if model_unavailable(exc):
                continue
            raise AIError(f'Image generation failed: {exc}') from exc
        item = response.data[0]
        if getattr(item, 'b64_json', None):
            from credit import ledger
            ledger.record(cost=Decimal(str(settings.STUDIO_PRICES['image'])), model=model, provider='openai',
                          feature=(ledger.current().get('feature') or 'studio_images'))
            return base64.b64decode(item.b64_json), model
        raise AIError('The image model returned no image.')
    raise AIError(f'Image generation failed: {last_error}')


def describe_image(part):
    """Vision (Claude Haiku, structured output): {description, alt, tags, colors} for one image or SVG text block;
    returns (result, usage)."""
    from attnd import claude
    from studio.assets import DESCRIBE_SCHEMA
    meta = {}
    try:
        result, _cost = claude.structured(
            system='You describe images an event organizer uploaded for their event website, so a designer can reuse '
                   'them. Be factual and brief.', content=[part, {'type': 'text', 'text': 'Describe this.'}],
            schema=DESCRIBE_SCHEMA, effort='low', max_tokens=800, meta=meta)
    except claude.ClaudeError as exc:
        raise AIError(str(exc)) from exc
    return result, {'model': meta.get('model', claude.fast_model()), 'input': meta.get('input', 0),
                    'output': meta.get('output', 0), 'cached': meta.get('cached', 0)}