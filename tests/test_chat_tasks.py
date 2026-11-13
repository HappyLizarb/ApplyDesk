"""Draft-only execution, evidence gates, stale reviews, and credential-free persistence."""

import base64
import copy
from unittest.mock import patch

from chat_helpers import ChatFixture
from test_documents import sample_docx
from applydesk.workspace import Workspace
from test_workspace import FakeSecrets
from applydesk.agent_contract import profile_draft


class ChatTaskTests(ChatFixture):
    def test_full_draft_and_two_reviews_never_approve_application(self):
        """Even reported passing subagents cannot mark a job ready or submitted."""
        with patch.object(self.w, "client", side_effect=AssertionError("Provider access")):
            context = self.w.chat.submit(self.payload())
            self.assertEqual(context["task"]["status"], "awaiting_review")
            self.w.chat.review(self.reviewer("document_reviewer", "reviewer-1"))
            result = self.w.chat.review(self.reviewer("application_auditor", "auditor-1"))
        self.assertEqual(result["status"], "reviewed_draft")
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

    def test_assumed_qualification_allows_draft_but_not_invented_answers(self):
        """A broad qualification assumption never supplies a specific professional history answer."""
        payload = self.payload()
        payload["result"]["analysis"]["required"][0].update(met=False, evidence_ids=[])
        payload["result"].update(form_text="How many years of professional Python experience?", answers=[
            {"question": "How many years of professional Python experience?", "answer": "5", "fact_ids": []}])
        with self.assertRaisesRegex(ValueError, "confirmed supporting facts"):
            self.w.chat.submit(payload)
        payload["result"]["answers"] = []
        context = self.w.chat.submit(payload)
        self.assertEqual(context["task"]["status"], "awaiting_review")
        self.assertFalse(context["task"]["result"]["analysis"]["required"][0]["met"])

    def test_self_review_missing_coverage_and_conflicting_verdict_are_rejected(self):
        """Recorded reviewer separation and full coverage are mandatory even in experimental mode."""
        self.w.chat.submit(self.payload())
        for change in [{"agent_id": "main-1"}, {"checked_items": []}, {"issues": ["Unresolved"]}, {"passed": "true"}]:
            review = self.reviewer("document_reviewer", "reviewer-1") | change
            with self.assertRaises(ValueError):
                self.w.chat.review(review)
        self.w.chat.review(self.reviewer("document_reviewer", "reviewer-1"))
        with self.assertRaises(ValueError):
            self.w.chat.review(self.reviewer("application_auditor", "reviewer-1"))

    def test_profile_change_and_resume_replacement_invalidate_reviews(self):
        """Both task submissions and reviews require the latest profile and resume selection."""
        old = self.payload()
        profile = self.w.profile();profile["skills"].append("SQL");self.w.save_profile(profile)
        with self.assertRaises(ValueError):
            self.w.chat.submit(old)
        self.w.chat.submit(self.payload())
        review = self.reviewer("application_auditor", "auditor-1")
        self.document("replacement", "resume");self.w.resumes.add("replacement")
        self.w.resumes.tag({"document_id": "replacement", "category": "General"})
        self.assertEqual(self.w.chat.get(self.task["id"])["task"]["status"], "stale")
        with self.assertRaises(ValueError):
            self.w.chat.review(review)
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

    def test_cover_rendering_preserves_resume_and_resubmission_clears_reviews(self):
        """Use the real DOCX renderer once to exercise the complete draft document path."""
        template = self.w.import_file({"filename": "Letter.docx", "kind": "cover_template", "data": base64.b64encode(sample_docx()).decode()})
        self.w.save_settings({"cover_template_id": template["id"]})
        block = next(block for block in template["blocks"] if block.get("editable", True))
        payload = self.payload()
        payload["result"]["cover_requirement"] = {"state": "required", "quote": "Cover letter required", "source_text": "Cover letter required", "source_url": "https://example.test/apply"}
        payload["result"]["cover_edits"] = [{"id": block["id"], "text": "Alex Test", "fact_ids": ["identity.legal_name"]}]
        with patch("applydesk.agent_contract.public_job_url", side_effect=lambda url: url), patch.object(self.w, "client", side_effect=AssertionError("Provider call")):
            context = self.w.chat.submit(payload)
        self.assertEqual(len(context["review"]["documents"]), 2)
        letter = context["review"]["documents"][1]
        self.assertEqual(letter["document_type"], "cover")
        self.w.docs.verify(letter["id"])
        self.w.chat.review(self.reviewer("document_reviewer", "reviewer-1"))
        self.w.chat.submit(self.payload())
        self.assertEqual(self.w.chat.get(self.task["id"])["task"]["reviews"], {})
        self.assertEqual(self.w.docs.get("resume"), self.resume)

    def test_capture_respects_removed_records_and_blocks_survive_restart(self):
        """Copied evidence is marked as reported and cannot restore excluded applications."""
        capture = {"url": self.job["url"], "company": "Example", "title": "Engineer", "details": "Required: Python."}
        with patch("applydesk.agent_tasks.public_job_url", side_effect=lambda url: url):
            self.assertEqual(self.w.chat.capture(capture)["capture_verification"], "agent_reported")
            self.w.store.remove_jobs([self.job["id"]])
            with self.assertRaises(ValueError):
                self.w.chat.capture(capture)
        self.w.store.job(self.job["id"], removed_at=None)
        self.w.chat.block({"task_id": self.task["id"], "version": self.w.chat.get(self.task["id"])["version"], "issues": ["Need more evidence"]})
        self.w.close()
        self.w = Workspace(self.w.root, secrets=FakeSecrets())
        self.assertEqual(self.w.chat.get(self.task["id"])["next_action"], "resolve_input")

    def test_provider_settings_and_client_are_disabled_without_erasing_secrets(self):
        """Old credentials stay unused, and no agent configuration is exposed to the UI."""
        self.w.secrets.set("api:openai", "saved-before-experiment")
        with self.assertRaises(ValueError):
            self.w.client("application")
        with self.assertRaises(ValueError):
            self.w.save_settings({"roles": {}, "api_keys": {"openai": "replacement"}})
        self.assertEqual(self.w.secrets.get("api:openai"), "saved-before-experiment")
        self.assertEqual(set(self.w.state()["settings"]), {"cover_template_id"})

    def test_preview_changes_and_attachment_changes_invalidate_passed_reviews(self):
        """A passing report covers exact previews and the same current job attachments."""
        self.w.chat.submit(self.payload())
        self.w.chat.review(self.reviewer("document_reviewer", "reviewer-1"))
        self.w.docs.asset("resume/page.png").write_bytes(b"changed preview")
        context = self.w.chat.get(self.task["id"])
        self.assertEqual(context["task"]["status"], "stale")
        self.assertEqual(context["next_action"], "draft")
        self.w.chat.submit(self.payload())
        self.w.store.job(self.job["id"], resume_id=None)
        self.assertEqual(self.w.chat.get(self.task["id"])["task"]["status"], "stale")

    def test_resume_edit_requests_are_rejected_before_rendering(self):
        """The chat's document workflow cannot revive removed resume customization."""
        with self.assertRaisesRegex(ValueError, "Only a generated cover letter"):
            self.w.chat.enqueue({"kind": "document", "target_id": "resume", "instruction": "Rewrite"})

    def test_cover_edit_task_creates_separate_draft_with_template_review(self):
        """Chat edits preserve the attached letter and require template page coverage."""
        template = self.w.import_file({"filename": "Letter.docx", "kind": "cover_template", "data": base64.b64encode(sample_docx()).decode()})
        block = next(block for block in template["blocks"] if block.get("editable", True))
        original = self.w.docs.create(template["id"], {block["id"]: "Alex Test"}, "Original letter", job_id=self.job["id"])
        self.w.store.job(self.job["id"], cover_id=original["id"])
        task = self.w.chat.enqueue({"kind": "document", "target_id": original["id"], "instruction": "Use my name"})
        context = self.w.chat.get(task["id"])
        payload = {"task_id": task["id"], "version": context["version"], "agent_id": "main-1",
                   "result": {"edits": [{"id": block["id"], "text": "Alex Test", "fact_ids": ["identity.legal_name"]}], "issues": []}}
        with patch.object(self.w, "client", side_effect=AssertionError("Provider access")):
            result = self.w.chat.submit(payload)
        self.assertNotEqual(result["task"]["document_ids"], [original["id"]])
        self.assertEqual(self.w.store.get("job", self.job["id"])["cover_id"], original["id"])
        self.assertIn("template:page:1", result["review"]["expected_checks"]["document_reviewer"])
        self.w.docs.verify(original["id"])

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
