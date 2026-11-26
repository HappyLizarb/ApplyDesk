"""Small transactional store; secrets belong in Keychain, never in these records."""

from contextlib import contextmanager
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .job_details import parse_job_details


def now():
    """Return a sortable UTC timestamp for local history."""
    return datetime.now(timezone.utc).isoformat()


class Store:
    """Persist profile, settings, and job records without an ORM."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.record_leases = {}
        self.db = sqlite3.connect(self.root / "workspace.sqlite", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, data TEXT, PRIMARY KEY(kind,id))")
        # Drop the retired opt-in wherever an older build wrote it. Other preferences stay intact.
        self.db.execute("""UPDATE records SET data=json_remove(data,'$.accept_required_terms')
            WHERE kind='settings' AND id='main' AND json_type(data,'$.accept_required_terms') IS NOT NULL""")
        self.db.commit()
        for job in self.all("job"):
            self.put("job", job["id"], job)

    def get(self, kind, record_id, default=None):
        """Load a detached JSON value so callers cannot mutate stored records accidentally."""
        with self.lock:
            row = self.db.execute("SELECT data FROM records WHERE kind=? AND id=?", (kind, record_id)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, kind, record_id, value):
        """Commit a complete record atomically."""
        if kind == "job":
            # Explicit raw captures may include employer evidence beyond the platform posting.
            source = value.get("recommendation_source")
            source_text = source.get("description") if isinstance(source, dict) else None
            captured = value.get("details_origin") == "captured"
            summary = (not source_text and not captured and value.get("details")
                       and value.get("details") == (value.get("analysis") or {}).get("details"))
            description = value.get("details", "") if captured else source_text or value.get("details", "")
            origin = "summary" if summary else "captured" if captured else "source" if source_text else "unknown"
            value = {**value, "details_origin": origin,
                     **parse_job_details("" if summary else description)}
        with self.lock, self.db:
            self._check_lease(kind, record_id)
            self.db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)", (kind, record_id, json.dumps(value)))
        return value

    @contextmanager
    def lease(self, *records):
        """Freeze selected records during browser work without blocking independent readers."""
        owner = threading.get_ident()
        with self.lock:
            for kind, identity in records:
                self._check_lease(kind, identity)
            for record in records:
                self.record_leases[record] = owner
        try:
            yield
        finally:
            with self.lock:
                for record in records:
                    self.record_leases.pop(record, None)

    def _check_lease(self, kind, identity):
        """Concurrent account edits must wait for the active authentication operation to finish."""
        owner = self.record_leases.get((kind, identity))
        if owner is not None and owner != threading.get_ident():
            raise ValueError("Account authentication is in progress. Try the edit again when it finishes.")

    def all(self, kind):
        """Return records in creation order for the spreadsheet."""
        with self.lock:
            rows = self.db.execute("SELECT data FROM records WHERE kind=? ORDER BY rowid", (kind,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def clear(self, kind):
        """Delete one app-owned record collection atomically without touching other workspace data."""
        with self.lock, self.db:
            for row in self.db.execute("SELECT id FROM records WHERE kind=?", (kind,)).fetchall():
                self._check_lease(kind, row[0])
            cursor = self.db.execute("DELETE FROM records WHERE kind=?", (kind,))
            return cursor.rowcount

    def delete(self, kind, record_id):
        """Remove one identified record without discarding unrelated cached results."""
        with self.lock, self.db:
            self._check_lease(kind, record_id)
            cursor = self.db.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, record_id))
            return cursor.rowcount

    def delete_matching(self, kind, field, value):
        """Remove related cache records while preserving unrelated entries of the same kind."""
        with self.lock, self.db:
            rows = self.db.execute("SELECT id, data FROM records WHERE kind=?", (kind,)).fetchall()
            ids = [(kind, identity) for identity, raw in rows if json.loads(raw).get(field) == value]
            for record_kind, identity in ids:
                self._check_lease(record_kind, identity)
            self.db.executemany("DELETE FROM records WHERE kind=? AND id=?", ids)
            return len(ids)

    def job(self, record_id, **changes):
        """Update one job under a lock, preserving concurrent progress messages."""
        with self.lock:
            job = self.get("job", record_id)
            if not job:
                raise ValueError("Application not found.")
            job.update(changes, updated_at=now())
            return self.put("job", record_id, job)

    def set_board_status(self, data):
        """Record a manual tracker edit without changing the employer receipt or submission attempt."""
        labels = {"new": "Not started", "draft": "Draft", "needs_review": "Needs input",
                  "submitted": "Submitted", "skipped": "Skipped"}
        if set(data) != {"id", "status", "previous"} or not all(isinstance(value, str) for value in data.values()) or data["status"] not in labels:
            raise ValueError("Choose an available application status.")
        with self.lock:
            job = self.get("job", data["id"])
            if not job or job.get("removed_at"):
                raise ValueError("This application is no longer on the board.")
            if job["status"] in {"preparing", "submitting"} or any(
                    run.get("status") == "running" and run.get("current_job") == job["id"] for run in self.all("application_run")):
                raise ValueError("This application is in progress. Change its status when the agent finishes.")
            current = job.get("manual_status") or job["status"]
            if current != data["previous"] and current != data["status"]:
                raise ValueError("The application status changed. Refresh the row and try again.")
            if job.get("manual_status") != data["status"]:
                entry = {"at": now(), "message": "Manually marked " + labels[data["status"]] + "."}
                self.job(job["id"], manual_status=data["status"], history=(job.get("history", []) + [entry])[-100:])
            return {"id": job["id"], "manual_status": data["status"]}

    def begin_preparation(self, job_id):
        """Claim the current row only when its latest user decision permits another preparation."""
        with self.lock:
            job = self.get("job", job_id)
            if not job or job.get("removed_at") or job["status"] == "submitted":
                raise ValueError("Choose an application that has not been removed or submitted.")
            if job.get("manual_status") in {"submitted", "skipped"}:
                raise ValueError("This application has been manually marked Submitted or Skipped.")
            self.job(job_id, status="preparing", exceptions=[], manual_status=None)
            return job

    def remove_jobs(self, ids):
        """Retain selected records as import exclusions in one atomic transaction."""
        if not isinstance(ids, list) or not ids or any(not isinstance(value, str) or not value for value in ids):
            raise ValueError("Select at least one application.")
        ids = list(dict.fromkeys(ids))
        with self.lock, self.db:
            jobs = [self.get("job", record_id) for record_id in ids]
            if any(job is None for job in jobs):
                raise ValueError("Application not found. Refresh the list and try again.")
            timestamp = now()
            records = [(json.dumps({**job, "removed_at": job.get("removed_at") or timestamp}), job["id"])
                       for job in jobs]
            self.db.executemany("UPDATE records SET data=? WHERE kind='job' AND id=?", records)
        return ids

    def add_job(self, url):
        """Create a job with explicit unknown document and application states."""
        job_id = uuid.uuid4().hex
        job = {"id": job_id, "url": url, "company": "", "title": "New application",
               "details": "", "status": "new", "exceptions": [], "resume_id": None,
               "cover_id": None, "cover_state": "not_checked", "account": "",
               "created_at": now(), "updated_at": now(), "history": []}
        return self.put("job", job_id, job)

    def event(self, job_id, message):
        """Record concise progress without storing page contents or credentials in logs."""
        with self.lock:
            job = self.get("job", job_id)
            history = (job.get("history", []) + [{"at": now(), "message": message}])[-100:]
            return self.job(job_id, history=history, progress=message)

    def release_unissued(self, job_id, key, permit, message):
        """Return the budget slot only when native input was rejected before dispatch."""
        with self.lock, self.db:
            self._check_lease("job", job_id)
            attempt, job = self.get("submission_attempt", key), self.get("job", job_id)
            if (not attempt or attempt["id"] != permit or attempt["status"] != "submitting"
                    or attempt["job_id"] != job_id or job["status"] != "submitting"):
                raise ValueError("The reserved application changed. Check the existing attempt.")
            stamp = now()
            attempt.update(status="not_sent", finished_at=stamp, reason=message)
            job.update(status="needs_review", exceptions=[message], progress=message, updated_at=stamp,
                       history=(job.get("history", []) + [{"at": stamp, "message": message}])[-100:])
            self.db.execute("UPDATE records SET data=? WHERE kind='job' AND id=?", (json.dumps(job), job_id))
            self.db.execute("INSERT INTO records VALUES ('unissued_submission',?,?)", (permit, json.dumps(attempt)))
            self.db.execute("DELETE FROM records WHERE kind='submission_attempt' AND id=?", (key,))

    def close(self):
        """Flush local state during a clean app shutdown."""
        self.db.close()
