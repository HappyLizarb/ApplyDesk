"""Saved public listings use the native application pipeline and durable submission accounting."""

import json
from unittest.mock import patch

from native_pipeline_fixture import NativePipelineFixture
from recommendation_helpers import public_source, saved_listing
from test_orchestrator import response
from applydesk import ai_prompts
from applydesk.orchestrator_prompts import SELF_CHECK
from applydesk.application_client import STEP_BOUNDARY
from applydesk.chatgpt_inference import output_text
from applydesk.application_submission import attempt_key
from applydesk.application_intent import INTENT_PROMPT


class PublicApplicationFixture(NativePipelineFixture):
    """Two jobs share a synthetic authorization for one application in Boston."""

    job_count = 0
    run_public = NativePipelineFixture.run_applications

    def setUp(self):
        super().setUp()
        self.w.store.clear("job")
        self.jobs = [self.public_job("101"), self.public_job("102")]
        self.fail_analysis = False
        self.page_change = lambda page: page
        message = self.engine.active["message"]
        message["text"] = "Please send one hardware application in Boston."
        self.w.store.put("chat_message", message["id"], message)
        self.intent.update(limit=1, locations=["Boston"], instruction=message["text"], resume_id="")

    def browser(self, engine, *, job_id):
        """Allow a test to simulate a changed public posting before screening."""
        window = super().browser(engine, job_id=job_id)
        window.page = self.page_change(window.page)
        return window

    def analysis(self, data):
        result = super().analysis(data)
        if self.fail_analysis:
            result.update(identity_consistent=False, consistency_issues=["Unresolved employer identity."])
        return result

    def public_job(self, identity):
        """Save source provenance exactly as a public-page import would."""
        source = public_source("https://careers.example.test/opening?id=" + identity)
        job = self.w.store.add_job(source["url"])
        return self.w.store.job(job["id"], company=source["company"], title=source["title"],
                                public_source=source, details=source["description"], details_origin="captured")


class PublicApplicationTests(PublicApplicationFixture):
    """Check admission, observed public URLs and original source continuity."""

    def test_public_run_needs_no_platform_connection_and_keeps_tracker_order(self):
        run = self.run_public()
        self.assertEqual(run["status"], "complete")
        self.assertEqual(run["searches"], [])
        self.assertEqual(run["attempted_count"], 1)
        result = run["results"][0]
        self.assertEqual((result["job_id"], result["status"]), (self.jobs[0]["id"], "submitted"))
        self.assertTrue(result["receipt"]["confirmed"])
        self.assertEqual(result["receipt"]["attempt_id"], self.w.store.all("submission_attempt")[0]["id"])
        self.assertEqual(result["receipt"]["method"], "native_visible_confirmation")
        self.assertEqual([window.job["url"] for window in self.browsers], [self.jobs[0]["url"]])
        self.assertEqual(self.w.store.get("job", self.jobs[1]["id"])["status"], "new")
        self.assertEqual(set(self.roles), {"master"})

    def test_public_and_historical_jobs_share_saved_order(self):
        source = saved_listing()
        native = self.w.store.add_job(source["source_url"])
        self.w.store.job(native["id"], recommendation_source=source)
        candidates = self.engine.applications._candidates()
        self.assertEqual([job["id"] for job in candidates], [job["id"] for job in [*self.jobs, native]])
        self.w.store.remove_jobs([self.jobs[0]["id"]])
        self.w.store.put("submission_attempt", attempt_key(self.jobs[1]), {"status": "submission_unknown"})
        self.assertEqual([job["id"] for job in self.engine.applications._candidates()], [native["id"]])

    def test_corrupt_capture_never_opens_or_fills_that_job(self):
        source = {**self.jobs[0]["public_source"], "description": "Changed source"}
        self.w.store.job(self.jobs[0]["id"], public_source=source)
        self.intent["limit"] = 2
        run = self.run_public()
        results = {item["job_id"]: item["status"] for item in run["results"]}
        self.assertEqual(results[self.jobs[0]["id"]], "needs_review")
        self.assertNotIn(self.jobs[0]["url"], [window.job["url"] for window in self.browsers])
        self.assertEqual(results[self.jobs[1]["id"]], "submitted")

    def test_changed_public_url_stops_before_personal_fields(self):
        self.page_change = lambda page: {**page, "url": "https://careers.example.test/another-role"}
        run = self.run_public()
        self.assertEqual(run["attempted_count"], 0)
        self.assertTrue(all(result["status"] == "needs_review" for result in run["results"]))
        self.assertTrue(all(not browser.name for browser in self.browsers))

    def test_fresh_page_must_keep_captured_employer_identity(self):
        self.page_change = lambda page: {**page, "title": "Example Corp Analyst", "text": page["text"].replace("Example Co", "Example Corp")}
        run = self.run_public()
        self.assertEqual(run["attempted_count"], 0)
        self.assertTrue(all(not browser.name for browser in self.browsers))

    def test_platform_login_source_cannot_be_opened_as_a_public_job(self):
        job = self.jobs[0]
        source = public_source("https://www.linkedin.com/jobs/view/101")
        self.jobs[0] = self.w.store.job(job["id"], url=source["url"], public_source=source)
        self.w.store.remove_jobs([self.jobs[1]["id"]])
        run = self.run_public()
        self.assertEqual(run["results"][0]["status"], "needs_review")
        self.assertEqual(self.browsers, [])

    def test_public_application_instructions_are_checked_before_form_filling(self):
        quote = "Apply by email to the employer's recruiting team."
        self.page_change = lambda page: {**page, "text": page["text"] + " " + quote}
        original = self.model
        def model(payload, cancelled, emit):
            result = original(payload, cancelled, emit)
            if payload["instructions"] == SELF_CHECK + STEP_BOUNDARY + ai_prompts.ANALYZE:
                analysis = json.loads(output_text(result))
                analysis["process_restrictions"] = [{"kind": "other", "quote": quote, "text": "Applications are accepted by email."}]
                return response(json.dumps(analysis))
            return result
        self.model = model
        run = self.run_public()
        self.assertEqual(run["attempted_count"], 0)
        self.assertTrue(all(not browser.name for browser in self.browsers))
        self.assertTrue(all(any("email" in issue for issue in result["issues"]) for result in run["results"]))

    def test_empty_tracker_can_search_save_and_start_within_the_same_human_turn(self):
        self.w.store.remove_jobs([job["id"] for job in self.jobs])
        waiting = self.run_public()
        self.assertEqual((waiting["status"], waiting["remaining_count"]), ("needs_jobs", 1))
        self.assertEqual(self.w.store.all("application_run"), [])
        saved = self.public_job("103")
        run = self.run_public()
        self.assertEqual(run["results"][0]["job_id"], saved["id"])
        self.assertEqual(run["attempted_count"], 1)
        self.assertEqual(len(self.w.store.all("application_run")), 1)

    def test_empty_continuation_keeps_budget_and_can_retry_after_saving_jobs(self):
        self.intent["limit"] = 2
        self.w.store.remove_jobs([self.jobs[1]["id"]])
        first = self.run_public()
        message = self.engine.history.add("user", "Continue the same application run.")
        self.engine.active = {"message": message}
        self.intent.update(action="continue", run_id=first["id"])
        waiting = self.run_public()
        self.assertEqual((waiting["status"], waiting["remaining_count"]), ("needs_jobs", 1))
        self.assertNotIn(message["id"], self.w.store.get("application_run", first["id"])["message_ids"])
        self.public_job("103")
        resumed = self.run_public()
        self.assertEqual(resumed["id"], first["id"])
        self.assertEqual(resumed["attempted_count"], 2)
        self.assertEqual(len(self.w.store.all("application_run")), 1)

    def test_same_turn_batches_share_one_grant_without_another_intent_request(self):
        self.intent["limit"] = 5
        self.w.store.remove_jobs([self.jobs[1]["id"]])
        first = self.run_public()
        self.assertEqual(first["attempted_count"], 1)
        self.assertEqual(self.run_public(), first)
        saved = self.public_job("103")
        second = self.run_public()
        self.assertEqual((second["id"], second["attempted_count"], second["limit"]), (first["id"], 2, 5))
        self.assertEqual([item["job_id"] for item in second["results"]], [self.jobs[0]["id"], saved["id"]])
        self.assertEqual(len(self.w.store.all("application_run")), 1)
        self.assertEqual(sum(prompt == INTENT_PROMPT for prompt, _ in self.requests), 1)
        self.assertEqual(sum(browser.clicks for browser in self.browsers), 2)
        self.assertEqual(self.run_public(), second)

    def test_same_turn_batches_do_not_retry_previous_failed_jobs(self):
        self.intent["limit"] = 5
        self.fail_analysis = True
        first = self.run_public()
        self.assertEqual(first["attempted_count"], 0)
        self.fail_analysis = False
        saved = self.public_job("103")
        second = self.run_public()
        self.assertEqual((second["id"], second["attempted_count"]), (first["id"], 1))
        self.assertEqual([item["job_id"] for item in second["results"]], [job["id"] for job in self.jobs] + [saved["id"]])
        self.assertEqual(len(self.browsers), 3)

    def test_same_turn_batches_recheck_original_profile_account_and_resume(self):
        self.intent.update(limit=5, resume_id="resume", instruction="Send five applications in Boston using resume.pdf unchanged.")
        message = self.engine.active["message"]
        message["text"] = self.intent["instruction"]
        self.w.store.put("chat_message", message["id"], message)
        self.w.store.remove_jobs([self.jobs[1]["id"]])
        first = self.run_public()
        self.public_job("103")
        checks = [(patch.object(self.w, "profile", return_value={**self.w.profile(), "revision": "changed"}), InterruptedError, "Profile changed"),
                  (patch.object(self.engine.accounts, "state", return_value={"active": "different"}), InterruptedError, "Account changed"),
                  (patch.object(self.w.resumes, "selected", side_effect=ValueError("The resume changed")), ValueError, "resume changed")]
        for changed, error, reason in checks:
            with self.subTest(reason=reason), changed, self.assertRaisesRegex(error, reason):
                self.run_public()
            self.assertEqual(self.w.store.get("application_run", first["id"]), first)
            self.assertEqual(len(self.browsers), 1)
