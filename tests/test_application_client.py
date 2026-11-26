"""Malformed model output may be repaired once without bypassing application validation."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from applydesk.application_client import ApplicationClient
from test_orchestrator import response


class ApplicationClientTests(unittest.TestCase):
    def test_same_agent_repairs_syntax_and_receives_user_confirmation(self):
        engine = SimpleNamespace(active={"message": {"role": "user", "text": "Earliest start May 2027."}},
                                 run_agent=Mock(side_effect=[("a", response('{"ok":true broken}')),
                                                           ("b", response('{"ok":true}'))]))
        result = ApplicationClient(engine, "application", "job").ask("Return JSON.", {"page": "fixture"})
        self.assertEqual(result, {"ok": True})
        self.assertEqual(engine.run_agent.call_count, 2)
        self.assertTrue(all(call.args[0] == "master" for call in engine.run_agent.call_args_list))
        self.assertIn("May 2027", engine.run_agent.call_args_list[0].args[2][0]["content"][0]["text"])

    def test_repeated_invalid_output_is_bounded(self):
        engine = SimpleNamespace(active=None, run_agent=Mock(return_value=("a", response("invalid"))))
        with self.assertRaisesRegex(ValueError, "after one repair"):
            ApplicationClient(engine, "application", "job").ask("Return JSON.", {})
        self.assertEqual(engine.run_agent.call_count, 2)
