"""Synthetic local fixtures shared by chat workflow and HTTP boundary checks."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from applydesk.document_import import selected_resume
from applydesk.workspace import Workspace
from test_workspace import FakeSecrets


def selected_fixture(service, identity, source):
    """Register a synthetic selection without running an office renderer for every pipeline test."""
    data, binding = selected_resume(source)
    directory = service.root / identity
    directory.mkdir()
    (directory / ".location").write_text(json.dumps(binding), encoding="utf-8")
    (directory / "page.png").write_bytes(b"synthetic PNG page")
    digest = hashlib.sha256(data).hexdigest()
    alias = identity + "/selected-original"
    return service._save({"id": identity, "kind": "resume", "document_type": "resume", "filename": source.name,
                          "source_path": alias, "pdf_path": alias, "source_size": len(data), "source_sha256": digest,
                          "sha256": digest, "image_paths": [identity + "/page.png"], "text": "Alex Test",
                          "blocks": [{"id": "line:0", "text": "Alex Test", "editable": False}]})


class ChatFixture(unittest.TestCase):
    def setUp(self):
        """Use immutable synthetic files without accessing any saved personal workspace."""
        self.temp = tempfile.TemporaryDirectory()
        self.w = Workspace(Path(self.temp.name), secrets=FakeSecrets())
        profile = self.w.profile()
        profile["identity"].update(legal_name="Alex Test", email="alex@example.test")
        profile.update(confirmed=True, skills=["Python"])
        profile["preferences"].update(disability="yes", veteran="decline")
        self.w.save_profile(profile)
        self.resume = self.document("resume", "resume")
        self.w.resumes.add("resume")
        self.w.resumes.tag({"document_id": "resume", "category": "General"})
        self.job = self.w.store.add_job("https://example.test/jobs/one")
        self.w.store.job(self.job["id"], company="Example", title="Engineer", details="Required: Python.", details_origin="captured")
        self.task = self.w.chat.enqueue({"kind": "application", "target_id": self.job["id"]})

    def tearDown(self):
        """Release the database before deleting the isolated fixture."""
        self.w.close()
        self.temp.cleanup()

    def document(self, identity, kind):
        """Create known immutable source and preview bytes for version tests."""
        if kind == "resume":
            source = Path(self.temp.name) / (identity + ".pdf")
            source.write_bytes(b"synthetic PDF source")
            return selected_fixture(self.w.docs, identity, source)
        directory = self.w.docs.root / identity
        directory.mkdir()
        source, image = directory / "original.pdf", directory / "page.png"
        source.write_bytes(b"synthetic PDF source")
        image.write_bytes(b"synthetic PNG page")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        record = {"id": identity, "kind": kind, "filename": identity + ".pdf", "document_type": kind,
                  "source_path": identity + "/original.pdf", "pdf_path": identity + "/original.pdf",
                  "source_sha256": digest, "sha256": digest, "image_paths": [identity + "/page.png"],
                  "blocks": [{"id": "line:0", "text": "Alex Test", "editable": False}], "text": "Alex Test"}
        return self.w.docs._save(record)

    def payload(self):
        """Represent a grounded draft with no observed application form or cover requirement."""
        return {"task_id": self.task["id"], "version": self.w.chat.get(self.task["id"])["version"], "agent_id": "main-1",
                "result": {"analysis": {"category": "General", "requirements_complete": True,
                "required": [{"text": "Python", "met": True, "evidence_ids": ["skills.0"]}]},
                "answers": [], "form_text": "", "cover_requirement": {"state": "unknown", "quote": "", "source_url": "", "source_text": ""},
                "cover_edits": []}}
