"""Send a generically reviewed application through the existing durable attempt ledger."""

from .agent_browser_review import validate_review
from .application_answers import with_application_source
from .application_submission import finish_attempt, renew_verification_attempt, reserve_attempt


def submit_agent_ready(engine, job, run, checkpoint, retry_attempt=None):
    """Revalidate current evidence, reserve once, then require an employer receipt."""
    workspace, session = engine.w, engine.w.sessions[job["id"]]
    checkpoint()
    page = session.call("agent_observe")
    if page.get("snapshot_id") != job.get("last_snapshot"):
        raise ValueError("The application changed after its final review. Nothing was submitted.")
    profile = with_application_source(workspace.profile(), job)
    audit = validate_review(workspace, job, profile, page, job.get("attachments", []), job["agent_browser_review"])
    current = workspace.store.get("job", job["id"]) or {}
    if any(item.get("final_audit", {}).get("review_digest") != audit["review_digest"] for item in (job, current)):
        raise ValueError("The final application review changed. Check the current page again.")
    attempt = renew_verification_attempt(engine, job, run, retry_attempt, checkpoint) if retry_attempt else reserve_attempt(engine, job, run, checkpoint)
    try:
        review = job["agent_browser_review"]
        receipt = session.call("agent_submit", {"snapshot_id": page["snapshot_id"],
            "final_element_id": audit["final_element_id"], "attempt_id": attempt["id"],
            "company": job["company"], "title": job["title"], "review_digest": audit["review_digest"],
            "company_quote": review["company_quote"], "title_quote": review["title_quote"],
            "resume_quote": review["resume_quote"], "retry_verification": retry_attempt is not None})
        confirmed = receipt.get("confirmed") is True and receipt.get("attempt_id") == attempt["id"] and bool(receipt.get("receipt"))
        status = "submitted" if confirmed else "verification_required" if receipt.get("verification_required") else "submission_unknown"
        return finish_attempt(workspace, job, attempt, status, receipt)
    except BaseException:
        finish_attempt(workspace, job, attempt, "submission_unknown", {})
        raise
