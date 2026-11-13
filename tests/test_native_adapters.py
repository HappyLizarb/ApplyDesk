"""Offline protocol fixtures exercise native tools, images, and safe completion boundaries."""

import copy
import json
import unittest

from applydesk import anthropic_agent, google_agent
from applydesk.chatgpt_inference import output_text
from applydesk.orchestrator_tools import master_tools
from applydesk.providers import ProviderError

IMAGE = 'data:image/png;base64,iVBORw0KGgo='


def payload(model, effort='default'):
    """Represent the same text-and-image request used by the document reviewer."""
    return {'model': model, 'instructions': 'Review only the supplied evidence.',
            'input': [{'role': 'user', 'content': [{'type': 'input_text', 'text': 'Page 1'},
                       {'type': 'input_image', 'image_url': IMAGE}]}],
            'reasoning': {'effort': effort}, 'tools': master_tools()}


def claude_response(blocks, reason='end_turn'):
    """Provide a completed native response with an app-visible provider identity."""
    return {'id': 'msg-test', 'type': 'message', 'role': 'assistant',
            'model': 'claude-sonnet-4-6', 'stop_reason': reason, 'content': blocks}


def gemini_response(parts, reason='STOP'):
    """Keep the returned native content distinct from normalized tool outputs."""
    return {'responseId': 'response-test', 'candidates': [{
        'finishReason': reason, 'content': {'role': 'model', 'parts': parts}}]}


class NativeAdapterTests(unittest.TestCase):
    def test_anthropic_schema_images_and_effort(self):
        """The reviewer receives image bytes and master tools retain their full JSON schema."""
        body = anthropic_agent.request_body(payload('claude-sonnet-4-6', 'high'))
        self.assertEqual(body['system'], 'Review only the supplied evidence.')
        self.assertEqual(body['messages'][0]['content'][1]['source'],
                         {'type': 'base64', 'media_type': 'image/png', 'data': IMAGE.split(',')[1]})
        self.assertEqual(body['thinking'], {'type': 'adaptive'})
        self.assertEqual(body['output_config'], {'effort': 'high'})
        declaration = next(tool for tool in body['tools'] if tool['name'] == 'run_applications')
        self.assertEqual(declaration['input_schema']['properties'], {})
        self.assertEqual(declaration['input_schema']['required'], [])
        self.assertFalse(declaration['input_schema']['additionalProperties'])

    def test_anthropic_signed_thinking_tool_round_trip(self):
        """Thinking signatures are echoed unchanged but never become user-visible text."""
        blocks = [{'type': 'thinking', 'thinking': 'private', 'signature': 'opaque'},
                  {'type': 'text', 'text': 'Checking your profile.'},
                  {'type': 'tool_use', 'id': 'toolu-1', 'name': 'read_profile', 'input': {}}]
        original = copy.deepcopy(blocks)
        response = anthropic_agent.parse_response(claude_response(blocks, 'tool_use'))
        history = [{'role': 'user', 'content': 'Review my profile.'}, *response['output'],
                   {'type': 'function_call_output', 'call_id': 'toolu-1', 'output': '{"confirmed":true}'}]
        mapped = anthropic_agent.messages(history)
        self.assertEqual(mapped[1], {'role': 'assistant', 'content': original})
        self.assertEqual(mapped[2]['content'], [{'type': 'tool_result', 'tool_use_id': 'toolu-1', 'content': '{"confirmed":true}'}])
        self.assertEqual(output_text(response), 'Checking your profile.')
        self.assertEqual(blocks, original)
        call = next(item for item in response['output'] if item['type'] == 'function_call')
        self.assertEqual((call['namespace'], call['name'], json.loads(call['arguments'])), ('applydesk', 'read_profile', {}))

    def test_anthropic_refusal_and_truncated_tool_are_rejected(self):
        """A partial or refused answer cannot be mistaken for a completed review."""
        for reason in ['max_tokens', 'refusal', 'pause_turn', 'model_context_window_exceeded', None]:
            with self.subTest(reason=reason), self.assertRaises(ProviderError):
                anthropic_agent.parse_response(claude_response([{'type': 'text', 'text': 'partial'}], reason))
        with self.assertRaises(ProviderError):
            anthropic_agent.parse_response(claude_response([], 'tool_use'))
        with self.assertRaises(ProviderError):
            anthropic_agent.parse_response(claude_response([{'type': 'refusal', 'refusal': 'No'}]))

    def test_google_schema_images_and_effort(self):
        """GenerateContent uses native parts and uppercase reasoning enum values."""
        body = google_agent.request_body(payload('gemini-3.8-flash', 'medium'))
        self.assertEqual(body['systemInstruction']['parts'][0]['text'], 'Review only the supplied evidence.')
        self.assertEqual(body['contents'][0]['parts'][1],
                         {'inlineData': {'mimeType': 'image/png', 'data': IMAGE.split(',')[1]}})
        self.assertEqual(body['generationConfig']['thinkingConfig'], {'thinkingLevel': 'MEDIUM'})
        declaration = body['tools'][0]['functionDeclarations'][0]
        self.assertIn('parametersJsonSchema', declaration)
        self.assertNotIn('parameters', declaration)

    def test_google_parallel_call_ids_and_signatures_round_trip(self):
        """All parallel results retain their names and IDs without flattening native parts."""
        parts = [{'text': 'private', 'thought': True, 'thoughtSignature': 'thought-opaque'},
                 {'functionCall': {'id': 'call-one', 'name': 'read_profile', 'args': {}}, 'thoughtSignature': 'call-opaque'},
                 {'functionCall': {'id': 'call-two', 'name': 'workspace_overview', 'args': {}}}]
        response = google_agent.parse_response(gemini_response(parts))
        history = [{'role': 'user', 'content': 'Inspect my workspace'}, *response['output'],
                   {'type': 'function_call_output', 'call_id': 'call-one', 'output': '{"confirmed":true}'},
                   {'type': 'function_call_output', 'call_id': 'call-two', 'output': '{"jobs":[]}'}]
        mapped = google_agent.messages(history)
        self.assertEqual(mapped[1], {'role': 'model', 'parts': parts})
        self.assertEqual(mapped[2]['parts'], [
            {'functionResponse': {'name': 'read_profile', 'id': 'call-one', 'response': {'confirmed': True}}},
            {'functionResponse': {'name': 'workspace_overview', 'id': 'call-two', 'response': {'jobs': []}}}])
        self.assertEqual(output_text(response), '')

    def test_google_generated_local_id_does_not_modify_signed_call(self):
        """Gemini's optional call ID remains absent in native content and tool responses."""
        part = {'functionCall': {'name': 'read_profile', 'args': {}}, 'thoughtSignature': 'opaque'}
        response = google_agent.parse_response(gemini_response([part]))
        call = response['output'][1]
        mapped = google_agent.messages([*response['output'], {
            'type': 'function_call_output', 'call_id': call['call_id'], 'output': '[1,2]'}])
        self.assertEqual(mapped[0]['parts'], [part])
        self.assertEqual(mapped[1]['parts'][0]['functionResponse'], {'name': 'read_profile', 'response': {'result': [1, 2]}})

    def test_google_refusal_truncation_and_empty_content_fail_closed(self):
        """Blocked, malformed, and partial candidates never authorize tool execution."""
        for reason in ['MAX_TOKENS', 'SAFETY', 'RECITATION', 'MALFORMED_FUNCTION_CALL', None]:
            with self.subTest(reason=reason), self.assertRaises(ProviderError):
                google_agent.parse_response(gemini_response([{'text': 'partial'}], reason))
        with self.assertRaises(ProviderError):
            google_agent.parse_response({'promptFeedback': {'blockReason': 'SAFETY'}})
        with self.assertRaises(ProviderError):
            google_agent.parse_response(gemini_response([{'thought': True, 'text': 'private'}]))
        with self.assertRaises(ProviderError):
            google_agent.parse_response(gemini_response([{'executableCode': {'code': 'unsafe'}}]))

    def test_duplicate_tool_identifiers_fail_before_dispatch(self):
        """Ambiguous IDs cannot overwrite the mapping from results to provider functions."""
        blocks = [{'type': 'tool_use', 'id': 'duplicate', 'name': name, 'input': {}}
                  for name in ['read_profile', 'workspace_overview']]
        with self.assertRaisesRegex(ProviderError, 'duplicate tool identifiers'):
            anthropic_agent.parse_response(claude_response(blocks, 'tool_use'))
        parts = [{'functionCall': {'id': 'duplicate', 'name': name, 'args': {}}}
                 for name in ['read_profile', 'workspace_overview']]
        with self.assertRaisesRegex(ProviderError, 'duplicate tool identifiers'):
            google_agent.parse_response(gemini_response(parts))

    def test_defaults_do_not_invent_effort_or_remote_image_fetches(self):
        """Unknown-model defaults stay unset and image URLs cannot cause provider fetches."""
        for adapter, model in [(anthropic_agent, 'claude-haiku-4-5'), (google_agent, 'gemini-2.5-pro')]:
            with self.subTest(model=model):
                body = adapter.request_body(payload(model))
                self.assertNotIn('thinking', body)
                self.assertNotIn('thinkingConfig', body.get('generationConfig', {}))
                invalid = payload(model)
                invalid['input'][0]['content'][1]['image_url'] = 'https://private.example/image.png'
                with self.assertRaises(ProviderError):
                    adapter.request_body(invalid)

    def test_provider_history_cannot_cross_connection_sources(self):
        """Opaque signatures from one provider are never sent to a different provider."""
        response = anthropic_agent.parse_response(claude_response([{'type': 'text', 'text': 'Done'}]))
        with self.assertRaises(ProviderError):
            google_agent.messages(response['output'])
        with self.assertRaises(ProviderError):
            google_agent.messages([{'type': 'function_call_output', 'call_id': 'missing', 'output': '{}'}])


if __name__ == '__main__':
    unittest.main()
