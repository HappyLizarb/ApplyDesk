"""Require fresh, verified evidence before labeling a live application ready."""

import re

from .mapping import COVER_LETTER_REQUIRED, document_kind, normalize

FINAL_LABELS = {"submit application", "send application", "submit job application"}


def verify_readiness(job, current_job, profile, snapshot, ledger):
    """Return review issues without mutating records or taking browser actions."""
    issues = _version_issues(job, current_job, profile)
    button, final_issues = _final_control(current_job, snapshot)
    issues.extend(final_issues)
    issues.extend(_audit_issues(snapshot, button))
    issues.extend(_attachment_issues(current_job, profile, snapshot, ledger, button))
    guard = snapshot.get("guard", {})
    audited_browser = guard.get("page_networking") == "normal" and guard.get("action_audited") is True
    if guard.get("manual") or not (audited_browser or guard.get("mutations_blocked") is True or guard.get("submission_guarded") is True):
        issues.append("The browser is not in guarded automation mode.")
    if guard.get("blocked_requests"):
        issues.append("The site attempted an unverified network action; review it manually.")
    issues.extend(snapshot.get("warnings", []))
    cover_state, cover_issues = _cover_state(current_job, snapshot, ledger, not issues)
    issues.extend(cover_issues)
    return {"ready": not issues, "issues": list(dict.fromkeys(issues)), "cover_state": cover_state}


def _version_issues(job, current_job, profile):
    """A concurrent profile or document edit invalidates the prepared application."""
    issues = []
    revision = profile.get("revision")
    if not profile.get("confirmed") or revision is None:
        issues.append("The current profile has not been confirmed.")
    if job.get("profile_revision") != revision or current_job.get("profile_revision") != revision:
        issues.append("The profile changed after this application was screened.")
    for key in ("id", "url", "resume_id", "cover_id"):
        if job.get(key) != current_job.get(key):
            issues.append(f"The application {key.replace('_', ' ')} changed during preparation.")
    if not current_job.get("resume_id"):
        issues.append("No current resume version is selected.")
    if current_job.get("status") == "submitted":
        issues.append("This application is already marked submitted.")
    return issues


def _final_control(job, snapshot):
    """Generic Finish, Apply, and Submit controls never prove final application context."""
    boundaries = [button for button in snapshot.get("buttons", [])
                  if button.get("action") == "final_or_ambiguous" or normalize(button.get("text")) in FINAL_LABELS]
    if len(boundaries) != 1:
        return None, ["A single explicit final application submission control was not found."]
    button = boundaries[0]
    if normalize(button.get("text")) not in FINAL_LABELS or button.get("disabled"):
        return None, ["The final control is ambiguous or disabled; review the application manually."]
    if not button.get("form_id") and not button.get("context_id"):
        return None, ["The final control is not associated with an identifiable application section."]
    text = normalize(str(snapshot.get("title", "")) + " " + str(snapshot.get("text", "")))
    expected = [normalize(job.get(key) or "") for key in ("company", "title")]
    if not all(expected) or not all(re.search(r"(?:^| )" + re.escape(value) + r"(?: |$)", text) for value in expected):
        return None, ["The live final view does not identify the expected company and job title."]
    context = normalize(button.get("context_text", ""))
    if not button.get("form_id") and not all(value in context for value in expected):
        return None, ["The final control's own section does not identify the expected job."]
    return button, []


def _associated(field, button):
    """Prefer native form ownership; section identity is only a fallback for SPA forms."""
    if not button:
        return False
    key = "form_id" if button.get("form_id") else "context_id"
    return bool(field.get(key)) and field.get(key) == button.get(key)


def _audit_issues(snapshot, button):
    """The readback check must name this exact snapshot and the relevant live fields."""
    audit = snapshot.get("audit", {})
    if not snapshot.get("snapshot_id") or audit.get("passed") is not True or audit.get("snapshot_id") != snapshot.get("snapshot_id"):
        return ["The final browser snapshot lacks a matching field readback check."]
    audited = set(audit.get("field_ids", []))
    issues, relevant = [], []
    for field in snapshot.get("fields", []):
        if field.get("disabled") or field.get("type") == "file":
            continue
        if field.get("type") == "radio":
            issues.extend(_radio_issues(field, snapshot.get("fields", []), audited))
            if not field.get("checked"):
                continue
        populated = field.get("checked") if field.get("type") in {"radio", "checkbox"} else field.get("value") not in (None, "", False, [])
        if not field.get("required") and not populated:
            continue
        relevant.append(field)
        if field.get("type") == "password" or not field.get("supported", False):
            issues.append("The final view contains a credential or unsupported required/populated control.")
        if field.get("field_id") not in audited:
            issues.append("A required or populated live field is missing from the readback check.")
        if not _associated(field, button):
            issues.append("An audited field is outside the final application form or section.")
    if not relevant:
        issues.append("No verified application fields are visible at the final control.")
    return issues


def _radio_issues(field, fields, audited):
    """An audited checked member satisfies its native required radio group."""
    if not field.get("name"):
        return ["A radio group has no stable native group name; review it manually."]
    peers = [item for item in fields if item.get("type") == "radio" and not item.get("disabled")
             and all(item.get(key) == field.get(key) for key in ("name", "form_id", "group_id"))]
    selected = [item for item in peers if item.get("checked")]
    if len(selected) > 1 or (any(item.get("required") for item in peers) and len(selected) != 1):
        return ["A radio group has missing or conflicting selections."]
    if selected and selected[0].get("field_id") not in audited:
        return ["The selected radio answer is missing from the readback check."]
    return []


def _attachment_issues(job, profile, snapshot, ledger, button):
    """Every historical upload must remain independently verifiable in the final view."""
    issues, recorded = [], {}
    fields = {field.get("field_id"): field for field in snapshot.get("fields", []) if field.get("type") == "file"}
    expected_ids = {"resume": job.get("resume_id"), "cover": job.get("cover_id"), "transcript": profile.get("transcript_id")}
    for item in ledger:
        field_id = item.get("field_id")
        if not field_id or field_id in recorded:
            issues.append("The upload ledger contains an ambiguous or duplicate field identity.")
        recorded[field_id] = item
        issues.extend(_verify_attachment(item, fields.get(field_id), expected_ids, button))
    if not any(item.get("kind") == "resume" for item in ledger):
        issues.append("The selected resume has no verified upload.")
    cover_fields = [field for field in snapshot.get("fields", []) if document_kind(field) == "cover"]
    required_cover_file = any(field.get("type") == "file" and field.get("required") for field in cover_fields)
    if required_cover_file and not any(item.get("kind") == "cover" for item in ledger):
        issues.append("The selected cover letter has no verified upload.")
    if job.get("cover_id") and not cover_fields:
        issues.append("The selected cover letter is not visible in the final application view.")
    for field_id, field in fields.items():
        if (field.get("files") or field.get("required")) and field_id not in recorded:
            issues.append("A required or populated attachment is absent from the upload ledger.")
    return issues


def _verify_attachment(item, field, expected_ids, button):
    """Bind document version, current bytes, actual File bytes, and final form ownership."""
    issues = []
    kind = item.get("kind")
    if not item.get("document_id") or item.get("document_id") != expected_ids.get(kind):
        issues.append("An uploaded attachment is not the currently selected document version.")
    digest = item.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or item.get("expected_sha256") != digest:
        issues.append("Uploaded document bytes do not match freshly verified current document bytes.")
    if not field:
        return issues + ["An earlier upload is hidden or missing from the final view; verify it manually."]
    files = field.get("files", [])
    if field.get("disabled") or not _associated(field, button):
        issues.append("An uploaded attachment is outside the active final application form.")
    if document_kind(field) != kind:
        issues.append("An attachment field's purpose changed after upload.")
    if len(files) != 1 or files[0].get("sha256") != digest or files[0].get("hash_error"):
        issues.append("Actual browser attachment bytes do not match the uploaded document.")
    return issues


def _cover_state(job, snapshot, ledger, verified_final):
    """A slash is justified only after a proven final view excludes a cover requirement."""
    fields = [field for field in snapshot.get("fields", []) if document_kind(field) == "cover"]
    if job.get("cover_id") or job.get("cover_state") == "required" or any(field.get("required") for field in fields) or any(item.get("kind") == "cover" for item in ledger):
        return "required", [COVER_LETTER_REQUIRED]
    if any(field.get("files") or field.get("value") not in (None, "", False, []) for field in fields):
        return "not_checked", ["An optional cover letter is populated without a current required document; review it manually."]
    if not verified_final:
        return "not_checked", []
    text = normalize(snapshot.get("text", ""))
    if "cover letter" in text and not fields:
        return "not_checked", ["The final view mentions a cover letter without a verifiable requirement field."]
    return "not_required", []
