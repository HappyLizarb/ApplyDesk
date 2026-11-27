"""Draft-only execution, evidence gates, stale reviews, and credential-free persistence."""

import copy
from unittest.mock import patch

from chat_helpers import ChatFixture
from test_documents import saved_letter
from applydesk.workspace import Workspace
from test_workspace import FakeSecrets
from applydesk.agent_contract import profile_draft
from applydesk.agent_context import digest


class ChatTaskTests(ChatFixture):
    def test_full_draft_and_two_reviews_never_approve_application(self):
        """Even reported passing subagents cannot mark a job ready or submitted."""
        with patch.object(self.w, "client", side_effect=AssertionError("Provider access")):
            context = self.w.chat.submit(self.payload())
            self.assertEqual(context["task"]["status"], "draft_ready")
            result = context["task"]
        self.assertEqual(result["status"], "draft_ready")
        self.assertEqual(self.w.chat.get(self.task["id"])["next_action"], "await_user")
        self.assertEqual(self.w.store.get("job", self.job["id"])["status"], "draft")
        task = self.w.chat.get(self.task["id"])["task"]
        self.assertTrue(all(not review["identity_verified"] for review in task["reviews"].values()))
        self.assertEqual(self.w.chat.enqueue({"kind": "application", "target_id": self.job["id"]})["id"], task["id"])
        self.assertNotIn("disability", str(self.w.chat.get(task["id"])["inputs"]))

    def test_unknown_fields_and_invented_requirements_or_evidence_are_rejected(self):
        """A chat result cannot smuggle ready status or replace actual supporting evidence."""
        cases = []
        payload = self.payload();payload["status"] = "ready";cases.append(payload)
        payload = self.payload();payload["result"]["analysis"]["required"][0]["evidence_ids"] = ["invented"];cases.append(payload)
        payload = self.payload();payload["result"]["analysis"]["required"][0]["text"] = "Made up";cases.append(payload)
        for payload in cases:
            with self.assertRaises(ValueError):
                self.w.chat.submit(payload)
        self.assertIsNone(self.w.chat.get(self.task["id"])["task"]["result"])

    def test_saved_draft_round_trips_using_current_listing_as_its_source(self):
        """A copied draft can be revised, but only the saved employer listing supports a quote."""
        draft = self.w.chat.submit(self.payload())["task"]["result"]
        latest = "Required: Python. Updated posting."
        self.w.store.job(self.job["id"], details=latest)
        payload = {**self.payload(), "result": draft}
        saved = self.w.chat.submit(payload)["task"]
        self.assertEqual(saved["draft_revision"], 2)
        self.assertEqual(saved["result"]["analysis"]["source_text"], latest)
        draft["analysis"]["source_text"] = "Forged requirement"
        draft["analysis"]["required"][0]["text"] = "Forged requirement"
        with self.assertRaisesRegex(ValueError, "quote"):
            self.w.chat.submit({**self.payload(), "result": draft})

    def test_failed_rework_waits_at_its_version_without_discarding_the_prior_draft(self):
        """New inputs can reopen a blocker without reprocessing the same revision."""
        saved = self.w.chat.submit(self.payload())["task"]
        self.w.store.job(self.job["id"], details="Required: Python. Revised listing.")
        task_id = self.task["id"]
        changed = self.w.chat.get(task_id)
        self.assertEqual(changed["task"]["status"], "stale")
        self.w.chat.block({"task_id": task_id, "version": changed["version"], "issues": ["Invalid draft"]})
        blocked = self.w.chat.get(task_id)
        self.assertEqual((blocked["task"]["status"], blocked["next_action"]), ("needs_input", "resolve_input"))
        self.assertEqual(blocked["task"]["result"], saved["result"])
        self.assertEqual(blocked["task"]["draft_revision"], 1)
        with patch.object(self.w.orchestrator, "run_agent") as model:
            with self.assertRaisesRegex(ValueError, "blocker"):
                self.w.orchestrator.workflow.prepare(task_id, "Try the same inputs")
            model.assert_not_called()
        self.w.store.job(self.job["id"], details="Required: Python. Another revision.")
        changed = self.w.chat.get(task_id)
        self.assertEqual((changed["task"]["status"], changed["next_action"]), ("stale", "draft"))
        self.assertEqual(self.w.chat.submit(self.payload())["task"]["draft_revision"], 2)

    def test_blocked_attachment_change_and_explicit_retry_keep_the_saved_draft(self):
        """Both replacing the selected attachment and explicit retry permit another draft attempt."""
        original = self.w.chat.submit(self.payload())["task"]
        task_id = self.task["id"]
        self.w.store.job(self.job["id"], resume_id=None)
        self.w.chat.block({"task_id": task_id, "version": self.payload()["version"], "issues": ["Missing attachment"]})
        self.assertEqual(self.w.chat.get(task_id)["next_action"], "resolve_input")
        self.w.store.job(self.job["id"], resume_id=self.resume["id"])
        self.assertEqual(self.w.chat.get(task_id)["next_action"], "draft")
        self.w.store.job(self.job["id"], details="Required: Python. Revised listing.")
        self.w.chat.block({"task_id": task_id, "version": self.payload()["version"], "issues": ["Answer required"]})
        self.w.orchestrator.retry({"task_id": task_id})
        retried = self.w.chat.get(task_id)
        self.assertEqual((retried["task"]["status"], retried["next_action"]), ("queued", "draft"))
        self.assertEqual(retried["task"]["result"], original["result"])
        self.assertEqual(retried["task"]["document_ids"], original["document_ids"])
        self.assertEqual(retried["task"]["issues"], [])
        self.assertEqual(self.w.chat.submit(self.payload())["task"]["draft_revision"], 2)

    def test_assumed_skill_allows_draft_but_not_invented_work_years(self):
        """An unlisted skill never supplies a specific professional history answer."""
        payload = self.payload()
        payload["result"]["analysis"]["required"][0].update(met=True, evidence_ids=[])
        payload["result"].update(form_text="How many years of professional Python experience?", answers=[
            {"question": "How many years of professional Python experience?", "answer": "5", "fact_ids": []}])
        with self.assertRaisesRegex(ValueError, "confirmed supporting facts"):
            self.w.chat.submit(payload)
        payload["result"]["answers"] = []
        context = self.w.chat.submit(payload)
        self.assertEqual(context["task"]["status"], "draft_ready")
        self.assertTrue(context["task"]["result"]["analysis"]["required"][0]["met"])

    def test_failed_requirement_cannot_be_saved_as_a_prepared_draft(self):
        """An explicitly unmet requirement stays blocked before any draft artifact is saved."""
        payload = self.payload()
        payload["result"]["analysis"]["required"][0].update(met=False, evidence_ids=[])
        with self.assertRaisesRegex(ValueError, "mandatory requirement"):
            self.w.chat.submit(payload)
        self.assertIsNone(self.w.chat.get(self.task["id"])["task"]["result"])

    def test_legacy_pending_reviews_become_prepared_without_model_calls(self):
        """Existing drafts no longer wait for retired agents, while input changes stay stale."""
        self.w.chat.submit(self.payload())
        task = self.w.store.get("chat_task", self.task["id"])
        task["status"] = "awaiting_review"
        self.w.store.put("chat_task", task["id"], task)
        context = self.w.chat.get(self.task["id"])
        self.assertEqual(context["task"]["status"], "draft_ready")
        self.assertEqual(context["next_action"], "await_user")
        self.assertNotIn("pending_review_roles", context)

    def test_legacy_completed_review_preserves_exact_artifact_binding(self):
        """Opening an old draft keeps it prepared until its source or preview actually changes."""
        context = self.w.chat.submit(self.payload())
        material = context["review"]
        checks = material["expected_checks"]
        legacy = {**material, "expected_checks": {"document_reviewer": checks["layout"], "application_auditor": checks["facts"]}}
        task = self.w.store.get("chat_task", self.task["id"])
        version = digest({"inputs": context["inputs"], "material": legacy, "draft_revision": task["draft_revision"]})
        task.pop("draft_version")
        task.update(status="reviewed_draft", reviews={"document_reviewer": {"version": version, "passed": True}})
        self.w.store.put("chat_task", task["id"], task)
        self.assertEqual(self.w.chat.get(task["id"])["task"]["status"], "draft_ready")
        self.w.docs.asset("resume/page.png").write_bytes(b"changed preview")
        self.assertEqual(self.w.chat.get(task["id"])["task"]["status"], "stale")

    def test_profile_change_and_resume_replacement_invalidate_reviews(self):
        """Both task submissions and reviews require the latest profile and resume selection."""
        old = self.payload()
        profile = self.w.profile();profile["skills"].append("SQL");self.w.save_profile(profile)
        with self.assertRaises(ValueError):
            self.w.chat.submit(old)
        self.w.chat.submit(self.payload())
        self.document("replacement", "resume");self.w.resumes.add("replacement")
        self.w.resumes.tag({"document_id": "replacement", "category": "General"})
        self.assertEqual(self.w.chat.get(self.task["id"])["task"]["status"], "stale")
        self.w.chat.submit(self.payload())
        self.assertEqual(self.w.store.get("job", self.job["id"])["resume_id"], "replacement")

    def test_changed_document_bytes_and_removed_jobs_block_review(self):
        """Readiness cannot survive file tampering or application removal."""
        self.w.chat.submit(self.payload())
        self.w.docs.asset(self.resume["source_path"]).write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.w.chat.get(self.task["id"])
        self.assertEqual(self.w.chat.list()[0]["status"], "needs_input")
        self.w.store.remove_jobs([self.job["id"]])
        with self.assertRaises(ValueError):
            self.w.chat.get(self.task["id"])

    def test_profile_extraction_stays_separate_and_disallows_sensitive_inference(self):
        """Profile proposals never update the saved facts or user confirmation."""
        self.document("linkedin", "profile")
        task = self.w.chat.enqueue({"kind": "profile", "target_id": "linkedin"})
        original = self.w.profile()
        proposed = copy.deepcopy(original);proposed["identity"]["legal_name"] = "New draft name"
        data = {"task_id": task["id"], "version": self.w.chat.get(task["id"])["version"], "agent_id": "main-1",
                "result": {"profile": proposed, "notes": []}}
        result = self.w.chat.submit(data)["task"]["result"]["profile"]
        self.assertEqual(self.w.profile(), original)
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["facts"], [])
        self.assertEqual(result["authorizations"], [])
        self.assertEqual(result["preferences"]["disability"], "unknown")

    def test_answers_and_cover_requirement_need_observed_evidence(self):
        """An optional or unknown letter cannot trigger generation; unknown question labels fail."""
        payload = self.payload()
        payload["result"]["answers"] = [{"question": "Name?", "answer": "Alex Test", "fact_ids": ["identity.legal_name"]}]
        with self.assertRaises(ValueError):
            self.w.chat.submit(payload)
        payload["result"]["form_text"] = "Name?"
        self.w.chat.submit(payload)
        payload = self.payload();payload["result"]["cover_edits"] = [{"id": "x", "text": "Unsolicited"}]
        with self.assertRaises(ValueError):
            self.w.chat.submit(payload)

    def test_required_letter_blocks_without_loading_templates_or_rendering(self):
        """A required letter is reported without generating a document or sending template content."""
        payload = self.payload()
        payload["result"]["cover_requirement"] = {"state": "required", "quote": "Cover letter required",
            "source_text": "Cover letter required", "source_url": "https://example.test/apply"}
        with patch("applydesk.agent_contract.public_job_url", side_effect=lambda url: url):
            with self.assertRaisesRegex(ValueError, "requires a cover letter"):
                self.w.chat.submit(payload)
        self.assertEqual(self.w.docs.get("resume"), self.resume)
        self.assertIsNone(self.w.store.get("job", self.job["id"]).get("cover_id"))

    def test_blocked_tasks_survive_restart(self):
        """A blocked task and its evidence survive a workspace restart."""
        self.w.chat.block({"task_id": self.task["id"], "version": self.w.chat.get(self.task["id"])["version"], "issues": ["Need more evidence"]})
        self.w.close()
        self.w = Workspace(self.w.root, secrets=FakeSecrets())
        self.assertEqual(self.w.chat.get(self.task["id"])["next_action"], "resolve_input")

    def test_provider_settings_and_client_are_disabled_without_erasing_secrets(self):
        """Old credentials stay unused, and no agent configuration is exposed to the UI."""
        self.w.secrets.set("api:openai", "saved-before-experiment")
        with self.assertRaises(ValueError):
            self.w.client("application")
        self.assertEqual(self.w.secrets.get("api:openai"), "saved-before-experiment")
        self.assertEqual(set(self.w.state()["settings"]), {"cover_template_id"})

    def test_preview_changes_and_attachment_changes_invalidate_passed_reviews(self):
        """A passing report covers exact previews and the same current job attachments."""
        self.w.chat.submit(self.payload())
        self.w.docs.asset("resume/page.png").write_bytes(b"changed preview")
        context = self.w.chat.get(self.task["id"])
        self.assertEqual(context["task"]["status"], "stale")
        self.assertEqual(context["next_action"], "draft")
        self.w.chat.submit(self.payload())
        self.w.store.job(self.job["id"], resume_id=None)
        self.assertEqual(self.w.chat.get(self.task["id"])["task"]["status"], "stale")

    def test_resume_edit_requests_are_rejected_before_rendering(self):
        """The chat's document workflow cannot revive removed resume customization."""
        with self.assertRaisesRegex(ValueError, "customization is unavailable"):
            self.w.chat.enqueue({"kind": "document", "target_id": "resume", "instruction": "Rewrite"})

    def test_legacy_letter_edit_task_is_readable_but_cannot_restart(self):
        """Saved files survive; queued legacy customization does not trigger a model request."""
        _, original = saved_letter(self.w.docs, self.job["id"])
        task = {"id": "legacy-letter-edit", "kind": "document", "target_id": original["id"], "instruction": "Old edit",
                "status": "queued", "draft_revision": 0, "reviews": {}, "document_ids": [], "result": None}
        self.w.store.put("chat_task", task["id"], task)
        context = self.w.chat.get(task["id"])
        self.assertEqual((context["task"]["status"], context["next_action"]), ("needs_input", "resolve_input"))
        with patch.object(self.w.orchestrator.runner, "_request") as request:
            with self.assertRaisesRegex(ValueError, "blocker"):
                self.w.orchestrator.workflow.prepare(task["id"], "Continue")
        request.assert_not_called()
        self.w.docs.verify(original["id"])
        with self.assertRaisesRegex(ValueError, "customization is unavailable"):
            self.w.chat.enqueue({"kind": "document", "target_id": original["id"]})

    def test_profile_coursework_list_becomes_plain_text_without_confirming_profile(self):
        """The former agent contract's list shape is normalized only for coursework."""
        raw = {"profile": {"education": [{"institution": "Example University", "coursework": ["Algorithms", "Statistics"]}]}, "notes": []}
        original = copy.deepcopy(raw)
        result = profile_draft(raw)
        self.assertEqual(result["profile"]["education"][0]["coursework"], "Algorithms\nStatistics")
        self.assertFalse(result["profile"]["confirmed"])
        self.assertEqual(raw, original)
        raw["profile"]["education"][0]["coursework"] = "Algorithms\nStatistics"
        self.assertEqual(profile_draft(raw), result)

    def test_profile_coursework_rejects_nested_objects_and_other_structured_fields(self):
        """Compatibility with course lists never permits arbitrary structured profile values."""
        for row in [{"coursework": [{"name": "Algorithms"}]}, {"coursework": [True]}, {"institution": ["Example University"]}]:
            with self.assertRaises(ValueError):
                profile_draft({"profile": {"education": [row]}, "notes": []})

    def test_source_website_without_scheme_is_normalized_for_profile(self):
        """Adding HTTPS preserves the exact source host and path without fabricating a website."""
        raw = {"profile": {"websites": [{"type": "LinkedIn", "url": "www.linkedin.com/in/example"}]}, "notes": []}
        result = profile_draft(raw)
        self.assertEqual(result["profile"]["websites"][0]["url"], "https://www.linkedin.com/in/example")
        self.assertEqual(raw["profile"]["websites"][0]["url"], "www.linkedin.com/in/example")
        for url in ["", "javascript:alert(1)", "not a website", "user@example.test", "/example.test", "http://example.test"]:
            with self.assertRaises(ValueError):
                profile_draft({"profile": {"websites": [{"type": "Portfolio", "url": url}]}, "notes": []})
