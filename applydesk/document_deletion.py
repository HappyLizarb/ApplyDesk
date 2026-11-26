"""Delete selected local files while preserving application outcomes and saved profile facts."""

import time

from .agent_contract import object_fields, string
from .document_cleanup import DeletionRecoveryError
from .storage import now


class DocumentDeletion:
    def __init__(self, workspace):
        self.w = workspace

    def delete(self, data):
        """Stop active agent work before removing the selected original and its previews."""
        object_fields(data, ["document_id"])
        identity = string(data["document_id"], 100)
        with self.w.lock:
            self.w.docs.get(identity)
            if self.w.orchestrator.active:
                self.w.orchestrator.control({"paused": True})
        self._wait_for_stop()
        with self.w.lock:
            if self.w.orchestrator.active:
                raise ValueError("The agent restarted. Stop it before deleting this file.")
            record = self.w.docs.get(identity)
            jobs = self.w.store.all("job")
            self._close_sessions(identity, jobs)
            detached = self._detach(record)
            try:
                cleanup_pending = self.w.docs.remove_upload(identity)
            except DeletionRecoveryError:
                raise
            except BaseException:
                if detached:
                    self.w.store.put(detached[0], "main", detached[1])
                raise
            profile_changed = bool(detached and detached[0] == "profile")
            self._invalidate(record, jobs, profile_changed)
            self._clear_tasks(identity, profile_changed)
            return {"document_id": identity, "removed": True, "application_copies_kept": False,
                    "cleanup_pending": cleanup_pending}

    def _wait_for_stop(self):
        """Let cancelled workers release their files without holding the workspace write lock."""
        deadline = time.monotonic() + 15
        while self.w.orchestrator.active and time.monotonic() < deadline:
            time.sleep(.05)
        if self.w.orchestrator.active:
            raise ValueError("Work is still stopping. The file is unchanged; retry deletion when it stops.")

    def _detach(self, record):
        """Persist selected-file removal first; a failed deletion restores the prior selection."""
        identity, kind = record["id"], record["kind"]
        if kind == "resume":
            kind, previous = "resume_library", self.w.store.get("resume_library", "main", [])
            updated = [row for row in previous if row["document_id"] != identity]
        elif kind == "transcript":
            kind, previous = "profile", self.w.profile()
            updated = {**previous, "transcript_id": None, "revision": previous["revision"] + 1} if previous.get("transcript_id") == identity else previous
        elif kind.endswith("_template"):
            kind, previous = "settings", self.w.settings()
            updated = {**previous, "cover_template_id": ""} if previous.get("cover_template_id") == identity else previous
        else:
            return None
        if updated == previous:
            return None
        self.w.store.put(kind, "main", updated)
        return kind, previous

    def _references(self, job, identity):
        """Current selections and historical upload ledgers identify affected applications."""
        references = {job.get("resume_id"), job.get("cover_id")}
        references.update(item.get("document_id") for item in job.get("attachments", []))
        return identity in references

    def _close_sessions(self, identity, jobs):
        """Release browsers using this document before its bytes are removed."""
        for job in jobs:
            if not self._references(job, identity):
                continue
            session = self.w.sessions.pop(job["id"], None)
            if session:
                if hasattr(session, "cancel"):
                    session.cancel()
                session.close()

    def _invalidate(self, record, jobs, profile_changed):
        """Keep submission history while affected unfinished applications display Needs input."""
        identity = record["id"]
        reason = "A file was deleted: " + (record.get("filename") or record.get("title") or "document")
        for job in jobs:
            pending = job["status"] not in {"new", "submitted", "submission_unknown", "submitting"}
            if not self._references(job, identity) and not (profile_changed and pending):
                continue
            changes = {key: None for key in ("resume_id", "cover_id") if job.get(key) == identity}
            if job["status"] not in {"submitted", "submission_unknown", "submitting"} and not job.get("removed_at"):
                changes.update(status="needs_review", final_audit=None, last_snapshot=None, exceptions=[reason])
            self.w.store.job(job["id"], **changes)
            self.w.store.event(job["id"], reason)

    def _clear_tasks(self, identity, profile_changed):
        """Discard source-dependent drafts and cached extraction, keeping merged profile facts."""
        self.w.store.delete("document_review", identity)
        self.w.store.delete_matching("review", "document_id", identity)
        for task in self.w.store.all("chat_task"):
            if task.get("target_id") == identity and task["kind"] in {"profile", "document"}:
                self.w.store.delete("chat_task", task["id"])
            elif identity in task.get("document_ids", []) or (profile_changed and task["kind"] == "application"):
                task.update(status="needs_input", result=None, reviews={}, input_version=None, draft_version=None,
                            document_ids=[], issues=["A source document was deleted. Upload a replacement to continue."], updated_at=now())
                self.w.store.put("chat_task", task["id"], task)
        for name, task in list(self.w.tasks.items()):
            result = task.get("result")
            if task["status"] != "running" and isinstance(result, dict) and identity in {result.get("id"), result.get("document_id")}:
                self.w.tasks.pop(name)
