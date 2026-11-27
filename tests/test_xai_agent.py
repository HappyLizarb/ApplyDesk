"""Grok protocol checks use public API shapes without credentials or provider requests."""

import unittest
from unittest.mock import patch

from applydesk.model_catalog import catalog_models
from applydesk.model_catalog import list_models
from applydesk.providers import ProviderError
from applydesk.xai_agent import parse_response, request_body


class GrokTests(unittest.TestCase):
    def test_catalog_is_read_only_and_excludes_non_chat_and_multi_agent_models(self):
        """Model choices come from the key's catalog; unsupported effort is never invented."""
        page = {"data": [{"id": name} for name in ['grok-4.7', 'grok-4.5', 'grok-future', 'grok-imagine-image', 'grok-4.20-multi-agent']]}
        with patch('applydesk.model_catalog._request_json', return_value=page) as request:
            rows = catalog_models('xai', list_models('xai', 'test-key'))
        self.assertEqual(request.call_args.args[:3], ('xai', 'https://api.x.ai/v1/models', {'Authorization': 'Bearer test-key'}))
        self.assertEqual({row['id'] for row in rows}, {'grok-4.7', 'grok-4.5', 'grok-future'})
        choices = {row['id']: row['thinking'] for row in rows}
        self.assertEqual(choices['grok-4.7'], ['low', 'medium', 'high', 'xhigh'])
        self.assertEqual(choices['grok-future'], [])

    def test_thinking_and_image_inputs_are_preserved_without_storing_responses(self):
        """Task inputs and explicit effort survive translation; no hidden remote tools are added."""
        payload = {'model': 'grok-4.7', 'instructions': 'Check your work.', 'reasoning': {'effort': 'low'},
                   'input': [{'role': 'user', 'content': [{'type': 'input_image', 'image_url': 'data:image/png;base64,YQ=='}]}]}
        body = request_body(payload)
        self.assertEqual(body['input'][1:], payload['input'])
        self.assertEqual(body['reasoning'], {'effort': 'low'})
        self.assertEqual(body['input'][0], {'role': 'system', 'content': 'Check your work.'})
        self.assertFalse(body['store'])
        self.assertNotIn('tools', body)

    def test_malformed_or_incomplete_output_never_becomes_a_tool_action(self):
        """Refusals, missing tool IDs, duplicate IDs and bad arguments stop the request."""
        call = {'type': 'function_call', 'name': 'read_profile', 'arguments': '{}', 'call_id': 'call-1'}
        cases = [[], [dict(call, arguments='not json')], [dict(call, call_id=None)], [call, call],
                 [{'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'No'}]}],
                 [{'type': 'message', 'content': 'invalid'}], [{'type': 'message', 'content': [None]}]]
        for output in cases:
            with self.subTest(output=output), self.assertRaises(ProviderError):
                parse_response({'id': 'test', 'status': 'completed', 'output': output})
        with self.assertRaises(ProviderError):
            parse_response({'id': 'test', 'status': 'incomplete', 'output': [call]})

    def test_source_switch_rejects_foreign_native_history(self):
        """A provider's opaque continuation is never sent to another provider."""
        with self.assertRaises(ProviderError):
            request_body({'model': 'grok-4.7', 'instructions': 'Check.',
                          'input': [{'type': 'provider_message', 'provider': 'google', 'message': []}]})
