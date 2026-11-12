"""Small transactional store; secrets belong in Keychain, never in these records."""

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
        self.db = sqlite3.connect(self.root / "workspace.sqlite", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, data TEXT, PRIMARY KEY(kind,id))")
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
            self.db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)", (kind, record_id, json.dumps(value)))
        return value

    def all(self, kind):
        """Return records in creation order for the spreadsheet."""
        with self.lock:
            rows = self.db.execute("SELECT data FROM records WHERE kind=? ORDER BY rowid", (kind,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def clear(self, kind):
        """Delete one app-owned record collection atomically without touching other workspace data."""
        with self.lock, self.db:
            cursor = self.db.execute("DELETE FROM records WHERE kind=?", (kind,))
            return cursor.rowcount

    def delete(self, kind, record_id):
        """Remove one identified record without discarding unrelated cached results."""
        with self.lock, self.db:
            cursor = self.db.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, record_id))
            return cursor.rowcount

    def delete_matching(self, kind, field, value):
        """Remove related cache records while preserving unrelated entries of the same kind."""
        with self.lock, self.db:
            rows = self.db.execute("SELECT id, data FROM records WHERE kind=?", (kind,)).fetchall()
            ids = [(kind, identity) for identity, raw in rows if json.loads(raw).get(field) == value]
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

    def close(self):
        """Flush local state during a clean app shutdown."""
        self.db.close()
