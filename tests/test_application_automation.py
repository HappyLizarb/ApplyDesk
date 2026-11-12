"""Unattended runs use fictional profiles, distinct model executions, and counted browser writes."""

import copy
import hashlib
import json
from unittest.mock import Mock, patch

from chat_helpers import ChatFixture
from test_orchestrator import response
from test_workflow import BrowserFixture, snapshot
from applydesk import ai_prompts
from applydesk.application_policy import SCOPE_PROMPT, authorized_request
from applydesk.application_submission import attempt_key


class AutomationTests(ChatFixture):
    def setUp(self):
        """Create a confirmed applicant, a connected account, and two observed native listings."""
        super().setUp()
        self.engine = self.w.orchestrator
        self.w.store.put("chatgpt", "accounts", {"active": "test", "accounts": [{"id": "test", "label": "Test", "client_id": "oaiapp_test", "subject": "subject", "connected": True}]})
        self.w.store.put("chatgpt_models", "test", [{"id": "test-model", "name": "Test", "thinking": []}])
        profile = self.w.profile()
        profile["industries"] = [{"name": "Hardware", "years": "0"}]
        profile["preferences"]["levels"] = ["Entry"]
        self.w.save_profile(profile)
        self.w.store.put("recommendation_preference", "handshake", {"enabled": True})
        self.jobs = [self.listing("101"), self.listing("102")]
        self.roles, self.browsers = [], []
        self.fail_audit, self.fail_receipt = False, False
        self.engine.active = {"message": {"id": "permission-one", "role": "user", "text": "Submit up to 1 application in Boston."}}

    def listing(self, identity):
        """Stable listing IDs and source hashes prevent repeated or replaced captures."""
        description = "Example Co Analyst. Entry hardware role in Boston. Python required."
        source = {"platform": "handshake", "job_id": identity, "source_url": "https://app.joinhandshake.com/job-search/" + identity,
                  "company": "Example Co", "title": "Analyst", "description": description, "complete": True, "issues": [],
                  "captured_at": "2026-10-29T01:00:00+00:00", "description_sha256": hashlib.sha256(description.encode()).hexdigest(), "employer_url": ""}
        job = self.w.store.add_job(source["source_url"])
        return self.w.store.job(job["id"], recommendation_source=source, details=description, company="Example Co", title="Analyst")

    def browser(self, _engine, _root, **_options):
        """The submission counter observes controller actions rather than its reported success."""
        page = snapshot()
        index = len(self.browsers)
        page.update(url=self.jobs[index]["url"], application_source="handshake")
        browser = BrowserFixture(page)
        if getattr(self, "change_profile", False):
            browser.after_fill = lambda: self.w.save_profile(self.w.profile())
        original = browser.call
        def call(method, params=None):
            if method == "submit":
                browser.methods.append(method)
                return {"confirmed": not self.fail_receipt, "attempt_id": params["attempt_id"], "receipt": "Your application was submitted" if not self.fail_receipt else ""}
            return original(method, params)
        browser.call, browser.close = call, Mock()
        self.browsers.append(browser)
        return browser

    def model(self, payload, _cancelled, _emit):
        """Every check has its own provider request with no access to the other reviewer session."""
        prompt = payload["instructions"]
        self.roles.append(self.engine.runner.current["role"])
        if prompt == SCOPE_PROMPT:
            result = {"matches": True, "industry": "Hardware", "level": "Entry", "location": "Boston", "issues": [],
                      "quotes": {"industry": "hardware", "level": "Entry", "location": "Boston"}}
        elif prompt in {ai_prompts.ANALYZE, ai_prompts.AUDIT_ELIGIBILITY}:
            result = {"company": "Example Co", "title": "Analyst", "details": "Summary", "country": "US", "category": "Hardware", "preferred": [],
                      "cover_letter_required": False, "requirements_complete": True, "required": [{"text": "Python required.", "met": True, "evidence_ids": ["skills.0"]}],
                      "passed": True, "issues": [], "process_complete": True, "identity_consistent": True, "employment_type_consistent": True,
                      "consistency_issues": [], "process_restrictions": []}
        elif prompt == ai_prompts.AUDIT_DOCUMENT:
            result = {"passed": not self.fail_audit, "issues": ["Unverified claim"] if self.fail_audit else [],
                      "block_checks": [{"id": "line:0", "personal_claims": True, "fact_ids": ["identity.legal_name"]}]}
        else:
            self.assertIn(prompt, {ai_prompts.LAYOUT, ai_prompts.AUDIT_FIELDS})
            result = {"passed": True, "issues": []}
        return response(json.dumps(result))

    def run_applications(self):
        """Exercise scope checks, real mapping, independent audits, readiness, and durable submission."""
        with patch("applydesk.application_automation.find_jobs", return_value={"status": "complete", "platform": "handshake"}):
            with patch.object(self.w.recommendations, "read_job", side_effect=lambda source: copy.deepcopy(source)):
                with patch("applydesk.application_automation.ApplicationSession", side_effect=self.browser):
                    with patch.object(self.engine.runner, "_request", side_effect=self.model):
                        return self.engine.applications.start(1, ["Boston"])

    def test_explicit_budget_drives_separate_reviews_and_exactly_one_submission(self):
        """A real qualified pipeline submits once, uses General fallback, and leaves the second job untouched."""
        run = self.run_applications()
        self.assertEqual(run["status"], "complete")
        self.assertEqual(run["results"][0]["status"], "submitted")
        self.assertEqual(sum(browser.methods.count("submit") for browser in self.browsers), 1)
        self.assertTrue({"application", "document_reviewer", "application_auditor"} <= set(self.roles))
        self.assertEqual(self.w.store.get("job", self.jobs[0]["id"])["resume_id"], "resume")
        self.assertEqual(self.w.store.get("job", self.jobs[1]["id"])["status"], "new")
        self.assertEqual(self.engine.applications.start(1, ["Boston"])["id"], run["id"])

    def test_failed_independent_review_never_fills_or_submits(self):
        """A failed factual audit cannot be waived by the master or the document reviewer."""
        self.fail_audit = True
        run = self.run_applications()
        self.assertEqual(len(run["results"]), 2)
        self.assertFalse(self.w.store.all("submission_attempt"))
        self.assertTrue(all("fill" not in browser.methods and "submit" not in browser.methods for browser in self.browsers))

    def test_uncertain_result_consumes_budget_and_is_excluded_from_later_runs(self):
        """A missing receipt does not become success or trigger a second irreversible click."""
        self.fail_receipt = True
        run = self.run_applications()
        self.assertEqual(run["results"][0]["status"], "submission_unknown")
        self.assertEqual(len(self.browsers), 1)
        self.assertEqual(self.w.store.get("submission_attempt", attempt_key(self.jobs[0]))["status"], "submission_unknown")
        self.assertEqual([job["id"] for job in self.engine.applications._candidates(["handshake"])], [self.jobs[1]["id"]])

    def test_automatic_draft_or_invented_limit_cannot_authorize_submission(self):
        """Only a current human message grants a budget; tool arguments cannot enlarge it."""
        with self.assertRaisesRegex(ValueError, "explicit limit"):
            self.engine.applications.start(2, ["Boston"])
        with self.assertRaisesRegex(ValueError, "current chat"):
            self.engine.applications.start(1, ["London"])
        self.engine.active = {"task_id": self.task["id"]}
        with self.assertRaisesRegex(ValueError, "new chat request"):
            self.engine.applications.start(1, ["Boston"])
        self.assertFalse(self.w.store.all("application_run"))

    def test_negative_request_does_not_grant_a_submission_budget(self):
        """An explicit no-submit instruction cannot be ignored because a count also appears."""
        with self.assertRaises(ValueError):
            authorized_request("Do not submit 5 applications in Boston", 5, ["Boston"])

    def test_profile_edit_during_filling_stops_the_whole_run(self):
        """The final click cannot use facts from before a concurrent profile edit."""
        self.change_profile = True
        with self.assertRaises(InterruptedError):
            self.run_applications()
        self.assertFalse(self.w.store.all("submission_attempt"))
        self.assertEqual(self.w.store.all("application_run")[0]["status"], "interrupted")
        self.assertTrue(all("submit" not in browser.methods for browser in self.browsers))

    def test_startup_preserves_crashed_attempt_as_uncertain(self):
        """A process restart cannot resume a click whose outcome may have reached the employer."""
        from applydesk.application_automation import ApplicationAutomation
        job = self.jobs[0]
        self.w.store.put("submission_attempt", attempt_key(job), {"id": "attempt", "run_id": "run", "job_id": job["id"], "status": "submitting"})
        self.w.store.put("application_run", "run", {"id": "run", "status": "running"})
        recovered = ApplicationAutomation(self.engine)
        self.assertEqual(self.w.store.get("submission_attempt", attempt_key(job))["status"], "submission_unknown")
        self.assertEqual(self.w.store.get("application_run", "run")["status"], "interrupted")
        self.assertNotIn(job["id"], [item["id"] for item in recovered._candidates(["handshake"])])
