"""Chat tools for web research, workspace files, and application workflows."""

import json

from .agent_contract import object_fields, string
from .agent_context import source_record
from .chat_evidence import record_chat_evidence
from .profile import model_profile
from .recommendation_wait import find_jobs, wait_for_jobs
from .web_research import WebResearch

DEFINITIONS = {
    "workspace_overview": ("List tasks, jobs, documents, connected sources and application runs.", {}),
    "web_search": ("Search the public web in a browser and read result links. Use role and location keywords.", {
        "query": {"type": "string"}, "engine": {"type": "string", "enum": ["google", "bing", "duckduckgo"]}}),
    "read_page": ("Read any public webpage, including company careers, job boards, search pages and linked listings. Returns numbered text lines and links.", {
        "url": {"type": "string"}}),
    "refresh_page": ("Read a public page again when you expect its contents to have changed since the current turn's capture.", {
        "url": {"type": "string"}}),
    "save_job": ("Add an individual matching job to the tracker from a read_page capture. Copy the observed company/title and choose the first and last lines covering the FULL description, responsibilities, requirements and compensation. Pages are saved in the order you choose them.", {
        "capture_id": {"type": "string"}, "company": {"type": "string"}, "title": {"type": "string"},
        "first_line": {"type": "integer"}, "last_line": {"type": "integer"}}),
    "read_profile": ("Read confirmed profile evidence; sensitive voluntary disclosures are excluded.", {}),
    "record_chat_evidence": ("Attach evidence references to specific facts the user confirmed in this conversation, or process corrections. Reuse existing confirmations for application answers; assumed qualifications do not need new evidence.", {
        "facts": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string"}, "kind": {"type": "string", "enum": ["skill", "education", "projects", "professional_experience", "language"]}},
            "required": ["text", "kind"], "additionalProperties": False}}}),
    "read_document": ("Read the verified text, blocks and page metadata of an uploaded file by its workspace ID. Use the existing upload instead of asking the user to paste it. Image-only sources can be read by queueing a profile draft.", {
        "document_id": {"type": "string"}}),
    "task_context": ("Read an existing task's versioned input and draft result.", {"task_id": {"type": "string"}}),
    "queue_task": ("Queue an unfinished profile, application, or generated cover edit draft.", {
        "kind": {"type": "string", "enum": ["profile", "application", "document"]},
        "target_id": {"type": "string"}, "instruction": {"type": "string"}}),
    "prepare_task": ("Prepare a queued draft with the Application Agent and separate reviewers.", {
        "task_id": {"type": "string"}, "instruction": {"type": "string"}}),
    "retry_task": ("Retry a blocked draft after the user asks to continue and its missing input has been resolved.", {
        "task_id": {"type": "string"}}),
    "find_jobs": ("Search a connected LinkedIn or Handshake session and save relevant jobs. Use a short query for one related specialty at a time; broad role families need varied searches. Use an empty query only for recommendations.", {
        "platform": {"type": "string", "enum": ["linkedin", "handshake"]}, "query": {"type": "string"}}),
    "job_search_status": ("Read progress and results of an existing connected-source search.", {
        "platform": {"type": "string", "enum": ["linkedin", "handshake"]}, "run_id": {"type": "string"}}),
    "run_applications": ("Apply to saved matching jobs using the permission, remaining total allowance, locations, role preferences and resume agreed in conversation.", {}),
}


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
        object_fields(data, DEFINITIONS[name][1])
        if name in {"web_search", "read_page", "refresh_page", "save_job"}:
            return self._research(name, data)
        if name in {"prepare_task", "retry_task"}:
            return self.prepare(name, data)
        if name == "record_chat_evidence":
            return record_chat_evidence(self.engine, data)
        if name == "find_jobs":
            return find_jobs(self.engine, data["platform"], string(data["query"], 300, empty=True))
        if name == "job_search_status":
            return wait_for_jobs(self.engine, data["platform"], string(data["run_id"], 100))
        if name == "run_applications":
            return self.engine.applications.start()
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

    def _research(self, name, data):
        """Route browser research independently of the selected model provider."""
        if name == "web_search":
            return self.web.search(data["query"], data["engine"])
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
            if (self.engine.active or {}).get("message", {}).get("role") != "user":
                raise ValueError("Retry this blocked draft from a user chat message.")
            return self.engine.retry({"task_id": task_id})
        return self.workflow.prepare(task_id, string(data["instruction"], 5000, empty=True))

    def overview(self):
        """Inventory supplies exact IDs without account credentials or unnecessary document text."""
        documents = self.w.docs.manifest.copy().values()
        return {"tasks": self.w.chat.list(), "application_runs": self.w.store.all("application_run")[-10:],
                "jobs": [{key: job.get(key) for key in ("id", "company", "title", "status")} for job in self.w.store.all("job") if not job.get("removed_at")],
                "documents": [{key: doc.get(key) for key in ("id", "kind", "document_type", "title", "filename")} for doc in documents],
                "platforms": self.w.recommendations.preferences(), "searches": self.w.recommendations.state()}
