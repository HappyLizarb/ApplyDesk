"""Application resources and task lifecycle, with no provider calls on startup."""

import base64
import threading
import shlex
import sys
from pathlib import Path

from . import PROGRAM_ROOT
from .documents import DocumentService
from .employer_accounts import EmployerAccounts
from .keychain import Keychain
from .profile import PROFILE_SCHEMA_VERSION, empty_profile, model_profile, profile_with_defaults, public_job_url, validate_profile
from .agent_tasks import AgentTasks
from .native_application_browser import NativeApplicationBrowser
from .orchestrator import Orchestrator
from .recommendations import Recommendations
from .resumes import ResumeLibrary
from .storage import Store, now

class Workspace:
    """Share local storage and bounded background work across HTTP requests."""

    def __init__(self, root, secrets=None):
        self.root = Path(root)
        self.store = Store(self.root)
        self.secrets = secrets or Keychain()
        self.docs = DocumentService(self.root / "documents")
        self.sessions = {}
        self.application_browser = NativeApplicationBrowser(self)
        self.tasks = {}
        self.lock = threading.RLock()
        self.resumes = ResumeLibrary(self)
        self.chat = AgentTasks(self)
        self.recommendations = Recommendations(self)
        self.orchestrator = Orchestrator(self)
        for job in self.store.all("job"):
            if job["status"] in {"ready", "preparing", "manual_review"}:
                self.store.job(job["id"], status="needs_review", exceptions=["The previous live browser session ended. Prepare again before continuing."])
        discovery = self.store.get("discovery", "main", {})
        if discovery.get("status") == "searching":
            discovery.update(status="error", finished_at=now(),
                             warnings=[*discovery.get("warnings", []), "The previous search stopped when the app closed. Run Find jobs again."])
            self.store.put("discovery", "main", discovery)

    def profile(self):
        """Return the canonical profile or a blank onboarding draft."""
        return profile_with_defaults(self.store.get("profile", "main", empty_profile()))

    def settings(self):
        """Keep document preferences; the orchestrator owns the single model selection."""
        return self.store.get("settings", "main", {"cover_template_id": ""})

    def state(self):
        """Expose UI state while keeping secrets and browser session internals private."""
        return {"profile": self.profile(), "profile_schema": PROFILE_SCHEMA_VERSION,
                "settings": {"cover_template_id": self.settings().get("cover_template_id", "")},
                "chat_tasks": self.chat.list(), "draft_only": False, "submission_requires_chat_limit": False, "orchestrator": self.orchestrator.state(),
                "chat_command": shlex.join([sys.executable, str(PROGRAM_ROOT / "agent.py")]),
                "profile_imports": [{key: record.get(key) for key in ("id", "filename", "needs_manual_entry")}
                                    for record in self.docs.manifest.copy().values() if record["kind"] == "profile"],
                "discovery": self.store.get("discovery", "main", {}),
                "recommendations": self.recommendations.state(),
                "recommendation_preferences": self.recommendations.preferences(),
                "jobs": [EmployerAccounts(self).display(job) for job in self.store.all("job") if not job.get("removed_at")], "templates": self.docs.list_templates(),
                "documents": self.docs.list_documents(), "library": self.store.all("library"),
                "uploads": [{key: record.get(key) for key in ("id", "kind", "filename", "title")}
                            for record in self.docs.manifest.copy().values() if record["kind"] != "document"],
                "resumes": self.resumes.list(), "resume_categories": self.resumes.categories(),
                "tasks": list(self.tasks.values()), "data_path": str(self.root)}

    def client(self, role):
        """Provider access is disabled; this experiment uses only the user's chat."""
        raise ValueError("Provider calls are disabled. Continue the draft in your connected chat.")

    def save_settings(self, data):
        """Only the cover-template preference remains editable; old credentials stay unused."""
        if set(data) != {"cover_template_id"}:
            raise ValueError("Agent settings are no longer available in chat-only mode.")
        template_id = data["cover_template_id"]
        if not isinstance(template_id, str):
            raise ValueError("Choose a cover letter template.")
        if template_id:
            template = self.docs.get_template(template_id)
            if template["kind"] != "cover_template" or not template["confirmed"]:
                raise ValueError("Choose a confirmed cover letter template.")
        current = {**self.settings(), "cover_template_id": template_id}
        self.store.put("settings", "main", current)
        return {"cover_template_id": template_id}

    def save_profile(self, data):
        """Changing facts invalidates pending readiness until eligibility is checked again."""
        profile = validate_profile(data, self.profile())
        self.store.put("profile", "main", profile)
        self.invalidate_profile_jobs("Profile changed. Prepare again to check eligibility and answers.")
        return profile

    def invalidate_profile_jobs(self, message):
        """Changed confirmed inputs require fresh checks while preserving completed applications."""
        for job in self.store.all("job"):
            if job["status"] not in {"new", "submitted", "submitting", "submission_unknown"}:
                self.store.job(job["id"], status="needs_review", exceptions=[message])

    def add_job(self, data):
        """Accept only a user-supplied public job URL and avoid duplicate rows."""
        url = public_job_url(str(data.get("url", "")).strip())
        existing = next((job for job in self.store.all("job") if job["url"] == url), None)
        if existing and existing.get("removed_at"):
            return self.store.job(existing["id"], removed_at=None)
        return existing or self.store.add_job(url)

    def remove_jobs(self, data):
        """Hide selected applications and close their controlled browser sessions."""
        with self.lock:
            if any(task["status"] == "running" for task in self.tasks.values()):
                raise ValueError("Another task is running. Wait for it to finish first.")
            ids = self.store.remove_jobs(data.get("ids"))
            sessions = [self.sessions.pop(job_id, None) for job_id in ids]
        for session in sessions:
            if session:
                session.close()
        return {"removed": len(ids)}

    def import_file(self, data):
        """Decode bounded uploads and retain transcript identity in the profile."""
        if data.get("kind") in {"resume_template", "cover_template"}:
            raise ValueError("Template uploads are no longer available. Upload a finished resume instead.")
        try:
            content = base64.b64decode(data["data"], validate=True)
        except (ValueError, KeyError):
            raise ValueError("Invalid file upload.") from None
        record = self.docs.import_file(data["filename"], content, data["kind"])
        if data["kind"] == "resume":
            self.resumes.add(record["id"])
        if data["kind"] == "transcript":
            profile = self.profile()
            profile["transcript_id"] = record["id"]
            profile["revision"] += 1
            self.store.put("profile", "main", profile)
            self.invalidate_profile_jobs("Transcript changed. Prepare again to check the attachment.")
        return record

    def start(self, name, action, *args):
        """Run one bounded background task at a time with visible progress and errors."""
        with self.lock:
            if any(task["status"] == "running" for task in self.tasks.values()):
                raise ValueError("Another task is running. Wait for it to finish first.")
            self.tasks[name] = {"id": name, "status": "running", "message": "Working…"}
        threading.Thread(target=self._run, args=(name, action, args), daemon=True).start()
        return {"task": name}

    def _run(self, name, action, args):
        """Keep exceptions visible without returning private request payloads or traces."""
        try:
            result = action(*args)
            self.tasks[name].update(status="done", message="Finished", result=result)
        except Exception as exc:
            self.tasks[name].update(status="error", message=str(exc))

    def close(self):
        """Close controlled browser sessions without changing application statuses."""
        self.orchestrator.close()
        self.recommendations.close()
        for session in self.sessions.values():
            if getattr(self.application_browser, "native", False) and getattr(session, "manager", None) is self.application_browser:
                session.park()
            else:
                session.close()
        self.application_browser.close()
        self.store.close()
