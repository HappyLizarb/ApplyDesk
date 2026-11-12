"""Exercise real runner records and the real master tool loop with offline native replies."""

import copy
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from applydesk.agent_settings import AgentSettings, ROLES
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

    def test_four_roles_create_independent_observable_records(self):
        """Every role uses its selected model and writes its own completed local identity."""
        response = {'id': 'provider-result', 'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'Complete'}]}
        identities = []
        with patch('applydesk.chatgpt_inference.native_request', return_value=response) as request:
            for role in ROLES:
                identity, result = self.runner.run(role, 'Review facts.', [{'role': 'user', 'content': 'Evidence'}], self.cancelled, self.emit)
                identities.append(identity)
                record = self.workspace.store.get('agent_run', identity)
                self.assertEqual((record['role'], record['source'], record['status']), (role, 'anthropic_api', 'completed'))
                self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(set(identities)), 4)
        self.assertEqual(request.call_count, 4)
        self.accounts.api_key.assert_called_with('anthropic')
        self.accounts.token.assert_not_called()
        self.assertNotIn('private-key', str(self.workspace.store.records))

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
        engine._stream = Mock()
        engine.tools = Mock()
        engine.tools.call.return_value = {'confirmed': True}
        return engine

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
