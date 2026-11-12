"""Durable submission attempts prevent retries after timeouts, crashes, or missing receipts."""

import hashlib
import re
import uuid

from .readiness import verify_readiness
from .storage import now

DECLARATION = re.compile(r"\b(i (agree|accept|certify|attest|authorize|acknowledge|consent)|by (submitting|applying|clicking|continuing|proceeding|sending).{0,240}(agree|accept|certify|attest|authorize|acknowledge|consent)|terms of (service|use)|electronic signature)\b", re.I | re.S)


def attempt_key(job):
    """Platform job identity is stable across imported URLs and separate unattended runs."""
    source = job.get("recommendation_source") or {}
    identity = f"{source['platform']}:{source['job_id']}" if source.get("job_id") else job["url"].split("#")[0]
    return hashlib.sha256(identity.encode()).hexdigest()


def submit_ready(engine, job, run, checkpoint):
    """Reserve a slot before the one irreversible click; no response means an uncertain outcome."""
    workspace = engine.w
    session = workspace.sessions[job["id"]]
    checkpoint()
    snapshot = session.call("snapshot")
    if snapshot["snapshot_id"] != job.get("last_snapshot"):
        raise ValueError("The application changed after its final field audit. Nothing was submitted.")
    if DECLARATION.search(snapshot.get("text", "")):
        raise ValueError("The application contains a declaration or agreement requiring your review.")
    snapshot["audit"] = job["final_audit"]
    readiness = verify_readiness(job, workspace.store.get("job", job["id"]), workspace.profile(), snapshot, job["attachments"])
    if not readiness["ready"]:
        raise ValueError("; ".join(readiness["issues"]))
    button = next(button for button in snapshot["buttons"] if button["action"] == "final_or_ambiguous")
    attempt = reserve_attempt(engine, job, run, checkpoint)
    try:
        receipt = session.call("submit", {"snapshot_id": snapshot["snapshot_id"], "field_id": button["field_id"],
                                          "attempt_id": attempt["id"], "company": job["company"], "title": job["title"]})
        confirmed = receipt.get("confirmed") is True and receipt.get("attempt_id") == attempt["id"] and bool(receipt.get("receipt"))
        return finish_attempt(workspace, job, attempt, "submitted" if confirmed else "submission_unknown", receipt)
    except BaseException:
        finish_attempt(workspace, job, attempt, "submission_unknown", {})
        raise


def reserve_attempt(engine, job, run, checkpoint):
    """Budget and duplicate checks commit atomically before the browser receives permission."""
    workspace = engine.w
    with workspace.lock, workspace.store.lock:
        checkpoint()
        attempts = workspace.store.all("submission_attempt")
        if workspace.store.get("submission_attempt", attempt_key(job)):
            raise ValueError("This listing already has a submission attempt. Review its receipt before any further action.")
        if sum(attempt.get("run_id") == run["id"] for attempt in attempts) >= run["limit"]:
            raise ValueError("The submission limit for this chat request has been reached.")
        current = workspace.store.get("job", job["id"])
        if current.get("removed_at") or current["status"] != "ready":
            raise ValueError("This application is no longer ready for submission.")
        attempt = {"id": uuid.uuid4().hex, "run_id": run["id"], "job_id": job["id"], "status": "submitting",
                   "snapshot_id": job["last_snapshot"], "started_at": now(), "profile_revision": run["profile_revision"]}
        workspace.store.put("submission_attempt", attempt_key(job), attempt)
        workspace.store.job(job["id"], status="submitting", progress="Sending the audited application once…")
        return attempt


def finish_attempt(workspace, job, attempt, status, receipt):
    """Persist observed outcome even after cancellation; never erase an attempted submission."""
    attempt.update(status=status, finished_at=now(), receipt=receipt)
    workspace.store.put("submission_attempt", attempt_key(job), attempt)
    message = "Application submitted; confirmation received." if status == "submitted" else "Submission outcome is uncertain. Check the platform before retrying; automatic retries are blocked."
    workspace.store.job(job["id"], status=status, submission_receipt=receipt, progress=message)
    workspace.store.event(job["id"], message)
    return {"job_id": job["id"], "status": status, "receipt": receipt}
