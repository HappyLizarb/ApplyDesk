"""Saved public listings use the same audited application pipeline as connected sources."""

import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from chat_helpers import ChatFixture
import test_application_automation as automation
from test_orchestrator import response
from test_workflow import BrowserFixture, snapshot
from applydesk import ai_prompts
from applydesk.chatgpt_inference import output_text
from applydesk.application_submission import attempt_key
from applydesk.application_intent import INTENT_PROMPT
from applydesk.public_source import validate_public_analysis, validate_public_page, validate_public_source


def public_source(url="https://careers.example.test/opening?id=101"):
    """Use a complete synthetic posting whose URL need not resemble a particular ATS."""
    description = ("Example Co Analyst. Entry publishing role in Boston. Full-time new-grad position. "
                   "Python required. Responsibilities include testing electronic publishing and documenting laboratory results.")
    return {"url": url, "company": "Example Co", "title": "Analyst", "description": description,
            "description_sha256": hashlib.sha256(description.encode()).hexdigest(),
            "captured_at": "2026-11-12T01:00:00+00:00", "complete": True}


class PublicApplicationTests(ChatFixture):
    """Reuse model fixtures while exercising actual source selection, mapping and submission accounting."""

    listing = automation.AutomationTests.listing
    model = automation.AutomationTests.model

    def setUp(self):
        """The connected-model fixture remains, while every job platform is disabled."""
        super().setUp()
        self.engine = self.w.orchestrator
        self.w.store.put("chatgpt", "accounts", {"active": "test", "accounts": [{"id": "test", "label": "Test", "client_id": "oaiapp_test", "subject": "subject", "connected": True}]})
        self.w.store.put("chatgpt_models", "test", [{"id": "test-model", "name": "Test", "thinking": []}])
        profile = self.w.profile()
        profile["industries"] = [{"name": "Publishing", "years": "0"}]
        profile["preferences"]["levels"] = ["Entry"]
        self.w.save_profile(profile)
        self.w.store.clear("job")
        self.w.store.put("recommendation_preference", "handshake", {"enabled": False})
        self.jobs = [self.public_job("101"), self.public_job("102")]
        self.roles, self.browsers, self.requests = [], [], []
        self.fail_audit, self.fail_receipt = False, False
        message = self.engine.history.add("user", "Please send one publishing application in Boston.")
        self.engine.active = {"message": message}
        self.intent = {"action": "start", "run_id": "", "authorization_message_id": message["id"], "limit": 1,
                       "locations": ["Boston"], "instruction": message["text"], "resume_id": "", "issues": []}
        self.opened = []
        self.page_change = lambda page: page

    def public_job(self, identity):
        """Save source provenance exactly as a public-page import would."""
        source = public_source("https://careers.example.test/opening?id=" + identity)
        job = self.w.store.add_job(source["url"])
        return self.w.store.job(job["id"], company=source["company"], title=source["title"],
                                public_source=source, details=source["description"], details_origin="captured")

    def browser(self, _engine, root, **options):
        """A public browser has no platform cookie source and records each write independently."""
        self.assertNotIn("application_source", options)
        job = self.w.store.get("job", Path(root).name)
        page = {**snapshot(), "url": job["url"], "text": job["public_source"]["description"]}
        browser = BrowserFixture(self.page_change(page))
        original = browser.call
        def call(method, params=None):
            if method == "open":
                self.opened.append(params["url"])
            if method == "submit":
                browser.methods.append(method)
                return {"confirmed": True, "attempt_id": params["attempt_id"], "receipt": "Your application was submitted"}
            return original(method, params)
        browser.call, browser.close = call, Mock()
        self.browsers.append(browser)
        return browser

    def run_public(self):
        """The run consumes saved listings without invoking platform discovery or source readers."""
        with patch.object(self.w.recommendations, "read_job", side_effect=AssertionError("Unexpected platform read")):
            with patch.object(self.w.recommendations, "begin", side_effect=AssertionError("Unexpected platform search")):
                with patch("applydesk.application_automation.ApplicationSession", side_effect=self.browser):
                    with patch.object(self.engine.runner, "_request", side_effect=self.model):
                        return self.engine.applications.start()

    def test_public_run_needs_no_platform_connection_and_keeps_tracker_order(self):
        run = self.run_public()
        self.assertEqual(run["status"], "complete")
        self.assertEqual(run["searches"], [])
        self.assertEqual(run["attempted_count"], 1)
        self.assertEqual(run["results"][0], {"job_id": self.jobs[0]["id"], "status": "submitted",
                                           "receipt": {"confirmed": True, "attempt_id": self.w.store.all("submission_attempt")[0]["id"],
                                                       "receipt": "Your application was submitted"}})
        self.assertEqual(self.opened, [self.jobs[0]["url"]])
        self.assertEqual(self.w.store.get("job", self.jobs[1]["id"])["status"], "new")
        self.assertTrue({"application", "document_reviewer", "application_auditor"} <= set(self.roles))

    def test_public_candidates_and_enabled_native_captures_share_saved_order(self):
        native = self.listing("103")
        candidates = self.engine.applications._candidates([])
        self.assertEqual([job["id"] for job in candidates], [job["id"] for job in self.jobs])
        candidates = self.engine.applications._candidates(["handshake"])
        self.assertEqual([job["id"] for job in candidates], [job["id"] for job in [*self.jobs, native]])
        self.w.store.remove_jobs([self.jobs[0]["id"]])
        self.w.store.put("submission_attempt", attempt_key(self.jobs[1]), {"status": "submission_unknown"})
        self.assertEqual([job["id"] for job in self.engine.applications._candidates(["handshake"])], [native["id"]])

    def test_corrupt_capture_never_opens_or_fills_that_job(self):
        source = {**self.jobs[0]["public_source"], "description": "Changed source"}
        self.w.store.job(self.jobs[0]["id"], public_source=source)
        self.intent["limit"] = 2
        run = self.run_public()
        results = {item["job_id"]: item["status"] for item in run["results"]}
        self.assertEqual(results[self.jobs[0]["id"]], "needs_review")
        self.assertNotIn(self.jobs[0]["url"], self.opened)
        self.assertEqual(results[self.jobs[1]["id"]], "submitted")

    def test_changed_public_url_stops_before_personal_fields(self):
        self.page_change = lambda page: {**page, "url": "https://careers.example.test/another-role"}
        run = self.run_public()
        self.assertEqual(run["attempted_count"], 0)
        self.assertTrue(all(result["status"] == "needs_review" for result in run["results"]))
        self.assertTrue(all("fill" not in browser.methods for browser in self.browsers))

    def test_fresh_page_must_keep_captured_employer_identity(self):
        self.page_change = lambda page: {**page, "title": "Example Corp Analyst", "text": page["text"].replace("Example Co", "Example Corp")}
        run = self.run_public()
        self.assertEqual(run["attempted_count"], 0)
        self.assertTrue(all("fill" not in browser.methods for browser in self.browsers))

    def test_public_capture_on_a_platform_host_uses_its_observed_page(self):
        job = self.jobs[0]
        source = public_source("https://www.linkedin.com/jobs/view/101")
        self.jobs[0] = self.w.store.job(job["id"], url=source["url"], public_source=source)
        self.w.store.remove_jobs([self.jobs[1]["id"]])
        run = self.run_public()
        self.assertEqual(run["results"][0]["status"], "submitted")
        self.assertEqual(self.opened, [source["url"]])

    def test_public_application_instructions_are_checked_before_form_filling(self):
        quote = "Apply by email to the employer's recruiting team."
        self.page_change = lambda page: {**page, "text": page["text"] + " " + quote}
        original = self.model
        def model(payload, cancelled, emit):
            result = original(payload, cancelled, emit)
            if payload["instructions"] == ai_prompts.ANALYZE:
                analysis = json.loads(output_text(result))
                analysis["process_restrictions"] = [{"kind": "other", "quote": quote, "text": "Applications are accepted by email."}]
                return response(json.dumps(analysis))
            return result
        self.model = model
        run = self.run_public()
        self.assertEqual(run["attempted_count"], 0)
        self.assertTrue(all("fill" not in browser.methods for browser in self.browsers))
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
        self.assertEqual(sum(item["instructions"] == INTENT_PROMPT for item in self.requests), 1)
        self.assertEqual(sum(browser.methods.count("submit") for browser in self.browsers), 2)
        self.assertEqual(self.run_public(), second)

    def test_same_turn_batches_do_not_retry_previous_failed_jobs(self):
        self.intent["limit"] = 5
        self.fail_audit = True
        first = self.run_public()
        self.assertEqual(first["attempted_count"], 0)
        self.fail_audit = False
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


class PublicSourceTests(unittest.TestCase):
    """Source checks distinguish original listing reads from observed application destinations."""

    def test_capture_hash_and_complete_fields_are_required(self):
        for change in ({"complete": False}, {"company": ""}, {"description_sha256": "wrong"},
                       {"url": "file:///tmp/example"}, {"url": "https://name:secret@example.test"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_public_source({**public_source(), **change})

    def test_canonical_tracking_aliases_preserve_the_same_listing(self):
        source = public_source()
        page = {"url": source["url"] + "&utm_source=example", "title": "Example Co — Analyst", "text": source["description"]}
        self.assertEqual(validate_public_page(source, page)["public_source"], source)

    def test_unknown_or_truncated_public_requirements_stop(self):
        source = public_source()
        page = {"url": source["url"], "title": "Example Co — Analyst", "text": source["description"]}
        for change in ({"truncated": True}, {"text_truncated": True}, {"text": "Example Co Analyst"},
                       {"text": source["description"] + " This position is no longer available."}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_public_page(source, {**page, **change})

    def test_application_destination_can_change_but_identity_must_remain(self):
        source = public_source()
        form = {"url": "https://application.example.test/123", "title": "Apply", "text": "Example Co Analyst application"}
        self.assertEqual(validate_public_page(source, form, listing=False)["url"], form["url"])
        with self.assertRaises(ValueError):
            validate_public_page(source, {**form, "text": "Another Company Analyst"}, listing=False)

    def test_analyzer_cannot_replace_the_saved_employer_or_role(self):
        validate_public_analysis(public_source(), {"company": " example   co ", "title": "ANALYST"})
        for change in ({"company": "Another Company"}, {"title": "Senior Analyst"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_public_analysis(public_source(), {"company": "Example Co", "title": "Analyst", **change})
