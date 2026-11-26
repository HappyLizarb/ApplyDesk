"""Conversation interpretation uses synthetic model output and real local message provenance."""

import copy
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from applydesk.application_intent import INTENT_PROMPT, resolve_intent
from applydesk.chat_history import ChatHistory
from applydesk.storage import Store


class ApplicationIntentTests(unittest.TestCase):
    """No provider request, personal data, or employer interaction occurs in these checks."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="applydesk-intent-")
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.addCleanup(self.store.close)
        resumes = [{"id": "resume-hardware", "filename": "Hardware.pdf", "category": "Hardware"}]
        self.workspace = SimpleNamespace(store=self.store, lock=threading.RLock(), resumes=SimpleNamespace(list=Mock(return_value=resumes)))
        self.history = ChatHistory(self.workspace)
        self.request = self.history.add("user", "Please apply for five full-time hardware new-grad jobs anywhere, using Hardware.pdf unchanged.")
        self.assistant = self.history.add("assistant", "Confirm your profile and I can continue.")
        self.current = self.history.add("user", "My profile is confirmed now.", "running")
        self.engine = SimpleNamespace(w=self.workspace, history=self.history, check_active=Mock(),
                                      accounts=SimpleNamespace(state=Mock(return_value={"active": "account"}), generation=1),
                                      active={"message": self.current}, run_agent=Mock(side_effect=self.model))
        self.result = {"action": "start", "run_id": "", "authorization_message_id": self.request["id"],
                       "limit": 5, "locations": ["Anywhere"], "instruction": self.request["text"],
                       "resume_id": "resume-hardware", "issues": []}
        self.payload = None

    def model(self, role, prompt, inputs):
        """Capture the independent interpretation context without pretending to evaluate language quality."""
        self.assertEqual(role, "master")
        self.assertEqual(prompt, INTENT_PROMPT)
        self.payload = json.loads(inputs[0]["content"])
        response = {"output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(self.result)}]}]}
        return "synthetic-intent", response

    def previous_run(self, **changes):
        """Create one known allowance tied to the original human request and current chat."""
        run = {"id": "run-one", "authorization_message_id": self.request["id"], "conversation_id": self.history.since,
               "account_id": "account", "limit": 5, "scope": {"locations": ["Anywhere"]},
               "instruction": self.request["text"], "resume_id": "resume-hardware", "status": "interrupted"}
        return self.store.put("application_run", run["id"], {**run, **changes})

    def continue_run(self, **changes):
        """The same allowance keeps its original grant when a follow-up resumes it."""
        self.previous_run()
        self.result.update(action="continue", run_id="run-one", **changes)
        return resolve_intent(self.engine)

    def test_follow_up_uses_earlier_permission_and_chosen_resume(self):
        plan = resolve_intent(self.engine)
        self.assertEqual(plan["authorization_message_id"], self.request["id"])
        self.assertEqual(plan["resume_id"], "resume-hardware")
        self.assertEqual(plan["limit"], 5)
        self.assertEqual([item["id"] for item in self.payload["conversation"]],
                         [self.request["id"], self.assistant["id"], self.current["id"]])
        self.assertEqual(self.payload["conversation"][0]["content"], self.request["text"])
        self.assertEqual(self.payload["resumes"][0]["filename"], "Hardware.pdf")

    def test_missing_permission_returns_a_normal_clarification(self):
        self.result.update(action="none", issues=["Should I submit these applications or keep them as drafts?"])
        with self.assertRaisesRegex(ValueError, "keep them as drafts"):
            resolve_intent(self.engine)
        self.assertEqual(self.store.all("application_run"), [])

    def test_assistant_and_invented_message_ids_cannot_authorize(self):
        for identity in (self.assistant["id"], "invented-message"):
            self.result["authorization_message_id"] = identity
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                resolve_intent(self.engine)

    def test_invalid_limits_locations_and_resume_ids_fail_closed(self):
        original = copy.deepcopy(self.result)
        for change in ({"limit": True}, {"limit": 0}, {"limit": -1}, {"limit": 1.5}, {"limit": "5"}, {"locations": []},
                       {"locations": ["Boston", "boston"]}, {"locations": ["Anywhere", "Boston"]},
                       {"resume_id": "removed-resume"}, {"action": "execute"}, {"action": {}}, {"instruction": ""}):
            self.result = {**original, **change}
            with self.subTest(change=change), self.assertRaises(ValueError):
                resolve_intent(self.engine)

    def test_unlimited_and_large_explicit_counts_are_valid(self):
        """Null preserves an unrestricted request, and a count above 100 is not clamped."""
        for count in (None, 101, 10000):
            self.result["limit"] = count
            with self.subTest(count=count):
                self.assertEqual(resolve_intent(self.engine)["limit"], count)

    def test_same_authorization_cannot_start_another_allowance(self):
        self.previous_run()
        with self.assertRaisesRegex(ValueError, "remaining allowance"):
            resolve_intent(self.engine)

    def test_consumed_grant_cannot_restart_after_account_switch_or_legacy_run(self):
        for changes in ({"account_id": "another-account"}, {"conversation_id": "older-chat"},
                        {"authorization_message_id": None, "message_id": self.request["id"]}):
            self.previous_run(**changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "remaining allowance"):
                resolve_intent(self.engine)
            self.assertIn(self.request["id"], self.payload["used_authorization_ids"])

    def test_continue_preserves_run_identity_and_counts_all_attempts(self):
        self.store.put("submission_attempt", "one", {"run_id": "run-one", "status": "submitted"})
        self.store.put("submission_attempt", "two", {"run_id": "run-one", "status": "submission_unknown"})
        plan = self.continue_run()
        self.assertEqual(plan["run_id"], "run-one")
        self.assertEqual(plan["authorization_message_id"], self.request["id"])
        self.assertEqual(self.payload["previous_runs"][0]["attempted_count"], 2)

    def test_explicit_cap_change_can_keep_the_same_attempt_ledger(self):
        self.current["text"] = "Make the total cap seven, then continue."
        self.store.put("chat_message", self.current["id"], self.current)
        plan = self.continue_run(limit=7)
        self.assertEqual((plan["run_id"], plan["limit"]), ("run-one", 7))

    def test_continuation_cannot_replace_original_authorization(self):
        self.previous_run()
        self.result.update(action="continue", run_id="run-one", authorization_message_id=self.current["id"])
        with self.assertRaises(ValueError):
            resolve_intent(self.engine)

    def test_continuation_looks_up_the_run_original_grant(self):
        """The agent identifies the old run once; the app validates its stored human grant."""
        plan = self.continue_run(authorization_message_id="")
        self.assertEqual(plan["authorization_message_id"], self.request["id"])

    def test_foreign_account_chat_and_legacy_runs_are_not_continuable(self):
        for changes in ({"account_id": "another-account"}, {"conversation_id": "older-chat"}, {"conversation_id": None}):
            self.previous_run(**changes)
            self.result.update(action="continue", run_id="run-one")
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                resolve_intent(self.engine)
            self.assertEqual(self.payload["previous_runs"], [])

    def test_older_original_grant_is_supplied_for_existing_run(self):
        self.previous_run()
        for _ in range(35):
            self.history.add("assistant", "Synthetic intervening discussion.")
        self.current = self.history.add("user", "Continue the remaining applications.", "running")
        self.engine.active = {"message": self.current}
        self.result.update(action="continue", run_id="run-one")
        resolve_intent(self.engine)
        self.assertNotIn(self.request["id"], [item["id"] for item in self.payload["conversation"]])
        self.assertEqual(self.payload["prior_authorizations"],
                         [{"id": self.request["id"], "role": "user", "content": self.request["text"]}])

    def test_deleted_original_grant_does_not_survive_in_run_metadata(self):
        self.previous_run()
        self.store.delete("chat_message", self.request["id"])
        self.result.update(action="continue", run_id="run-one")
        with self.assertRaises(ValueError):
            resolve_intent(self.engine)

    def test_automatic_draft_has_no_submission_authority(self):
        self.engine.active = {"task_id": "draft"}
        with self.assertRaises(ValueError):
            resolve_intent(self.engine)
        self.engine.run_agent.assert_not_called()

    def test_reset_during_interpretation_invalidates_result(self):
        def changed(*arguments):
            result = self.model(*arguments)
            self.history.clear()
            return result
        self.engine.run_agent.side_effect = changed
        with self.assertRaises(InterruptedError):
            resolve_intent(self.engine)
        self.assertEqual(self.store.all("application_run"), [])

    def test_account_change_during_interpretation_invalidates_result(self):
        def changed(*arguments):
            result = self.model(*arguments)
            self.engine.accounts.state.return_value = {"active": "another-account"}
            return result
        self.engine.run_agent.side_effect = changed
        with self.assertRaises(InterruptedError):
            resolve_intent(self.engine)

    def test_status_question_with_a_none_decision_never_returns_a_run_plan(self):
        previous = self.previous_run()
        self.current["text"] = "How many were submitted?"
        self.store.put("chat_message", self.current["id"], self.current)
        self.result.update(action="none", issues=["This asks for status; report the existing run without starting work."])
        with self.assertRaisesRegex(ValueError, "asks for status"):
            resolve_intent(self.engine)
        self.assertEqual(self.store.all("application_run"), [previous])
        self.assertEqual(self.payload["conversation"][-1]["content"], "How many were submitted?")

    def test_resume_removal_during_interpretation_invalidates_result(self):
        def changed(*arguments):
            result = self.model(*arguments)
            self.workspace.resumes.list.return_value = []
            return result
        self.engine.run_agent.side_effect = changed
        with self.assertRaises(InterruptedError):
            resolve_intent(self.engine)

    def test_malformed_model_output_is_a_readable_failure(self):
        self.engine.run_agent.return_value = ("synthetic", {"output": []})
        self.engine.run_agent.side_effect = None
        with self.assertRaisesRegex(ValueError, "could not be read"):
            resolve_intent(self.engine)


if __name__ == "__main__":
    unittest.main()
