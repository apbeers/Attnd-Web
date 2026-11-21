"""Test helper: a fake Claude for code that goes through attnd/claude.py (never calls the API)."""
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock


def reply(data, stop='end_turn'):
    text = data if isinstance(data, str) else json.dumps(data)
    return SimpleNamespace(content=[SimpleNamespace(type='text', text=text)], stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=100, output_tokens=50, cache_read_input_tokens=0,
                                                 cache_creation_input_tokens=0))


class FakeClaude:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []
        self.messages = self

    def _next(self, kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer if hasattr(answer, 'stop_reason') else reply(answer)

    def create(self, **kwargs):
        return self._next(kwargs)

    @contextmanager
    def stream(self, **kwargs):
        response = self._next(kwargs)
        yield SimpleNamespace(get_final_message=lambda: response)

    @property
    def last(self):
        return self.calls[-1]


@contextmanager
def fake_claude(*answers):
    """with fake_claude({'name': …}) as claude: … claude.last['messages'] …"""
    fake = FakeClaude(*answers)
    with mock.patch('attnd.claude.client', return_value=fake), mock.patch('attnd.claude.available', return_value=True):
        yield fake
