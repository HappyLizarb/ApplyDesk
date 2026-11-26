"""A malformed citation may be repaired once; a genuine mismatch must never be retried away."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from applydesk.application_automation import ApplicationAutomation


class ScopeRepairTests(unittest.TestCase):
    def test_repairs_evidence_once_and_retains_source(self):
        controller = SimpleNamespace(check_active=Mock(), run={"scope": {"locations": ["Anywhere"]}, "instruction": "Find hardware roles"})
        match = {"matches": True, "issues": [], "industry": "Hardware", "level": "", "location": "Anywhere",
                 "quotes": {"industry": "Design hardware.", "level": "", "location": ""}}
        malformed = {**match, "quotes": {**match["quotes"], "industry": "Designing hardware."}}
        worker = Mock(ask=Mock(side_effect=[malformed, match]))
        analysis = {"company": "Example", "title": "Engineer", "required": []}
        ApplicationAutomation._scope(controller, lambda role: worker, analysis, {"text": "Design hardware."})
        self.assertEqual(worker.ask.call_count, 2)
        self.assertEqual(worker.ask.call_args.args[1]["source_text"], "Design hardware.")
        self.assertEqual(worker.ask.call_args.args[1]["previous_result"], malformed)

    def test_real_mismatch_is_not_repaired_into_a_match(self):
        controller = SimpleNamespace(check_active=Mock(), run={"scope": {"locations": ["Anywhere"]}, "instruction": "Find hardware roles"})
        worker = Mock(ask=Mock(return_value={"matches": False, "issues": ["The user excludes this role."]}))
        with self.assertRaisesRegex(ValueError, "excludes"):
            ApplicationAutomation._scope(controller, lambda role: worker,
                                         {"company": "Example", "title": "Engineer", "required": []}, {"text": "Design hardware."})
        self.assertEqual(worker.ask.call_count, 1)
