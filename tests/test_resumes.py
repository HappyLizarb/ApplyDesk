"""Uploaded resume replacement, selection, immutable bytes, and AI boundaries."""

import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from applydesk.applications import Applications
from applydesk.document_deletion import DocumentDeletion
from applydesk.documents import DocumentTasks
from applydesk.native_application_io import _document
from applydesk.workspace import Workspace
from chat_helpers import ChatFixture
from test_documents import sample_docx, sample_pdf
from test_workspace import FakeSecrets


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.w = Workspace(Path(self.temp.name), secrets=FakeSecrets())
        profile = self.w.profile()
        profile["industries"] = [{"name": "Engineering", "years": "2"}, {"name": "Finance", "years": "1"}]
        self.w.save_profile(profile)

    def tearDown(self):
        self.w.close()
        self.temp.cleanup()

    def upload(self, filename="Resume.pdf", content=None):
        """Run the real import and renderer with synthetic documents."""
        content = sample_pdf() if content is None else content
        source = Path(self.temp.name) / filename
        source.write_bytes(content)
        return self.w.import_local_resume(source)

    def tag(self, document, category):
        self.w.resumes.tag({"document_id": document["id"], "category": category})

    def test_upload_is_untagged_and_preserves_general_without_ai(self):
        general = self.upload()
        self.tag(general, "General")
        with patch.object(self.w, "client", side_effect=AssertionError("Upload called AI")):
            pending = self.upload("Industry.pdf")
        self.assertEqual(self.w.resumes.select("Finance")["id"], general["id"])
        self.assertEqual(self.w.resumes.list()[-1]["category"], "")
        self.assertIn(pending["id"], [row["id"] for row in self.w.state()["resumes"]])

    def test_exact_industry_wins_then_general(self):
        general, specific = self.upload(), self.upload("Engineering.pdf")
        self.tag(general, "General")
        self.tag(specific, "Engineering")
        self.assertEqual(self.w.resumes.select(" engineering ")["id"], specific["id"])
        for category in ("Finance", "", "Unrecognized industry"):
            self.assertEqual(self.w.resumes.select(category)["id"], general["id"])

    def test_missing_match_pauses_instead_of_using_unrelated_or_untagged_resume(self):
        document = self.upload()
        with self.assertRaisesRegex(ValueError, "Upload and tag"):
            self.w.resumes.select("Engineering")
        self.tag(document, "Engineering")
        with self.assertRaisesRegex(ValueError, "Upload and tag"):
            self.w.resumes.select("Finance")

    def test_replacement_retains_historical_bytes_and_invalidates_readiness(self):
        first, second = self.upload(), self.upload("New.pdf")
        self.tag(first, "Engineering")
        job = self.w.store.add_job("https://example.test/job")
        submitted = self.w.store.add_job("https://example.test/submitted")
        self.w.store.job(job["id"], resume_id=first["id"], status="ready")
        self.w.store.job(submitted["id"], resume_id=first["id"], status="submitted")
        self.tag(second, "Engineering")
        self.assertEqual([row["id"] for row in self.w.resumes.list()], [second["id"]])
        self.assertTrue(self.w.docs.verify(first["id"]))
        self.assertEqual(self.w.store.get("job", job["id"])["status"], "documents_changed")
        self.assertEqual(self.w.store.get("job", submitted["id"])["resume_id"], first["id"])
        self.assertEqual(self.w.store.get("job", submitted["id"])["status"], "submitted")
        with self.assertRaisesRegex(ValueError, "active"):
            self.tag(first, "General")

    def test_retagging_moves_the_row_and_replaces_destination(self):
        first, second = self.upload(), self.upload("Second.pdf")
        self.tag(first, "General")
        self.tag(second, "Engineering")
        self.tag(first, "Engineering")
        self.assertEqual([(row["id"], row["category"]) for row in self.w.resumes.list()], [(first["id"], "Engineering")])
        with self.assertRaisesRegex(ValueError, "Upload and tag"):
            self.w.resumes.select("Finance")

    def test_tags_are_profile_bound_and_removed_industries_do_not_match(self):
        document = self.upload()
        with self.assertRaisesRegex(ValueError, "General or an industry"):
            self.tag(document, "Invented category")
        self.tag(document, "Engineering")
        profile = self.w.profile()
        profile["industries"] = [{"name": "Finance", "years": "1"}, {"name": "general", "years": "0"}]
        self.w.save_profile(profile)
        self.assertEqual(self.w.resumes.categories(), ["General", "Finance"])
        with self.assertRaisesRegex(ValueError, "Upload and tag"):
            self.w.resumes.select("Engineering")

    def test_pdf_and_docx_attach_the_original_bytes_only(self):
        for filename, content in (("Original.pdf", sample_pdf()), ("Original.docx", sample_docx())):
            with self.subTest(filename=filename):
                document = self.upload(filename, content)
                path, _, digest = _document(self.w, {"resume_id": document["id"]},
                                            {"kind": "resume", "document_id": document["id"]})
                self.assertEqual(path, Path(self.temp.name) / filename)
                self.assertEqual(path.read_bytes(), content)
                self.assertEqual(digest, hashlib.sha256(content).hexdigest())
                self.assertTrue(document["blocks"])
                self.assertNotIn("template_id", document)

    def test_picker_preserves_name_location_permissions_and_keeps_the_path_private(self):
        chosen = Path(self.temp.name) / "Folder chosen by applicant"
        chosen.mkdir()
        source = chosen / "Jane Example — Resume.PDF"
        data = sample_pdf()
        source.write_bytes(data)
        source.chmod(0o640)
        record = self.w.import_local_resume(source)
        self.assertEqual(self.w.docs.source_file(record, for_upload=True), source)
        self.assertEqual(self.w.docs.asset_bytes(record["source_path"]), (source.name, data))
        self.assertNotIn(str(chosen), json.dumps(self.w.state(), default=str))
        self.assertEqual({p.name for p in chosen.iterdir()}, {source.name})
        self.assertEqual(source.stat().st_mode & 0o777, 0o640)
        directory = self.w.docs.root / record["id"]
        self.assertFalse(any(p.suffix.lower() == ".pdf" for p in directory.rglob("*")))
        for private in ("manifest.json", record["id"] + "/.location", record["id"] + "/.location.pdf"):
            with self.assertRaises(ValueError):
                self.w.docs.asset_bytes(private)
        DocumentDeletion(self.w).delete({"document_id": record["id"]})
        self.assertFalse(directory.exists())
        self.assertEqual(source.read_bytes(), data)

    def test_moved_replaced_or_symlinked_source_requires_reselection_even_with_matching_bytes(self):
        original = self.upload("Selected resume.pdf")
        source = self.w.docs.source_file(original, for_upload=True)
        moved = source.with_name("Moved resume.pdf")
        source.rename(moved)
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.w.docs.source_file(original, for_upload=True)
        source.write_bytes(moved.read_bytes())
        with self.assertRaisesRegex(ValueError, "replaced"):
            self.w.docs.asset_bytes(original["source_path"])
        source.unlink()
        source.symlink_to(moved)
        with self.assertRaisesRegex(ValueError, "link"):
            self.w.docs.source_file(original, for_upload=True)
        with self.assertRaisesRegex(ValueError, "link"):
            self.w.import_local_resume(source)

    def test_legacy_resume_can_be_read_but_requires_a_new_native_selection_to_attach(self):
        directory = self.w.docs.root / "legacy"
        directory.mkdir()
        data = sample_pdf()
        source = directory / "Same Name.pdf"
        source.write_bytes(data)
        alias = "legacy/Same Name.pdf"
        record = self.w.docs._save({"id": "legacy", "kind": "resume", "filename": source.name, "source_path": alias,
                                    "pdf_path": alias, "source_sha256": hashlib.sha256(data).hexdigest()})
        self.assertEqual(self.w.docs.asset_bytes(alias), (source.name, data))
        with self.assertRaisesRegex(ValueError, "Choose the original resume again"):
            _document(self.w, {"resume_id": record["id"]}, {"document_id": record["id"], "kind": "resume"})
        with self.assertRaisesRegex(ValueError, "file picker"):
            self.w.import_file({"kind": "resume", "filename": source.name, "data": ""})

    def test_changed_original_is_rejected(self):
        document = self.upload()
        self.tag(document, "General")
        self.w.docs.asset(document["source_path"]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.w.resumes.select("Engineering")

    def test_preparation_reselects_current_resume_instead_of_stale_job_copy(self):
        document = self.upload()
        self.tag(document, "General")
        job = self.w.store.add_job("https://example.test/job")
        job["resume_id"] = "obsolete"
        apps = Applications(self.w)
        with patch.object(apps.documents, "require_review") as review:
            apps._resume(job, {"category": "Engineering"})
        review.assert_called_once_with(document["id"])
        self.assertEqual(self.w.store.get("job", job["id"])["resume_id"], document["id"])

    def test_document_generation_and_template_uploads_are_unavailable(self):
        """The document workflow contains only local checks, and uploads reject templates."""
        document = self.upload()
        tasks = DocumentTasks(self.w)
        for method in ("generate", "cover", "chat", "save"):
            self.assertFalse(hasattr(tasks, method))
        for kind in ("resume_template", "cover_template"):
            with self.assertRaisesRegex(ValueError, "Template uploads are no longer available"):
                self.w.import_file({"kind": kind})
        self.w.docs.verify(document["id"])

    def test_resume_integrity_is_checked_without_another_model_or_template(self):
        """An unchanged uploaded resume needs no separate layout or factual agent."""
        document = self.upload()
        tasks = DocumentTasks(self.w)
        with patch.object(self.w, "client", side_effect=AssertionError("Unexpected model call")):
            result = tasks.require_review(document["id"])
        self.assertTrue(result["passed"])
        self.assertEqual(result["method"], "file_integrity")
        self.w.docs.asset(document["image_paths"][0]).write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "previews are missing"):
            tasks.require_review(document["id"])


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
        path, _, digest = _document(self.w, {"resume_id": selected}, {"kind": "resume", "document_id": selected})
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
        self.w.docs._save({**self.chosen, "source_sha256": digest, "sha256": digest, "source_size": len(source.read_bytes())})
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

    def test_deleted_upload_preserves_outcome_but_removes_local_bytes(self):
        """Deleting a chosen upload cannot make a new run silently use General instead."""
        historical = self.w.store.add_job("https://example.test/jobs/history")
        self.w.store.job(historical["id"], resume_id=self.chosen["id"], status="submitted")
        result = DocumentDeletion(self.w).delete({"document_id": self.chosen["id"]})
        self.assertFalse(result["application_copies_kept"])
        app = Applications(self.w, resume_choice=self.choice)
        with patch.object(app.documents, "require_review") as review:
            with self.assertRaisesRegex(ValueError, "no longer available"):
                app._resume(self.job, {"category": "Hardware"})
        review.assert_not_called()
        self.assertFalse((self.w.docs.root / self.chosen["id"]).exists())
        self.assertIsNone(self.w.store.get("job", historical["id"])["resume_id"])
        self.assertEqual(self.w.store.get("job", historical["id"])["status"], "submitted")
