"""Let the configured single agent choose live form actions from current evidence."""

from .agent_browser_prompt import PAGE_PROMPT
from .application_steps import choose_attachment
from .application_answers import with_application_source
from .browser_bridge import BrowserError
from .application_terms import covers
from .employer_accounts import EmployerAccounts
from .intelligence import _facts


class ApplicationInputRequired(RuntimeError):
    """A real missing fact or required approval ends this job without an action retry."""


class AgentBrowser:
    """The model decides; the controller binds writes to observed elements and files."""

    def __init__(self, applications, session, job, profile, analysis):
        self.app, self.w, self.session = applications, applications.w, session
        self.job, self.profile, self.analysis = job, with_application_source(profile, job), analysis
        self.client = applications.client("application")
        self.page, self.ledger, self.history = {}, {}, []
        self.failures = 0

    def run(self):
        """Recover from unfamiliar controls without replaying an unchanged failed action."""
        self.page = self.session.call("agent_observe")
        for _ in range(120):
            self.app.checkpoint()
            decision = self.client.ask(PAGE_PROMPT, self._context())
            before = self.page.get("snapshot_id")
            try:
                result = self._decide(decision)
                if result is not None:
                    return result
                self.failures = self.failures + 1 if before == self.page.get("snapshot_id") else 0
            except (ValueError, BrowserError) as error:
                self.app.checkpoint()
                self.failures += 1
                self.history.append({"error": str(error)[:600]})
                self.page = self.session.call("agent_observe")
            if self.failures >= 3:
                raise ValueError("The agent could not make progress: " + str(self.history[-1])[:600])
        raise ValueError("The application needs review after repeated browser actions without a verified final view.")

    def _context(self):
        """Give the model current visible evidence, without passwords or local file paths."""
        documents = []
        for kind, identity in (("resume", self.job.get("resume_id")), ("transcript", self.profile.get("transcript_id"))):
            if identity:
                record = self.w.docs.get(identity)
                documents.append({"id": identity, "kind": kind, "filename": record.get("filename", "")})
        return {"job": {key: self.job.get(key) for key in ("id", "company", "title")},
                "profile": self.profile, "page": self.page, "documents": documents,
                "account": EmployerAccounts(self.w).display(self.job),
                "recent_actions": self.history[-6:], "uploaded": [
                    {key: item.get(key) for key in ("document_id", "kind", "filename", "sha256")}
                    for item in self.ledger.values()]}

    def _decide(self, decision):
        """Keep navigation, file upload and final review distinct without site-specific flows."""
        action = decision.get("action")
        purpose = str(decision.get("purpose", "Inspecting the application"))[:180]
        self.w.store.event(self.job["id"], purpose)
        self.history.append({"action": action, "purpose": purpose})
        if action == "needs_input":
            raise ApplicationInputRequired(str(decision.get("reason") or "A required application answer needs confirmation."))
        if action == "review":
            return self._review(decision.get("review", {}))
        if action == "act":
            self._actions(decision.get("operations"))
        elif action == "upload":
            self._upload(decision)
        elif action == "authenticate":
            from .agent_browser_auth import AuthenticationInputRequired, authenticate
            try:
                self.page = authenticate(self.w, self.session, self.job, self.page, decision)
            except AuthenticationInputRequired as error:
                raise ApplicationInputRequired(str(error)) from error
        elif action == "observe":
            self.page = self.session.call("agent_observe")
        else:
            raise ValueError("Choose an observed browser action, upload, authentication, review or missing input.")
        return None

    def _actions(self, operations):
        """Stop a batch when the form structure changes so the agent can inspect new fields."""
        if not isinstance(operations, list) or not 1 <= len(operations) <= 8:
            raise ValueError("Choose one to eight concrete browser actions.")
        for operation in operations:
            self.app.checkpoint()
            if not isinstance(operation, dict):
                raise ValueError("Each action must identify an observed control.")
            self._answer_evidence(operation)
            structure = self._structure()
            parameters = {key: operation[key] for key in ("action", "element_id", "value", "intent") if key in operation}
            parameters["snapshot_id"] = self.page["snapshot_id"]
            self.page = self.session.call("agent_action", parameters)
            if structure != self._structure() or operation.get("action") in {"click", "back"}:
                break

    def _answer_evidence(self, operation):
        """Unknown controls are allowed; invented personal answers and password text are not."""
        element = next((item for item in self.page.get("elements", []) if item["id"] == operation.get("element_id")), {})
        if element.get("type") == "password":
            raise ValueError("Use authenticate; password values must stay in the saved account service.")
        answering = operation.get("action") in {"fill", "select", "check"} or element.get("role") == "option"
        if not answering:
            return
        if not element.get("required") and operation.get("value") in ("", False):
            return
        label = element.get("label", "")
        if operation.get("action") == "check" and operation.get("value") is True and element.get("required") and covers(label):
            return
        facts, ids = _facts(self.profile), operation.get("fact_ids")
        if not isinstance(ids, list) or not ids or any(identity not in facts for identity in ids):
            raise ValueError("Every personal answer must cite confirmed profile facts; inspect or leave an optional answer blank.")

    def _structure(self):
        """Values may change during a batch; changed controls require a fresh model decision."""
        keys = ("id", "type", "role", "label", "required", "disabled", "form_id")
        return tuple(tuple(str(element.get(key, "")) for key in keys) for element in self.page.get("elements", []))

    def _upload(self, decision):
        """Resolve only the selected resume or confirmed transcript into a private upload path."""
        kind, identity = decision.get("kind"), decision.get("document_id")
        expected = {"resume": self.job.get("resume_id"), "transcript": self.profile.get("transcript_id")}
        if kind not in expected or not identity or identity != expected[kind]:
            raise ValueError("Use the selected unchanged resume or confirmed transcript; cover letters are not generated.")
        element = next((item for item in self.page.get("elements", []) if item["id"] == decision.get("element_id")), None)
        if not element or element.get("type") != "file":
            raise ValueError("Choose the observed file upload control.")
        record = self.w.docs.get(identity)
        path, digest = choose_attachment(self.w, record, element)
        upload = {"path": str(path), "sha256": digest, "document_id": identity, "kind": kind,
                  "filename": path.name, "size": path.stat().st_size, "field_id": element["id"]}
        self.page = self.session.call("agent_action", {"snapshot_id": self.page["snapshot_id"],
            "action": "upload", "element_id": element["id"], "upload": upload, "intent": "explore"})
        verified = self.page.get("result", {}).get("upload") or {}
        if verified.get("sha256") != digest or verified.get("selected_file_verified") is not True:
            raise ValueError("The browser has not verified the selected attachment bytes.")
        self.ledger[element["id"]] = {**upload, **verified, "element_id": element["id"]}
        self.w.store.job(self.job["id"], attachments=list(self.ledger.values()))

    def _review(self, review):
        """Store the exact fresh self-check for the separate, durable submission operation."""
        from .agent_browser_review import validate_review
        self.app.checkpoint()
        current = self.session.call("agent_observe")
        if current["snapshot_id"] != self.page["snapshot_id"]:
            self.page = current
            raise ValueError("The page changed during review. Check the current view again.")
        audit = validate_review(self.w, self.job, self.profile, current, list(self.ledger.values()), review)
        return self.w.store.job(self.job["id"], status="ready", exceptions=[], live_url=current["url"],
            attachments=list(self.ledger.values()), last_snapshot=current["snapshot_id"],
            final_audit=audit, agent_browser_review=review, cover_state="not_required")
