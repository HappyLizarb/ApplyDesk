"""Document-library deletion removes unused files and preserves application-owned copies."""

import json
from http.client import HTTPConnection
import threading
from unittest.mock import Mock, patch

from chat_helpers import ChatFixture
from applydesk.document_deletion import DocumentDeletion
from applydesk.document_tasks import DocumentTasks
from applydesk.documents import DocumentService
from applydesk.server import AppServer
from test_documents import sample_docx


class DocumentDeletionTests(ChatFixture):
    def setUp(self):
        """Use synthetic files and a private workspace without connected accounts."""
        super().setUp()
        self.deletion = DocumentDeletion(self.w)

    def test_unused_resume_removes_bytes_previews_text_registry_and_its_reviews(self):
        """Deleting an unused upload is a physical deletion, not a hidden library row."""
        self.w.store.put("document_review", "resume", {"document_id": "resume", "text": "old review"})
        self.w.store.put("review", "cache", {"document_id": "resume", "text": "old review"})
        self.w.store.put("review", "other", {"document_id": "other", "text": "keep"})
        self.w.tasks["import"] = {"id": "import", "status": "done", "result": self.resume}
        profile = self.w.profile()
        result = self.deletion.delete({"document_id": "resume"})
        self.assertFalse(result["application_copies_kept"])
        self.assertFalse((self.w.docs.root / "resume").exists())
        self.assertNotIn("resume", json.loads(self.w.docs.manifest_path.read_text()))
        self.assertEqual(self.w.resumes.list(), [])
        self.assertIsNone(self.w.store.get("document_review", "resume"))
        self.assertIsNone(self.w.store.get("review", "cache"))
        self.assertEqual(self.w.store.get("review", "other")["text"], "keep")
        self.assertEqual(self.w.profile(), profile)
        self.assertNotIn("import", self.w.tasks)
        with self.assertRaisesRegex(ValueError, "not found"):
            self.w.docs.asset(self.resume["source_path"])

    def test_used_resume_leaves_application_copy_and_invalidates_only_pending_readiness(self):
        """Submitted and uncertain outcomes remain intact while pending work must reselect files."""
        jobs = []
        for status in ("ready", "submitted", "submission_unknown"):
            job = self.w.store.add_job("https://example.test/" + status)
            self.w.store.job(job["id"], status=status, resume_id="resume", final_audit={"passed": True}, last_snapshot="old")
            jobs.append(job["id"])
        session = Mock()
        self.w.sessions[jobs[0]] = session
        result = self.deletion.delete({"document_id": "resume"})
        self.assertTrue(result["application_copies_kept"])
        self.assertTrue(self.w.docs.verify("resume"))
        self.assertEqual(self.w.resumes.list(), [])
        pending = self.w.store.get("job", jobs[0])
        self.assertEqual((pending["status"], pending["resume_id"]), ("documents_changed", "resume"))
        self.assertIsNone(pending["last_snapshot"])
        session.cancel.assert_called_once()
        session.close.assert_called_once()
        for identity, status in zip(jobs[1:], ("submitted", "submission_unknown")):
            self.assertEqual(self.w.store.get("job", identity)["status"], status)
            self.assertEqual(self.w.store.get("job", identity)["resume_id"], "resume")
        with self.assertRaisesRegex(ValueError, "Upload and tag"):
            self.w.resumes.select("General")

    def test_attachment_ledger_and_completed_draft_keep_historical_files(self):
        """Historical references are preserved even when they are not the current resume selection."""
        for reference in ("ledger", "task"):
            with self.subTest(reference=reference):
                record = self.document(reference, "resume")
                self.w.resumes.add(record["id"])
                if reference == "ledger":
                    self.w.store.job(self.job["id"], attachments=[{"document_id": record["id"]}])
                else:
                    task = self.w.store.get("chat_task", self.task["id"])
                    self.w.store.put("chat_task", task["id"], {**task, "document_ids": [record["id"]]})
                result = self.deletion.delete({"document_id": record["id"]})
                self.assertTrue(result["application_copies_kept"])
                self.assertTrue(self.w.docs.asset(record["source_path"]).is_file())

    def test_template_removal_preserves_existing_cover_edit_and_review_but_blocks_new_letters(self):
        """A removed library template remains available only as existing-letter provenance."""
        template = self.w.docs.import_file("Cover.docx", sample_docx(), "cover_template")
        self.w.save_settings({"cover_template_id": template["id"]})
        letter = self.w.docs.create(template["id"], {}, "Existing letter", job_id=self.job["id"])
        self.w.store.job(self.job["id"], cover_id=letter["id"])
        result = self.deletion.delete({"document_id": template["id"]})
        self.assertTrue(result["application_copies_kept"])
        self.assertEqual(self.w.docs.list_templates(), [])
        self.assertEqual(self.w.settings()["cover_template_id"], "")
        self.assertTrue(self.w.docs.verify(letter["id"]))
        edited = self.w.docs.edit(letter["id"], {})
        self.assertTrue(self.w.docs.verify(edited["id"]))
        tasks = DocumentTasks(self.w, client=Mock())
        with patch("applydesk.intelligence.review_format", return_value={"passed": True}), patch("applydesk.intelligence.audit_document", return_value={"passed": True}):
            self.assertTrue(tasks.review(edited["id"])["passed"])
        with self.assertRaisesRegex(ValueError, "deleted from Documents"):
            self.w.docs.create(template["id"], {}, "New letter")
        with self.assertRaisesRegex(ValueError, "deleted from Documents"):
            self.w.save_settings({"cover_template_id": template["id"]})

    def test_unused_template_is_removed_without_touching_other_files_or_preferences(self):
        """Deleting a default clears that selection, while transcript and profile remain untouched."""
        template = self.document("template", "cover_template")
        self.w.store.put("settings", "main", {**self.w.settings(), "cover_template_id": template["id"]})
        result = self.deletion.delete({"document_id": template["id"]})
        self.assertFalse(result["application_copies_kept"])
        self.assertEqual(self.w.settings()["cover_template_id"], "")
        self.assertFalse((self.w.docs.root / template["id"]).exists())
        self.assertTrue(self.w.docs.verify("resume"))

    def test_agent_and_import_work_block_deletion_before_any_changes(self):
        """The same workspace lock gates active work and deletion to prevent late file use."""
        for running in ("agent", "import"):
            with self.subTest(running=running):
                self.w.orchestrator.active = {"message": {}} if running == "agent" else None
                self.w.tasks = {"import": {"status": "running"}} if running == "import" else {}
                with self.assertRaisesRegex(ValueError, "Pause the agent"):
                    self.deletion.delete({"document_id": "resume"})
                self.assertTrue(self.w.docs.verify("resume"))
                self.assertEqual(len(self.w.resumes.list()), 1)
        self.w.tasks, self.w.orchestrator.active = {}, None

    def test_profile_transcript_generated_documents_and_invalid_ids_are_rejected(self):
        """The Documents action cannot delete unrelated imports or arbitrary local paths."""
        for kind in ("profile", "transcript", "document"):
            record = self.document(kind, kind)
            with self.assertRaisesRegex(ValueError, "Only uploaded resumes"):
                self.deletion.delete({"document_id": record["id"]})
            self.assertTrue(self.w.docs.asset(record["source_path"]).is_file())
        for identity in ("../resume", "missing", "", 7):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                self.deletion.delete({"document_id": identity})

    def test_failed_manifest_write_restores_staged_bytes_before_restoring_selection(self):
        """A precommit metadata failure cannot leave a visible library entry with missing bytes."""
        with patch.object(self.w.docs, "_write_manifest", side_effect=OSError("Manifest write failed")):
            with self.assertRaises(OSError):
                self.deletion.delete({"document_id": "resume"})
        self.assertEqual(self.w.state()["resumes"][0]["id"], "resume")
        self.assertTrue(self.w.docs.verify("resume"))
        self.assertFalse((self.w.docs.root / ".deleting-resume").exists())

    def test_partial_byte_cleanup_stays_pending_without_restoring_deleted_reference(self):
        """Postcommit cleanup failure stays explicit and is retried on the next startup."""
        def partial_cleanup(directory):
            (directory / "page.png").unlink()
            raise OSError("Cannot remove remaining file")
        with patch("applydesk.document_cleanup.shutil.rmtree", side_effect=partial_cleanup):
            result = self.deletion.delete({"document_id": "resume"})
        staged = self.w.docs.root / ".deleting-resume"
        self.assertTrue(result["cleanup_pending"])
        self.assertTrue((staged / "original.pdf").is_file())
        self.assertEqual(self.w.state()["resumes"], [])
        self.assertNotIn("resume", self.w.docs.manifest)
        DocumentService(self.w.docs.root)
        self.assertFalse(staged.exists())

    def test_exception_after_manifest_commit_does_not_restore_stale_library_reference(self):
        """The durable manifest, not an uncertain write acknowledgement, controls rollback."""
        original = self.w.docs._write_manifest
        def committed_then_failed(records):
            original(records)
            raise OSError("Write acknowledgement failed")
        with patch.object(self.w.docs, "_write_manifest", side_effect=committed_then_failed):
            result = self.deletion.delete({"document_id": "resume"})
        self.assertFalse(result["cleanup_pending"])
        self.assertEqual(self.w.state()["resumes"], [])
        self.assertFalse((self.w.docs.root / ".deleting-resume").exists())
        self.assertFalse((self.w.docs.root / "resume").exists())

    def test_startup_restores_a_directory_staged_before_manifest_commit(self):
        """A process interruption before commit preserves the original filename and bytes."""
        directory = self.w.docs.root / "resume"
        directory.rename(self.w.docs.root / ".deleting-resume")
        recovered = DocumentService(self.w.docs.root)
        self.assertTrue(recovered.verify("resume"))
        self.assertEqual(recovered.get("resume")["filename"], self.resume["filename"])
        self.assertFalse((self.w.docs.root / ".deleting-resume").exists())

    def test_failed_resume_registry_write_leaves_file_manifest_and_dashboard_usable(self):
        """A database error cannot create a library reference to an already-deleted file."""
        original = self.w.store.put
        def fail_registry(kind, identity, value):
            if kind == "resume_library":
                raise OSError("Database write failed")
            return original(kind, identity, value)
        with patch.object(self.w.store, "put", side_effect=fail_registry):
            with self.assertRaisesRegex(OSError, "Database write failed"):
                self.deletion.delete({"document_id": "resume"})
        self.assertTrue(self.w.docs.verify("resume"))
        self.assertIn("resume", json.loads(self.w.docs.manifest_path.read_text()))
        self.assertEqual(self.w.state()["resumes"][0]["id"], "resume")

    def test_failed_default_template_write_keeps_its_files_and_selection(self):
        """A failed settings update must not destroy the selected template first."""
        template = self.w.docs.import_file("Cover.docx", sample_docx(), "cover_template")
        self.w.store.put("settings", "main", {**self.w.settings(), "cover_template_id": template["id"]})
        with patch.object(self.w.store, "put", side_effect=OSError("Database write failed")):
            with self.assertRaises(OSError):
                self.deletion.delete({"document_id": template["id"]})
        self.assertTrue(self.w.docs.asset(template["source_path"]).is_file())
        self.assertEqual(self.w.state()["settings"]["cover_template_id"], template["id"])
        self.assertEqual(self.w.state()["templates"][0]["id"], template["id"])

    def test_delete_endpoint_requires_ui_credentials_and_old_assets_stop_resolving(self):
        """Only the explicit local UI action can remove a file, never the agent capability."""
        server = AppServer(self.w)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        ui = {"Cookie": "applydesk=" + server.token, "Origin": server.origin,
              "X-ApplyDesk": "1", "Content-Type": "application/json"}
        agent = {"Authorization": "Bearer " + server.agent_token, "Content-Type": "application/json"}
        try:
            for headers, expected in ((agent, 403), (ui, 200)):
                connection.request("POST", "/api/document/delete", json.dumps({"document_id": "resume"}), headers)
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, expected)
            for path in ("/api/document?id=resume", "/asset/" + self.resume["source_path"]):
                connection.request("GET", path, headers=ui)
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 400)
        finally:
            connection.close()
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()
