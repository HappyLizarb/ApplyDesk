"""Durable submission attempts prevent retries after timeouts, crashes, or missing receipts."""

import hashlib
import re

from .application_terms import unresolved_declarations
import uuid

from .readiness import verify_readiness
from .storage import now

NATIVE_VERIFICATION = ("Complete email verification on the employer’s original application page in Chrome. "
                       "Enter the latest code there and look for the employer’s submission confirmation.")

DECLARATION = re.compile(r"\b(i (agree|accept|certify|attest|authorize|acknowledge|consent)|by (submitting|applying|clicking|continuing|proceeding|sending).{0,240}(agree|accept|certify|attest|authorize|acknowledge|consent)|terms of (service|use)|electronic signature)\b", re.I | re.S)


def attempt_key(job):
    """Platform job identity is stable across imported URLs and separate unattended runs."""
    source = job.get("recommendation_source") or {}
    identity = f"{source['platform']}:{source['job_id']}" if source.get("job_id") else job["url"].split("#")[0]
    return hashlib.sha256(identity.encode()).hexdigest()


def submit_ready(engine, job, run, checkpoint, retry_attempt=None):
    """Reserve a slot before the one irreversible click; no response means an uncertain outcome."""
    if job.get("native_review") is not None:
        from .native_submission import submit_native_ready
        return submit_native_ready(engine, job, run, checkpoint, retry_attempt)
    if job.get("agent_browser_review") is not None:
        from .agent_browser_submission import submit_agent_ready
        return submit_agent_ready(engine, job, run, checkpoint, retry_attempt)
    return _submit_legacy(engine, job, run, checkpoint, retry_attempt)


def _submit_legacy(engine, job, run, checkpoint, retry_attempt):
    """Preserve audited draft compatibility while production applications use native input."""
    workspace = engine.w
    session = workspace.sessions[job["id"]]
    checkpoint()
    snapshot = session.call("snapshot")
    if snapshot["snapshot_id"] != job.get("last_snapshot"):
        raise ValueError("The application changed after its final field audit. Nothing was submitted.")
    if unresolved_declarations(snapshot.get("declaration_text", snapshot.get("text", "")), DECLARATION):
        raise ValueError("The application contains a declaration or agreement requiring your review.")
    snapshot["audit"] = job["final_audit"]
    readiness = verify_readiness(job, workspace.store.get("job", job["id"]), workspace.profile(), snapshot, job["attachments"])
    if not readiness["ready"]:
        raise ValueError("; ".join(readiness["issues"]))
    button = next(button for button in snapshot["buttons"] if button["action"] == "final_or_ambiguous")
    attempt = renew_verification_attempt(engine, job, run, retry_attempt, checkpoint) if retry_attempt else reserve_attempt(engine, job, run, checkpoint)
    try:
        receipt = session.call("submit", {"snapshot_id": snapshot["snapshot_id"], "field_id": button["field_id"],
                                          "attempt_id": attempt["id"], "company": job["company"], "title": job["title"],
                                          "retry_verification": retry_attempt is not None})
        confirmed = receipt.get("confirmed") is True and receipt.get("attempt_id") == attempt["id"] and bool(receipt.get("receipt"))
        status = "submitted" if confirmed else "verification_required" if receipt.get("verification_required") else "submission_unknown"
        return finish_attempt(workspace, job, attempt, status, receipt)
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
        if run["limit"] is not None and sum(attempt.get("run_id") == run["id"] for attempt in attempts) >= run["limit"]:
            raise ValueError("The submission limit for this chat request has been reached.")
        current = workspace.store.get("job", job["id"])
        if current.get("removed_at") or current["status"] != "ready" or current.get("manual_status") in {"submitted", "skipped"}:
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
    messages = {"submitted": "Application submitted; confirmation received.",
                "verification_required": receipt.get("issue") or "The employer requires email verification. Continue in the retained browser.",
                "submission_unknown": "Submission outcome is uncertain. Check the platform before retrying; automatic retries are blocked."}
    message = messages[status]
    manual_verification = status == "verification_required" and job.get("native_review") is not None
    if manual_verification:
        message = f"{message} {NATIVE_VERIFICATION}"
    job_status = "needs_review" if status == "verification_required" else status
    workspace.store.job(job["id"], status=job_status, submission_receipt=receipt, progress=message)
    workspace.store.event(job["id"], message)
    result = {"job_id": job["id"], "status": job_status, "receipt": receipt}
    if manual_verification:
        result["issues"] = [NATIVE_VERIFICATION]
    return result


def renew_verification_attempt(engine, job, run, attempt, checkpoint):
    """An explicitly requested refill reuses only an employer-rejected verification attempt."""
    workspace = engine.w
    with workspace.lock, workspace.store.lock:
        checkpoint()
        saved = workspace.store.get("submission_attempt", attempt_key(job))
        if not saved or saved["id"] != attempt["id"] or saved["run_id"] != run["id"]:
            raise ValueError("The original verification attempt changed.")
        receipt = saved.get("receipt", {})
        if saved["status"] != "verification_required" or receipt.get("confirmed") or receipt.get("issue") != "Incorrect security code":
            raise ValueError("Refill requires a confirmed code rejection; uncertain submissions cannot be retried.")
        saved.setdefault("verification_history", []).append(receipt)
        saved.update(status="submitting", snapshot_id=job["last_snapshot"], profile_revision=run["profile_revision"], receipt={})
        workspace.store.put("submission_attempt", attempt_key(job), saved)
        workspace.store.job(job["id"], status="submitting", progress="Sending the refilled application after email-code rejection…")
        return saved
