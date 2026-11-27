"""Private account and original-document operations through the visible native Chrome window."""

import hashlib
import re
import time

from .account_scope import NativeInputRequired, employer_key, native_account_scope as _account_scope
from .native_browser import BrowserError
from .employer_accounts import EmployerAccounts


def authenticate(session, workspace, job, profile, page, decision, *, registration_route=None):
    """Keep credentials in the backend while the actual browser receives ordinary native input."""
    page = _fresh(session, workspace, job, profile, page)
    username, passwords, submit = _account_controls(page, decision)
    origin, realm = _account_scope(workspace, job, page, decision, registration_route)
    accounts, mode = EmployerAccounts(workspace), decision["mode"]
    action = "login" if mode == "sign_in" else "create_account"
    with workspace.store.lease(("job", job["id"]), ("profile", "main"), ("employer_account", employer_key(job))):
        try:
            account, password = accounts.credentials(job, origin, realm, action, reserve_only=True)
        except ValueError as error:
            raise NativeInputRequired(str(error)) from None
        except Exception:
            raise NativeInputRequired("The saved employer credentials could not be read securely.") from None
        page = _enter_credentials(session, page, username, account, passwords, password)
        _element(page, submit["id"])
        _account_scope(workspace, job, page, decision, registration_route)
        accounts.mark_authentication(job, action)
        try:
            page = _act(session, page, action="click", element_id=submit["id"], final_permit=True)
        except InterruptedError:
            raise
        except Exception:
            raise ValueError("The sign-in button changed. Inspect the employer’s response before retrying.") from None
    page = _after_login(session, page)
    return page, {"kind": "authentication", "mode": mode, "attempted": True,
                  "confirmed": False, "snapshot_id": page["snapshot_id"], "origin": origin}


def _enter_credentials(session, page, username, account, fields, password):
    """Keep a matching visible email and enter the secret only into the observed secure fields."""
    binding = ("pid", "window_id", "active_tab_id", "generation", "url")
    entry = tuple(page.get(key) for key in binding)
    if username.get("value") != account:
        try:
            page = _act(session, page, action="type", element_id=username["id"], text=account, replace=True)
        except InterruptedError:
            raise
        except Exception:
            raise ValueError("The account field changed during entry. Inspect the current sign-in form.") from None
        email = _element(page, username["id"])
        if email.get("value") != account:
            raise ValueError("The account field did not retain the saved account name.")
        visible = {item["id"] for item in page.get("elements", []) if item.get("secure")}
        if (email.get("focused") and not page.get("native_modal") and None not in entry
                and tuple(page.get(key) for key in binding) == entry and any(field["id"] not in visible for field in fields)):
            # Dismiss Chrome's email suggestion if it has covered a previously verified password field.
            page = _act(session, page, action="key", key="escape", element_id=username["id"])
    for field in fields:
        current = _element(page, field["id"])
        if current.get("secure") is not True or tuple(page.get(key) for key in binding) != entry or page.get("native_modal"):
            raise ValueError("The secure password field changed. Inspect the current sign-in form.")
        try:
            page = _act(session, page, action="type", element_id=current["id"], text=password, replace=True, private=True)
        except InterruptedError:
            raise
        except Exception:
            raise ValueError("Chrome could not type in the selected secure password field. Inspect the visible field.") from None
    return page


def _after_login(session, page):
    """Give the employer time to replace the credential form; observe without sending a second login."""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        password_visible = any(field.get("secure") for field in page.get("elements", []))
        if page.get("text", "").strip() and not page.get("loading") and not password_visible:
            break
        time.sleep(0.4)
        page = session.call("native_observe", {"screenshot": False})
    return page


def upload(session, workspace, job, profile, page, decision):
    """Choose only the current unchanged source, then record its visible filename without claiming remote byte proof."""
    page = _fresh(session, workspace, job, profile, page)
    path, record, digest = _document(workspace, job, profile, decision)
    target_id = decision.get("element_id")
    page, picker = _upload_picker(session, page, target_id)
    page = _select_file(session, page, picker["id"], path, digest)
    page = _await_page(session, page, lambda value: not any(item["id"] == picker["id"] for item in value.get("dialogs", []))
                       and path.name in value.get("text", ""), "The file was selected. Check the employer’s upload result and filename.")
    _unchanged(path, digest)
    evidence = {"kind": decision["kind"], "document_id": record["id"], "filename": path.name, "element_id": target_id,
                "sha256": digest, "size": path.stat().st_size, "source_verified": True,
                "browser_bytes_verified": False, "upload_confirmed": False,
                "selection_method": "native_file_chooser", "visible_filename": path.name,
                "snapshot_id": page["snapshot_id"]}
    return page, evidence


def _upload_picker(session, page, target_id):
    """An employer notice can open the attached picker before the agent requests document selection."""
    existing = _picker(page, set())
    if page.get("native_modal") or any(item.get("native") for item in page.get("dialogs", [])):
        if existing and existing["id"] == target_id:
            return page, existing
        raise ValueError("Choose the observed native upload control.")
    control = _element(page, target_id)
    if control.get("secure") or control.get("role") not in {"AXButton", "AXLink", "AXTextField"}:
        raise ValueError("Choose the observed native upload control.")
    previous = {item["id"] for item in page.get("dialogs", [])}
    page = _act(session, page, action="click", element_id=target_id)
    page = _await_page(session, page, lambda view: _picker(view, previous) is not None, "The file picker did not open. Inspect the employer’s upload controls or notice.")
    return page, _picker(page, previous)


def _enter_path(session, page, picker_id, path):
    """Only a fresh native folder sheet or the picker's explicit Go field may receive the private path."""
    previous = {item["id"] for item in page.get("dialogs", [])}
    page = _act(session, page, action="key", key="cmd+shift+g")
    page = _await_page(session, page, lambda value: _folder_field(value, picker_id, previous) is not None, "The file picker opened but its path field did not appear.")
    field = _folder_field(page, picker_id, previous)
    page = _act(session, page, action="type", element_id=field["id"], text=str(path), replace=True, private=True)
    current = _element(page, field["id"])
    if page.get("private_value_matches") is not True or current.get("focused") is not True:
        raise ValueError("The approved document path was not confirmed in the native file picker.")
    return _act(session, page, action="key", key="return", element_id=field["id"])


def _select_file(session, page, picker_id, path, digest):
    """When Open was rejected before dispatch, reselect the original file in the same picker and tab."""
    owner = tuple(page.get(key) for key in ("pid", "window_id", "active_tab_id", "generation"))
    for attempt in range(3):
        page = _enter_path(session, page, picker_id, path)
        page = _await_page(session, page, lambda value: _open_button(value, picker_id) is not None, "The file picker could not select the original file. Inspect the open dialog.")
        _unchanged(path, digest)
        try:
            return _act(session, page, action="click", element_id=_open_button(page, picker_id)["id"])
        except BrowserError as error:
            if getattr(error, "code", None) != "stale_observation" or attempt == 2 or None in owner:
                raise
            page = session.call("native_observe", {"screenshot": False})
            current = tuple(page.get(key) for key in ("pid", "window_id", "active_tab_id", "generation"))
            if current != owner or (_picker(page, set()) or {}).get("id") != picker_id:
                raise


def _fresh(session, workspace, job, profile, page):
    """Concurrent profile/job edits and stale UI invalidate private input before it starts."""
    current = workspace.store.get("job", job["id"]) or {}
    if not profile.get("confirmed") or workspace.profile().get("revision") != profile.get("revision"):
        raise NativeInputRequired("Confirm the current profile before entering application details.")
    if current.get("removed_at") or any(current.get(key) != job.get(key) for key in ("id", "url", "company", "resume_id")):
        raise ValueError("The application changed. Reopen it before entering private information.")
    observed = session.call("native_observe", {"screenshot": False})
    if observed.get("snapshot_id") != page.get("snapshot_id"):
        raise ValueError("The application window changed. Inspect it again before entering private information.")
    return observed


def _element(page, identity):
    """A unique, enabled observed control is the only valid native input target."""
    matches = [item for item in page.get("elements", page.get("controls", [])) if item.get("id") == identity]
    if not isinstance(identity, str) or len(matches) != 1 or matches[0].get("enabled") is not True:
        raise ValueError("The observed native control is unavailable. Inspect the page again.")
    return matches[0]


def _act(session, page, **parameters):
    """Every native event is bound to its immediately preceding observation."""
    return session.call("native_action", {"snapshot_id": page["snapshot_id"], **parameters})


def _account_controls(page, decision):
    """The same agent selects native controls; secure fields cannot be mistaken for ordinary inputs."""
    mode, identities = decision.get("mode"), decision.get("password_ids")
    if mode not in {"sign_in", "create_account"} or not isinstance(identities, list) or not 1 <= len(identities) <= 2:
        raise ValueError("Choose the observed account mode, email field, password fields and submit button.")
    if any(not isinstance(identity, str) for identity in identities) or len(set(identities)) != len(identities):
        raise ValueError("The observed password fields are ambiguous.")
    username = _element(page, decision.get("username_id"))
    passwords = [_element(page, identity) for identity in identities]
    submit = _element(page, decision.get("submit_id"))
    if (username.get("secure") or username.get("role") not in {"AXTextField", "AXComboBox"}
            or any(item.get("secure") is not True for item in passwords) or submit.get("role") != "AXButton"
            or (mode == "sign_in" and len(passwords) != 1)):
        raise ValueError("The selected controls do not identify an ordinary account sign-in or signup.")
    visible = {item["id"] for item in page.get("elements", page.get("controls", [])) if item.get("secure") and item.get("enabled")}
    if visible != set(identities):
        raise ValueError("Inspect all visible secure fields before entering credentials.")
    labels = " ".join(item.get("label", "") for item in [*passwords, submit])
    if re.search(r"\bcurrent password\b|\b(reset|change|update|recover)\b.{0,40}\bpassword\b", labels, re.I):
        raise NativeInputRequired("Password changes and account recovery need your input.")
    return username, passwords, submit


def _document(workspace, job, profile, decision):
    """Model choices resolve to a selected private source, never to a model-written filesystem path."""
    expected = {"resume": job.get("resume_id"), "transcript": profile.get("transcript_id")}
    kind, identity = decision.get("kind"), decision.get("document_id")
    if kind not in expected or not identity or identity != expected[kind]:
        raise NativeInputRequired("Choose the current uploaded resume or confirmed transcript.")
    record = workspace.docs.get(identity)
    if record.get("kind") != kind:
        raise ValueError("The selected document kind changed.")
    path = workspace.docs.asset(record["source_path"])
    digest = record.get("source_sha256")
    _unchanged(path, digest)
    return path, record, digest


def _unchanged(path, digest):
    """Verify current local bytes without claiming access to browser or employer file storage."""
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("The original document changed. Select its current uploaded version before continuing.")


def _native_dialog(page, identity):
    """Web dialogs cannot masquerade as the operating system's file picker."""
    matches = [item for item in page.get("dialogs", []) if item.get("id") == identity]
    if len(matches) != 1 or matches[0].get("native") is not True:
        return None
    dialog = matches[0]
    valid = dialog.get("role") in {"AXSheet", "AXDialog"} or (dialog.get("role") == "AXWindow" and dialog.get("subrole") == "AXDialog")
    bounds = dialog.get("bounds", {})
    return dialog if valid and bounds.get("width", 0) > 0 and bounds.get("height", 0) > 0 else None


def _dialog_controls(page, identity):
    """Native ancestry and containment tie picker controls to that exact observed dialog."""
    dialog = _native_dialog(page, identity)
    if not dialog:
        return []
    bounds, controls = dialog["bounds"], []
    for item in page.get("elements", page.get("controls", [])):
        area = item.get("bounds", {})
        inside = area.get("x", -1e9) >= bounds["x"] and area.get("y", -1e9) >= bounds["y"]
        inside = inside and area.get("x", 0) + area.get("width", 0) <= bounds["x"] + bounds["width"]
        inside = inside and area.get("y", 0) + area.get("height", 0) <= bounds["y"] + bounds["height"]
        if item.get("parent_dialog_id") == identity and inside and not item.get("web_content"):
            controls.append(item)
    return controls


def _picker(page, previous):
    """An upload must open a new native file browser, not merely a webpage claiming to upload."""
    candidates = []
    for dialog in page.get("dialogs", []):
        controls = _dialog_controls(page, dialog["id"])
        buttons = {item.get("label") for item in controls if item.get("role") == "AXButton"}
        browser = any(item.get("role") in {"AXBrowser", "AXOutline", "AXTable"} for item in controls)
        if dialog["id"] not in previous and browser and {"Open", "Cancel"} <= buttons:
            candidates.append(dialog)
    return candidates[0] if len(candidates) == 1 else None


def _folder_field(page, picker_id, previous):
    """A fresh nested folder sheet needs one input; an inline picker input still needs an explicit Go action."""
    candidates = []
    if not _native_dialog(page, picker_id):
        return None
    for dialog in page.get("dialogs", []):
        nested = dialog["id"] not in previous and dialog.get("parent_dialog_id") == picker_id
        if dialog["id"] != picker_id and not nested:
            continue
        controls = _dialog_controls(page, dialog["id"])
        fields = [item for item in controls if item.get("role") in {"AXTextField", "AXComboBox"} and item.get("enabled") and not item.get("secure")]
        if len(fields) == 1 and (nested or any(item.get("role") == "AXButton" and item.get("label") == "Go" for item in controls)):
            candidates.extend(fields)
    return candidates[0] if len(candidates) == 1 else None


def _open_button(page, picker_id):
    """Only the original file picker's enabled Open button can finish selecting the approved path."""
    controls = _dialog_controls(page, picker_id)
    buttons = [item for item in controls if item.get("role") == "AXButton" and item.get("label") == "Open" and item.get("enabled")]
    dialogs = page.get("dialogs", [])
    nested = any(item.get("parent_dialog_id") == picker_id and _native_dialog(page, item["id"]) for item in dialogs)
    folder = _folder_field(page, picker_id, {item["id"] for item in dialogs})
    return buttons[0] if len(buttons) == 1 and not nested and folder is None else None


def _await_page(session, page, ready, timeout_message="The expected native file picker state was not visible. Inspect the application window."):
    """Allow short native UI transitions without retrying a click or entering data into an unknown dialog."""
    deadline = time.monotonic() + 5
    while not ready(page):
        if time.monotonic() >= deadline:
            raise ValueError(timeout_message)
        time.sleep(0.1)
        page = session.call("native_observe", {"screenshot": False})
    return page
