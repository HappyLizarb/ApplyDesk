"""Confirmed chat evidence uses synthetic messages and independent reviewer results."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

from applydesk.chat_evidence import PROMPT, record_chat_evidence
from applydesk.intelligence import validate_eligibility
from chat_helpers import ChatFixture


class ChatEvidenceTests(ChatFixture):
    def setUp(self):
        """Use actual profile/task persistence without accessing accounts or provider services."""
        super().setUp()
        self.history = self.w.orchestrator.history
        self.question = self.history.add("assistant", "Have you personally used object-oriented SystemVerilog and debugged publishing testbenches in coursework or projects?")
        self.confirmation = self.history.add("user", "Yes, I personally did both in coursework and projects, not professional employment.")
        self.current = self.history.add("user", "Continue using my unchanged uploaded resume.", "running")
        self.engine = SimpleNamespace(w=self.w, history=self.history, active={"message": self.current},
                                      check_active=Mock(), run_agent=Mock(side_effect=self.model),
                                      accounts=SimpleNamespace(state=Mock(return_value={"active": "test-account"}), generation=1))
        self.claim = {"text": "Used object-oriented SystemVerilog and personally debugged publishing testbenches in coursework and projects.", "kind": "projects"}
        self.sources = [{"message_id": self.question["id"], "quote": self.question["text"]},
                        {"message_id": self.confirmation["id"], "quote": self.confirmation["text"]}]
        self.result = {"facts": [{**self.claim, "sources": self.sources}], "retractions": [], "issues": []}
        self.payload = None

    def model(self, role, prompt, inputs):
        """Capture actual independent-review input while returning a controlled semantic verdict."""
        self.assertEqual((role, prompt), ("application_auditor", PROMPT))
        self.payload = json.loads(inputs[0]["content"])
        response = {"output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(self.result)}]}]}
        return "synthetic-evidence-review", response

    def record(self):
        """Exercise the public evidence operation with the exact proposed personal qualification."""
        return record_chat_evidence(self.engine, {"facts": [self.claim]})

    def test_prior_confirmation_gets_durable_ids_in_draft_context(self):
        revision = self.w.profile()["revision"]
        result = self.record()
        fact = result["facts"][0]
        self.assertTrue(fact["id"].startswith("chat."))
        self.assertEqual(fact["kind"], "projects")
        self.assertEqual(fact["sources"][1]["message_id"], self.confirmation["id"])
        self.assertEqual(fact["sources"][1]["quote"], self.confirmation["text"])
        self.assertEqual(self.w.profile()["revision"], revision + 1)
        facts = self.w.chat.get(self.task["id"])["inputs"]["profile"]["facts"]
        self.assertIn(fact, facts)
        self.assertEqual(self.payload["conversation"][-1]["text"], self.current["text"])
        self.assertEqual(self.w.store.get("job", self.job["id"])["status"], "needs_review")

    def test_repeated_confirmation_is_idempotent_and_preserves_budget(self):
        run = self.w.store.put("application_run", "run", {"id": "run", "limit": 5, "status": "complete"})
        attempt = self.w.store.put("submission_attempt", "one", {"run_id": "run", "status": "submitted"})
        first, second = self.record(), self.record()
        self.assertEqual(first, second)
        self.assertEqual(len(self.w.profile()["chat_evidence"]), 1)
        self.assertEqual(self.w.store.get("application_run", "run"), run)
        self.assertEqual(self.w.store.get("submission_attempt", "one"), attempt)

    def test_assumed_years_do_not_reclassify_coursework_as_employment(self):
        """Eligibility may be assumed while stored personal facts retain their actual meaning."""
        fact = self.record()["facts"][0]
        profile = self.w.profile()
        requirement = {"text": "Object-oriented SystemVerilog", "met": True, "evidence_ids": [fact["id"]]}
        analysis = {"requirements_complete": True, "required": [requirement]}
        self.assertTrue(validate_eligibility(analysis, profile)["eligible"])
        requirement["text"] = "Two years of professional publishing experience"
        requirement["evidence_ids"] = []
        self.assertTrue(validate_eligibility(analysis, profile)["eligible"])
        self.assertEqual(self.w.profile(), profile)
        self.assertEqual(fact["kind"], "projects")

    def test_quoted_job_text_is_not_recorded_when_reviewer_rejects_confirmation(self):
        self.current["text"] = 'The job description says "Requires five years editorial experience"; do I qualify?'
        self.w.store.put("chat_message", self.current["id"], self.current)
        self.result = {"facts": [], "retractions": [], "issues": ["The quoted requirement is not a personal statement."]}
        revision = self.w.profile()["revision"]
        result = self.record()
        self.assertEqual(result["facts"], [])
        self.assertEqual(self.w.profile()["chat_evidence"], [])
        self.assertEqual(self.w.profile()["revision"], revision)
        self.assertIn("quoted job requirements", PROMPT)

    def test_forged_quotes_unknown_ids_and_assistant_only_sources_cannot_write(self):
        invalid = [[self.sources[0]], [{"message_id": "invented", "quote": "Yes"}],
                   [{"message_id": self.confirmation["id"], "quote": "Ten professional years"}], []]
        for sources in invalid:
            self.result["facts"][0]["sources"] = sources
            with self.subTest(sources=sources), self.assertRaises(ValueError):
                self.record()
        self.assertEqual(self.w.profile()["chat_evidence"], [])

    def test_unauthorized_fact_kinds_and_invented_claims_cannot_write(self):
        original = copy.deepcopy(self.result)
        for changes in ({"kind": "authorizations"}, {"kind": "disclosure"}, {"text": "Fluent in another skill"}, {"kind": []}):
            self.result = copy.deepcopy(original)
            self.result["facts"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.record()
        self.assertEqual(self.w.profile()["chat_evidence"], [])

    def test_correction_retracts_old_reference_and_rechecks_pending_jobs(self):
        old = self.record()["facts"][0]
        correction = self.history.add("user", "Correction: I have not personally debugged publishing testbenches.", "running")
        self.engine.active = {"message": correction}
        self.result = {"facts": [], "retractions": [{"fact_id": old["id"],
                       "sources": [{"message_id": correction["id"], "quote": correction["text"]}]}], "issues": []}
        revision = self.w.profile()["revision"]
        result = record_chat_evidence(self.engine, {"facts": []})
        self.assertEqual(result["retracted_ids"], [old["id"]])
        self.assertEqual(self.w.profile()["chat_evidence"], [])
        self.assertNotIn(old["id"], {fact["id"] for fact in self.w.profile()["facts"]})
        self.assertEqual(self.w.profile()["revision"], revision + 1)

    def test_profile_edits_and_chat_reset_preserve_saved_evidence_not_client_forgery(self):
        original = self.record()["facts"][0]
        profile = self.w.profile()
        profile["chat_evidence"] = [{"id": "forged", "text": "Invented", "confirmed": True}]
        self.w.save_profile(profile)
        self.history.clear()
        self.assertEqual(self.w.profile()["chat_evidence"], [original])
        self.assertIn(original, self.w.profile()["facts"])
        self.assertEqual(self.w.store.all("chat_message"), [])

    def test_new_profile_cannot_inject_chat_evidence(self):
        profile = self.w.profile()
        profile["chat_evidence"] = [{"id": "forged", "text": "Invented", "confirmed": True}]
        self.w.save_profile(profile)
        self.assertEqual(self.w.profile()["chat_evidence"], [])

    def test_unconfirmed_profile_is_not_confirmed_by_recording_one_skill(self):
        profile = self.w.profile()
        profile["confirmed"] = False
        self.w.save_profile(profile)
        self.record()
        self.assertFalse(self.w.profile()["confirmed"])
        self.assertEqual(self.w.profile()["facts"], [])

    def test_automatic_tasks_cannot_record_confirmation(self):
        for active in ({"task_id": self.task["id"]}, {"message": self.question}, None):
            self.engine.active = active
            with self.subTest(active=active), self.assertRaises(ValueError):
                self.record()
        self.engine.run_agent.assert_not_called()

    def test_reset_account_switch_and_profile_change_invalidate_review(self):
        for change in (lambda: self.history.clear(), lambda: setattr(self.engine.accounts, "generation", 2),
                       lambda: self.w.save_profile(self.w.profile())):
            with self.subTest(change=change):
                self.engine.accounts.generation = 1
                self.history.since = ""
                for message in (self.question, self.confirmation, self.current):
                    self.w.store.put("chat_message", message["id"], message)
                def changed(*arguments):
                    response = self.model(*arguments)
                    change()
                    return response
                self.engine.run_agent.side_effect = changed
                with self.assertRaises(InterruptedError):
                    self.record()
                self.assertEqual(self.w.profile()["chat_evidence"], [])

    def test_evidence_cannot_set_authorization_or_sensitive_answers(self):
        profile = self.w.profile()
        self.record()
        after = self.w.profile()
        self.assertEqual(after["authorizations"], profile["authorizations"])
        self.assertEqual(after["preferences"], profile["preferences"])
        for kind in ("authorizations", "disclosure", "identity", "declaration"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                record_chat_evidence(self.engine, {"facts": [{"text": "Yes", "kind": kind}]})

    def test_old_confirmation_is_not_lost_to_an_arbitrary_message_count(self):
        for _ in range(110):
            self.history.add("assistant", "Synthetic intervening discussion.")
        self.current = self.history.add("user", "Use the facts I already confirmed.", "running")
        self.engine.active = {"message": self.current}
        result = self.record()
        self.assertFalse(result["conversation_truncated"])
        self.assertIn(self.confirmation["id"], {item["id"] for item in self.payload["conversation"]})
        self.assertEqual(len(result["facts"]), 1)

    def test_context_limit_is_reported_instead_of_implying_missing_qualifications(self):
        self.history.add("assistant", "Synthetic intervening discussion. " * 4000)
        self.current = self.history.add("user", "Use the facts I already confirmed.", "running")
        self.engine.active = {"message": self.current}
        self.result = {"facts": [], "retractions": [], "issues": ["Earlier confirmation is outside the supplied context."]}
        result = self.record()
        self.assertTrue(result["conversation_truncated"])
        self.assertTrue(self.payload["conversation_truncated"])
        self.assertEqual(self.w.profile()["chat_evidence"], [])
