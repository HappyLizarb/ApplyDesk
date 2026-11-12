"""Persistent draft tasks owned by ApplyDesk and executed by the user's chat."""

import uuid

from .agent_context import guide, inputs_for, task_context
from .agent_contract import application_draft, document_draft, object_fields, profile_draft, string, strings
from .profile import public_job_url
from .storage import now


class AgentTasks:
    """Accept bounded proposals and experimental reviews, never readiness or submission."""

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
        if data["kind"] not in {"profile", "application", "document"}:
            raise ValueError("Choose profile, application, or document.")
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
        self._execution(data, execution, "application", context)
        validators = {"application": application_draft, "document": document_draft}
        result = (profile_draft(data["result"]) if task["kind"] == "profile"
                  else validators[task["kind"]](data["result"], inputs))
        documents = self._artifacts(task, inputs, result)
        task.update(result=result, document_ids=documents, agent_id=actor, reviews={}, issues=[], status="awaiting_review",
                    draft_revision=task["draft_revision"] + 1, input_version=context["input_version"], updated_at=now())
        self.w.store.put("chat_task", task["id"], task)
        if task["kind"] == "application":
            self.w.store.job(task["target_id"], status="draft", analysis=result["analysis"], resume_id=documents[0],
                             cover_id=documents[1] if len(documents) > 1 else None,
                             cover_state=result["cover_requirement"]["state"], draft_answers=result["answers"],
                             progress="Draft saved. Separate chat reviews are pending; nothing has been filled or submitted.")
        return self.get(task["id"])

    def _artifacts(self, task, inputs, result):
        """Render cover drafts locally while selecting uploaded resumes without changing bytes."""
        if task["kind"] == "profile":
            return []
        if task["kind"] == "document":
            edits = {item["id"]: item["text"] for item in result["edits"]}
            return [self.w.docs.edit(task["target_id"], edits)["id"]]
        resume = self.w.resumes.select(result["analysis"]["category"])
        documents = [resume["id"]]
        if result["cover_requirement"]["state"] == "required":
            edits = {item["id"]: item["text"] for item in result["cover_edits"]}
            letter = self.w.docs.create(inputs["template"]["id"], edits,
                                        inputs["job"]["company"] + " cover letter draft", job_id=task["target_id"])
            documents.append(letter["id"])
        return documents

    def review(self, data, execution=None):
        """Record distinct reported reviewers; passing reviews still produce only a draft."""
        object_fields(data, ["task_id", "version", "agent_id", "role", "passed", "issues", "checked_items"])
        context = self.get(data["task_id"])
        task, actor = context["task"], string(data["agent_id"], 200)
        verified = self._execution(data, execution, data["role"], context)
        roles = ["application_auditor"] if task["kind"] == "profile" else ["document_reviewer", "application_auditor"]
        if not task.get("result") or task["status"] == "stale" or data["version"] != context["version"]:
            raise ValueError("Review the current draft and input version first.")
        if data["role"] not in roles or not isinstance(data["passed"], bool):
            raise ValueError("Choose a required reviewer role and a boolean verdict.")
        others = [review["agent_id"] for role, review in task["reviews"].items() if role != data["role"]]
        if actor == task["agent_id"] or actor in others:
            raise ValueError("The drafter and each reviewer must report different agent IDs.")
        issues, checked = strings(data["issues"]), strings(data["checked_items"], 5000)
        expected = context["review"]["expected_checks"][data["role"]]
        if len(checked) != len(set(checked)) or set(checked) != set(expected):
            raise ValueError("The review must cover every expected item exactly once.")
        if data["passed"] and issues:
            raise ValueError("A passing review cannot contain unresolved issues.")
        task["reviews"][data["role"]] = {**data, "updated_at": now(), "identity_verified": verified}
        complete = all(task["reviews"].get(role, {}).get("passed") for role in roles)
        task.update(status="reviewed_draft" if complete else "awaiting_review" if data["passed"] else "needs_input", updated_at=now())
        self.w.store.put("chat_task", task["id"], task)
        if task["kind"] == "application":
            self.w.store.job(task["target_id"], status="draft", progress="Chat review recorded. Draft only; user review is still required.")
        return {"task_id": task["id"], "status": task["status"], "draft_only": True}

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
        task.update(status="needs_input", issues=issues, updated_at=now())
        self.w.store.put("chat_task", task["id"], task)
        return {"task_id": task["id"], "status": task["status"], "issues": issues}

    def capture(self, data):
        """Save a read-only page capture as unverified source material, never eligibility."""
        object_fields(data, ["url", "company", "title", "details"])
        url = public_job_url(string(data["url"], 2000))
        company, title = string(data["company"], 500), string(data["title"], 500)
        details = string(data["details"], 200000)
        existing = next((job for job in self.w.store.all("job") if job["url"] == url), None)
        if existing and (existing.get("removed_at") or existing["status"] == "submitted"):
            raise ValueError("Removed and submitted applications cannot be replaced by an agent capture.")
        job = existing or self.w.store.add_job(url)
        return self.w.store.job(job["id"], company=company, title=title, details=details, details_origin="captured",
                                capture_verification="agent_reported", status="draft", progress="Chat capture; user review required.")

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
