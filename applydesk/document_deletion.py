"""Remove library uploads while preserving files already attached to applications."""

from .agent_contract import object_fields, string
from .document_cleanup import DeletionRecoveryError


class DocumentDeletion:
    def __init__(self, workspace):
        self.w = workspace

    def delete(self, data):
        """A user-only action cannot race document preparation or an application submission."""
        object_fields(data, ["document_id"])
        identity = string(data["document_id"], 100)
        with self.w.lock:
            if self.w.orchestrator.active or any(task["status"] == "running" for task in self.w.tasks.values()):
                raise ValueError("Pause the agent and wait for current work to stop before deleting a file.")
            record = self.w.docs.get(identity)
            if record["kind"] not in {"resume", "cover_template"}:
                raise ValueError("Only uploaded resumes and cover letter templates can be deleted here.")
            jobs = self.w.store.all("job")
            retained = self._used(identity, jobs)
            detached = self._detach(record)
            try:
                cleanup_pending = self.w.docs.remove_upload(identity, retain=retained)
            except DeletionRecoveryError:
                raise
            except BaseException:
                if detached:
                    self.w.store.put(detached[0], "main", detached[1])
                raise
            self._invalidate(identity, jobs)
            if not retained:
                self.w.store.delete("document_review", identity)
                self.w.store.delete_matching("review", "document_id", identity)
                for name, task in list(self.w.tasks.items()):
                    result = task.get("result")
                    if isinstance(result, dict) and identity in {result.get("id"), result.get("document_id")}:
                        self.w.tasks.pop(name)
            return {"document_id": identity, "removed": True, "application_copies_kept": retained,
                    "cleanup_pending": cleanup_pending}

    def _detach(self, record):
        """Persist reference removal before deleting bytes; failed writes leave the upload usable."""
        if record["kind"] == "resume":
            kind = "resume_library"
            previous = self.w.store.get(kind, "main", [])
            updated = [row for row in previous if row["document_id"] != record["id"]]
        else:
            kind, previous = "settings", self.w.settings()
            updated = {**previous, "cover_template_id": ""} if previous.get("cover_template_id") == record["id"] else previous
        if updated == previous:
            return None
        self.w.store.put(kind, "main", updated)
        return kind, previous

    def _used(self, identity, jobs):
        """Historical application files and generated-letter sources remain independently usable."""
        for job in jobs:
            if identity in {job.get("resume_id"), job.get("cover_id")}:
                return True
            if any(item.get("document_id") == identity for item in job.get("attachments", [])):
                return True
        for document in self.w.docs.manifest.values():
            if identity in {document.get("template_id"), document.get("parent_id")}:
                return True
        for task in self.w.store.all("chat_task"):
            if identity == task.get("target_id") or identity in task.get("document_ids", []):
                return True
        return False

    def _invalidate(self, identity, jobs):
        """Clear stale readiness without changing submitted or uncertain application history."""
        protected = {"submitted", "submission_unknown", "submitting"}
        for job in jobs:
            references = {job.get("resume_id"), job.get("cover_id")}
            references.update(item.get("document_id") for item in job.get("attachments", []))
            if identity not in references or job["status"] in protected or job.get("removed_at"):
                continue
            session = self.w.sessions.pop(job["id"], None)
            if session:
                session.cancel()
                session.close()
            self.w.store.job(job["id"], status="documents_changed", final_audit=None, last_snapshot=None,
                             exceptions=["A file was removed from Documents. Prepare again to select and check the current files."])

