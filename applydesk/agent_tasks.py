"""Persistent draft tasks owned by ApplyDesk and executed by the user's chat."""

import uuid

from .agent_context import inputs_for, task_context
from .agent_contract import application_draft, object_fields, profile_draft, string, strings
from .storage import now


class AgentTasks:
    """Accept bounded drafts without granting readiness or submission authority."""

    def __init__(self, workspace):
        # Storage survives chat interruptions; no background model process is started.
        self.w = workspace

    def get(self, identity):
        """Resolve a task by its server-issued identity and recompute its current version."""
        task = self.w.store.get("chat_task", string(identity, 100))
        if not task:
            raise ValueError("Chat task not found.")
        return task_context(self.w, task)

    def list(self):
        """Summaries expose stale or unavailable inputs without failing the entire dashboard."""
        rows = []
        for task in self.w.store.all("chat_task"):
            status, issue = task["status"], ""
            try:
                status = task_context(self.w, task)["task"]["status"]
            except ValueError as exc:
                status, issue = "needs_input", str(exc)
            rows.append({key: task.get(key) for key in ("id", "kind", "target_id", "title", "updated_at")}
                        | {"status": status, "issue": issue})
        return rows

    def enqueue(self, data):
        """Queueing is durable and idempotent; it never starts a provider request."""
        object_fields(data, ["kind", "target_id"], ["instruction"])
        if data["kind"] not in {"profile", "application"}:
            raise ValueError("Choose profile or application. Letter customization is unavailable.")
        instruction = string(data.get("instruction", ""), 5000, empty=True)
        task = {"id": uuid.uuid4().hex, "kind": data["kind"], "target_id": string(data["target_id"], 100),
                "instruction": instruction, "status": "queued", "draft_revision": 0, "reviews": {},
                "document_ids": [], "result": None, "updated_at": now()}
        inputs = inputs_for(self.w, task)
        target = inputs.get("job") or inputs.get("source") or inputs.get("document")
        task["title"] = target.get("title") or target.get("filename") or "Application draft"
        for prior in self.w.store.all("chat_task"):
            if all(prior.get(key) == task[key] for key in ("kind", "target_id", "instruction")):
                return prior
        self.w.store.put("chat_task", task["id"], task)
        if task["kind"] == "application":
            self.w.store.job(task["target_id"], status="draft", progress="Queued for this chat; not running in the background.")
        return task

    def submit(self, data, execution=None):
        """Validate the current snapshot before creating draft artifacts and clearing old reviews."""
        object_fields(data, ["task_id", "version", "agent_id", "result"])
        context = self.get(data["task_id"])
        if data["version"] != context["version"]:
            raise ValueError("Inputs changed. Read the task again before submitting a draft.")
        task, inputs = context["task"], context["inputs"]
        actor = string(data["agent_id"], 200)
        self._execution(data, execution, "master", context)
        if task["kind"] == "document":
            raise ValueError("Letter customization is unavailable; saved letters can be previewed or downloaded.")
        result = (profile_draft(data["result"]) if task["kind"] == "profile"
                  else application_draft(data["result"], inputs))
        documents = self._artifacts(task, result)
        task.pop("blocked_version", None)
        task.update(result=result, document_ids=documents, agent_id=actor, reviews={}, issues=[], status="draft_ready", draft_version=None,
                    draft_revision=task["draft_revision"] + 1, input_version=context["input_version"], updated_at=now())
        self.w.store.put("chat_task", task["id"], task)
        if task["kind"] == "application":
            self.w.store.job(task["target_id"], status="draft", analysis=result["analysis"], resume_id=documents[0],
                             cover_state=result["cover_requirement"]["state"], draft_answers=result["answers"], exceptions=[],
                             progress="Draft saved. Nothing has been filled or submitted.")
        task["draft_version"] = self.get(task["id"])["draft_version"]
        self.w.store.put("chat_task", task["id"], task)
        return self.get(task["id"])

    def _artifacts(self, task, result):
        """Select an unchanged uploaded resume; this workflow creates no letter artifacts."""
        if task["kind"] == "profile":
            return []
        resume = self.w.resumes.select(result["analysis"]["category"])
        return [resume["id"]]

    def block(self, data):
        """Uncertainty persists as a visible blocker instead of a guessed answer."""
        object_fields(data, ["task_id", "version", "issues"])
        context = self.get(data["task_id"])
        if data["version"] != context["version"]:
            raise ValueError("Read the latest task before reporting a blocker.")
        issues = strings(data["issues"])
        if not issues:
            raise ValueError("Describe what is missing or uncertain.")
        task = context["task"]
        task.update(status="needs_input", issues=issues, blocked_version=context["version"], updated_at=now())
        self.w.store.put("chat_task", task["id"], task)
        if task["kind"] == "application":
            self.w.store.job(task["target_id"], status="needs_review", exceptions=issues,
                             progress="Needs input: " + issues[0])
        return {"task_id": task["id"], "status": task["status"], "issues": issues}

    def _execution(self, data, identity, role, context):
        """Only app-owned completed runs can establish local execution provenance."""
        if identity is None:
            if str(data["agent_id"]).startswith("local:"):
                raise ValueError("Local agent identities are reserved for the app controller.")
            return False
        run = self.w.store.get("agent_run", identity)
        valid = run and identity == data["agent_id"] and run["status"] == "completed"
        valid = valid and run["role"] == role and run["task_id"] == context["task"]["id"] and run["version"] == context["version"]
        if not valid:
            raise ValueError("This result does not belong to the current agent execution.")
        return True
