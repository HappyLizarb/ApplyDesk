"""Let the configured agent explore visible Chrome using native mouse and keyboard input."""

from .account_scope import bound_signin_host, employer_key, is_registration_link, is_signin_switch
from .workday_skill import adapt_context
from .agent_context import with_application_source
from .native_browser import BrowserError
from .employer_accounts import EmployerAccounts
from .native_agent_prompt import PAGE_PROMPT
from .native_page import return_to_listing
from .native_window_recovery import observe_application
from .native_application_io import NativeInputRequired, authenticate, upload
from .native_evidence import agreement_facts, native_point, observation, public_page, review_context, validate_operation
from .native_review import NativeReviewError, cite_missing_answers, retained_resume, review_feedback, review_surface, validate_review

# Only fixed local diagnostics may enter persistent history; other exceptions can contain private input.
RECOVERY_MESSAGES = frozenset({
    "Use the saved job title, employer or published requisition in the employer search field.",
    "This control requires the protected account or reviewed submission operation.",
    "Choose the observed native upload control.",
    "The application changed. Reopen it before entering private information.",
    "The application window changed. Inspect it again before entering private information.",
    "The application window returned. Check the form to see whether the last action took effect.",
    "The observed native control is unavailable. Inspect the page again.",
    "The approved document path was not confirmed in the native file picker.",
    "The file was selected. Check the employer’s upload result and filename.",
    "The file picker did not open. Inspect the employer’s upload controls or notice.",
    "The file picker opened but its path field did not appear.",
    "The file picker could not select the original file. Inspect the open dialog.",
    "The original document changed. Select its current uploaded version before continuing.",
    "The selected document kind changed.",
    "Document not found.",
    "Document asset not found.",
    "The sign-in button changed. Inspect the employer’s response before retrying.",
    "The account field changed during entry. Inspect the current sign-in form.",
    "The account field did not retain the saved account name.",
    "The secure password field changed. Inspect the current sign-in form.",
    "Chrome could not type in the selected secure password field. Inspect the visible field.",
    "The owned Chrome window is ambiguous or unavailable.",
    "The owned Chrome window lost focus. Observe it again.",
    "The visible control moved or is covered. Observe again.",
    "The observed control is no longer visible.",
    "The observed control is disabled.",
    "Use the approved document upload action for a native file picker, or Escape to cancel.",
    "Observe a screenshot before choosing a coordinate.",
    "Identify the observed ordinary privacy choice.",
    "Quote the visible notice for this privacy choice. Use the agreement action for binding terms.",
    "Scroll needs a current point inside the owned window and a bounded distance.",
    "The native browser did not respond. Reopen its application window.",
    "The visible page changed during review. Check it again.",
    "Return to the application before interacting with the form.",
    "The original application tab is unavailable.",
    "The original application tab changed while the notice was open.",
    "The application's tab is not visible in its Chrome window. Reopen the application to continue.",
    "A native dialog covers the application tab.",
    "The application-window screenshot timed out.",
    "The application-window screenshot was unavailable.",
    "The application window changed. Observe it again.",
    "The application window changed during its screenshot. Observe it again.",
    "Enable Screen Recording for ApplyDesk to inspect application-window screenshots.",
    "Unsupported native browser key.",
    "Choose the visible agreement checkbox or radio button itself.",
    "Show the agreement wording next to its checkbox and inspect it again.",
    "This agreement needs the user's recorded approval for this posting.",
    "Choose the observed agreement control.",
    "Use the agreement action for this checkbox.",
})


def recovery_message(action, error):
    """Name the failed operation without copying model text, control values or private exception details."""
    operation = {"upload": "document upload", "authenticate": "account entry", "act": "browser action",
                 "observe": "page inspection", "screenshot": "image inspection", "review": "application review"}.get(action, "application step")
    message = str(error)
    if message not in RECOVERY_MESSAGES and not isinstance(error, NativeReviewError):
        message = {"stale_observation": "The visible page changed before the action.",
                   "window_closed": "The application window was closed."}.get(getattr(error, "code", None))
        message = message or ("The browser action could not be completed." if isinstance(error, BrowserError)
                              else "The step did not pass validation.")
    return f"Recovering from {operation}: {message}"[:700]


class NativeAgent:
    """Own the live decision loop; the native driver owns window and input authority."""

    def __init__(self, applications, session, job, profile, analysis, *, resumed_page=None):
        self.app, self.w, self.session = applications, applications.w, session
        self.job, self.profile, self.analysis = job, with_application_source(profile, job), analysis
        self.client = applications.client("application")
        self.page, self.uploads, self.history, self.evidence = {}, {}, [], []
        self.registration_route = None
        self.resumed_page = resumed_page
        self.review_feedback = {}

    def run(self):
        """Recover from stale controls with fresh evidence, without replaying unchanged failures."""
        self._observe()
        failures, image_seen = 0, None
        for _ in range(180):
            self.app.checkpoint()
            image = None if self.page.get("native_modal") else self.page.get("screenshot")
            if image:
                image_seen = self.page.get("snapshot_id")
            context = self._context()
            guide = adapt_context(context, self.w.store)
            prompt = PAGE_PROMPT + "\n\n" + guide if guide else PAGE_PROMPT
            decision = self.client.ask(prompt, context, images=[image] if image else None)
            before, prior_corrections = self.page.get("snapshot_id"), self.review_feedback.get("review_corrections")
            try:
                result = self._decide(decision)
                if result is not None:
                    return result
                failures = failures + 1 if before == self.page.get("snapshot_id") else 0
                if failures == 2 and decision.get("action") == "observe" and not image and not self.page.get("native_modal"):
                    self._observe(screenshot=True)
            except NativeInputRequired:
                raise
            except (ValueError, BrowserError) as error:
                self.app.checkpoint()
                changed_review = isinstance(error, NativeReviewError) and self.review_feedback.get("review_corrections") != prior_corrections
                failures = 1 if changed_review else failures + 1
                message = recovery_message(decision.get("action"), error)
                self.history.append({"error": message})
                self.w.store.event(self.job["id"], message)
                self._observe()
            fresh_image = self.page.get("screenshot") and self.page.get("snapshot_id") != image_seen
            if failures >= 4 and not fresh_image:
                raise ValueError("The agent could not make progress in the visible page: " + str(self.history[-1])[:700])
        raise ValueError("This application needs review after repeated native actions without a final application review.")

    def _context(self):
        """Keep secrets and file paths private while retaining concise prior-step evidence."""
        self.profile = with_application_source(self.profile, self.job)
        documents = []
        if identity := self.job.get("resume_id"):
            record = self.w.docs.get(identity)
            documents.append({"id": identity, "kind": "resume", "filename": record.get("filename", "")})
        # Account context excludes stored job history and previous blockers.
        accounts = EmployerAccounts(self.w)
        account = accounts.display(self.job)
        if account.get("employer_account_id") and accounts.registration_pending(self.job):
            account["registration_pending"] = True
        if account.get("employer_account_id"):
            saved = self.w.store.get("employer_account", account["employer_account_id"]) or {}
            host = bound_signin_host(self.job, saved, self.page.get("url"))
            if host:
                account["verified_signin_host"] = host
        account_fields = ("account", "account_origin", "has_password", "employer_account_id", "registration_pending", "verified_signin_host")
        job_context = {key: self.job.get(key) for key in ("id", "company", "title")}
        job_context["listing_company"] = (self.job.get("public_source") or {}).get("company", "")
        return {"job": job_context,
                "profile": {**self.profile, **{key: [fact for fact in self.profile.get(key, [])
                    if fact.get("kind") != "application_agreement"] for key in ("facts", "chat_evidence")}},
                "page": public_page(self.page), "documents": documents,
                "account": {key: account[key] for key in account_fields if key in account}, "recent_actions": self.history[-6:],
                **self.review_feedback,
                "approved_agreements": [{"id": key, "terms": fact["text"]} for key, fact in agreement_facts(self.profile, self.job).items()],
                "review_evidence": review_context(self.evidence, observation(self.page)), "uploaded": [
                    {key: item.get(key) for key in ("document_id", "kind", "filename", "visible_filename")}
                    for item in self.uploads.values()]}

    def _decide(self, decision):
        """Authentication, attachments and final submission keep their private backend boundaries."""
        action, purpose = decision.get("action"), str(decision.get("purpose", "Inspecting the application"))[:180]
        self.w.store.event(self.job["id"], purpose)
        self.history.append({"action": action, "purpose": purpose})
        if self.page.get("reading_notice"):
            if action == "return_from_notice":
                self.page = self.session.call("return_from_notice")
                self._remember()
                return None
            if action not in {"observe", "screenshot", "act", "needs_input"}:
                raise ValueError("Return to the application before interacting with the form.")
        if action == "needs_input":
            raise NativeInputRequired(str(decision.get("reason") or "A required answer needs confirmation."))
        if action == "review":
            return self._review(decision.get("review"))
        if action == "act":
            self._actions(decision.get("operations"))
        elif action in {"observe", "screenshot", "return_to_listing"}:
            self._observe(screenshot=action == "screenshot", listing=action == "return_to_listing")
        elif action == "authenticate":
            self.page, record = authenticate(self.session, self.w, self.job, self.profile, self.page, decision,
                                             registration_route=self.registration_route)
            self.history.append({"authentication": record})
            self._remember()
        elif action == "upload":
            self.page, record = upload(self.session, self.w, self.job, self.profile, self.page, decision)
            self.uploads[record["document_id"]] = record
            self.w.store.job(self.job["id"], attachments=list(self.uploads.values()))
            self._remember()
        else:
            raise ValueError("Choose a visible native action, observation, upload, authentication or final review.")
        return None

    def _actions(self, operations):
        """Batch independent visible fields, stopping at a changed structure or navigation."""
        if not isinstance(operations, list) or not 1 <= len(operations) <= 6:
            raise ValueError("Choose one to six native actions.")
        keys = ("action", "element_id", "text", "replace", "key", "delta_y", "delta_x", "point")
        for index, operation in enumerate(operations, 1):
            self.app.checkpoint()
            operation = validate_operation(self.profile, self.page, operation, self.job)
            structure = [(row.get("id"), row.get("role"), row.get("label")) for row in self.page.get("elements", [])]
            parameters = {key: operation[key] for key in keys if key in operation}
            route = self._account_navigation(operation, parameters)
            if "point" in parameters:
                parameters["point"] = native_point(self.page, parameters["point"])
            before = self.page
            self.page = self.session.call("native_action", {**parameters, "snapshot_id": before["snapshot_id"]})
            if operation["action"] == "click":
                self._note_choice(operation, before)
            if route:
                self.registration_route = route
            self._remember()
            if operation["action"] == "scroll" and before.get("snapshot_id") == self.page.get("snapshot_id"):
                self.history.append({"scroll": "The viewport stayed in place. An open selector can swallow scrolling. "
                                     "Use Escape to close its menu, then Page Down or End with the page focused."})
            current = [(row.get("id"), row.get("role"), row.get("label")) for row in self.page.get("elements", [])]
            if current != structure or operation["action"] in {"click", "scroll"}:
                if index < len(operations):
                    self.history.append({"completed_operations": index, "requested_operations": len(operations),
                                         "last_action": operation["action"], "last_element": operation.get("element_id"),
                                         "remaining_operations": "Not executed. Choose the next action from the current view."})
                break

    def _account_navigation(self, operation, parameters):
        """Keep mode switches on observed account forms distinct from credential submission."""
        if operation.get("action") != "click":
            return None
        if is_registration_link(self.page, operation):
            parameters["final_permit"] = True
            link = next(row for row in self.page["elements"] if row["id"] == operation["element_id"])
            return self.page["url"], link["url"]
        if self.job.get("employer_account_id"):
            account = self.w.store.get("employer_account", employer_key(self.job))
            if is_signin_switch(self.page, operation, account, self.job):
                parameters["final_permit"] = True
        return None

    def _note_choice(self, operation, before):
        """Tell the agent when a click was sent but Chrome exposed no checked state."""
        identity = operation.get("element_id")
        original = next((item for item in before.get("elements", []) if item.get("id") == identity), {})
        if original.get("role") not in {"AXCheckBox", "AXRadioButton"}:
            return
        current = next((item for item in self.page.get("elements", []) if item.get("id") == identity), None)
        if current is None or current.get("value") not in (None, "") or current.get("selected"):
            return
        self.history.append({"choice_clicked": identity, "checked_state": "not exposed",
                             "next": "Continue to account creation or a non-submitting review. "
                                     "Use the employer's validation or summary before clicking this choice again."})

    def _observe(self, screenshot=False, listing=False):
        """Read the current page, or return directly to this job from the candidate portal."""
        self.page = return_to_listing(self.session, self.job, self.page) if listing else observe_application(self.session, self.job, screenshot)
        if self.page.get("recovered_draft"):
            self.resumed_page = self.page
            self.evidence, self.uploads, self.review_feedback = [], {}, {}
            self.history.append({"recovery": "Reopened the verified draft. Recheck the current form before submission."})
        self._remember()

    def _remember(self):
        """Retain one evidence record per changed visible viewport, bounded to this application."""
        if self.job.get("attachments") and self.job.get("resume_id") not in self.uploads:
            saved = retained_resume(self.w, self.job, self.page, self.resumed_page)
            if saved:
                self.uploads[saved["document_id"]] = saved
        view = observation(self.page)
        if not self.evidence or self.evidence[-1]["snapshot_id"] != view["snapshot_id"]:
            self.evidence.append(view)
            self.evidence = self.evidence[-48:]
        if not self.page.get("reading_notice") and self.page.get("url"):
            self.w.store.job(self.job["id"], live_url=self.page["url"])

    def _review(self, review, *, repair=True):
        """Reobserve and bind the single agent's self-check before the durable submission step."""
        self.app.checkpoint()
        current = self.session.call("native_observe")
        owner_known = all(current.get(key) is not None for key in ("pid", "window_id", "active_tab_id", "generation"))
        if current.get("snapshot_id") != self.page.get("snapshot_id") and (
                not owner_known or review_surface(current) != review_surface(self.page)):
            self.page = current
            self._remember()
            raise ValueError("The visible page changed during review. Check it again.")
        ledger = list(self.uploads.values())
        try:
            audit = validate_review(self.w, self.job, self.profile, current, ledger, self.evidence, review)
        except NativeReviewError as error:
            if isinstance(review, dict):
                self.review_feedback = review_feedback(review, self.profile, current, self.evidence, error)
                if repair and str(error) == "No confirmed supporting evidence.":
                    corrected = cite_missing_answers(self.client, self.profile, review, current, self.evidence)
                    if corrected:
                        return self._review(corrected, repair=False)
            raise
        return self.w.store.job(self.job["id"], status="ready", exceptions=[], live_url=current["url"],
            attachments=ledger, last_snapshot=current["snapshot_id"], final_audit=audit,
            native_review=review, native_evidence=self.evidence, cover_state="not_required")
