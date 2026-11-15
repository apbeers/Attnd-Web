"""Studio's AI provider layer: OpenAI (the existing SDK) and Anthropic (Messages API over urllib), images,
vision descriptions, cost logging and the per-event daily spend cap.

Both chat providers expose the same small interface for the agent loop (studio/agent.py):

    provider = get_provider()
    messages = provider.start(user_text)
    turn = provider.chat(system, messages, TOOLS)      # Turn(text, calls=[ToolCall(id, name, args)], usage)
    provider.add_turn(messages, turn)
    provider.add_results(messages, [(call, "result text"), ...])
"""

import base64
import json
import logging
import urllib.error
import urllib.request
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
    raw: object = None  # provider-native assistant message, appended back as is


# ---------------------------------------------------------------------------
# Cost and the spend cap
# ---------------------------------------------------------------------------

def price(model):
    prices = settings.STUDIO_PRICES
    return prices.get(model) or prices['default']


def token_cost(model, input_tokens, output_tokens):
    per_in, per_out = price(model)
    return Decimal(str(round(input_tokens * per_in / 1_000_000 + output_tokens * per_out / 1_000_000, 4)))


def spent_today(event):
    from studio.models import StudioUsage
    start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    total = StudioUsage.objects.filter(event=event, created_at__gte=start).aggregate(t=Sum('cost_usd'))['t']
    return total or Decimal('0')


def budget_left(event):
    return Decimal(str(settings.STUDIO_DAILY_SPEND_CAP_USD)) - spent_today(event)


def check_budget(event):
    if budget_left(event) <= 0:
        raise BudgetExceeded(f'Today\'s AI budget for this event (${settings.STUDIO_DAILY_SPEND_CAP_USD:.2f}) is used up. '
                             f'It resets at midnight UTC; you can still edit the code by hand.')


def record_usage(event, run, kind, provider, model, input_tokens=0, output_tokens=0, images=0):
    from studio.models import StudioUsage
    cost = token_cost(model, input_tokens, output_tokens) + Decimal(str(settings.STUDIO_PRICES['image'])) * images
    StudioUsage.objects.create(event=event, run=run, kind=kind, provider=provider, model=model[:64],
                               input_tokens=input_tokens, output_tokens=output_tokens, images=images, cost_usd=cost)
    return cost


def usage_summary(event, days=30):
    from studio.models import StudioUsage
    since = timezone.now() - timedelta(days=days)
    rows = StudioUsage.objects.filter(event=event, created_at__gte=since)
    agg = rows.aggregate(cost=Sum('cost_usd'), inp=Sum('input_tokens'), out=Sum('output_tokens'), img=Sum('images'))
    return {'today': spent_today(event), 'cap': settings.STUDIO_DAILY_SPEND_CAP_USD, 'month': agg['cost'] or 0,
            'input_tokens': agg['inp'] or 0, 'output_tokens': agg['out'] or 0, 'images': agg['img'] or 0}


# ---------------------------------------------------------------------------
# Chat providers
# ---------------------------------------------------------------------------

def provider_name():
    choice = settings.STUDIO_AI_PROVIDER
    if choice == 'auto':
        return 'anthropic' if settings.ANTHROPIC_API_KEY else 'openai'
    return choice


def get_provider():
    name = provider_name()
    if name == 'anthropic':
        if not settings.ANTHROPIC_API_KEY:
            raise AIError('STUDIO_AI_PROVIDER is anthropic but ANTHROPIC_API_KEY is not set.')
        return AnthropicProvider()
    import os
    if not os.getenv('OPENAI_API_KEY'):
        raise AIError('No AI key is configured on the server (OPENAI_API_KEY or ANTHROPIC_API_KEY).')
    return OpenAIProvider()


_unavailable_models = set()  # OpenAI models this account can't use (fell back once; remembered per process)


class OpenAIProvider:
    name = 'openai'

    def __init__(self):
        self.model = settings.STUDIO_OPENAI_MODEL
        if self.model in _unavailable_models and settings.STUDIO_OPENAI_FALLBACK_MODEL:
            self.model = settings.STUDIO_OPENAI_FALLBACK_MODEL

    @staticmethod
    def _tools(tools):
        return [{'type': 'function', 'function': {'name': t['name'], 'description': t['description'],
                                                  'parameters': t['parameters']}} for t in tools]

    def start(self, user_text):
        return [{'role': 'user', 'content': user_text}]

    def chat(self, system, messages, tools):
        from openai import BadRequestError, NotFoundError, OpenAI
        client = OpenAI(timeout=600)
        kwargs = {'reasoning_effort': 'medium'} if self.model.startswith(('gpt-5', 'o3', 'o4')) else {}
        try:
            response = client.chat.completions.create(
                model=self.model, messages=[{'role': 'system', 'content': system}, *messages],
                tools=self._tools(tools), **kwargs)
        except (NotFoundError, BadRequestError) as exc:
            fallback = settings.STUDIO_OPENAI_FALLBACK_MODEL
            if 'model' in str(exc).lower() and fallback and self.model != fallback:
                logger.warning('Studio model %s unavailable (%s); using %s', self.model, exc, fallback)
                _unavailable_models.add(self.model)
                self.model = fallback
                return self.chat(system, messages, tools)
            raise AIError(f'The AI request failed: {exc}') from exc
        message = response.choices[0].message
        calls = []
        for tc in message.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or '{}')
            except json.JSONDecodeError:
                args = {'_invalid_json': tc.function.arguments[:200]}
            calls.append(ToolCall(tc.id, tc.function.name, args if isinstance(args, dict) else {}))
        raw = {'role': 'assistant', 'content': message.content or ''}
        if message.tool_calls:
            raw['tool_calls'] = [{'id': tc.id, 'type': 'function',
                                  'function': {'name': tc.function.name, 'arguments': tc.function.arguments}}
                                 for tc in message.tool_calls]
        usage = response.usage
        return Turn(text=message.content or '', calls=calls, raw=raw, model=self.model,
                    input_tokens=getattr(usage, 'prompt_tokens', 0) or 0,
                    output_tokens=getattr(usage, 'completion_tokens', 0) or 0)

    def add_turn(self, messages, turn):
        messages.append(turn.raw)

    def add_results(self, messages, results):
        for call, text in results:
            messages.append({'role': 'tool', 'tool_call_id': call.id, 'content': text})


ANTHROPIC_URL = 'https://api.anthropic.com/v1/messages'


class AnthropicProvider:
    """Claude through the Messages API with urllib (no SDK dependency). Thinking is adaptive by default on the current
    models; effort high; refusals fall back server-side (`fallbacks: "default"`)."""
    name = 'anthropic'

    def __init__(self):
        self.model = settings.STUDIO_ANTHROPIC_MODEL

    def start(self, user_text):
        return [{'role': 'user', 'content': user_text}]

    def _post(self, body):
        request = urllib.request.Request(ANTHROPIC_URL, data=json.dumps(body).encode(), method='POST', headers={
            'x-api-key': settings.ANTHROPIC_API_KEY, 'anthropic-version': '2023-06-01',
            'anthropic-beta': 'server-side-fallback-2026-07-01', 'content-type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:500].decode('utf-8', 'replace')
            raise AIError(f'The AI request failed ({exc.code}): {detail}') from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise AIError(f'Could not reach the AI service: {exc}') from exc

    def chat(self, system, messages, tools):
        body = {
            'model': self.model, 'max_tokens': 16000, 'system': system, 'messages': messages,
            'tools': [{'name': t['name'], 'description': t['description'], 'input_schema': t['parameters']}
                      for t in tools],
            'output_config': {'effort': 'high'}, 'fallbacks': 'default',
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
                    output_tokens=usage.get('output_tokens') or 0)

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
            if 'model' in str(exc).lower():
                continue
            raise AIError(f'Image generation failed: {exc}') from exc
        item = response.data[0]
        if getattr(item, 'b64_json', None):
            return base64.b64decode(item.b64_json), model
        raise AIError('The image model returned no image.')
    raise AIError(f'Image generation failed: {last_error}')


def describe_image(part):
    """Vision: {description, alt, tags, colors} for one image content part; returns (result, usage)."""
    from openai import OpenAI

    from studio.assets import DESCRIBE_SCHEMA
    model = settings.STUDIO_VISION_MODEL
    kwargs = {'reasoning_effort': 'low'} if model.startswith(('gpt-5', 'o3', 'o4')) else {}
    response = OpenAI(timeout=120).chat.completions.create(
        model=model,
        response_format={'type': 'json_schema',
                         'json_schema': {'name': 'asset', 'strict': True, 'schema': DESCRIBE_SCHEMA}},
        messages=[{'role': 'system', 'content': 'You describe images an event organizer uploaded for their event '
                                                'website, so a designer can reuse them. Be factual and brief.'},
                  {'role': 'user', 'content': [part]}], **kwargs)
    usage = response.usage
    result = json.loads(response.choices[0].message.content or '{}')
    return result, {'model': model, 'input': getattr(usage, 'prompt_tokens', 0) or 0,
                    'output': getattr(usage, 'completion_tokens', 0) or 0}
