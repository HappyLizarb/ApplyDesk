"""Only a native rejection before input can release or retry a reserved application click."""

import tempfile
import unittest
from applydesk.application_submission import attempt_key, finish_attempt, submit_ready
from applydesk.native_browser import BrowserError
from applydesk.storage import Store
from native_pipeline_fixture import NativeReviewFixture
from types import SimpleNamespace
from unittest.mock import patch


class NativeSubmissionRecoveryTests(unittest.TestCase):
    def setUp(self):
        """Use the regular ready-form fixture with an identified native window and live tab."""
        self.form = NativeReviewFixture()
        self.form.setUp()
        self.addCleanup(self.form.tearDown)
        self.form.page.update(pid=10, window_id=21, active_tab_id="tab", generation=1)
        self.engine, self.run = self.form.ready()
        self.stale = BrowserError("The visible page changed before the action.")
        self.stale.code = "stale_observation"

    def run_form(self, responses):
        """Drive the ordinary submission entry point with observed browser responses."""
        self.form.session.call.side_effect = responses
        return submit_ready(self.engine, self.form.job, self.run, lambda: None)

    def test_observed_verification_challenge_keeps_evidence_and_explains_manual_step(self):
        """The original native attempt and employer text survive the applicant handoff."""
        page = self.form.page
        quote = "Enter your email verification code."
        after = {**page, "snapshot_id": "challenge", "text": quote}
        with patch("applydesk.application_client.ApplicationClient") as client:
            client.return_value.ask.return_value = {"status": "verification_required", "quote": quote, "reason": "Email check"}
            result = self.run_form([page, after])
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("original application page in Chrome", result["issues"][0])
        saved = self.form.store.all("submission_attempt")
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["status"], "verification_required")
        self.assertEqual(saved[0]["receipt"], result["receipt"])
        self.assertEqual(result["receipt"]["receipt"], quote)
        self.assertFalse(result["receipt"]["confirmed"])

    def test_toolbar_change_reuses_one_reservation_and_requires_receipt(self):
        page = self.form.page
        fresh = {**page, "snapshot_id": "new"}
        receipt = {**fresh, "snapshot_id": "receipt", "text": "Application received."}
        with patch("applydesk.application_client.ApplicationClient") as client:
            client.return_value.ask.return_value = {"status": "submitted", "quote": receipt["text"]}
            result = self.run_form([page, self.stale, fresh, receipt])
        self.assertEqual(result["status"], "submitted")
        clicks = [call.args[1] for call in self.form.session.call.call_args_list if call.args[0] == "native_action"]
        self.assertEqual(len(clicks), 2)
        self.assertEqual(clicks[0]["final_permit"], clicks[1]["final_permit"])
        self.assertEqual(clicks[1]["snapshot_id"], "new")

    def test_material_change_after_rejected_click_preserves_unsent_audit_and_budget(self):
        page = self.form.page
        result = self.run_form([page, self.stale, {**page, "text": "A new required agreement"}])
        self.assertTrue(result["click_not_sent"])
        self.assertEqual(self.form.store.all("submission_attempt"), [])
        self.assertEqual(self.form.store.all("unissued_submission")[0]["status"], "not_sent")
        self.assertEqual(self.form.store.get("job", self.form.job["id"])["status"], "needs_review")

    def test_error_checking_receipt_after_dispatch_keeps_slot_and_blocks_replay(self):
        with patch("applydesk.application_submission._receipt", side_effect=self.stale), self.assertRaises(BrowserError):
            self.run_form([self.form.page, self.form.page])
        self.assertEqual(self.form.store.all("submission_attempt")[0]["status"], "submission_unknown")
        self.assertEqual(self.form.store.all("unissued_submission"), [])

    def test_obstructed_final_click_releases_only_a_proven_unsent_attempt(self):
        """A failed native hit test cannot become either a counted submission or an automatic repeat click."""
        error = BrowserError("The visible control moved or is covered. Observe again.")
        error.code = "click_obstructed_before_dispatch"
        result = self.run_form([self.form.page, error])
        self.assertTrue(result["click_not_sent"])
        self.assertEqual(self.form.store.all("submission_attempt"), [])
        self.assertEqual(self.form.store.all("unissued_submission")[0]["status"], "not_sent")

    def test_same_form_field_error_preserves_outstanding_attempt_for_late_receipt(self):
        """A required-field rejection is distinct from a pre-click cancellation and a receipt."""
        page = self.form.page
        error = "Error: Select at least one value."
        checkbox = {"id": "consent", "role": "AXCheckBox", "required": True, "enabled": True, "value": 0, "label": "I consent"}
        after = dict(page, snapshot_id="rejected", elements=[*page["elements"], checkbox,
                     {"id": "error", "role": "AXStaticText", "value": error}])
        result = {"status": "field_error", "field_id": "consent", "quote": error}
        with patch("applydesk.application_client.ApplicationClient") as client:
            client.return_value.ask.return_value = result
            self.assertEqual(self.run_form([page, after])["status"], "submission_unknown")
        self.assertEqual(self.form.store.all("submission_attempt")[0]["receipt"]["field_rejection"]["error"], error)
        from applydesk.application_submission import rejected_field
        for changed in ({"url": "https://other.test/"}, {"active_tab_id": "other"}, {"loading": True},
                        {"elements": [checkbox]}, {"snapshot_id": page["snapshot_id"]}):
            self.assertIsNone(rejected_field(page, {**after, **changed}, result, "final"))
        self.assertIsNone(rejected_field(dict(page, text=error), after, result, "final"))


class NativeSubmissionReceiptTests(NativeReviewFixture, unittest.TestCase):
    """Check which final observations may record a receipt or reserve an attempt."""

    def test_only_new_visible_receipt_counts_and_duplicate_is_not_clicked(self):
        engine, run = self.ready()
        answer = {"status": "submitted", "quote": "Your application was received."}
        with patch("applydesk.application_client.ApplicationClient") as client:
            client.return_value.ask.return_value = answer
            self.assertEqual(submit_ready(engine, self.job, run, lambda: None)["status"], "submitted")
        self.assertEqual(self.store.get("submission_attempt", attempt_key(self.job))["status"], "submitted")
        calls = len(self.session.call.mock_calls)
        with self.assertRaises(ValueError):
            submit_ready(engine, self.job, run, lambda: None)
        self.assertEqual(len(self.session.call.mock_calls), calls + 1)

    def test_fabricated_receipt_and_click_failure_remain_uncertain(self):
        engine, run = self.ready()
        with patch("applydesk.application_client.ApplicationClient") as client:
            client.return_value.ask.return_value = {"status": "submitted", "quote": "Invented success"}
            self.assertEqual(submit_ready(engine, self.job, run, lambda: None)["status"], "submission_unknown")

    def test_changed_review_snapshot_cannot_reserve_attempt(self):
        engine, run = self.ready()
        self.session.call.side_effect = lambda *args: {**self.page, "snapshot_id": "changed"}
        with self.assertRaisesRegex(ValueError, "changed after review"):
            submit_ready(engine, self.job, run, lambda: None)
        self.assertEqual(self.store.all("submission_attempt"), [])


class VerificationTests(unittest.TestCase):
    def test_verification_is_not_a_submission_or_an_erased_attempt(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            self.addCleanup(store.close)
            job = store.add_job('https://employer.example/job/123')
            attempt = {"id": "attempt-1", "job_id": job["id"], "status": "submitting"}
            receipt = {"confirmed": False, "verification_required": True, "issue": "An email code is required.", "receipt": ""}
            result = finish_attempt(SimpleNamespace(store=store), job, attempt, 'verification_required', receipt)
            self.assertEqual(result['status'], 'needs_review')
            self.assertEqual(store.get('job', job['id'])['progress'], 'An email code is required.')
            self.assertEqual(store.get('submission_attempt', attempt_key(job))['id'], 'attempt-1')
            self.assertEqual(store.get('submission_attempt', attempt_key(job))['status'], 'verification_required')
