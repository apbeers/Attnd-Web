"""attnd.claude.structured: stop reasons, request ids, tolerant JSON reading (batch 5)."""
from django.test import TestCase

from attnd import claude
from attnd.testing import fake_claude, reply

SCHEMA = {'type': 'object', 'properties': {'name': {'type': 'string'}}, 'required': ['name']}


class StructuredTests(TestCase):
    def ask(self, *answers, **kw):
        with fake_claude(*answers) as fake:
            out = claude.structured(system='s', content='c', schema=SCHEMA, **kw)
        return out, fake

    def test_json_with_fences_or_prose_or_braces_after_it(self):
        for text in ('```json\n{"name": "A"}\n```', 'Here it is: {"name": "A"} (all {done})', '{"name": "A"}'):
            (data, _), _fake = self.ask(reply(text))
            self.assertEqual(data, {'name': 'A'}, text)
        with self.assertRaisesMessage(claude.ClaudeError, 'no JSON'), self.assertLogs('attnd.claude', 'WARNING'):
            self.ask(reply('Sorry, nothing to read.'))

    def test_max_tokens_gets_one_retry_with_twice_the_room(self):
        (data, _), fake = self.ask(reply('{"name": "A', stop='max_tokens'), reply({'name': 'A'}), max_tokens=8000)
        self.assertEqual(data, {'name': 'A'})
        self.assertEqual([c['max_tokens'] for c in fake.calls], [8000, 16000])
        with self.assertRaisesMessage(claude.ClaudeError, 'too long'), self.assertLogs('attnd.claude', 'WARNING'):
            self.ask(reply('{', stop='max_tokens'), max_tokens=claude.MAX_OUTPUT)

    def test_refusal_is_a_clear_error(self):
        with self.assertRaisesMessage(claude.ClaudeError, 'declined'), self.assertLogs('attnd.claude', 'WARNING'):
            self.ask(reply('', stop='refusal'))

    def test_cost_counts_every_call(self):
        (_, cost), _fake = self.ask(reply('{', stop='max_tokens'), reply({'name': 'A'}))
        (_, one), _fake = self.ask(reply({'name': 'A'}))
        self.assertEqual(cost, one * 2)
