"""Application counts use synthetic listings, profiles, model replies and browser receipts."""

import unittest

import test_public_applications as public_applications


class ApplicationCountTests(unittest.TestCase):
    """Exercise unrestricted runs through the same public-source pipeline as finite requests."""

    def setUp(self):
        self.case = public_applications.PublicApplicationTests()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def test_unspecified_count_passes_five_and_resumes_newly_found_jobs(self):
        case = self.case
        case.intent["limit"] = None
        for index in range(103, 109):
            case.public_job(str(index))
        first = case.run_public()
        self.assertIsNone(first["limit"])
        self.assertIsNone(first["remaining_count"])
        self.assertEqual(first["submitted_count"], 8)
        saved = case.public_job("109")
        second = case.run_public()
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["attempted_count"], 9)
        self.assertEqual(second["results"][-1]["job_id"], saved["id"])
        self.assertEqual(sum(browser.methods.count("submit") for browser in case.browsers), 9)

    def test_exact_requested_count_stops_before_next_job(self):
        case = self.case
        case.intent["limit"] = 7
        for index in range(103, 109):
            case.public_job(str(index))
        run = case.run_public()
        self.assertEqual((run["attempted_count"], run["remaining_count"]), (7, 0))
        self.assertEqual(sum(browser.methods.count("submit") for browser in case.browsers), 7)
        self.assertEqual(len(case.engine.applications._candidates([])), 1)
        self.assertEqual(case.run_public()["attempted_count"], 7)

    def test_large_count_keeps_actual_remaining_allowance(self):
        self.case.intent["limit"] = 150
        run = self.case.run_public()
        self.assertEqual((run["limit"], run["attempted_count"], run["remaining_count"]), (150, 2, 148))

    def test_unrestricted_empty_tracker_returns_search_request(self):
        case = self.case
        case.intent["limit"] = None
        case.w.store.remove_jobs([job["id"] for job in case.jobs])
        waiting = case.run_public()
        self.assertEqual(waiting["status"], "needs_jobs")
        self.assertIsNone(waiting["remaining_count"])
        self.assertEqual(case.w.store.all("application_run"), [])

    def test_removing_a_count_retains_prior_attempts(self):
        case = self.case
        first = case.run_public()
        message = case.engine.history.add("user", "Continue without an application count limit.")
        case.engine.active = {"message": message}
        case.intent.update(action="continue", run_id=first["id"], limit=None)
        continued = case.run_public()
        self.assertEqual(continued["id"], first["id"])
        self.assertIsNone(continued["limit"])
        self.assertEqual(continued["attempted_count"], 2)
        self.assertEqual(len(case.w.store.all("application_run")), 1)


if __name__ == "__main__":
    unittest.main()
