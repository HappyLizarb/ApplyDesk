"""Unattended runs use fictional profiles, distinct model executions, and counted browser writes."""

import copy
import hashlib
import json
from unittest.mock import Mock, patch

from chat_helpers import ChatFixture
from test_orchestrator import response
from test_workflow import BrowserFixture, snapshot
from applydesk import ai_prompts
from applydesk.application_policy import SCOPE_PROMPT
from applydesk.application_intent import INTENT_PROMPT
from applydesk.application_submission import attempt_key


class AutomationTests(ChatFixture):
    def setUp(self):
        """Create a confirmed applicant, a connected account, and two observed native listings."""
        super().setUp()
        self.engine = self.w.orchestrator
        self.w.store.put("chatgpt", "accounts", {"active": "test", "accounts": [{"id": "test", "label": "Test", "client_id": "oaiapp_test", "subject": "subject", "connected": True}]})
        self.w.store.put("chatgpt_models", "test", [{"id": "test-model", "name": "Test", "thinking": []}])
        profile = self.w.profile()
        profile["industries"] = [{"name": "Publishing", "years": "0"}]
        profile["preferences"]["levels"] = ["Entry"]
        self.w.save_profile(profile)
        self.w.store.put("recommendation_preference", "handshake", {"enabled": True})
        self.jobs = [self.listing("101"), self.listing("102")]
        self.roles, self.browsers, self.requests = [], [], []
        self.fail_audit, self.fail_receipt = False, False
        message = self.engine.history.add("user", "Please send one publishing application in Boston.")
        self.engine.active = {"message": message}
        self.intent = {"action": "start", "run_id": "", "authorization_message_id": message["id"], "limit": 1,
                       "locations": ["Boston"], "instruction": message["text"], "resume_id": "", "issues": []}

    def listing(self, identity):
        """Stable listing IDs and source hashes prevent repeated or replaced captures."""
        description = "Example Co Analyst. Entry publishing role in Boston. Full-time new-grad position. Python required."
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
        self.requests.append(copy.deepcopy(payload))
        if prompt == INTENT_PROMPT:
            result = self.intent
        elif prompt == SCOPE_PROMPT:
            result = {"matches": True, "industry": "Publishing", "level": "Entry", "location": self.intent["locations"][0], "issues": [],
                      "quotes": {"industry": "publishing", "level": "Entry", "location": "" if self.intent["locations"] == ["Anywhere"] else "Boston"}}
        elif prompt in {ai_prompts.ANALYZE, ai_prompts.AUDIT_ELIGIBILITY}:
            result = {"company": "Example Co", "title": "Analyst", "details": "Summary", "country": "US", "category": "Publishing", "preferred": [],
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
        with patch.object(self.w.recommendations, "read_job", side_effect=lambda source: copy.deepcopy(source)):
            with patch("applydesk.application_automation.ApplicationSession", side_effect=self.browser):
                with patch.object(self.engine.runner, "_request", side_effect=self.model):
                    return self.engine.applications.start()

    def test_explicit_budget_drives_separate_reviews_and_exactly_one_submission(self):
        """A real qualified pipeline submits once, uses General fallback, and leaves the second job untouched."""
        run = self.run_applications()
        self.assertEqual(run["status"], "complete")
        self.assertEqual(run["results"][0]["status"], "submitted")
        self.assertEqual(sum(browser.methods.count("submit") for browser in self.browsers), 1)
        self.assertTrue({"application", "document_reviewer", "application_auditor"} <= set(self.roles))
        self.assertEqual(self.w.store.get("job", self.jobs[0]["id"])["resume_id"], "resume")
        self.assertEqual(self.w.store.get("job", self.jobs[1]["id"])["status"], "new")
        with patch.object(self.engine.runner, "_request", side_effect=AssertionError("Repeated call must not infer again")):
            self.assertEqual(self.engine.applications.start()["id"], run["id"])

    def test_named_resume_is_pinned_and_uploaded_instead_of_general(self):
        """A specifically requested active upload wins without changing its bytes or tag."""
        chosen = self.document("chosen", "resume")
        content = b"synthetic chosen resume source"
        self.w.docs.asset(chosen["source_path"]).write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        chosen.update(source_sha256=digest, sha256=digest)
        self.w.docs._save(chosen)
        self.w.resumes.add(chosen["id"])
        self.intent.update(resume_id=chosen["id"], instruction="Apply once in Boston using chosen.pdf unchanged.")
        message = self.engine.active["message"]
        message["text"] = self.intent["instruction"]
        self.w.store.put("chat_message", message["id"], message)
        run = self.run_applications()
        job = self.w.store.get("job", self.jobs[0]["id"])
        self.assertEqual((run["resume_id"], run["resume_sha256"]), (chosen["id"], digest))
        self.assertEqual((job["status"], job["resume_id"]), ("submitted", chosen["id"]))
        self.assertEqual([(item["document_id"], item["sha256"]) for item in job["attachments"]], [(chosen["id"], digest)])
        self.assertEqual(self.w.resumes.select("General")["id"], "resume")
        self.assertEqual(self.w.docs.asset(chosen["source_path"]).read_bytes(), content)

    def test_continuation_cannot_repin_changed_bytes_under_the_same_resume_id(self):
        """Updating both the source and manifest cannot silently change a pinned selection."""
        self.intent.update(resume_id="resume", instruction="Apply once in Boston using resume.pdf unchanged.")
        message = self.engine.active["message"]
        message["text"] = self.intent["instruction"]
        self.w.store.put("chat_message", message["id"], message)
        first = self.run_applications()
        changed = self.w.docs.get("resume")
        content = b"synthetic replaced resume"
        self.w.docs.asset(changed["source_path"]).write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        self.w.docs._save({**changed, "source_sha256": digest, "sha256": digest})
        self.w.docs.verify("resume")
        current = self.engine.history.add("user", "Continue with the same resume, unchanged.")
        self.engine.active = {"message": current}
        self.intent.update(action="continue", run_id=first["id"])
        with self.assertRaisesRegex(ValueError, "resume changed"):
            self.run_applications()
        self.assertEqual(self.w.store.get("application_run", first["id"])["resume_sha256"], first["resume_sha256"])
        self.assertEqual(len(self.browsers), 1)

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
        current = self.engine.history.add("user", "Please continue that run.")
        self.engine.active = {"message": current}
        self.intent.update(action="continue", run_id=run["id"])
        resumed = self.run_applications()
        self.assertEqual((resumed["id"], resumed["attempted_count"], resumed["uncertain_count"]), (run["id"], 1, 1))
        self.assertEqual(len(self.browsers), 1)

    def test_background_draft_and_out_of_range_budget_do_not_start_a_run(self):
        """The semantic result still needs a human turn and a bounded numeric budget."""
        self.intent["limit"] = 101
        with self.assertRaises(ValueError):
            self.run_applications()
        self.engine.active = {"task_id": self.task["id"]}
        with patch.object(self.engine.runner, "_request") as model, self.assertRaises(ValueError):
            self.engine.applications.start()
        model.assert_not_called()
        self.assertFalse(self.w.store.all("application_run"))

    def test_revocation_result_prevents_browser_work(self):
        """A semantic no-submit result wins over earlier permission without keyword parsing."""
        current = self.engine.history.add("user", "Actually, keep everything as drafts for now.")
        self.engine.active = {"message": current}
        self.intent.update(action="none", issues=["The user wants drafts only."])
        with self.assertRaisesRegex(ValueError, "drafts only"):
            self.run_applications()
        self.assertFalse(self.browsers)
        self.assertFalse(self.w.store.all("application_run"))
        conversation = self.input_for(INTENT_PROMPT)["conversation"]
        self.assertEqual(conversation[-1]["content"], current["text"])

    def input_for(self, prompt):
        """Read actual independent model input rather than relying on the returned plan."""
        request = next(item for item in self.requests if item["instructions"] == prompt)
        content = request["input"][0]["content"]
        return json.loads(content if isinstance(content, str) else content[0]["text"])

    def test_profile_confirmation_uses_prior_instructions_and_unrestricted_location(self):
        """A short follow-up carries the original role, cap, and location into both reviews."""
        original = self.engine.active["message"]
        original["text"] = "Apply for five full-time publishing new-grad roles, anywhere, with my existing resume."
        self.w.store.put("chat_message", original["id"], original)
        current = self.engine.history.add("user", "My profile is confirmed now; go ahead.")
        self.engine.active = {"message": current}
        self.intent.update(limit=5, locations=["Anywhere"], instruction=original["text"])
        run = self.run_applications()
        self.assertEqual(run["limit"], 5)
        self.assertEqual(run["scope"]["locations"], ["Anywhere"])
        self.assertEqual(run["instruction"], original["text"])
        conversation = self.input_for(INTENT_PROMPT)["conversation"]
        self.assertEqual([item["content"] for item in conversation], [original["text"], current["text"]])
        self.assertEqual(self.input_for(SCOPE_PROMPT)["request"], original["text"])
        self.assertEqual(run["submitted_count"], 2)

    def test_followup_continues_same_remaining_budget_after_profile_confirmation(self):
        """An interrupted run keeps its spent allowance when the user corrects their profile."""
        original = self.engine.active["message"]
        original["text"] = "Please send two publishing applications in Boston."
        self.w.store.put("chat_message", original["id"], original)
        self.intent.update(limit=2, instruction=original["text"])
        with patch.object(self.engine.applications, "_candidates", return_value=self.jobs[:1]):
            first = self.run_applications()
        self.assertEqual(first["submitted_count"], 1)
        self.w.store.put("application_run", first["id"], {**first, "status": "interrupted"})
        self.w.save_profile(self.w.profile())
        current = self.engine.history.add("user", "Profile confirmed. Please continue.")
        self.engine.active = {"message": current}
        self.intent.update(action="continue", run_id=first["id"])
        resumed = self.run_applications()
        self.assertEqual(resumed["id"], first["id"])
        self.assertEqual(resumed["submitted_count"], 2)
        self.assertEqual(resumed["attempted_count"], 2)
        self.assertEqual(resumed["profile_revision"], self.w.profile()["revision"])
        self.assertEqual(len(self.w.store.all("application_run")), 1)
        self.assertEqual(sum(browser.methods.count("submit") for browser in self.browsers), 2)

    def test_followup_cannot_reset_an_exhausted_allowance(self):
        """Continuing a completed run does not authorize another batch of the same size."""
        first = self.run_applications()
        current = self.engine.history.add("user", "Continue with that run.")
        self.engine.active = {"message": current}
        self.intent.update(action="continue", run_id=first["id"])
        resumed = self.run_applications()
        self.assertEqual(resumed["id"], first["id"])
        self.assertEqual(resumed["attempted_count"], 1)
        self.assertEqual(len(self.browsers), 1)
        self.assertEqual(self.w.store.get("job", self.jobs[1]["id"])["status"], "new")

    def test_explicitly_changed_total_cap_keeps_previous_attempts(self):
        """Raising the agreed total to two adds one remaining place, not two new places."""
        first = self.run_applications()
        current = self.engine.history.add("user", "Make that two applications total, please.")
        self.engine.active = {"message": current}
        self.intent.update(action="continue", run_id=first["id"], limit=2)
        resumed = self.run_applications()
        self.assertEqual(resumed["id"], first["id"])
        self.assertEqual((resumed["limit"], resumed["attempted_count"]), (2, 2))
        self.assertEqual(len(self.w.store.all("application_run")), 1)
        self.assertEqual(sum(browser.methods.count("submit") for browser in self.browsers), 2)

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
