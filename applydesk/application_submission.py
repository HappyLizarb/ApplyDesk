"""Durably reserve and record native submissions, including interrupted attempts."""

import hashlib
import re
import time
import uuid

from .agent_context import with_application_source
from .native_browser import BrowserError
from .native_agent_prompt import RECEIPT_PROMPT
from .native_evidence import evidence_digest, page_text, public_page, quoted
from .native_review import review_surface, validate_review
from .storage import now

NATIVE_VERIFICATION = ("Complete email verification on the employer’s original application page in Chrome. "
                       "Enter the latest code there and look for the employer’s submission confirmation.")

def attempt_key(job):
    """Platform job identity is stable across imported URLs and separate unattended runs."""
    source = job.get("recommendation_source") or {}
    identity = f"{source['platform']}:{source['job_id']}" if source.get("job_id") else job["url"].split("#")[0]
    return hashlib.sha256(identity.encode()).hexdigest()


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


RECEIVED = re.compile(r"\b(?:application (?:was |has been )?(?:received|submitted)|already applied|thank you for applying|received your application)\b", re.I)


def rejected_field(before, after, result, final_id):
    """Identify a field diagnostic without treating it as proof of non-delivery."""
    name = result.get("quote")
    if result.get("status") != "field_error" or not isinstance(name, str) or not re.fullmatch(
            r"(?:Error:\s*)?(?:select at least one (?:value|option)|this field is required|please (?:select|enter) a value)[.!]?", name, re.I):
        return None
    binding = ("pid", "window_id", "active_tab_id", "generation", "url")
    if (any(before.get(key) is None or before[key] != after.get(key) for key in binding)
            or not before["url"].startswith("https://") or not after.get("snapshot_id")
            or before.get("snapshot_id") == after["snapshot_id"] or after.get("native_modal") or after.get("loading")
            or RECEIVED.search(page_text(after)) or quoted(name, page_text(before))):
        return None
    elements = [item for item in after.get("elements", []) if item.get("web_content", True)]
    errors = [item for item in elements if item.get("role") in {"AXStaticText", "AXHeading"}
              and name in (item.get("label"), item.get("value"))]
    field = next((item for item in elements if item.get("id") == result.get("field_id") and item.get("enabled")
                  and item.get("role") in {"AXCheckBox", "AXComboBox", "AXPopUpButton", "AXTextField"}
                  and item.get("value") in (None, "", False, 0) and item.get("required")), None)
    final = any(item.get("id") == final_id and item.get("enabled") and item.get("role") in {"AXButton", "AXLink"} for item in elements)
    if field and errors and final:
        return {"error": name, "field": str(field.get("label", ""))[:160], "url": before["url"],
                "snapshot_id": after["snapshot_id"]}
    return None


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


def submit_ready(engine, job, run, checkpoint):
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
    attempt = reserve_attempt(engine, job, run, checkpoint)
    try:
        try:
            result = _click_reviewed(session, page, audit, attempt, checkpoint)
        except ReviewChangedBeforeClick as error:
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
