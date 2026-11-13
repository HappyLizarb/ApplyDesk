"""A chat-selected resume stays pinned to one active upload and its unchanged bytes."""

import hashlib
from unittest.mock import patch

from chat_helpers import ChatFixture
from applydesk.applications import Applications
from applydesk.application_steps import choose_attachment
from applydesk.document_deletion import DocumentDeletion


class RunResumeTests(ChatFixture):
    """Use local synthetic files; no provider or employer receives a test document."""

    def setUp(self):
        super().setUp()
        profile = self.w.profile()
        profile["industries"] = [{"name": "Hardware", "years": "0"}]
        self.w.save_profile(profile)
        self.chosen = self.document("chosen", "resume")
        self.w.resumes.add(self.chosen["id"])
        self.choice = {"id": self.chosen["id"], "sha256": self.chosen["source_sha256"]}

    def test_explicit_untagged_upload_wins_and_attaches_original_bytes(self):
        """A named resume does not silently turn into General or the industry-tagged file."""
        self.w.resumes.tag({"document_id": self.resume["id"], "category": "Hardware"})
        app = Applications(self.w, resume_choice=self.choice)
        with patch.object(app.documents, "require_review") as review:
            app._resume(self.job, {"category": "Hardware"})
        review.assert_called_once_with(self.chosen["id"])
        selected = self.w.store.get("job", self.job["id"])["resume_id"]
        self.assertEqual(selected, self.chosen["id"])
        path, digest = choose_attachment(self.w, self.w.docs.get(selected), {"accept": ".pdf"})
        self.assertEqual(path.read_bytes(), b"synthetic PDF source")
        self.assertEqual(digest, self.choice["sha256"])

    def test_unspecified_choice_keeps_general_fallback(self):
        """Existing category selection still applies when chat did not name an upload."""
        app = Applications(self.w)
        with patch.object(app.documents, "require_review"):
            app._resume(self.job, {"category": "Hardware"})
        self.assertEqual(self.w.store.get("job", self.job["id"])["resume_id"], self.resume["id"])

    def test_changed_source_bytes_stop_before_attachment_selection(self):
        """Tampered files cannot pass simply because the requested document ID still exists."""
        self.w.docs.asset(self.chosen["source_path"]).write_bytes(b"changed source")
        app = Applications(self.w, resume_choice=self.choice)
        with patch.object(app.documents, "require_review") as review:
            with self.assertRaisesRegex(ValueError, "changed"):
                app._resume(self.job, {"category": "Hardware"})
        review.assert_not_called()
        self.assertIsNone(self.w.store.get("job", self.job["id"])["resume_id"])

    def test_updated_manifest_does_not_replace_the_run_hash(self):
        """A newly valid document version must still match the separately pinned run digest."""
        source = self.w.docs.asset(self.chosen["source_path"])
        source.write_bytes(b"new version")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        self.w.docs._save({**self.chosen, "source_sha256": digest, "sha256": digest})
        self.assertTrue(self.w.docs.verify(self.chosen["id"]))
        with self.assertRaisesRegex(ValueError, "since this run"):
            self.w.resumes.selected(self.chosen["id"], self.choice["sha256"])

    def test_replaced_library_upload_does_not_fall_back_or_delete_history(self):
        """Category replacement keeps historical copies while blocking an old run selection."""
        self.w.resumes.tag({"document_id": self.chosen["id"], "category": "Hardware"})
        historical = self.w.store.add_job("https://example.test/jobs/history")
        self.w.store.job(historical["id"], resume_id=self.chosen["id"], status="submitted")
        self.w.resumes.tag({"document_id": self.resume["id"], "category": "Hardware"})
        with self.assertRaisesRegex(ValueError, "no longer available"):
            self.w.resumes.selected(self.chosen["id"], self.choice["sha256"])
        self.assertTrue(self.w.docs.verify(self.chosen["id"]))
        self.assertEqual(self.w.store.get("job", historical["id"])["resume_id"], self.chosen["id"])

    def test_deleted_library_upload_keeps_existing_application_copy(self):
        """Deleting a chosen upload cannot make a new run silently use General instead."""
        historical = self.w.store.add_job("https://example.test/jobs/history")
        self.w.store.job(historical["id"], resume_id=self.chosen["id"], status="submitted")
        result = DocumentDeletion(self.w).delete({"document_id": self.chosen["id"]})
        self.assertTrue(result["application_copies_kept"])
        app = Applications(self.w, resume_choice=self.choice)
        with patch.object(app.documents, "require_review") as review:
            with self.assertRaisesRegex(ValueError, "no longer available"):
                app._resume(self.job, {"category": "Hardware"})
        review.assert_not_called()
        self.assertTrue(self.w.docs.verify(self.chosen["id"]))
        self.assertEqual(self.w.store.get("job", historical["id"])["resume_id"], self.chosen["id"])
