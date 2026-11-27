"""Chat tools for web research, workspace files, and application workflows."""

import json
from urllib.parse import urldefrag

from .agent_contract import object_fields, string
from .agent_context import source_record
from .auto_start import usage_limited
from .chat_evidence import record_chat_evidence
from .chatgpt_transport import AccountError
from .profile import model_profile
from .discovery import google_xray_query
from .web_research import WebResearch
from .workday_reset import request_reset_email

CAPTURE_FIELDS = {"capture_id": {"type": "string"}, "company": {"type": "string"}, "title": {"type": "string"},
                  "first_line": {"type": "integer"}, "last_line": {"type": "integer"}}

DEFINITIONS = {
    "workspace_overview": ("List tasks, jobs, documents and application runs.", {}),
    "web_search": ("Search Google for public employer and recruiting-system listings. Use role/location keywords and an employer site: filter when useful.", {
        "query": {"type": "string"}, "engine": {"type": "string", "enum": ["google"]}}),
    "read_page": ("Read any public webpage, including company careers, job boards, search pages and linked listings. Returns numbered text lines and links.", {
        "url": {"type": "string"}}),
    "refresh_page": ("Read a public page again when you expect its contents to have changed since the current turn's capture.", {
        "url": {"type": "string"}}),
    "save_job": ("Add an individual matching job to the tracker from a read_page capture. Copy the observed company/title and choose the first and last lines covering the FULL description, responsibilities, requirements and compensation. Pages are saved in the order you choose them.", CAPTURE_FIELDS),
    "link_saved_listing": ("Attach the same employer’s Workday listing to a pending imported job if its old link is missing or unreadable. Read the listing first, copy the company and title, and select the full description.", {**CAPTURE_FIELDS, "job_id": {"type": "string"}}),
    "read_profile": ("Read confirmed profile evidence; sensitive voluntary disclosures are excluded.", {}),
    "record_chat_evidence": ("Attach evidence references to specific facts the user confirmed in this conversation, or process corrections. Reuse existing confirmations for application answers; unlisted skills or experience types do not need invented facts.", {
        "facts": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string"}, "kind": {"type": "string", "enum": ["skill", "education", "projects", "professional_experience", "language", "preference", "application_answer", "application_agreement", "veteran_service"]}},
            "required": ["text", "kind"], "additionalProperties": False}}}),
    "read_document": ("Read the verified text, blocks and page metadata of an uploaded file by its workspace ID. Use the existing upload instead of asking the user to paste it. Image-only sources can be read by queueing a profile draft.", {
        "document_id": {"type": "string"}}),
    "task_context": ("Read an existing task's versioned input and draft result.", {"task_id": {"type": "string"}}),
    "queue_task": ("Queue an unfinished profile or application draft.", {
        "kind": {"type": "string", "enum": ["profile", "application"]},
        "target_id": {"type": "string"}, "instruction": {"type": "string"}}),
    "prepare_task": ("Prepare a queued draft using your current model and self-check instructions.", {
        "task_id": {"type": "string"}, "instruction": {"type": "string"}}),
    "retry_task": ("Retry a blocked draft after the user asks to continue and its missing input has been resolved.", {
        "task_id": {"type": "string"}}),
    "find_jobs": ("Search Google X-ray for public employer and recruiting-system listings without job-platform accounts. Use short queries across adjacent specialties and site:employer.com when useful. Open promising results, read their complete descriptions, then save matching jobs with save_job.", {
        "query": {"type": "string"}}),
    "run_applications": ("Open live employer forms, fill and submit saved jobs using the conversation's permission and resume. Supply job_ids in priority order, or [] for all saved candidates. Honor the user's total submission count. Draft tools are not prerequisites. Report an interruption using the returned submission counts and interrupted_job.submission_status.", {"job_ids": {"type": "array", "items": {"type": "string"}}}),
    "request_reset_email": ("Request a single password-reset email for a saved Workday account when explicitly requested. Returns the employer response. No password is changed.", {"job_id": {"type": "string"}}),
}


def run_report(run, message_id=None):
    """Share cumulative counts and this request's outcomes without replaying earlier applications."""
    if "results" not in run:
        return run
    fields = ("id", "limit", "status", "submitted_count", "attempted_count", "uncertain_count", "remaining_count", "interrupted_job")
    report = {key: run[key] for key in fields if key in run}
    start = run.get("message_result_starts", {}).get(message_id)
    if start is None and message_id and run.get("message_id") == message_id:
        start = 0
    if start is not None:
        report["results"] = run["results"][start:]
    return report


def master_tools():
    """Namespace function tools as required by the ChatGPT-plan Responses integration."""
    tools = [{"type": "function", "name": name, "description": description, "strict": True,
              "parameters": {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}}
             for name, (description, properties) in DEFINITIONS.items()]
    return [{"type": "namespace", "name": "applydesk", "description": "Job research and application workspace", "tools": tools}]


class MasterTools:
    """Handle tool results as untrusted data and enforce the same user-facing boundaries."""

    def __init__(self, engine, workflow):
        self.engine, self.w, self.workflow = engine, engine.w, workflow
        self.web = WebResearch(engine)

    def call(self, item):
        """Unknown tools and malformed arguments fail closed, including fake approval calls."""
        self.engine.check_active()
        name = item.get("name", "")
        if item.get("namespace", "applydesk") != "applydesk" or name not in DEFINITIONS:
            raise ValueError("This tool is unavailable. Use only the supplied workspace operations.")
        data = json.loads(item.get("arguments", "{}"))
        object_fields(data, {} if name == "run_applications" else DEFINITIONS[name][1], ["job_ids"] if name == "run_applications" else [])
        if name == "request_reset_email":
            return request_reset_email(self.engine, string(data["job_id"], 100))
        if name in {"find_jobs", "web_search", "read_page", "refresh_page", "save_job", "link_saved_listing"}:
            return self._browser_tool(name, data)
        if name in {"prepare_task", "retry_task"}:
            return self.prepare(name, data)
        if name == "record_chat_evidence":
            return record_chat_evidence(self.engine, data)
        if name == "run_applications":
            return self._run_applications(data.get("job_ids"))
        with self.w.lock:
            self.engine.check_active()
            if name == "workspace_overview":
                return self.overview()
            if name == "read_profile":
                return model_profile(self.w.profile())
            if name == "read_document":
                return self.read_document(string(data["document_id"], 100))
            if name == "task_context":
                return self.w.chat.get(data["task_id"])
            if name == "queue_task":
                return self.w.chat.enqueue(data)
        raise ValueError("Unknown draft tool.")

    def _run_applications(self, job_ids):
        """Include saved submission evidence when an interrupted model call returns control to chat."""
        message_id = ((self.engine.active or {}).get("message") or {}).get("id")
        try:
            run = self.engine.applications.start(job_ids)
        except AccountError as error:
            run = self.engine.applications.run or {}
            if usage_limited(error) or run.get("status") != "interrupted" or not message_id:
                raise
            if message_id != run.get("message_id") and message_id not in run.get("message_ids", []):
                raise
            return {**run_report(run, message_id), "error": str(error)[:1000]}
        return run_report(run, message_id)

    def _browser_tool(self, name, data):
        """Route bounded browser research and user-authorized verification recovery."""
        if name in {"find_jobs", "web_search"}:
            if data.get("engine", "google") != "google":
                raise ValueError("Use Google X-ray for public employer job discovery.")
            return self.web.search(google_xray_query(data["query"]), "google")
        if name in {"read_page", "refresh_page"}:
            return self.web.read_page(data["url"])
        return self.web.save_job(data)

    def read_document(self, document_id):
        """Resolve files through the workspace manifest and preserve library deletions."""
        record = self.w.docs.get(document_id)
        if record.get("removed_from_library_at"):
            raise ValueError("This upload was deleted from Documents; use its retained application copy in that application's task context.")
        return source_record(self.w, document_id)

    def prepare(self, name, data):
        """Explicit chat retries reuse the existing draft reset without restarting application runs."""
        task_id = string(data["task_id"], 100)
        if name == "retry_task":
            active = self.engine.active or {}
            if active.get("message", {}).get("role") != "user" or active.get("auto_restart"):
                raise ValueError("Explicitly ask to retry this blocked draft in a new user chat message after resolving its missing input.")
            return self.engine.retry({"task_id": task_id})
        return self.workflow.prepare(task_id, string(data["instruction"], 5000, empty=True))

    def overview(self):
        """Inventory supplies exact IDs without account credentials or unnecessary document text."""
        inventory = []
        categories = {resume["id"]: resume["category"] for resume in self.w.resumes.list()}
        for doc in self.w.docs.manifest.copy().values():
            if doc.get("removed_from_library_at") or (doc["kind"] == "resume" and doc["id"] not in categories):
                continue
            item = {key: doc.get(key) for key in ("id", "kind", "document_type", "title", "filename")}
            if doc["kind"] == "resume":
                item["category"] = categories[doc["id"]]
            inventory.append(item)
        return {"tasks": self.w.chat.list(), "application_runs": [run_report(run) for run in self.engine.history.application_runs()],
                "jobs": [{**{key: job.get(key) for key in ("id", "company", "title")}, "status": job.get("manual_status") or job["status"],
                          "manually_set": bool(job.get("manual_status"))} for job in self.w.store.all("job") if not job.get("removed_at")],
                "documents": inventory}


class ToolProgress:
    """Track progress within one chat turn, without limiting useful research steps."""

    def __init__(self, call):
        self.call = call
        self.pages, self.seen = {}, set()
        self.repeats = 0

    def execute(self, item):
        """Repeated reads reuse the exact capture; changed results count as progress."""
        key = self.key(item)
        cached = item.get("name") in {"read_page", "web_search", "find_jobs"}
        if cached and key in self.pages:
            result = self.pages[key]
        else:
            try:
                result = self.call(item)
            except InterruptedError:
                raise
            except Exception as exc:
                if usage_limited(exc):
                    raise
                result = {"error": str(exc)[:1000]}
            if cached:
                self.pages[key] = result
            elif item.get("name") == "refresh_page":
                self.pages[self.key({**item, "name": "read_page"})] = result
        evidence = (key, json.dumps(result, sort_keys=True, ensure_ascii=False))
        self.repeats = self.repeats + 1 if evidence in self.seen else 0
        self.seen.add(evidence)
        if self.repeats and isinstance(result, dict):
            return {**result, "progress_note": "This operation returned the same information. Use a different query or source, or report the blocker."}
        return result

    def key(self, item):
        """Ignore argument ordering and URL fragments when identifying identical reads."""
        try:
            arguments = json.loads(item.get("arguments", "{}"))
            if item.get("name") == "read_page" and isinstance(arguments.get("url"), str):
                arguments["url"] = urldefrag(arguments["url"])[0]
            if item.get("name") in {"web_search", "find_jobs"} and isinstance(arguments.get("query"), str):
                arguments["query"] = " ".join(arguments["query"].split())
        except (ValueError, AttributeError, TypeError):
            arguments = item.get("arguments")
        return json.dumps([item.get("namespace", "applydesk"), item.get("name"), arguments], sort_keys=True)
