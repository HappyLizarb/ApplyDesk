"""Uploaded resumes remain readable evidence throughout chat draft preparation."""

import copy
import hashlib
import json
from unittest.mock import Mock

from chat_helpers import ChatFixture
from applydesk.applications import Applications
from applydesk.document_deletion import DocumentDeletion
from applydesk.orchestrator_tools import master_tools


class ResumeContextTests(ChatFixture):
    def read_document(self, identity):
        """Exercise the same manifest-ID tool used by the master chat agent."""
        call = {"namespace": "applydesk", "name": "read_document",
                "arguments": json.dumps({"document_id": identity})}
        return self.w.orchestrator.tools.call(call)

    def test_application_context_contains_exact_resume_content(self):
        """A drafter receives readable source content, not just an ID and checksum."""
        record = self.w.docs.get("resume")
        record.update(text="Alex Test\nVerilog RTL and logic synthesis.",
                      blocks=[{"id": "line:0", "text": "Verilog RTL and logic synthesis.", "editable": False}])
        self.w.docs._save(record)
        context = self.w.chat.get(self.task["id"])
        resume = next(row for row in context["inputs"]["resumes"] if row["id"] == "resume")
        for key in ("text", "blocks", "filename", "source_sha256", "image_paths"):
            self.assertEqual(resume[key], record[key])
        self.assertEqual(resume["category"], "General")
        self.assertEqual(resume["image_sha256"]["resume/page.png"],
                         hashlib.sha256(b"synthetic PNG page").hexdigest())

    def test_untagged_resume_is_readable_without_becoming_category_default(self):
        """An explicitly named new upload is visible before the user assigns its tag."""
        extra = self.document("untagged", "resume")
        self.w.resumes.add(extra["id"])
        resumes = self.w.chat.get(self.task["id"])["inputs"]["resumes"]
        supplied = next(row for row in resumes if row["id"] == extra["id"])
        self.assertEqual(supplied["text"], extra["text"])
        self.assertEqual(supplied["category"], "")
        self.assertEqual(self.w.resumes.select("General")["id"], "resume")

    def test_removed_library_file_is_excluded_but_application_copy_survives(self):
        """Deletion cannot offer a historical attachment as a new resume choice."""
        self.w.store.job(self.job["id"], resume_id="resume")
        DocumentDeletion(self.w).delete({"document_id": "resume"})
        context = self.w.chat.get(self.task["id"])
        self.assertEqual(context["inputs"]["resumes"], [])
        self.assertTrue(self.w.docs.asset(self.resume["source_path"]).is_file())
        self.assertEqual(self.w.store.get("job", self.job["id"])["resume_id"], "resume")
        with self.assertRaises(ValueError):
            self.read_document("resume")

    def test_resume_tampering_blocks_context_before_draft_is_saved(self):
        """Unreadable or changed bytes are a file problem, not negative qualification evidence."""
        source = self.w.docs.asset(self.resume["source_path"])
        source.write_bytes(b"different PDF bytes")
        with self.assertRaises(ValueError):
            self.w.chat.get(self.task["id"])
        with self.assertRaises(ValueError):
            self.read_document("resume")
        task = self.w.store.get("chat_task", self.task["id"])
        self.assertIsNone(task["result"])
        self.assertIsNone(self.w.store.get("job", self.job["id"])["resume_id"])

    def test_read_document_returns_verified_content_without_rewriting_file(self):
        """The master can inspect its existing upload using the advertised strict tool."""
        source = self.w.docs.asset(self.resume["source_path"])
        before = source.read_bytes()
        original = copy.deepcopy(self.w.docs.manifest)
        result = self.read_document("resume")
        self.assertEqual(result["text"], self.resume["text"])
        self.assertEqual(result["blocks"], self.resume["blocks"])
        self.assertEqual(result["source_sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(self.w.docs.manifest, original)
        schema = next(tool for tool in master_tools()[0]["tools"] if tool["name"] == "read_document")
        self.assertEqual(schema["parameters"]["required"], ["document_id"])
        self.assertFalse(schema["parameters"]["additionalProperties"])

    def test_read_document_rejects_unknown_ids_and_arbitrary_paths(self):
        """Document reading stays inside the workspace manifest and never opens raw paths."""
        for identity in ("missing", "../workspace.sqlite", "/etc/passwd", self.resume["source_path"]):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                self.read_document(identity)
        self.assertEqual(self.read_document("resume")["id"], "resume")

    def test_resume_can_supply_profile_draft_without_confirming_new_facts(self):
        """The uploaded resume can fill a proposed profile while saved facts remain unchanged."""
        original = self.w.profile()
        task = self.w.chat.enqueue({"kind": "profile", "target_id": "resume"})
        context = self.w.chat.get(task["id"])
        self.assertEqual(context["inputs"]["source"]["text"], self.resume["text"])
        proposed = copy.deepcopy(original)
        proposed["skills"] = ["Verilog"]
        result = self.w.chat.submit({"task_id": task["id"], "version": context["version"],
                                     "agent_id": "test-drafter", "result": {"profile": proposed, "notes": []}})
        draft = result["task"]["result"]["profile"]
        self.assertEqual(draft["skills"], ["Verilog"])
        self.assertFalse(draft["confirmed"])
        self.assertEqual(draft["facts"], [])
        self.assertEqual(self.w.profile(), original)

    def screening_inputs(self):
        """Use separate fake analysts with a grounded, independently passing qualification."""
        analysis = {"company": "Example", "title": "Engineer", "details": "Required: Python.",
                    "country": "US", "category": "General", "requirements_complete": True,
                    "required": [{"text": "Python", "met": True, "evidence_ids": ["skills.0"]}],
                    "preferred": [], "cover_letter_required": False}
        analyst = Mock()
        analyst.ask.return_value = analysis
        auditor = Mock()
        auditor.ask.return_value = {**copy.deepcopy(analysis), "passed": True, "issues": []}
        clients = {"application": analyst, "auditor": auditor}
        page = {"url": self.job["url"], "text": analysis["details"], "title": "Engineer"}
        return analysis, clients, page

    def test_live_qualification_agents_receive_resume_text_and_confirmed_facts(self):
        """Both independent readers see uploaded content before making a qualification decision."""
        analysis, clients, page = self.screening_inputs()
        app = Applications(self.w, client=clients.__getitem__)
        result = app._screen(self.job, self.w.profile(), page)
        self.assertEqual(result["required"], analysis["required"])
        for client in clients.values():
            supplied = client.ask.call_args.args[1]["profile"]
            self.assertTrue(supplied["confirmed"])
            self.assertEqual(supplied["resumes"][0]["text"], self.resume["text"])
            self.assertEqual(supplied["resumes"][0]["source_sha256"], self.resume["source_sha256"])
            self.assertTrue(any(fact["id"] == "skills.0" for fact in supplied["facts"]))
            self.assertNotIn("disability", str(supplied))

    def test_selected_resume_is_the_only_source_sent_to_qualification_agents(self):
        """An explicitly chosen untagged upload wins over General before analysis and audit."""
        selected = self.document("chosen", "resume")
        selected["text"] = "Alex Test\nChosen hardware resume."
        self.w.docs._save(selected)
        self.w.resumes.add("chosen")
        _, clients, page = self.screening_inputs()
        choice = {"id": "chosen", "sha256": selected["source_sha256"]}
        app = Applications(self.w, client=clients.__getitem__, resume_choice=choice)
        self.assertIsNotNone(app._screen(self.job, self.w.profile(), page))
        for client in clients.values():
            sources = client.ask.call_args.args[1]["profile"]["resumes"]
            self.assertEqual([source["id"] for source in sources], ["chosen"])
            self.assertEqual(sources[0]["text"], selected["text"])
        self.assertEqual(self.w.resumes.select("General")["id"], "resume")

    def test_wrong_pinned_resume_hash_blocks_before_any_qualification_request(self):
        """A previously named file cannot silently change while its filename stays the same."""
        _, clients, page = self.screening_inputs()
        app = Applications(self.w, client=clients.__getitem__,
                           resume_choice={"id": "resume", "sha256": "0" * 64})
        with self.assertRaisesRegex(ValueError, "changed since this run"):
            app._screen(self.job, self.w.profile(), page)
        for client in clients.values():
            client.ask.assert_not_called()
        self.assertIsNone(self.w.store.get("job", self.job["id"])["resume_id"])

    def test_user_chat_can_retry_draft_without_resetting_application_budget(self):
        """Fixing a blocked draft clears that task while preserving the submission ledger."""
        engine = self.w.orchestrator
        context = self.w.chat.get(self.task["id"])
        self.w.chat.block({"task_id": self.task["id"], "version": context["version"],
                           "issues": ["Resume contents unavailable"]})
        run = {"id": "existing-run", "status": "complete", "limit": 5, "attempted_count": 2,
               "submitted_count": 2, "resume_id": "resume", "results": []}
        attempt = {"id": "attempt-one", "run_id": run["id"], "status": "submitted"}
        self.w.store.put("application_run", run["id"], run)
        self.w.store.put("submission_attempt", attempt["id"], attempt)
        engine.applications.run = copy.deepcopy(run)
        message = engine.history.add("user", "The resume is available now; continue the review.")
        engine.active = {"message": message}
        result = engine.tools.call({"name": "retry_task", "arguments": json.dumps({"task_id": self.task["id"]})})
        task = self.w.chat.get(self.task["id"])["task"]
        self.assertEqual(result, {"status": "queued"})
        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["issues"], [])
        self.assertEqual(self.w.store.all("application_run"), [run])
        self.assertEqual(self.w.store.all("submission_attempt"), [attempt])
        self.assertEqual(engine.applications.run, run)

    def test_autonomous_draft_cannot_retry_its_own_blocker(self):
        """A failed background draft waits for user direction instead of retrying itself."""
        context = self.w.chat.get(self.task["id"])
        self.w.chat.block({"task_id": self.task["id"], "version": context["version"],
                           "issues": ["Review needs more evidence"]})
        before = self.w.store.get("chat_task", self.task["id"])
        for active in (None, {"task_id": self.task["id"]}, {"message": {"role": "assistant", "text": "Retry"}}):
            self.w.orchestrator.active = active
            with self.subTest(active=active), self.assertRaisesRegex(ValueError, "user chat"):
                self.w.orchestrator.tools.call({"name": "retry_task",
                    "arguments": json.dumps({"task_id": self.task["id"]})})
            self.assertEqual(self.w.store.get("chat_task", self.task["id"]), before)
