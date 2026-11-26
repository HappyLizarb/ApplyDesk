"""Reserve once, click through native input, and count only an explicit employer receipt."""

import time

from .application_answers import with_application_source
from .application_submission import attempt_key, finish_attempt, renew_verification_attempt, reserve_attempt
from .browser_bridge import BrowserError
from .native_agent_prompt import RECEIPT_PROMPT
from .native_evidence import evidence_digest, page_text, public_page, quoted
from .native_review import review_surface, validate_review
from .native_form_validation import rejected_field


class ReviewChangedBeforeClick(ValueError):
    """The native helper rejected the final action before sending any mouse event."""


def _click_reviewed(session, page, audit, attempt, checkpoint):
    """Retry only a proven pre-dispatch rejection on the same, still-identical reviewed form."""
    original = review_surface(page)
    for index in range(3):
        checkpoint()
        try:
            return session.call("native_action", {"snapshot_id": page["snapshot_id"], "action": "click",
                "element_id": audit["final_element_id"], "final_permit": attempt["id"]})
        except BrowserError as error:
            if getattr(error, "code", None) == "click_obstructed_before_dispatch":
                raise ReviewChangedBeforeClick("The Submit button was covered before the click. Dismiss any "
                                              "obstructing notice and scroll the button fully into view.") from None
            if getattr(error, "code", None) != "stale_observation":
                raise
        if index < 2 and all(page.get(k) is not None for k in ("pid", "window_id", "active_tab_id", "generation")):
            fresh = session.call("native_observe")
            if not fresh.get("native_modal") and original == review_surface(fresh):
                page = fresh
                continue
        raise ReviewChangedBeforeClick("The review page changed before Chrome sent the click. Review the updated form.")


def submit_native_ready(engine, job, run, checkpoint, retry_attempt=None):
    """Recheck the exact reviewed view before granting the one irreversible click."""
    workspace, session = engine.w, engine.w.sessions[job["id"]]
    checkpoint()
    page = session.call("native_observe")
    if page.get("snapshot_id") != job.get("last_snapshot"):
        raise ValueError("The visible application changed after review. Nothing was submitted.")
    profile = with_application_source(workspace.profile(), job)
    audit = validate_review(workspace, job, profile, page, job.get("attachments", []),
                            job.get("native_evidence", []), job["native_review"])
    current = workspace.store.get("job", job["id"]) or {}
    if any(item.get("final_audit", {}).get("review_digest") != audit["review_digest"] for item in (job, current)):
        raise ValueError("The saved application review changed. Review the visible form again.")
    attempt = renew_verification_attempt(engine, job, run, retry_attempt, checkpoint) if retry_attempt else reserve_attempt(engine, job, run, checkpoint)
    try:
        try:
            result = _click_reviewed(session, page, audit, attempt, checkpoint)
        except ReviewChangedBeforeClick as error:
            if retry_attempt:
                raise
            workspace.store.release_unissued(job["id"], attempt_key(job), attempt["id"], str(error))
            return {"job_id": job["id"], "status": "needs_review", "click_not_sent": True, "issues": [str(error)]}
        receipt = _receipt(engine, session, job, attempt, page, result, checkpoint)
        status = "submitted" if receipt["confirmed"] else "verification_required" if receipt.get("verification_required") else "submission_unknown"
        return finish_attempt(workspace, job, attempt, status, receipt)
    except BaseException:
        finish_attempt(workspace, job, attempt, "submission_unknown", {})
        raise


def _receipt(engine, session, job, attempt, before, page, checkpoint):
    """The same configured agent reads the result; no second submit is permitted."""
    from .application_client import ApplicationClient
    client = ApplicationClient(engine, "application", job["id"])
    result = {}
    for _ in range(4):
        checkpoint()
        image = page.get("screenshot")
        result = client.ask(RECEIPT_PROMPT, {"company": job["company"], "title": job["title"],
                            "before": public_page(before), "after": public_page(page)}, images=[image] if image else None)
        quote = result.get("quote")
        proof = rejected_field(before, page, result, job["native_review"]["final_element_id"])
        if proof:
            return {"confirmed": False, "field_rejection": proof, "attempt_id": attempt["id"],
                    "receipt": quote, "url": page["url"], "issue": "The employer displayed a validation error after Submit. Check the employer receipt before retrying."}
        observed = quoted(quote, page_text(page)) or bool(image and result.get("evidence_source") == "screenshot" and isinstance(quote, str) and quote.strip())
        fresh = page.get("snapshot_id") != before.get("snapshot_id") and not quoted(quote, page_text(before))
        if result.get("status") == "submitted" and observed and fresh:
            return {"confirmed": True, "attempt_id": attempt["id"], "receipt": quote,
                    "url": page.get("url"), "snapshot_id": page["snapshot_id"], "method": "native_visible_confirmation",
                    "image_digest": evidence_digest(image) if image else None}
        if result.get("status") == "inspect_image" and not image:
            page = session.call("native_observe", {"screenshot": True})
            continue
        if result.get("status") != "pending":
            return {"confirmed": False, "attempt_id": attempt["id"], "receipt": quote if observed else "",
                    "verification_required": result.get("status") == "verification_required" and observed,
                    "issue": str(result.get("reason", "The employer did not confirm this submission.")), "url": page.get("url")}
        time.sleep(1)
        checkpoint()
        page = session.call("native_observe")
    return {"confirmed": False, "attempt_id": attempt["id"], "receipt": "",
            "issue": "The employer has not yet confirmed this submission.", "url": page.get("url")}
