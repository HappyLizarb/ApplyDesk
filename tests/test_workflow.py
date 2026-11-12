"""Integration checks connect mapping, auditing, attachment provenance, and readiness."""

import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from applydesk.applications import Applications
from applydesk.profile import empty_profile, validate_profile
from applydesk.storage import Store


def field(identity, kind, label, required=True):
    """Describe one native control with the same contract as Playwright inspection."""
    return {"field_id": identity, "type": kind, "label": label, "required": required,
            "disabled": False, "supported": True, "value": "", "files": [],
            "form_id": "application", "group": "", "options": [], "accept": ".pdf"}


def snapshot(final=True):
    """Provide explicit application context, rather than an unrelated submit control."""
    button = {"field_id": "submit", "text": "Submit application", "action": "final_or_ambiguous",
              "form_id": "application", "disabled": False}
    next_button = {"field_id": "next", "text": "Next", "action": "advance", "disabled": False}
    return {"snapshot_id": "initial", "url": "https://example.test/apply", "title": "Example Co — Analyst",
            "text": "Example Co Analyst application", "fields": [field("name", "text", "Full name"), field("resume", "file", "Resume")],
            "buttons": [button, next_button] if final else [next_button], "links": [], "warnings": [],
            "guard": {"manual": False, "mutations_blocked": True, "blocked_requests": []}}


class BrowserFixture:
    """A deterministic browser double; the separate browser suite uses real Chrome."""

    def __init__(self, page, second=None):
        self.page, self.second = page, second
        self.methods, self.after_fill = [], None

    def call(self, method, params=None):
        """Apply requested values exactly and return newly captured snapshots."""
        self.methods.append(method)
        params = params or {}
        if method == "fill":
            return self.fill(params)
        if method == "next":
            self.page = self.second
        return copy.deepcopy(self.page)

    def fill(self, params):
        """Compute uploaded file hashes independently of the supplied expected hash."""
        readbacks = []
        for answer in params.get("answers", []):
            current = next(item for item in self.page["fields"] if item["field_id"] == answer["field_id"])
            current["value"] = answer["value"]
            readbacks.append({"field_id": current["field_id"], "matches": True, "value": current["value"]})
        for upload in params.get("uploads", []):
            current = next(item for item in self.page["fields"] if item["field_id"] == upload["field_id"])
            digest = hashlib.sha256(Path(upload["path"]).read_bytes()).hexdigest()
            current["files"] = [{"name": Path(upload["path"]).name, "sha256": digest}]
            readbacks.append({"field_id": current["field_id"], "matches": True, "files": current["files"]})
        self.page["snapshot_id"] = "filled-" + str(len(self.methods))
        if self.after_fill:
            self.after_fill()
        return {**copy.deepcopy(self.page), "readbacks": readbacks}


class WorkflowTests(unittest.TestCase):
    """No paid providers or employer websites are contacted by these tests."""

    def setUp(self):
        """Prepare a confirmed fictional identity and byte-addressed resume fixture."""
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        draft = empty_profile()
        draft["identity"].update(legal_name="Example Person", email="person@example.test")
        draft["confirmed"] = True
        self.profile = validate_profile(draft, empty_profile())
        path = self.root / "resume.pdf"
        path.write_bytes(b"synthetic attachment fixture")
        docs = SimpleNamespace(root=self.root, get=lambda _: {"id": "resume-version", "kind": "document", "pdf_path": path.name, "docx_path": path.name},
                               asset=lambda name: self.root / name, verify=lambda _: True)
        client = SimpleNamespace(ask=lambda *_args, **_kwargs: {"passed": True, "issues": []})
        self.w = SimpleNamespace(root=self.root, store=self.store, docs=docs, profile=lambda: self.profile, client=lambda _: client, sessions={})
        self.w.resumes = SimpleNamespace(categories=lambda: ["General"])
        self.app = Applications(self.w)
        self.app.documents.require_review = Mock(return_value={"passed": True})
        self.job = self.store.add_job("https://example.test/job")
        self.job = self.store.job(self.job["id"], title="Analyst", company="Example Co", status="preparing", resume_id="resume-version", profile_revision=self.profile["revision"])

    def tearDown(self):
        """Remove only the temporary synthetic workspace."""
        self.store.close()
        self.temp.cleanup()

    def run_walk(self, browser):
        """Exercise the real coordinator through its page preparation loop."""
        return self.app._walk(browser, self.job, self.profile, {"country": "US"}, copy.deepcopy(browser.page))

    def test_ready_keeps_attachment_ledger_and_never_advances_at_submit(self):
        """A positive path requires exact fields, reviews, file bytes, and final context."""
        browser = BrowserFixture(snapshot())
        result = self.run_walk(browser)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["cover_state"], "not_required")
        self.assertEqual(len(result["attachments"]), 1)
        self.assertNotIn("next", browser.methods)
        self.assertNotIn("authenticate", browser.methods)

    def test_unknown_required_answer_blocks_before_any_fill(self):
        """The coordinator cannot invent a response simply to finish a form."""
        page = snapshot()
        page["fields"].append(field("salary", "text", "Required salary"))
        browser = BrowserFixture(page)
        with self.assertRaisesRegex(ValueError, "Required salary"):
            self.run_walk(browser)
        self.assertNotIn("fill", browser.methods)

    def test_document_change_during_preparation_cannot_become_ready(self):
        """Readiness compares selected document IDs against current storage."""
        browser = BrowserFixture(snapshot())
        browser.after_fill = lambda: self.store.job(self.job["id"], resume_id="new-version")
        with self.assertRaisesRegex(ValueError, "changed|currently selected"):
            self.run_walk(browser)

    def test_historical_upload_hidden_at_final_is_not_lost(self):
        """A later page cannot erase the obligation to verify an earlier upload."""
        second = snapshot()
        second["fields"] = [field("name-final", "text", "Full name")]
        browser = BrowserFixture(snapshot(final=False), second)
        with self.assertRaisesRegex(ValueError, "earlier upload is hidden"):
            self.run_walk(browser)
        self.assertEqual(browser.methods.count("next"), 1)
        stored = self.store.get("job", self.job["id"])
        self.assertEqual(len(stored["attachments"]), 1)

    def test_generic_finish_button_does_not_prove_ready(self):
        """The worker must pause even when a vague control happens to look final."""
        page = snapshot()
        page["buttons"][0]["text"] = "Finish"
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.run_walk(BrowserFixture(page))

    def test_screen_preserves_full_description_instead_of_model_summary(self):
        """Eligibility summaries cannot replace the original text used for structured fields."""
        page = snapshot()
        page["text"] = "Responsibilities\nBuild tools.\nRequired Qualifications\nPython.\nSalary: $95,000 per year."
        analysis = {"company": "Example Co", "title": "Analyst", "details": "Short generated summary"}
        with patch("applydesk.intelligence.analyze_job", return_value=analysis):
            with patch("applydesk.intelligence.validate_eligibility", return_value={"eligible": False, "reasons": ["Missing evidence"]}):
                self.app._screen(self.job, self.profile, page)
        saved = self.store.get("job", self.job["id"])
        self.assertEqual(saved["details"], page["text"])
        self.assertEqual(saved["analysis"]["details"], "Short generated summary")
        self.assertIn("Python.", saved["detail_sections"]["required_qualifications"])
        self.assertIn("$95,000", saved["compensation"])


if __name__ == "__main__":
    unittest.main()
