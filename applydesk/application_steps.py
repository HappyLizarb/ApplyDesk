"""Field and attachment planning kept separate from browser execution."""

import hashlib
import re
from urllib.parse import urlsplit

from .account_scope import https_origin as _https_origin, sign_in_scope
from .employer_accounts import EmployerAccounts
from .mapping import answer_field, document_kind, normalize, plan_fields


def authenticate(workspace, session, job, snapshot):
    """Use saved credentials only on a distinct authentication screen."""
    if not any(field["type"] == "password" and not field.get("disabled") for field in snapshot["fields"]):
        return snapshot
    button, fields, origin = _auth_context(snapshot)
    with workspace.store.lock:
        accounts = EmployerAccounts(workspace)
        current = workspace.store.get("job", job["id"])
        if not current:
            raise ValueError("Application not found.")
        realm = sign_in_scope(current, snapshot, button)
        account = accounts.display(current).get("account") or workspace.profile()["identity"]["email"]
        answers, passwords = _auth_inputs(fields, button["action"], account, workspace.profile())
        saved_account, password = accounts.credentials(current, origin, realm, button["action"])
        if saved_account != account:
            answers, passwords = _auth_inputs(fields, button["action"], saved_account, workspace.profile())
        answers.extend({"field_id": field["field_id"], "value": password} for field in passwords)
        job.update(workspace.store.job(job["id"], live_url=snapshot["url"]))
        updated = session.call("fill", {"snapshot_id": snapshot["snapshot_id"], "answers": answers})
        if updated.get("intervention"):
            raise ValueError(updated["intervention"])
        intent = "sign_in" if button["action"] == "login" else "create_account"
        return session.call("authenticate", {"snapshot_id": updated["snapshot_id"], "field_id": button["field_id"], "intent": intent})


def _auth_context(snapshot):
    """Validate the native authentication form before retrieving or generating a secret."""
    if snapshot.get("warnings") or snapshot.get("intervention") or snapshot.get("guard", {}).get("blocked_requests"):
        raise ValueError("The authentication page requires manual inspection before credentials can be filled.")
    buttons = [button for button in snapshot["buttons"] if button["action"] in {"login", "create_account"} and not button.get("disabled")]
    if len(buttons) != 1:
        raise ValueError("Authentication is ambiguous. Sign in or create the account manually.")
    button = buttons[0]
    consent = r"\bby\b[^.!?]{0,180}\b(clicking|continuing|creating|registering|signing)\b[^.!?]{0,180}\b(agree|accept|consent)\b"
    if re.search(consent, button.get("context_text", ""), re.I):
        raise ValueError("Account creation includes an agreement. Review and accept it yourself in the browser.")
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
    names = {"first name", "last name", "full name", "name", "given name", "family name"}
    primary = {"password", "your password", "account password"}
    if action == "create_account":
        primary |= {"create password", "new password"}
    confirmation = {"confirm password", "confirm your password", "repeat password", "re enter password"}
    for field in fields:
        label, kind = normalize(field.get("label", "")), field["type"]
        if kind == "password" and label in primary | confirmation and not field.get("readonly"):
            passwords.append(field)
        elif kind in {"text", "email"} and label in {"email", "email address", "username", "user name"}:
            identifiers.append(field)
            answers.append({"field_id": field["field_id"], "value": account})
        elif action == "create_account" and kind == "text" and label in names:
            answer = answer_field(field, profile, "")
            if not answer:
                raise ValueError("The account's name fields need confirmed profile values.")
            answers.append(answer)
        elif kind == "checkbox" and not field.get("required") and not field.get("checked"):
            continue
        else:
            raise ValueError("The authentication form has an unknown field or requires consent. Use manual control.")
    required_passwords = 1 if action == "login" else len(passwords)
    if (not account or len(identifiers) != 1 or required_passwords not in {1, 2} or len(passwords) != required_passwords
            or sum(normalize(field["label"]) in primary for field in passwords) != 1):
        raise ValueError("The account identifier or password fields are ambiguous. Use manual control.")
    for answer in answers:
        field = next(field for field in fields if field["field_id"] == answer["field_id"])
        if field.get("readonly") and field.get("value") != answer["value"]:
            raise ValueError("The readonly account identifier does not match the saved account.")
    return answers, passwords


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
        if kind == "cover" and not job.get("cover_id"):
            job["cover_id"] = documents.cover(job)["id"]
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


def cover_text_plan(workspace, documents, job, snapshot):
    """Fill an explicitly required cover-letter text field from the exact reviewed document."""
    fields = [field for field in snapshot["fields"] if _cover_text_field(field)]
    if not fields:
        return [], set()
    if len(fields) != 1:
        raise ValueError("Multiple cover-letter text fields need manual review.")
    if not job.get("cover_id"):
        job["cover_id"] = documents.cover(job)["id"]
    documents.require_review(job["cover_id"])
    document = workspace.docs.get(job["cover_id"])
    value = "\n\n".join(block["text"] for block in document["blocks"] if block.get("text", "").strip())
    field = fields[0]
    if not value or (field.get("readonly") and field.get("value") != value):
        raise ValueError("The cover-letter text is empty or does not match the readonly field.")
    facts = [fact["id"] for fact in workspace.profile()["facts"] if fact.get("confirmed") is True and fact.get("kind") != "disclosure"]
    if not facts:
        raise ValueError("Cover-letter text requires confirmed profile evidence.")
    return [{"field_id": field["field_id"], "value": value, "fact_ids": facts,
             "document_id": document["id"]}], {field["field_id"]}


def _cover_text_field(field):
    """Use only an exact cover-letter label, required native text control, and visible metadata."""
    return (field.get("type") in {"text", "textarea"} and normalize(field.get("label", "")) == "cover letter"
            and field.get("required") is True and not field.get("disabled") and field.get("supported", True))


def audit_snapshot(workspace, session, profile, country, country_name, attachments, extra_answers=None, client=None):
    """Capture the live state independently and pass exact expectations to the auditor."""
    from .intelligence import audit_fields
    snapshot = session.call("snapshot")
    snapshot.pop("audit", None)
    extra_answers = extra_answers or []
    handled = {answer["field_id"] for answer in extra_answers}
    current = {field["field_id"] for field in snapshot["fields"] if _cover_text_field(field)}
    if len(handled) != len(extra_answers) or not handled <= current:
        raise ValueError("The cover-letter field changed before the audit.")
    expected, unresolved = plan_fields(snapshot, profile, country, country_name, ignored_ids=handled)
    expected.extend(extra_answers)
    if unresolved:
        raise ValueError("; ".join(unresolved))
    verify_attachments(snapshot, attachments)
    fields = [{**field, "id": field["field_id"], "value": field.get("checked") if field["type"] in {"radio", "checkbox"} else field.get("value")} for field in snapshot["fields"] if field["type"] != "password"]
    expected = [{"id": answer["field_id"], "value": answer["value"], "fact_ids": answer["fact_ids"]} for answer in expected]
    if expected:
        audit = audit_fields((client or workspace.client)("auditor"), profile, {**snapshot, "fields": fields}, expected)
        if not audit["passed"]:
            raise ValueError("Field audit: " + "; ".join(audit["issues"]))
        snapshot["audit"] = {"passed": True, "snapshot_id": snapshot["snapshot_id"], "field_ids": [answer["id"] for answer in expected]}
    if snapshot.get("warnings"):
        raise ValueError("; ".join(snapshot["warnings"]))
    if snapshot["guard"].get("blocked_requests"):
        raise ValueError("The site tried an unverified network action. Continue in the browser with manual control.")
    return snapshot
