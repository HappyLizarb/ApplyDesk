"""Selected batches share a per-message processing boundary and one durable submission allowance."""

import unittest

import test_public_applications as public_applications
from applydesk.application_automation import ApplicationAutomation
from applydesk.application_submission import attempt_key


class ApplicationContinuationTests(unittest.TestCase):
    """Exercise actual selection, persistence and accounting using fictional local browser fixtures."""

    def setUp(self):
        self.case = public_applications.PublicApplicationTests()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.case.intent["limit"] = 4
        message = self.case.engine.active["message"]
        message["text"] = "Submit four matching hardware applications in Boston."
        self.case.w.store.put("chat_message", message["id"], message)
        self.case.intent["instruction"] = message["text"]

    def continue_run(self, run):
        """Only a fresh human message can authorize another pass over older blocked results."""
        case = self.case
        message = case.engine.history.add("user", "Continue the same run with the remaining selected jobs.")
        case.engine.active = {"message": message}
        case.intent.update(action="continue", run_id=run["id"])
        return message

    def test_later_selected_batches_retry_older_blocks_once_in_the_new_message(self):
        """Retrying one old blocker must not prevent the other old blockers in a later tool call."""
        case = self.case
        jobs = [*case.jobs, case.public_job("103"), case.public_job("104")]
        identities = [job["id"] for job in jobs]
        case.fail_analysis = True
        original = case.run_public(identities)
        previous_results = original["results"][:]
        self.continue_run(original)
        first = case.run_public(identities[:1])
        self.assertEqual(len(first["results"]), 5)
        case.engine.applications = ApplicationAutomation(case.engine)
        final = case.run_public(identities[1:])
        self.assertEqual([result["job_id"] for result in final["results"]], identities + identities)
        self.assertEqual(final["results"][:4], previous_results)
        self.assertEqual((final["id"], final["attempted_count"], final["remaining_count"]), (original["id"], 0, 4))
        self.assertEqual(len(case.browsers), 8)
        self.assertEqual(case.run_public(identities), final)
        self.assertEqual(len(case.browsers), 8)
        self.assertEqual(case.w.store.all("submission_attempt"), [])

    def test_later_successful_batch_uses_only_the_original_remaining_allowance(self):
        """Historical submitted/uncertain jobs and removed jobs stay excluded after the new boundary."""
        case = self.case
        jobs = [*case.jobs, *(case.public_job(str(index)) for index in range(103, 108))]
        case.fail_analysis = True
        original = case.run_public()
        for job, status in zip(jobs[:3], ["submitted", "submission_unknown", "submitting"]):
            identity = attempt_key(job)
            case.w.store.put("submission_attempt", identity,
                             {"id": identity, "run_id": original["id"], "job_id": job["id"], "status": status})
            case.w.store.job(job["id"], status=status)
        case.w.store.remove_jobs([jobs[3]["id"]])
        self.continue_run(original)
        first = case.run_public([jobs[4]["id"]])
        self.assertEqual((first["attempted_count"], first["remaining_count"]), (3, 1))
        case.fail_analysis = False
        final = case.run_public()
        self.assertEqual(final["results"][-1]["job_id"], jobs[5]["id"])
        self.assertEqual((final["id"], final["attempted_count"], final["submitted_count"], final["uncertain_count"]),
                         (original["id"], 4, 2, 2))
        self.assertEqual(len(case.browsers), 9)
        self.assertEqual(sum(browser.methods.count("submit") for browser in case.browsers), 1)
        self.assertEqual(case.run_public([jobs[6]["id"]]), final)
        self.assertEqual(len(case.browsers), 9)
        self.assertEqual(len(case.w.store.all("application_run")), 1)

    def test_legacy_message_never_retries_without_a_new_human_continuation(self):
        """Missing metadata does not make an older message a fresh permission to repeat work."""
        case = self.case
        case.fail_analysis = True
        legacy = case.run_public()
        legacy.pop("message_result_starts", None)
        case.w.store.put("application_run", legacy["id"], legacy)
        self.assertEqual(case.run_public(), legacy)
        self.assertEqual(len(case.browsers), 2)
        self.continue_run(legacy)
        case.run_public([case.jobs[0]["id"]])
        final = case.run_public([case.jobs[1]["id"]])
        self.assertEqual(len(final["results"]), 4)
        self.assertEqual(len(case.browsers), 4)


if __name__ == "__main__":
    unittest.main()
