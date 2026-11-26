"""One natural request reaches five verified native submissions without site-specific adapters."""

from unittest.mock import patch

from native_pipeline_fixture import NativePipelineFixture
from applydesk.application_automation import ApplicationAutomation
from applydesk.application_submission import attempt_key
from applydesk.storage import Store


class NativePipelineTests(NativePipelineFixture):
    """Run actual automation, native decisions, final review, reservation and receipt persistence."""

    def test_five_unseen_jobs_submit_from_one_natural_prompt(self):
        """All five durable receipts require one reserved native click and the same configured agent."""
        source = self.w.docs.asset(self.resume["source_path"]).read_bytes()
        run = self.run_applications()
        self.assertEqual((run["status"], run["submitted_count"], run["attempted_count"]), ("complete", 5, 5), run)
        self.assertEqual((run["uncertain_count"], run["remaining_count"]), (0, 0))
        self.assertEqual(len(self.windows), 5)
        self.assertEqual(set(self.roles), {"master"})
        self.assertEqual(len(self.upload_calls), 5)
        self.assertEqual(self.w.docs.asset(self.resume["source_path"]).read_bytes(), source)
        for job in self.jobs:
            window = self.windows[job["id"]]
            saved = self.w.store.get("job", job["id"])
            self.assertEqual((window.clicks, window.parked, saved["status"]), (1, True, "submitted"))
            self.assertTrue(saved["final_audit"]["passed"])
            self.assertEqual(bool(saved["native_review"]["summaries"]), window.readonly)
            self.assertEqual(saved["submission_receipt"]["method"], "native_visible_confirmation")
            self.assertEqual({method for method, _ in window.calls}, {"open", "native_observe", "native_action"})
        self.assert_durable(run, 5, 0)

    def assert_durable(self, run, submitted, uncertain):
        """A second database connection sees committed outcomes, not just returned in-memory counts."""
        store = Store(self.w.store.root)
        try:
            attempts = store.all("submission_attempt")
            self.assertEqual(len(attempts), 5)
            self.assertEqual(sum(item["status"] == "submitted" for item in attempts), submitted)
            self.assertEqual(sum(item["status"] == "submission_unknown" for item in attempts), uncertain)
            self.assertEqual({item["run_id"] for item in attempts}, {run["id"]})
            self.assertEqual(store.get("application_run", run["id"])["submitted_count"], submitted)
        finally:
            store.db.close()

    def test_missing_receipt_is_not_success_and_cannot_replay(self):
        """A model's invented success cannot bypass visible proof or replay after a process restart."""
        self.missing_receipt = self.jobs[2]["id"]
        run = self.run_applications()
        self.assertEqual((run["submitted_count"], run["attempted_count"], run["uncertain_count"]), (4, 5, 1), run)
        uncertain = self.w.store.get("job", self.missing_receipt)
        self.assertEqual(uncertain["status"], "submission_unknown")
        self.assertFalse(uncertain["submission_receipt"]["confirmed"])
        self.assertEqual(uncertain["submission_receipt"]["receipt"], "")
        attempt = self.w.store.get("submission_attempt", attempt_key(uncertain))
        self.assertEqual(attempt["status"], "submission_unknown")
        self.assert_durable(run, 4, 1)
        calls = {identity: len(window.calls) for identity, window in self.windows.items()}
        self.engine.applications = ApplicationAutomation(self.engine)
        with patch.object(self.engine.runner, "_request", side_effect=AssertionError("No replay model call")):
            repeated = self.engine.applications.start()
        self.assertEqual(repeated["id"], run["id"])
        self.assertEqual(self.engine.applications._candidates(), [])
        self.assertEqual({identity: len(window.calls) for identity, window in self.windows.items()}, calls)
        self.assertEqual(sum(window.clicks for window in self.windows.values()), 5)
