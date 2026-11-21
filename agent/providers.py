"""Ask Attnd's model providers.

Ask Attnd runs on Anthropic's Messages API with the official SDK (`AGENT_PROVIDER`, default `anthropic` when
`ANTHROPIC_API_KEY` is set) and Claude Haiku 5.5, or on OpenAI through Studio's provider (`studio.ai`). Studio's own
provider choice is separate (`STUDIO_AI_PROVIDER`).

The Anthropic provider has the loop interface of `studio.ai` (start / chat / add_turn / add_results / state /
restore / effort), so `agent.loop` drives both the same way; we host the tool loop ourselves (tools run our views).

Request rules for claude-haiku-5-5 (see the model's migration notes):
- adaptive thinking is the default: `thinking` is omitted; depth is `output_config.effort` (one `AGENT_EFFORT` for every call, so the cache holds);
- no temperature / top_p / top_k, no assistant prefill (the conversation always ends with a user turn);
- strict tools, `tool_choice` auto with `disable_parallel_tool_use`;
- prompt caching: a breakpoint on the (stable) system prompt caches tools + system; one on the newest message caches
  the conversation so far. Everything volatile (the context block: now, the event, the page) is in the first user
  message, after the system breakpoint;
- every assistant content block goes back unchanged (thinking blocks included) and the history is append-only;
- `stop_reason` is checked before content: refusal → `Refused` (no server-side fallback on Haiku; the loop moves
  the run to OpenAI), max_tokens → one retry with a larger cap, pause_turn → continued.
"""

import logging

from django.conf import settings

from studio.ai import AIError, ToolCall, Turn

logger = logging.getLogger('attnd.agent')

MAX_TOKENS = 16000
MAX_TOKENS_RETRY = 32000
MAX_CONTINUATIONS = 3
STRICT_TOOLS = {'read_page', 'list_pages', 'list_events', 'search_events', 'ask_user', 'finish', 'show', 'get_component'}

# Strict schemas the API refused once (too complex): this process sends tools without `strict` from then on.
_strict_refused = set()


class Refused(AIError):
    """The model declined (stop_reason refusal). The loop retries the run on the OpenAI provider."""

    def __init__(self, message, category=''):
        super().__init__(message)
        self.category = category or ''


def provider_name():
    choice = (settings.AGENT_PROVIDER or '').strip().lower()
    if choice in ('anthropic', 'openai'):
        return choice
    return 'anthropic' if settings.ANTHROPIC_API_KEY else 'openai'


def get_provider(name=None, model=None):
    """The provider for a run. `name` / `model` restore a paused run's provider (or the refusal fallback)."""
    name = name or provider_name()
    if name == 'anthropic':
        if not settings.ANTHROPIC_API_KEY:
            raise AIError('AGENT_PROVIDER is anthropic but ANTHROPIC_API_KEY is not set.')
        return ClaudeProvider(model=model or settings.AGENT_ANTHROPIC_MODEL, effort=settings.AGENT_EFFORT)
    from studio import ai
    import os
    if not os.getenv('OPENAI_API_KEY'):
        raise AIError('No AI key is configured on the server (ANTHROPIC_API_KEY or OPENAI_API_KEY).')
    return ai.OpenAIProvider(model=model or settings.AGENT_MODEL, fallback_model=settings.AGENT_FALLBACK_MODEL,
                             effort=settings.AGENT_EFFORT, parallel_tool_calls=False)


UNSUPPORTED_KEYWORDS = ('minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf', 'minLength',
                        'maxLength', 'pattern', 'minItems', 'maxItems', 'uniqueItems')


def claude_schema(schema):
    """A tool schema as Anthropic's strict mode accepts it (a copy): an enum on a nullable type also lists null
    (as anyOf: the API checks every enum value against the declared type), and numeric / length / pattern constraints are
    dropped (not supported; our views validate anyway)."""
    if isinstance(schema, list):
        return [claude_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: claude_schema(v) for k, v in schema.items() if k not in UNSUPPORTED_KEYWORDS}
    kind = out.get('type')
    if 'enum' in out and isinstance(kind, list):
        # A nullable enum: the API reads a type list on an enum as one type ("Enum value 'new' does not match
        # declared type ['string', 'null']"), so it becomes anyOf: the enum of that type, or null.
        values = [v for v in out.pop('enum') if v is not None]
        out.pop('type')
        branches = [{'type': t, 'enum': [v for v in values if _is(t, v)]} for t in kind if t != 'null']
        branches = [b for b in branches if b['enum']]
        if 'null' in kind:
            branches.append({'type': 'null'})
        out['anyOf'] = branches
    return out


def _is(kind, value):
    if kind == 'string':
        return isinstance(value, str)
    if kind == 'integer':
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == 'number':
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == 'boolean':
        return isinstance(value, bool)
    return True


def _block_dict(block):
    if isinstance(block, dict):
        return block
    return block.model_dump(mode='json', exclude_none=True)


def first_text(messages):
    """The text of the conversation's first user message (the context block) in either provider's format."""
    if not messages:
        return ''
    content = messages[0].get('content') if isinstance(messages[0], dict) else ''
    if isinstance(content, str):
        return content
    return '\n'.join(b.get('text', '') for b in content or [] if isinstance(b, dict) and b.get('type') == 'text')


class ClaudeProvider:
    name = 'anthropic'

    def __init__(self, model=None, effort='medium', client=None):
        self.model = model or settings.AGENT_ANTHROPIC_MODEL
        self.effort = effort or 'medium'
        self._client = client

    # -- the loop interface -------------------------------------------------------------------------------------

    def start(self, user_text):
        return [{'role': 'user', 'content': [{'type': 'text', 'text': user_text}]}]

    def state(self):
        return {'name': self.name, 'model': self.model}  # `messages` holds the whole conversation

    def restore(self, state):
        self.model = state.get('model') or self.model

    def add_turn(self, messages, turn):
        messages.append(turn.raw)  # every block back unchanged, thinking blocks included

    def add_results(self, messages, results):
        if not results:
            return
        blocks = [{'type': 'tool_result', 'tool_use_id': call.id, 'content': text or '(empty)'}
                  for call, text in results]
        last = messages[-1] if messages else None
        if last and last.get('role') == 'user' and all(b.get('type') == 'tool_result' for b in last['content']):
            last['content'].extend(blocks)  # a second batch for the same turn (e.g. "not run" after a pause)
        else:
            messages.append({'role': 'user', 'content': blocks})

    # -- the request --------------------------------------------------------------------------------------------

    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=300.0, max_retries=2)
        return self._client

    def tools(self, tools):
        """Strict mode only for Ask Attnd's own small tools (STRICT_TOOLS). Found on dev: the API takes at most 20
        strict tools, and with the console's ~80 form-derived write schemas strict even "compiled grammar is too
        large". The writes are validated by their views (form errors go back to the model) anyway."""
        strict = self.model not in _strict_refused
        out = []
        for t in tools:
            tool = {'name': t['name'], 'description': t['description'][:1000], 'input_schema': claude_schema(t['parameters'])}
            if strict and t.get('strict') and t['name'] in STRICT_TOOLS:
                tool['strict'] = True
            out.append(tool)
        return out

    @staticmethod
    def system(text):
        return [{'type': 'text', 'text': text, 'cache_control': {'type': 'ephemeral'}}]

    @staticmethod
    def cached(messages):
        """A copy of the messages with a cache breakpoint on the newest block (the caller's list is unchanged)."""
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

    def _create(self, system, messages, tools, max_tokens):
        import anthropic
        body = dict(model=self.model, max_tokens=max_tokens, system=self.system(system),
                    messages=self.cached(messages), tools=self.tools(tools),
                    tool_choice={'type': 'auto', 'disable_parallel_tool_use': True},
                    output_config={'effort': self.effort})
        try:
            return self.client().messages.create(**body)
        except anthropic.BadRequestError as exc:
            text = str(exc).lower()
            if 'strict' in text and self.model not in _strict_refused:  # schema too complex for strict mode
                logger.warning('Ask Attnd: strict tools refused for %s (%s); sending them without strict', self.model,
                               exc)
                _strict_refused.add(self.model)
                return self._create(system, messages, tools, max_tokens)
            raise AIError(f'The AI request failed: {getattr(exc, "message", exc)}') from exc
        except anthropic.AuthenticationError as exc:
            raise AIError('The AI service rejected the server\'s key (ANTHROPIC_API_KEY).') from exc
        except anthropic.PermissionDeniedError as exc:
            raise AIError(f'The AI service refused this key: {getattr(exc, "message", exc)}') from exc
        except anthropic.NotFoundError as exc:
            raise AIError(f'The AI model {self.model} is not available.') from exc
        except anthropic.RateLimitError as exc:
            raise AIError('The AI service is busy (rate limited). Try again in a minute.') from exc
        except anthropic.APIStatusError as exc:
            raise AIError(f'The AI request failed ({exc.status_code}). Try again.') from exc
        except anthropic.APITimeoutError as exc:
            raise AIError('The AI service took too long to answer. Try again.') from exc
        except anthropic.APIConnectionError as exc:
            raise AIError('Could not reach the AI service. Try again.') from exc

    def chat(self, system, messages, tools):
        """One model turn. Handles pause_turn (continues, appending the paused content to `messages`),
        max_tokens (one retry with a larger cap) and refusal (raises Refused) before reading any content."""
        totals = {'input': 0, 'cached': 0, 'output': 0, 'written': 0}
        max_tokens, continuations = MAX_TOKENS, 0
        while True:
            response = self._create(system, messages, tools, max_tokens)
            usage = response.usage
            read = getattr(usage, 'cache_read_input_tokens', 0) or 0
            written = getattr(usage, 'cache_creation_input_tokens', 0) or 0
            totals['input'] += (usage.input_tokens or 0) + read + written
            totals['cached'] += read
            totals['written'] += written
            totals['output'] += usage.output_tokens or 0
            stop = response.stop_reason
            if stop == 'refusal':
                details = getattr(response, 'stop_details', None)
                category = getattr(details, 'category', None) or ''
                logger.warning('Ask Attnd: %s refused (category %s, request %s)', self.model, category,
                               getattr(response, '_request_id', ''))
                raise Refused('The AI declined this request.', category)
            content = [_block_dict(b) for b in response.content]
            if stop == 'pause_turn' and continuations < MAX_CONTINUATIONS:
                messages.append({'role': 'assistant', 'content': content})
                continuations += 1
                continue
            if stop == 'max_tokens':
                if max_tokens < MAX_TOKENS_RETRY:
                    max_tokens = MAX_TOKENS_RETRY
                    continue
                raise AIError('The AI\'s answer was cut off (too long). Try a smaller request.')
            break
        text = '\n'.join(b.get('text', '') for b in content if b.get('type') == 'text').strip()
        calls = [ToolCall(b['id'], b['name'], b.get('input') if isinstance(b.get('input'), dict) else {})
                 for b in content if b.get('type') == 'tool_use']
        turn = Turn(text=text, calls=calls, model=getattr(response, 'model', '') or self.model,
                    raw={'role': 'assistant', 'content': content}, input_tokens=totals['input'],
                    output_tokens=totals['output'], cached_tokens=totals['cached'])
        turn.cache_written = totals['written']
        return turn
