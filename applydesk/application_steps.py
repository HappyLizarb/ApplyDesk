"""Field and attachment planning kept separate from browser execution."""

import hashlib
import re
from urllib.parse import urlsplit

from .account_scope import https_origin as _https_origin, sign_in_scope, employer_key
from .employer_accounts import EmployerAccounts
from .mapping import COVER_LETTER_REQUIRED, answer_field, document_kind, normalize, plan_fields
from .browser_bridge import BrowserError
from .successfactors_auth import prepare_successfactors_account, successfactors_auth_context


def fill_current_form(session, params):
    """A stale pre-write snapshot causes replanning, never replay of answers against changed fields."""
    try:
        return session.call("fill", params)
    except BrowserError as error:
        if str(error) != "The form changed. Inspect it again before filling.":
            raise
        page = session.call("snapshot")
        return {**page, "intervention": "stale_before_fill"}


def enter_current_form(session, page, job):
    """Refresh a dynamic listing before opening its explicitly identified application control."""
    for attempt in range(3):
        try:
            return session.call("application_entry", {"snapshot_id": page["snapshot_id"], "company": job.get("company"), "title": job.get("title")})
        except BrowserError as error:
            if str(error) != "The form changed. Inspect it again before filling." or attempt == 2:
                raise
            page = session.call("snapshot")


def authenticate(workspace, session, job, snapshot):
    """Use saved credentials only on a distinct authentication screen."""
    unread = [field for field in snapshot["fields"] if field.get("choices_unread")]
    if unread:
        return session.call("read_choices", {"snapshot_id": snapshot["snapshot_id"], "field_id": unread[0]["field_id"]})
    prepared = prepare_successfactors_account(session, snapshot, workspace.profile()) if snapshot.get("successfactors_auth") else None
    if prepared is not None:
        return prepared
    if not any(field["type"] == "password" and not field.get("disabled") for field in snapshot["fields"]):
        return snapshot
    entries = [button for button in snapshot["buttons"] if button.get("navigation_kind") == "account_entry"]
    accounts = EmployerAccounts(workspace)
    if len(entries) == 1 and (not accounts.display(job).get("account") or accounts.registration_pending(job)):
        opened = session.call("next", {"snapshot_id": snapshot["snapshot_id"], "field_id": entries[0]["field_id"]})
        return {**opened, "intervention": "authentication_entry"}
    with workspace.store.lease(("job", job["id"]), ("employer_account", employer_key(job))):
        return _authenticate_account(workspace, session, job, snapshot)


def _authenticate_account(workspace, session, job, snapshot):
    """Keep account bindings fixed while the browser can still read current workspace state."""
    button, fields, origin = _auth_context(snapshot)
    with workspace.store.lock:
        accounts = EmployerAccounts(workspace)
        current = workspace.store.get("job", job["id"])
        if not current:
            raise ValueError("Application not found.")
        realm = sign_in_scope(current, snapshot, button)
        account = accounts.display(current).get("account") or workspace.profile()["identity"]["email"]
        answers, passwords = _auth_inputs(fields, button["action"], account, workspace.profile())
        saved_account, password = accounts.credentials(current, origin, realm, button["action"], reserve_only=True)
        if saved_account != account:
            answers, passwords = _auth_inputs(fields, button["action"], saved_account, workspace.profile())
        answers.extend({"field_id": field["field_id"], "value": password} for field in passwords)
        job.update(workspace.store.job(job["id"], live_url=snapshot["url"]))
    updated = session.call("fill", {"snapshot_id": snapshot["snapshot_id"], "answers": answers})
    if updated.get("intervention"):
        raise ValueError(updated["intervention"])
    if button.get("auth_widget") == "successfactors":
        _auth_context(updated)
    accounts.mark_authentication(job, button["action"])
    intent = "sign_in" if button["action"] == "login" else "create_account"
    page = session.call("authenticate", {"snapshot_id": updated["snapshot_id"], "field_id": button["field_id"], "intent": intent})
    return {**page, "intervention": "authentication_completed"}


def _auth_context(snapshot):
    """Validate the native authentication form before retrieving or generating a secret."""
    if snapshot.get("warnings") or snapshot.get("intervention") or snapshot.get("guard", {}).get("blocked_requests"):
        raise ValueError("The authentication page requires manual inspection before credentials can be filled.")
    password_forms = {field.get("form_id") for field in snapshot["fields"] if field["type"] == "password"}
    buttons = [button for button in snapshot["buttons"] if button["action"] in {"login", "create_account"}
               and button.get("type") == "submit" and button.get("form_id") in password_forms
               and button.get("form_id") and not button.get("disabled")]
    if len(buttons) != 1:
        raise ValueError("Authentication is ambiguous. Sign in or create the account manually.")
    button = buttons[0]
    if button.get("auth_widget") == "successfactors":
        return successfactors_auth_context(snapshot, button)
    action = button.get("form_action", "")
    origin = _https_origin(snapshot["url"])
    path = urlsplit(action).path
    if (button.get("auth_form") is not True or button.get("type") != "submit" or not button.get("form_id")
            or button.get("form_method", "").upper() != "POST" or _https_origin(action) != origin
            or not re.search(r"/(login|sign-?in|auth|register|sign-?up)/?$", path, re.I)):
        raise ValueError("Use manual control: the native form is not a separate HTTPS authentication endpoint.")
    fields = [field for field in snapshot["fields"] if field.get("form_id") == button["form_id"] and not field.get("disabled")]
    ids = [field.get("field_id") for field in fields]
    if (not fields or None in ids or len(ids) != len(set(ids))
            or any(field["type"] == "password" and field not in fields for field in snapshot["fields"] if not field.get("disabled"))):
        raise ValueError("The authentication fields do not belong to one native form.")
    for field in fields:
        if field.get("form_action") != action or field.get("form_method", "").upper() != "POST" or not field.get("supported", True):
            raise ValueError("The authentication form changed or contains unsupported fields.")
    return button, fields, origin


def _auth_inputs(fields, action, account, profile):
    """Only explicit account identifiers, passwords, and grounded signup names are filled."""
    answers, identifiers, passwords = [], [], []
    primary = {"password", "your password", "account password"}
    if action == "create_account":
        primary |= {"create password", "new password"}
    confirmation = {"confirm password", "confirm your password", "repeat password", "re enter password", "verify new password"}
    for field in fields:
        label, kind = normalize(field.get("label", "")), field["type"]
        if kind == "password" and label in primary | confirmation and not field.get("readonly"):
            passwords.append(field)
        elif kind in {"text", "email"} and label in {"email", "email address", "username", "user name"}:
            identifiers.append(field)
            answers.append({"field_id": field["field_id"], "value": account})
        else:
            answer = _signup_answer(field, action, account, profile)
            if answer:
                answers.append(answer)
    required_passwords = 1 if action == "login" else len(passwords)
    if (not account or len(identifiers) != 1 or required_passwords not in {1, 2} or len(passwords) != required_passwords
            or sum(normalize(field["label"]) in primary for field in passwords) != 1):
        raise ValueError("The account identifier or password fields are ambiguous. Use manual control.")
    for answer in answers:
        field = next(field for field in fields if field["field_id"] == answer["field_id"])
        if field.get("readonly") and field.get("value") != answer["value"]:
            raise ValueError("The readonly account identifier does not match the saved account.")
    return answers, passwords


def _signup_answer(field, action, account, profile):
    """Separate grounded signup details and explicit defaults from credential identity checks."""
    label, kind = normalize(field.get("label", "")), field["type"]
    names = {"first name", "last name", "full name", "name", "given name", "family name"}
    if action == "create_account":
        if kind in {"text", "email"} and label == "confirm email":
            return {"field_id": field["field_id"], "value": account}
        if kind == "checkbox" and field.get("account_notification"):
            return {"field_id": field["field_id"], "value": False}
        if (kind == "text" and label in names) or (kind == "select" and label == "country"):
            answer = answer_field(field, profile, "")
            if not answer:
                raise ValueError("The account details need confirmed profile values.")
            # Re-selecting an unchanged residence clears SuccessFactors privacy acceptance.
            return None if kind == "select" and field.get("value") == answer["value"] else answer
        if kind == "checkbox" and field.get("workday_consent") and label == "i agree":
            return {"field_id": field["field_id"], "value": True}
    if kind == "checkbox" and not field.get("required") and not field.get("checked"):
        return None
    raise ValueError("The authentication form has an unknown field or requires consent. Use manual control.")


def attachment_plan(workspace, documents, job, snapshot):
    """Bind every requested file input to an exact document version and byte hash."""
    uploads, unresolved = [], []
    fields = [field for field in snapshot["fields"] if field["type"] == "file" and not field["disabled"]]
    for field in fields:
        kind = document_kind(field)
        if kind in {"cover", "transcript"} and not field["required"]:
            if field.get("files"):
                unresolved.append("Remove or manually review the existing optional " + kind + " attachment.")
            continue
        if not kind:
            if field["required"] or field.get("files"):
                unresolved.append("Review unknown attachment: " + (field["label"] or "unlabelled file input"))
            continue
        if kind == "cover":
            unresolved.append(COVER_LETTER_REQUIRED)
            continue
        identity = workspace.profile().get("transcript_id") if kind == "transcript" else job.get(kind + "_id")
        if not identity:
            unresolved.append("Missing required " + kind + ".")
            continue
        record = workspace.docs.get(identity)
        if kind != "transcript":
            documents.require_review(identity)
        path, digest = choose_attachment(workspace, record, field)
        uploads.append({"field_id": field["field_id"], "path": str(path), "sha256": digest,
                        "document_id": identity, "kind": kind, "filename": path.name, "size": path.stat().st_size})
    return uploads, unresolved


def choose_attachment(workspace, document, field):
    """Respect accepted file types and use the PDF by default."""
    if document["kind"] in {"transcript", "resume"}:
        if document["kind"] == "resume":
            workspace.docs.verify(document["id"])
        candidates = [workspace.docs.asset(document["source_path"])]
    else:
        workspace.docs.verify(document["id"])
        candidates = [workspace.docs.asset(document[key]) for key in ("pdf_path", "docx_path")]
    path = next((candidate for candidate in candidates if _accepts(candidate, field.get("accept", ""))), None)
    if path is None:
        raise ValueError("The attachment field does not accept this document type.")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if document["kind"] == "transcript" and digest != document["source_sha256"]:
        raise ValueError("Transcript file changed after import. Upload it again.")
    return path, digest


def _accepts(path, accept):
    """Match complete HTML accept tokens instead of assuming every application MIME is PDF."""
    tokens = {token.strip().lower() for token in accept.split(",") if token.strip()}
    suffix = path.suffix.lower()
    mime = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}.get(suffix)
    return bool(mime) and (not tokens or bool(tokens & {suffix, mime, "application/*", "*/*"}))


def verify_attachments(snapshot, expected):
    """Use a fresh file-input inspection, not the worker's earlier success claim."""
    fields = {field["field_id"]: field for field in snapshot["fields"]}
    for attachment in expected:
        field = fields.get(attachment["field_id"])
        files = field.get("files", []) if field else []
        if len(files) != 1 or files[0].get("sha256") != attachment["sha256"]:
            raise ValueError("The live attachment does not match the selected document version.")


def audit_snapshot(workspace, session, profile, country, country_name, attachments):
    """Compare a fresh live snapshot with expected answers and uploaded file hashes."""
    from .intelligence import audit_fields
    snapshot = session.call("snapshot")
    snapshot.pop("audit", None)
    expected, unresolved = plan_fields(snapshot, profile, country, country_name)
    if unresolved:
        raise ValueError("; ".join(unresolved))
    verify_attachments(snapshot, attachments)
    fields = [{**field, "id": field["field_id"], "value": field.get("checked") if field["type"] in {"radio", "checkbox"} else field.get("value")} for field in snapshot["fields"] if field["type"] != "password"]
    expected = [{"id": answer["field_id"], "value": answer["value"], "fact_ids": answer["fact_ids"]} for answer in expected]
    if expected:
        audit = audit_fields(profile, {**snapshot, "fields": fields}, expected)
        if not audit["passed"]:
            raise ValueError("Field audit: " + "; ".join(audit["issues"]))
        snapshot["audit"] = {"passed": True, "snapshot_id": snapshot["snapshot_id"], "field_ids": [answer["id"] for answer in expected]}
    if snapshot.get("warnings"):
        raise ValueError("; ".join(snapshot["warnings"]))
    if snapshot["guard"].get("blocked_requests"):
        blocked = snapshot["guard"]["blocked_requests"]
        destinations = "; ".join(f"{item.get('method', '')} {item.get('url', '')} "
                                 f"{item.get('operation', '')}: {item.get('reason', '')}" for item in blocked)
        raise ValueError("The site tried an unverified network action: " + destinations + ". Continue in the browser with manual control.")
    return snapshot
