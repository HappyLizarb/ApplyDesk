"""Generic review and durable submission checks use an unseen employer and read-only summary."""

import copy
import hashlib
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from applydesk.agent_browser_review import validate_review
from applydesk.application_submission import attempt_key, submit_ready
from applydesk.storage import Store


class AgentReviewTests(unittest.TestCase):
    """The fixture has no editable fields and intentionally uses a plain Submit button."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.source = Path(self.directory.name) / "resume.pdf"
        self.source.write_bytes(b"unchanged applicant resume")
        self.digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.profile = {"revision": 3, "confirmed": True, "facts": [
            {"id": "name", "text": "Alex Test", "confirmed": True}]}
        self.job = {"id": "job", "url": "https://unseen.example.test/careers/92", "company": "Example Co",
                    "title": "Hardware Graduate", "resume_id": "resume-v1", "cover_id": None,
                    "profile_revision": 3, "status": "ready"}
        self.page = {"snapshot_id": "observed-review", "url": self.job["url"], "text":
                     "Example Co\nHardware Graduate\nName: Alex Test\nResume: resume.pdf\nSubmit", "elements": [
                         {"id": "final", "tag": "button", "type": "submit", "text": "Submit",
                          "label": "Submit", "visible": True, "disabled": False, "context": "Submit", "form_id": "application"}]}
        self.ledger = [{"element_id": "earlier-resume", "document_id": "resume-v1", "kind": "resume",
                        "filename": "resume.pdf", "size": self.source.stat().st_size, "sha256": self.digest,
                        "selected_file_verified": True, "upload_confirmed": False,
                        "files": [{"name": "resume.pdf", "size": self.source.stat().st_size, "type": "application/pdf", "sha256": self.digest}]}]
        self.review = {"final_element_id": "final", "company_quote": "Example Co", "title_quote": "Hardware Graduate",
                       "answers": [], "summaries": [{"quote": "Name: Alex Test", "fact_ids": ["name"]}],
                       "resume_quote": "Resume: resume.pdf", "required_complete": True}
        self.store = Store(Path(self.directory.name) / "store")
        self.store.put("job", "job", self.job)
        docs = Mock(get=Mock(return_value={"source_path": "resume.pdf", "source_sha256": self.digest}), asset=Mock(return_value=self.source))
        self.workspace = SimpleNamespace(store=self.store, docs=docs, profile=lambda: self.profile, lock=threading.RLock(), sessions={})

    def tearDown(self):
        self.store.db.close()
        self.directory.cleanup()

    def validate(self):
        return validate_review(self.workspace, self.job, self.profile, self.page, self.ledger, self.review)

    def ready(self):
        self.job.update(final_audit=self.validate(), attachments=self.ledger, agent_browser_review=self.review,
                        last_snapshot=self.page["snapshot_id"])
        self.store.put("job", "job", self.job)
        self.session = Mock()
        self.session.call.side_effect = lambda method, params=None: self.page if method == "agent_observe" else {
            "confirmed": True, "receipt": "Your application was received", "attempt_id": params["attempt_id"]}
        self.workspace.sessions["job"] = self.session
        return SimpleNamespace(w=self.workspace), {"id": "run", "limit": 1, "profile_revision": 3}

    def test_unseen_read_only_review_accepts_verified_history_without_fabricated_dom_hash(self):
        audit = self.validate()
        self.assertTrue(audit["passed"])
        self.assertEqual(audit["field_ids"], [])
        self.assertEqual(audit["summary_quotes"], ["Name: Alex Test"])
        self.assertNotIn("files", self.page["elements"][0])

    def test_current_profile_and_source_bytes_must_still_match(self):
        self.profile["revision"] = 4
        with self.assertRaisesRegex(ValueError, "profile changed"):
            self.validate()
        self.profile["revision"] = 3
        self.source.write_bytes(b"edited resume")
        with self.assertRaisesRegex(ValueError, "bytes changed"):
            self.validate()

    def test_changed_document_selection_is_rejected(self):
        self.store.job("job", resume_id="different")
        with self.assertRaisesRegex(ValueError, "resume id changed"):
            self.validate()

    def test_quotes_cannot_be_invented_or_for_another_employer(self):
        for field, value in (("company_quote", "Example"), ("title_quote", "Hardware Senior"), ("resume_quote", "Resume: other.pdf")):
            with self.subTest(field=field):
                original = self.review[field]
                self.review[field] = value
                with self.assertRaises(ValueError):
                    self.validate()
                self.review[field] = original

    def test_unknown_or_unconfirmed_summary_facts_are_rejected(self):
        self.review["summaries"][0]["fact_ids"] = ["invented"]
        with self.assertRaisesRegex(ValueError, "unknown"):
            self.validate()
        self.review["summaries"][0]["fact_ids"] = ["name"]
        self.profile["facts"][0]["confirmed"] = False
        with self.assertRaisesRegex(ValueError, "unconfirmed"):
            self.validate()

    def test_current_populated_answers_need_exact_readbacks_and_evidence(self):
        self.page["elements"].append({"id": "name-input", "tag": "input", "type": "text", "value": "Alex Test",
                                      "required": True, "label": "Full name", "form_id": "application"})
        with self.assertRaisesRegex(ValueError, "missing from the self-check"):
            self.validate()
        answer = {"element_id": "name-input", "value": "Someone Else", "fact_ids": ["name"]}
        self.review["answers"] = [answer]
        with self.assertRaisesRegex(ValueError, "differs"):
            self.validate()
        answer["value"] = "Alex Test"
        self.assertTrue(self.validate()["passed"])

    def test_duplicate_or_unverified_upload_ledger_is_rejected(self):
        self.ledger.append(copy.deepcopy(self.ledger[0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.validate()
        self.ledger.pop()
        self.ledger[0]["selected_file_verified"] = False
        with self.assertRaisesRegex(ValueError, "file-byte readback"):
            self.validate()

    def test_current_file_hash_mismatch_is_rejected(self):
        self.page["elements"].append({"id": "earlier-resume", "tag": "input", "type": "file",
                                      "files": [{**self.ledger[0]["files"][0], "sha256": "a" * 64}]})
        with self.assertRaisesRegex(ValueError, "current file control differs"):
            self.validate()

    def test_missing_resume_or_final_cannot_be_ready(self):
        self.ledger.clear()
        with self.assertRaisesRegex(ValueError, "no verified upload"):
            self.validate()
        self.page["elements"].clear()
        with self.assertRaisesRegex(ValueError, "final control is missing"):
            self.validate()

    def test_sign_in_cannot_submit_but_implicit_accuracy_terms_can(self):
        self.page["elements"][0]["authentication_form"] = True
        with self.assertRaisesRegex(ValueError, "sign-in control"):
            self.validate()
        self.page["elements"][0]["authentication_form"] = False
        self.page["text"] += "\nBy submitting you certify these answers."
        self.assertTrue(self.validate()["passed"])

    def test_footer_links_and_other_forms_do_not_block_the_application(self):
        self.page["text"] += "\nPrivacy notice\nTerms of Service\nSign In"
        self.page["elements"][0]["context"] += " Privacy notice Terms of Service Sign In"
        self.page["elements"].append({"id": "search", "tag": "input", "type": "search", "value": "hardware", "form_id": "navigation"})
        self.page["elements"].append({"id": "login", "tag": "input", "type": "password", "required": True, "form_id": "signin"})
        self.page["errors"] = [{"id": "login", "text": "Enter password", "invalid": True}]
        self.page["alerts"] = [{"id": "upload-status", "text": "Upload successful"}]
        self.assertTrue(self.validate()["passed"])

    def test_application_validation_and_required_terms_remain_blocked(self):
        self.page["elements"].append({"id": "consent", "tag": "input", "type": "checkbox", "label": "Terms of Service",
                                      "checked": False, "required": True, "form_id": "application"})
        self.page["errors"] = [{"id": "consent", "text": "Please check", "invalid": True}]
        with self.assertRaisesRegex(ValueError, "invalid answer.*required application answer is empty"):
            self.validate()

    def test_new_receipt_persists_through_existing_attempt_ledger(self):
        engine, run = self.ready()
        result = submit_ready(engine, self.job, run, lambda: None)
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(self.store.get("submission_attempt", attempt_key(self.job))["status"], "submitted")
        self.assertEqual(self.session.call.call_args.args[0], "agent_submit")

    def test_stale_observation_blocks_before_reservation(self):
        engine, run = self.ready()
        self.page["snapshot_id"] = "changed"
        with self.assertRaisesRegex(ValueError, "changed after its final review"):
            submit_ready(engine, self.job, run, lambda: None)
        self.assertEqual(self.store.all("submission_attempt"), [])

    def test_existing_attempt_never_clicks_again(self):
        engine, run = self.ready()
        self.store.put("submission_attempt", attempt_key(self.job), {"id": "old", "status": "submission_unknown"})
        with self.assertRaisesRegex(ValueError, "already has a submission attempt"):
            submit_ready(engine, self.job, run, lambda: None)
        self.assertEqual(self.session.call.call_count, 1)

    def test_transport_failure_retains_uncertain_attempt(self):
        engine, run = self.ready()
        self.session.call.side_effect = [self.page, TimeoutError("lost response")]
        with self.assertRaises(TimeoutError):
            submit_ready(engine, self.job, run, lambda: None)
        self.assertEqual(self.store.get("submission_attempt", attempt_key(self.job))["status"], "submission_unknown")

    def test_receipt_without_attempt_binding_is_not_success(self):
        engine, run = self.ready()
        self.session.call.side_effect = [self.page, {"confirmed": True, "receipt": "Thanks", "attempt_id": "other"}]
        self.assertEqual(submit_ready(engine, self.job, run, lambda: None)["status"], "submission_unknown")


if __name__ == "__main__":
    unittest.main()
