"""Synthetic native windows exercise the real application pipeline without browser automation."""

import copy
import hashlib
import json
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from chat_helpers import ChatFixture, selected_fixture
from test_orchestrator import response
from applydesk import ai_prompts
from applydesk.documents import DocumentService
from applydesk.application_client import STEP_BOUNDARY
from applydesk.application_intent import INTENT_PROMPT
from applydesk.application_policy import SCOPE_PROMPT
from applydesk.application_submission import attempt_key
from applydesk.native_agent_prompt import PAGE_PROMPT, RECEIPT_PROMPT
from applydesk.native_evidence import observation
from applydesk.native_review import validate_review
from applydesk.orchestrator_prompts import SELF_CHECK
from applydesk.storage import Store


class NativeWindow:
    """Reject every DOM operation and require a durable reservation before the final click."""

    def __init__(self, workspace, job, readonly, receipt=True):
        self.w, self.job, self.readonly, self.receipt = workspace, job, readonly, receipt
        self.calls, self.name, self.filename, self.clicks, self.parked = [], "", "", 0, False
        self.page = self.view("form")

    def view(self, stage):
        """Return only visible text and native accessibility controls for this fictional window."""
        text = self.job["public_source"]["description"]
        elements = [{"id": "upload", "role": "AXButton", "label": "Attach resume", "enabled": True},
                    {"id": "send", "role": "AXButton", "label": "Send application", "enabled": True}]
        if self.name:
            text += "\nName: " + self.name
        if self.filename:
            text += "\nResume: " + self.filename
        if not (self.readonly and self.filename):
            elements.insert(0, {"id": "name", "role": "AXTextField", "label": "Full name",
                "value": self.name, "enabled": True, "web_content": True, "secure": False})
        return {"native": True, "snapshot_id": self.job["id"] + ":" + stage, "url": self.job["url"],
                "title": self.job["title"], "text": text, "elements": elements,
                "viewport": {"width": 1000, "height": 800}, "loading": False}

    def call(self, method, parameters=None):
        """Only native observations, native input and ordinary opening are valid transports."""
        parameters = parameters or {}
        self.calls.append((method, copy.deepcopy(parameters)))
        if method not in {"open", "native_observe", "native_action"}:
            raise AssertionError("Forbidden DOM or legacy browser method: " + method)
        if method == "open":
            assert parameters == {"url": self.job["url"], "listing_only": True}
        elif method == "native_action":
            assert parameters["snapshot_id"] == self.page["snapshot_id"]
            self.act(parameters)
        return copy.deepcopy(self.page)

    def act(self, parameters):
        """Simulate one native action and independently inspect the database at submission time."""
        action = parameters["action"]
        if action == "type":
            assert parameters["element_id"] == "name" and parameters["replace"] is True
            self.name = parameters["text"]
            self.page = self.view("typed")
        elif action == "click":
            assert parameters["element_id"] == "send"
            attempt = self.w.store.get("submission_attempt", attempt_key(self.job))
            assert attempt["status"] == "submitting" and attempt["id"] == parameters["final_permit"]
            assert self.clicks == 0
            self.clicks += 1
            self.page = {**self.view("outcome"), "elements": [], "text": (
                "Your application was received by " + self.job["company"] + "."
                if self.receipt else "Connection lost. No application confirmation is available.")}
        else:
            assert action == "scroll" or (action == "key" and parameters["key"] == "home")

    def park(self):
        """Retain the application window after the real pipeline finishes its work."""
        self.parked = True
        assert self.page.get("native") is True

    def close(self):
        """Fixture teardown has no real browser or personal window to close."""
        self.parked = True
        self.calls.append(("fixture_close", {}))


class NativePipelineFixture(ChatFixture):
    """Configure the real workspace, master runner and durable store around synthetic I/O."""

    job_count = 5

    def setUp(self):
        super().setUp()
        # Synthetic viewports settle synchronously after every action.
        self.enterContext(patch("applydesk.native_page.time"))
        self.engine = self.w.orchestrator
        self.w.store.put("chatgpt", "accounts", {"active": "test", "accounts": [{"id": "test",
            "label": "Test", "client_id": "oaiapp_test", "subject": "subject", "connected": True}]})
        self.w.store.put("chatgpt_models", "test", [{"id": "test-model", "name": "Test", "thinking": []}])
        profile = self.w.profile()
        profile["industries"] = [{"name": "Hardware", "years": "0"}]
        profile["preferences"]["levels"] = ["Entry"]
        self.w.save_profile(profile)
        self.jobs = [self.listing(index) for index in range(self.job_count)]
        message = self.engine.history.add("user", "Please submit five new-grad hardware applications anywhere using my unchanged resume.")
        self.engine.active = {"message": message}
        self.intent = {"action": "start", "run_id": "", "authorization_message_id": message["id"],
            "limit": 5, "locations": ["Anywhere"], "instruction": message["text"], "resume_id": "resume", "issues": []}
        self.windows, self.requests, self.roles, self.upload_calls = {}, [], [], []
        self.missing_receipt = None
        self.browsers = []

    def listing(self, index):
        """Five unfamiliar public hosts share no adapter or known employer rules."""
        company, title = "Synthetic Circuits " + str(index), "Hardware Graduate " + str(index)
        description = company + ". " + title + ". Entry hardware role. Full-time new-grad position. Python required."
        description += " Design and test electronic hardware, document laboratory results, and collaborate with analog and digital engineering teams. Applications from graduating engineers are welcome."
        url = "https://unseen-" + str(index) + ".example.test/careers/hardware"
        source = {"url": url, "company": company, "title": title, "description": description,
            "description_sha256": hashlib.sha256(description.encode()).hexdigest(),
            "complete": True, "captured_at": "2026-11-17T00:00:00+00:00"}
        job = self.w.store.add_job(url)
        return self.w.store.job(job["id"], company=company, title=title, public_source=source)

    def browser(self, _engine, *, job_id):
        """Create a visible test window from the current saved listing."""
        job = self.w.store.get("job", job_id)
        window = NativeWindow(self.w, job, len(self.browsers) % 2 == 1, job_id != self.missing_receipt)
        self.windows[job_id] = window
        self.browsers.append(window)
        return window

    def upload(self, session, workspace, job, profile, page, decision):
        """Mock only the separately tested picker, retaining real selected-document bytes and hashes."""
        assert decision["document_id"] == job["resume_id"]
        assert decision["element_id"] == "upload" and page["native"] is True
        document = workspace.docs.get(decision["document_id"])
        source = workspace.docs.asset(document["source_path"])
        session.filename = source.name
        session.page = session.view("uploaded")
        self.upload_calls.append(job["id"])
        record = {"kind": "resume", "document_id": document["id"], "element_id": "upload",
            "filename": source.name, "visible_filename": source.name, "size": source.stat().st_size,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "source_verified": True,
            "browser_bytes_verified": False, "upload_confirmed": False, "selection_method": "native_file_chooser"}
        return copy.deepcopy(session.page), record

    def model(self, payload, _cancelled, _emit):
        """Return decisions through the real configured master runner and JSON client."""
        assert payload["instructions"].startswith(SELF_CHECK)
        prompt = payload["instructions"].removeprefix(SELF_CHECK).removeprefix(STEP_BOUNDARY)
        content = payload["input"][0]["content"]
        data = json.loads(content if isinstance(content, str) else content[0]["text"])
        self.roles.append(self.engine.runner.current["role"])
        self.requests.append((prompt, data))
        if prompt == INTENT_PROMPT:
            result = self.intent
        elif prompt == ai_prompts.ANALYZE:
            result = self.analysis(data)
        elif prompt == SCOPE_PROMPT:
            result = {"matches": True, "industry": "Hardware", "level": "Entry", "location": self.intent["locations"][0],
                "issues": [], "quotes": {"industry": "hardware", "level": "Entry",
                                         "location": "" if self.intent["locations"] == ["Anywhere"] else "Boston"}}
        elif prompt == PAGE_PROMPT:
            result = self.decision(data)
        elif prompt == RECEIPT_PROMPT:
            result = {"status": "submitted", "quote": "Your application was received by " + data["company"] + ".",
                "reason": "", "evidence_source": "text"}
        else:
            raise AssertionError("Unexpected model request")
        return response(json.dumps(result))

    def analysis(self, data):
        """Screen actual native listing text with exact requirements and confirmed supporting facts."""
        source = data["page"]["public_source"]
        assert data["page"]["native"] is True
        return {"company": source["company"], "title": source["title"], "details": "Entry hardware role.",
            "country": "US", "category": "Hardware", "preferred": [], "cover_letter_required": False,
            "requirements_complete": True, "required": [{"text": "Python required.", "met": True, "evidence_ids": ["skills.0"]}],
            "passed": True, "issues": [], "process_complete": True, "identity_consistent": True,
            "employment_type_consistent": True, "consistency_issues": [], "process_restrictions": []}

    def decision(self, data):
        """Fill a confirmed field, select the original attachment, then review current visible evidence."""
        page, job = data["page"], data["job"]
        assert page["native"] is True and "fields" not in page
        name = next((element for element in page["elements"] if element["id"] == "name"), None)
        fact = ["identity.legal_name"]
        if name and not name["value"]:
            return {"action": "act", "purpose": "Enter the confirmed name.", "operations": [{"action": "type",
                "element_id": "name", "text": "Alex Test", "replace": True, "intent": "answer", "fact_ids": fact}]}
        if not data["uploaded"]:
            return {"action": "upload", "purpose": "Select the unchanged resume.",
                    "element_id": "upload", "document_id": next(doc["id"] for doc in data["documents"] if doc["kind"] == "resume"), "kind": "resume"}
        answer = {"quote": "Name: Alex Test", "fact_ids": fact}
        review = {"final_element_id": "send", "company_quote": job["company"], "title_quote": job["title"],
            "resume_quote": "Resume: " + next(doc["filename"] for doc in data["documents"] if doc["kind"] == "resume"), "required_complete": True, "checked_all_steps": True,
            "no_unapproved_agreements": True, "answers": [], "summaries": []}
        review["answers" if name else "summaries"] = [{**answer, "element_id": "name", "value": "Alex Test"} if name else answer]
        return {"action": "review", "purpose": "Check all answers and the selected file.", "review": review}

    def run_applications(self, job_ids=None):
        """Leave all pipeline code real; replace only external model and native I/O boundaries."""
        with patch("applydesk.application_automation.ApplicationSession", side_effect=self.browser):
            with patch.object(self.engine.runner, "_request", side_effect=self.model):
                with patch("applydesk.native_agent.upload", side_effect=self.upload):
                    return self.engine.applications.start(job_ids)


class NativeReviewFixture:
    """Provide the same ready form to review, submission, and recovery tests."""

    def setUp(self):
        """Prepare a synthetic review page, confirmed profile, and original resume in a temporary store."""
        self.directory = tempfile.TemporaryDirectory()
        self.source = Path(self.directory.name) / "resume.pdf"
        self.source.write_bytes(b"original resume")
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.profile = {"confirmed": True, "revision": 3, "facts": [
            {"id": "name", "text": "Alex Test", "confirmed": True}]}
        self.job = {"id": "job", "company": "Example Co", "title": "Hardware Graduate",
                    "url": "https://unseen.example.test/role/91", "status": "ready",
                    "profile_revision": 3, "resume_id": "resume-v1"}
        self.page = {"native": True, "snapshot_id": "review", "url": self.job["url"],
                     "text": "Example Co\nHardware Graduate\nName: Alex Test\nResume: resume.pdf",
                     "elements": [{"id": "final", "role": "AXButton", "label": "Send application", "enabled": True}]}
        self.review = {"final_element_id": "final", "company_quote": "Example Co", "title_quote": "Hardware Graduate",
                       "resume_quote": "Resume: resume.pdf", "required_complete": True,
                       "checked_all_steps": True, "no_unapproved_agreements": True,
                       "answers": [], "summaries": [{"quote": "Name: Alex Test", "fact_ids": ["name"]}]}
        self.ledger = [{"kind": "resume", "document_id": "resume-v1", "filename": "resume.pdf",
                        "visible_filename": "resume.pdf", "sha256": digest, "size": self.source.stat().st_size,
                        "source_verified": True, "browser_bytes_verified": False, "selection_method": "native_file_chooser"}]
        self.evidence = [observation(self.page)]
        self.store = Store(Path(self.directory.name) / "store")
        self.store.put("job", "job", self.job)
        docs = DocumentService(Path(self.directory.name) / "documents")
        selected_fixture(docs, "resume-v1", self.source)
        self.workspace = SimpleNamespace(store=self.store, docs=docs, profile=lambda: self.profile,
                                        lock=threading.RLock(), sessions={})

    def tearDown(self):
        """Release the local database before removing the fixture workspace."""
        self.store.db.close()
        self.directory.cleanup()

    def validate(self):
        """Evaluate the fixture using the ordinary final review checks."""
        return validate_review(self.workspace, self.job, self.profile, self.page, self.ledger, self.evidence, self.review)

    def ready(self, result=None):
        """Prepare a reviewed job and a simulated application session for the submission code."""
        self.job.update(final_audit=self.validate(), attachments=self.ledger, native_review=self.review,
                        native_evidence=self.evidence, last_snapshot=self.page["snapshot_id"])
        self.store.put("job", "job", self.job)
        after = {**self.page, "snapshot_id": "received", "text": "Thank you. Your application was received."}
        self.session = Mock()
        self.session.call.side_effect = lambda method, params=None: self.page if method == "native_observe" else (result or after)
        self.workspace.sessions["job"] = self.session
        return SimpleNamespace(w=self.workspace), {"id": "run", "limit": 1, "profile_revision": 3}
