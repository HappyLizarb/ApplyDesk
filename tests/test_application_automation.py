"""Run authorization, changed documents and durable submission accounting on synthetic native windows."""

import hashlib
from unittest.mock import patch

from test_public_applications import PublicApplicationFixture
from applydesk import ai_prompts
from applydesk.application_policy import SCOPE_PROMPT
from applydesk.application_intent import INTENT_PROMPT
from applydesk.application_submission import attempt_key
from applydesk.chatgpt_transport import AccountError


class AutomationTests(PublicApplicationFixture):
    def test_selected_job_is_processed_first_without_expanding_budget(self):
        """Selecting one saved target opens just that listing and still submits at most once."""
        self.selected_ids = [self.jobs[1]["id"]]
        self.jobs.reverse()
        run = self.run_applications(self.selected_ids)
        self.assertEqual([result["job_id"] for result in run["results"]], self.selected_ids)
        self.assertEqual(run["submitted_count"], 1)
        self.assertEqual(self.w.store.get("job", self.jobs[1]["id"])["status"], "new")

    def test_one_agent_checks_listing_and_live_form_before_one_submission(self):
        """A real qualified pipeline submits once, uses General fallback, and leaves the second job untouched."""
        run = self.run_applications()
        self.assertEqual(run["status"], "complete")
        self.assertEqual(run["results"][0]["status"], "submitted")
        self.assertEqual(sum(browser.clicks for browser in self.browsers), 1)
        self.assertEqual(set(self.roles), {"master"})
        content = self.input_for(ai_prompts.ANALYZE)
        self.assertEqual(content["current_user_request"], self.engine.active["message"]["text"])
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
        chosen.update(source_sha256=digest, sha256=digest, source_size=len(content))
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
        self.w.docs._save({**changed, "source_sha256": digest, "sha256": digest, "source_size": len(content)})
        self.w.docs.verify("resume")
        current = self.engine.history.add("user", "Continue with the same resume, unchanged.")
        self.engine.active = {"message": current}
        self.intent.update(action="continue", run_id=first["id"])
        with self.assertRaisesRegex(ValueError, "resume changed"):
            self.run_applications()
        self.assertEqual(self.w.store.get("application_run", first["id"])["resume_sha256"], first["resume_sha256"])
        self.assertEqual(len(self.browsers), 1)

    def test_changed_document_bytes_never_fill_or_submit(self):
        """Removing reviewer calls preserves immutable upload validation."""
        self.w.docs.asset(self.resume["source_path"]).write_bytes(b"changed upload")
        run = self.run_applications()
        self.assertEqual(len(run["results"]), 2)
        self.assertFalse(self.w.store.all("submission_attempt"))
        self.assertTrue(all(not browser.name and not browser.clicks for browser in self.browsers))

    def test_uncertain_result_consumes_budget_and_is_excluded_from_later_runs(self):
        """A missing receipt does not become success or trigger a second irreversible click."""
        self.missing_receipt = self.jobs[0]["id"]
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

    def test_background_draft_and_invalid_count_do_not_start_a_run(self):
        """The semantic result still needs a human turn and a valid optional count."""
        self.intent["limit"] = -1
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
        return next(data for name, data in self.requests if name == prompt)

    def test_profile_confirmation_uses_prior_instructions_and_unrestricted_location(self):
        """A short follow-up carries the original role, cap, and location into the scope check."""
        original = self.engine.active["message"]
        original["text"] = "Apply for five full-time hardware new-grad roles, anywhere, with my existing resume."
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
        original["text"] = "Please send two hardware applications in Boston."
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
        self.assertEqual(sum(browser.clicks for browser in self.browsers), 2)

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
        self.assertEqual(sum(browser.clicks for browser in self.browsers), 2)

    def test_profile_edit_during_filling_stops_the_whole_run(self):
        """The final click cannot use facts from before a concurrent profile edit."""
        upload = self.upload
        def change_profile(*args):
            result = upload(*args)
            self.w.save_profile(self.w.profile())
            return result
        with patch.object(self, "upload", side_effect=change_profile), self.assertRaises(InterruptedError):
            self.run_applications()
        self.assertFalse(self.w.store.all("submission_attempt"))
        self.assertEqual(self.w.store.all("application_run")[0]["status"], "interrupted")
        self.assertTrue(all(not browser.clicks for browser in self.browsers))

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

    def test_transient_failure_resumes_only_unprocessed_jobs(self):
        """A later tool call resumes its original allowance without a second submission."""
        self.intent['limit'] = 2
        original = self.engine.applications._job
        def fail_second(job):
            if job['id'] == self.jobs[1]['id']:
                raise AccountError(503)
            return original(job)
        with patch.object(self.engine.applications, '_job', side_effect=fail_second):
            with self.assertRaises(AccountError):
                self.run_applications()
        resumed = self.run_applications()
        self.assertEqual(resumed['submitted_count'], 2)
        self.assertEqual(len(self.w.store.all('application_run')), 1)
        self.assertEqual(sum(browser.clicks for browser in self.browsers), 2)

    def test_changed_profile_still_blocks_transport_resume(self):
        """Retry permission never bypasses a changed applicant profile."""
        with patch.object(self.engine.applications, '_job', side_effect=AccountError(503)):
            with self.assertRaises(AccountError):
                self.run_applications()
        self.w.save_profile(self.w.profile())
        with self.assertRaisesRegex(InterruptedError, 'Profile changed'):
            self.run_applications()
        self.assertFalse(self.w.store.all('submission_attempt'))
