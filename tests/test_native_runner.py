"""Exercise real runner records and the real master tool loop with offline native replies."""

import copy
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from applydesk.agent_settings import AgentSettings
from applydesk.chatgpt_inference import ChatGPTRunner
from applydesk.orchestrator import Orchestrator
from applydesk.providers import ProviderError


class MemoryStore:
    """An isolated store captures records without touching the user's saved workspace."""

    def __init__(self):
        self.records = {}

    def get(self, kind, identity, default=None):
        return copy.deepcopy(self.records.get((kind, identity), default))

    def put(self, kind, identity, value):
        self.records[kind, identity] = copy.deepcopy(value)


class NativeRunnerTests(unittest.TestCase):
    def setUp(self):
        """Use actual settings validation with a small account-advertised model catalog."""
        self.workspace = SimpleNamespace(store=MemoryStore(), lock=threading.RLock())
        self.catalog = {'source': 'anthropic_api', 'active': 'anthropic_api', 'connected': True,
                        'models': [{'id': 'claude-sonnet-4-6', 'name': 'Claude', 'thinking': ['low', 'medium', 'high', 'max']}]}
        self.accounts = SimpleNamespace(generation=7, state=Mock(side_effect=lambda: self.catalog),
                                        api_key=Mock(return_value='private-key'), token=Mock())
        self.runner = ChatGPTRunner(self.workspace, self.accounts, AgentSettings(self.workspace, self.accounts))
        self.cancelled = threading.Event()
        self.emit = Mock()

    def test_retired_agent_roles_are_rejected_before_any_request(self):
        """Old role names cannot silently create another agent or model request."""
        with patch.object(self.runner, "_request") as request:
            for role in ("application", "document_reviewer", "application_auditor"):
                with self.assertRaisesRegex(ValueError, "one agent"):
                    self.runner.run(role, "Task", [], self.cancelled, self.emit)
        request.assert_not_called()
        self.assertEqual(self.workspace.store.records, {})

    def test_anthropic_real_master_tool_loop_keeps_signed_history(self):
        """The orchestrator executes normalized native tools and feeds results into the next call."""
        blocks = [{'type': 'thinking', 'thinking': '', 'signature': 'signature'},
                  {'type': 'tool_use', 'id': 'toolu-1', 'name': 'read_profile', 'input': {}}]
        replies = [{'id': 'one', 'stop_reason': 'tool_use', 'content': blocks},
                   {'id': 'two', 'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'Reviewed.'}]}]
        engine = self._engine()
        with patch('applydesk.chatgpt_inference.native_request', side_effect=replies) as request:
            answer = engine._master([{'role': 'user', 'content': 'Review my profile.'}])
        self.assertEqual(answer, 'Reviewed.')
        records = [row for (kind, _), row in self.workspace.store.records.items() if kind == 'agent_run']
        self.assertEqual([(row['role'], row['source'], row['status']) for row in records],
                         [('master', 'anthropic_api', 'completed')] * 2)
        self.accounts.api_key.assert_called_with('anthropic')
        self.accounts.token.assert_not_called()
        self.assertNotIn('private-key', str(self.workspace.store.records))
        call = engine.tools.call.call_args.args[0]
        self.assertEqual((call['name'], call['namespace']), ('read_profile', 'applydesk'))
        continuation = request.call_args_list[1].args[3]['messages']
        self.assertEqual(continuation[1]['content'], blocks)
        self.assertEqual(continuation[2]['content'][0]['tool_use_id'], 'toolu-1')
        self.assertEqual(engine.tools.call.call_count, 1)

    def test_google_real_master_tool_loop_and_native_key_selection(self):
        """Google tool results reach the correct native function with its signature intact."""
        self.catalog.update(source='google_api', active='google_api',
                            models=[{'id': 'gemini-3.8-flash', 'name': 'Gemini', 'thinking': ['low', 'medium', 'high']}])
        part = {'functionCall': {'name': 'workspace_overview', 'args': {}, 'id': 'call-1'}, 'thoughtSignature': 'signature'}
        replies = [{'responseId': 'one', 'candidates': [{'finishReason': 'STOP', 'content': {'role': 'model', 'parts': [part]}}]},
                   {'responseId': 'two', 'candidates': [{'finishReason': 'STOP', 'content': {'role': 'model', 'parts': [{'text': 'Reviewed.'}]}}]}]
        engine = self._engine()
        with patch('applydesk.chatgpt_inference.native_request', side_effect=replies) as request:
            answer = engine._master([{'role': 'user', 'content': 'Inspect workspace.'}])
        self.assertEqual(answer, 'Reviewed.')
        self.accounts.api_key.assert_called_with('google')
        continuation = request.call_args_list[1].args[3]['contents']
        self.assertEqual(continuation[1]['parts'], [part])
        self.assertEqual(continuation[2]['parts'][0]['functionResponse']['id'], 'call-1')
        self.assertEqual(engine.tools.call.call_count, 1)

    def _engine(self):
        """Use the real dispatch loop with inert tool actions and no background scheduler."""
        engine = Orchestrator.__new__(Orchestrator)
        engine.runner, engine.accounts = self.runner, self.accounts
        engine.cancelled, engine.stop = self.cancelled, threading.Event()
        engine.paused, engine.generation = False, self.accounts.generation
        engine.history, engine.active = SimpleNamespace(live={"id": "test-turn"}), None
        engine._stream = Mock()
        engine.tools = Mock()
        engine.tools.call.return_value = {'confirmed': True}
        return engine

    def test_grok_tool_loop_preserves_reasoning_and_uses_its_own_key(self):
        """Grok receives flat app tools and exact native history, never another provider's key."""
        self.catalog.update(source='xai_api', active='xai_api',
                            models=[{'id': 'grok-4.7', 'name': 'Grok', 'thinking': ['low', 'medium', 'high', 'xhigh']}])
        native = [{'type': 'reasoning', 'encrypted_content': 'opaque'},
                  {'type': 'function_call', 'name': 'read_profile', 'arguments': '{}', 'call_id': 'call-one'}]
        replies = [{'id': 'one', 'status': 'completed', 'output': native},
                   {'id': 'two', 'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Done.'}]}]}]
        engine = self._engine()
        with patch('applydesk.chatgpt_inference.native_request', side_effect=replies) as request:
            answer = engine._master([{'role': 'user', 'content': 'Check my profile.'}])
        self.assertEqual(answer, 'Done.')
        first, second = [call.args[3] for call in request.call_args_list]
        self.assertFalse(first['store'])
        self.assertFalse(first['parallel_tool_calls'])
        self.assertTrue(all(tool['type'] == 'function' for tool in first['tools']))
        self.assertEqual(second['input'][2:4], native)
        self.assertEqual(second['input'][-1]['call_id'], 'call-one')
        self.assertEqual(engine.tools.call.call_count, 1)
        self.accounts.api_key.assert_called_with('xai')
        self.accounts.token.assert_not_called()

    def test_cancellation_after_response_never_emits_final_text(self):
        """A completed HTTP response is discarded if Stop happened before delivery."""
        def cancel_after_request(*args):
            self.cancelled.set()
            return {'id': 'response', 'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'Do not display'}]}
        with patch('applydesk.chatgpt_inference.native_request', side_effect=cancel_after_request), self.assertRaises(InterruptedError):
            self.runner.run('master', 'Help.', [{'role': 'user', 'content': 'Question'}], self.cancelled, self.emit)
        self.emit.assert_not_called()
        records = list(self.workspace.store.records.values())
        self.assertEqual(records[-1]['status'], 'cancelled')
        self.assertIsNone(self.runner.current)

    def test_unsupported_reasoning_is_rejected_before_any_request(self):
        """An unsupported effort cannot slip through native request translation."""
        settings = self.runner.settings.read()
        settings['roles']['master']['thinking'] = 'ultra'
        self.workspace.store.put('agent_settings', 'anthropic_api', settings)
        with patch('applydesk.chatgpt_inference.native_request') as request, self.assertRaises(ValueError):
            self.runner.run('master', 'Help.', [{'role': 'user', 'content': 'Question'}], self.cancelled, self.emit)
        request.assert_not_called()
        self.accounts.api_key.assert_not_called()

    def test_unknown_source_never_falls_back_to_openai(self):
        """A corrupted or unsupported source cannot silently change credential destinations."""
        self.catalog['source'] = 'unsupported_api'
        with self.assertRaises(ProviderError):
            self.runner.run('master', 'Help.', [{'role': 'user', 'content': 'Question'}], self.cancelled, self.emit)
        self.accounts.token.assert_not_called()
        self.accounts.api_key.assert_not_called()


if __name__ == '__main__':
    unittest.main()
