"""Uploaded resume replacement, selection, immutable bytes, and AI boundaries."""

import base64
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from applydesk.applications import Applications
from applydesk.application_steps import choose_attachment
from applydesk.document_tasks import DocumentTasks
from applydesk.workspace import Workspace
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
        return self.w.import_file({"filename": filename, "kind": "resume", "data": base64.b64encode(content).decode()})

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
                path, digest = choose_attachment(self.w, document, {"accept": ""})
                self.assertEqual(path.suffix, Path(filename).suffix)
                self.assertEqual(path.read_bytes(), content)
                self.assertEqual(digest, hashlib.sha256(content).hexdigest())
                self.assertTrue(document["blocks"])
                self.assertNotIn("template_id", document)
                wrong = ".docx" if path.suffix == ".pdf" else ".pdf"
                with self.assertRaisesRegex(ValueError, "does not accept"):
                    choose_attachment(self.w, document, {"accept": wrong})

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
            with patch.object(self.w.docs, "get_template", side_effect=AssertionError("Unexpected template")):
                result = tasks.require_review(document["id"])
        self.assertTrue(result["passed"])
        self.assertEqual(result["method"], "file_integrity")
        self.w.docs.asset(document["image_paths"][0]).write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "previews are missing"):
            tasks.require_review(document["id"])
