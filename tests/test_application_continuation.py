"""Selected batches share a per-message processing boundary and one durable submission allowance."""

import unittest

from test_public_applications import PublicApplicationFixture
from applydesk.application_automation import ApplicationAutomation
from applydesk.application_submission import attempt_key


class ApplicationContinuationTests(PublicApplicationFixture):
    """Exercise actual selection, persistence and accounting using fictional local browser fixtures."""

    def setUp(self):
        super().setUp()
        self.intent["limit"] = 4
        message = self.engine.active["message"]
        message["text"] = "Submit four matching hardware applications in Boston."
        self.w.store.put("chat_message", message["id"], message)
        self.intent["instruction"] = message["text"]

    def continue_run(self, run):
        """Only a fresh human message can authorize another pass over older blocked results."""
        message = self.engine.history.add("user", "Continue the same run with the remaining selected jobs.")
        self.engine.active = {"message": message}
        self.intent.update(action="continue", run_id=run["id"])
        return message

    def test_later_selected_batches_retry_older_blocks_once_in_the_new_message(self):
        """Retrying one old blocker must not prevent the other old blockers in a later tool call."""
        jobs = [*self.jobs, self.public_job("103"), self.public_job("104")]
        identities = [job["id"] for job in jobs]
        self.fail_analysis = True
        original = self.run_public(identities)
        previous_results = original["results"][:]
        self.continue_run(original)
        first = self.run_public(identities[:1])
        self.assertEqual(len(first["results"]), 5)
        self.engine.applications = ApplicationAutomation(self.engine)
        final = self.run_public(identities[1:])
        self.assertEqual([result["job_id"] for result in final["results"]], identities + identities)
        self.assertEqual(final["results"][:4], previous_results)
        self.assertEqual((final["id"], final["attempted_count"], final["remaining_count"]), (original["id"], 0, 4))
        self.assertEqual(len(self.browsers), 8)
        self.assertEqual(self.run_public(identities), final)
        self.assertEqual(len(self.browsers), 8)
        self.assertEqual(self.w.store.all("submission_attempt"), [])

    def test_later_successful_batch_uses_only_the_original_remaining_allowance(self):
        """Historical submitted/uncertain jobs and removed jobs stay excluded after the new boundary."""
        jobs = [*self.jobs, *(self.public_job(str(index)) for index in range(103, 108))]
        self.fail_analysis = True
        original = self.run_public()
        for job, status in zip(jobs[:3], ["submitted", "submission_unknown", "submitting"]):
            identity = attempt_key(job)
            self.w.store.put("submission_attempt", identity,
                             {"id": identity, "run_id": original["id"], "job_id": job["id"], "status": status})
            self.w.store.job(job["id"], status=status)
        self.w.store.remove_jobs([jobs[3]["id"]])
        self.continue_run(original)
        first = self.run_public([jobs[4]["id"]])
        self.assertEqual((first["attempted_count"], first["remaining_count"]), (3, 1))
        self.fail_analysis = False
        final = self.run_public()
        self.assertEqual(final["results"][-1]["job_id"], jobs[5]["id"])
        self.assertEqual((final["id"], final["attempted_count"], final["submitted_count"], final["uncertain_count"]),
                         (original["id"], 4, 2, 2))
        self.assertEqual(len(self.browsers), 9)
        self.assertEqual(sum(browser.clicks for browser in self.browsers), 1)
        self.assertEqual(self.run_public([jobs[6]["id"]]), final)
        self.assertEqual(len(self.browsers), 9)
        self.assertEqual(len(self.w.store.all("application_run")), 1)

    def test_legacy_message_never_retries_without_a_new_human_continuation(self):
        """Missing metadata does not make an older message a fresh permission to repeat work."""
        self.fail_analysis = True
        legacy = self.run_public()
        legacy.pop("message_result_starts", None)
        self.w.store.put("application_run", legacy["id"], legacy)
        self.assertEqual(self.run_public(), legacy)
        self.assertEqual(len(self.browsers), 2)
        self.continue_run(legacy)
        self.run_public([self.jobs[0]["id"]])
        final = self.run_public([self.jobs[1]["id"]])
        self.assertEqual(len(final["results"]), 4)
        self.assertEqual(len(self.browsers), 4)


class ApplicationCountTests(PublicApplicationFixture):
    """Exercise unrestricted runs through the same public-source pipeline as finite requests."""

    def test_unspecified_count_passes_five_and_resumes_newly_found_jobs(self):
        self.intent["limit"] = None
        for index in range(103, 109):
            self.public_job(str(index))
        first = self.run_public()
        self.assertIsNone(first["limit"])
        self.assertIsNone(first["remaining_count"])
        self.assertEqual(first["submitted_count"], 8)
        saved = self.public_job("109")
        second = self.run_public()
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["attempted_count"], 9)
        self.assertEqual(second["results"][-1]["job_id"], saved["id"])
        self.assertEqual(sum(browser.clicks for browser in self.browsers), 9)

    def test_exact_requested_count_stops_before_next_job(self):
        self.intent["limit"] = 7
        for index in range(103, 109):
            self.public_job(str(index))
        run = self.run_public()
        self.assertEqual((run["attempted_count"], run["remaining_count"]), (7, 0))
        self.assertEqual(sum(browser.clicks for browser in self.browsers), 7)
        self.assertEqual(len(self.engine.applications._candidates()), 1)
        self.assertEqual(self.run_public()["attempted_count"], 7)

    def test_large_count_keeps_actual_remaining_allowance(self):
        self.intent["limit"] = 150
        run = self.run_public()
        self.assertEqual((run["limit"], run["attempted_count"], run["remaining_count"]), (150, 2, 148))

    def test_unrestricted_empty_tracker_returns_search_request(self):
        self.intent["limit"] = None
        self.w.store.remove_jobs([job["id"] for job in self.jobs])
        waiting = self.run_public()
        self.assertEqual(waiting["status"], "needs_jobs")
        self.assertIsNone(waiting["remaining_count"])
        self.assertEqual(self.w.store.all("application_run"), [])

    def test_removing_a_count_retains_prior_attempts(self):
        first = self.run_public()
        message = self.engine.history.add("user", "Continue without an application count limit.")
        self.engine.active = {"message": message}
        self.intent.update(action="continue", run_id=first["id"], limit=None)
        continued = self.run_public()
        self.assertEqual(continued["id"], first["id"])
        self.assertIsNone(continued["limit"])
        self.assertEqual(continued["attempted_count"], 2)
        self.assertEqual(len(self.w.store.all("application_run")), 1)


if __name__ == "__main__":
    unittest.main()
