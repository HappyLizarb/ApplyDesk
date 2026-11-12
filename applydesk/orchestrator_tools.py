"""The master receives only explicit app operations, with no arbitrary code or browser writes."""

import json

from .agent_contract import object_fields, string
from .profile import model_profile
from .recommendation_wait import find_jobs, wait_for_jobs

DEFINITIONS = {
    "workspace_overview": ("List task, job, and imported-document IDs and their statuses.", {}),
    "read_profile": ("Read confirmed profile evidence; sensitive voluntary disclosures are excluded.", {}),
    "task_context": ("Read an existing task's versioned input and draft result.", {"task_id": {"type": "string"}}),
    "queue_task": ("Queue an unfinished profile, application, or generated cover edit draft.", {
        "kind": {"type": "string", "enum": ["profile", "application", "document"]},
        "target_id": {"type": "string"}, "instruction": {"type": "string"}}),
    "prepare_task": ("Delegate a queued task to the Application Agent, then separate mandatory reviewers. Never submits.", {
        "task_id": {"type": "string"}, "instruction": {"type": "string"}}),
    "find_jobs": ("Match connected-platform recommendations to the current chat request, then add only relevant jobs. Does not apply.", {
        "platform": {"type": "string", "enum": ["linkedin", "handshake"]}}),
    "job_search_status": ("Continue a search and return its filtered jobs or blocker. Never restart just to poll.", {
        "platform": {"type": "string", "enum": ["linkedin", "handshake"]}, "run_id": {"type": "string"}}),
    "run_applications": ("Find, screen, independently review, fill and submit qualifying applications up to the user's explicit limit. Requires a CURRENT human chat request saying Submit up to N applications in LOCATION. Locations must be copied from that request. This is a one-time run, not a recurring schedule. No automatic retry of uncertain submissions.", {
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        "locations": {"type": "array", "items": {"type": "string"}}}),
}


def master_tools():
    """Namespace function tools as required by the ChatGPT-plan Responses integration."""
    tools = [{"type": "function", "name": name, "description": description, "strict": True,
              "parameters": {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}}
             for name, (description, properties) in DEFINITIONS.items()]
    return [{"type": "namespace", "name": "applydesk", "description": "Application workspace with bounded user-authorized submission runs", "tools": tools}]


class MasterTools:
    """Handle tool results as untrusted data and enforce the same user-facing boundaries."""

    def __init__(self, engine, workflow):
        self.engine, self.w, self.workflow = engine, engine.w, workflow

    def call(self, item):
        """Unknown tools and malformed arguments fail closed, including fake approval calls."""
        self.engine.check_active()
        name = item.get("name", "")
        if item.get("namespace", "applydesk") != "applydesk" or name not in DEFINITIONS:
            raise ValueError("This tool is unavailable. Use only the supplied workspace operations.")
        data = json.loads(item.get("arguments", "{}"))
        object_fields(data, DEFINITIONS[name][1])
        if name == "prepare_task":
            return self.workflow.prepare(string(data["task_id"], 100), string(data["instruction"], 5000, empty=True))
        if name == "find_jobs":
            return find_jobs(self.engine, data["platform"])
        if name == "job_search_status":
            return wait_for_jobs(self.engine, data["platform"], string(data["run_id"], 100))
        if name == "run_applications":
            return self.engine.applications.start(data["limit"], data["locations"])
        with self.w.lock:
            self.engine.check_active()
            if name == "workspace_overview":
                return self.overview()
            if name == "read_profile":
                return model_profile(self.w.profile())
            if name == "task_context":
                return self.w.chat.get(data["task_id"])
            if name == "queue_task":
                return self.w.chat.enqueue(data)
        raise ValueError("Unknown draft tool.")

    def overview(self):
        """Inventory supplies exact IDs without account credentials or unnecessary document text."""
        documents = self.w.docs.manifest.copy().values()
        return {"tasks": self.w.chat.list(), "application_runs": self.w.store.all("application_run")[-10:],
                "jobs": [{key: job.get(key) for key in ("id", "company", "title", "status")} for job in self.w.store.all("job") if not job.get("removed_at")],
                "documents": [{key: doc.get(key) for key in ("id", "kind", "document_type", "title", "filename")} for doc in documents],
                "platforms": self.w.recommendations.preferences(), "searches": self.w.recommendations.state()}
