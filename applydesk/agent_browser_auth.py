"""Bind agent-selected authentication controls to privately stored employer credentials."""

import re
from urllib.parse import parse_qs, unquote, urlsplit

from .account_scope import employer_key, https_origin, sign_in_scope
from .browser_bridge import BrowserError
from .employer_accounts import EmployerAccounts


class AuthenticationInputRequired(ValueError):
    """A real account, credential-scope or agreement question needs the user's input."""


def authenticate(workspace, session, job, page, decision):
    """An authorized application run may authenticate without exposing a password to its model."""
    username, passwords, button = _controls(page, decision)
    _purpose(page, decision["mode"], username, passwords, button)
    with workspace.store.lease(("job", job["id"]), ("employer_account", employer_key(job))):
        accounts = EmployerAccounts(workspace)
        current = workspace.store.get("job", job["id"])
        if not current or current.get("removed_at"):
            raise ValueError("The application is no longer available.")
        origin, realm = _scope(workspace, current, page, button)
        action = "login" if decision["mode"] == "sign_in" else "create_account"
        try:
            account, password = accounts.credentials(current, origin, realm, action, reserve_only=True)
            accounts.mark_authentication(current, action)
        except ValueError as error:
            raise AuthenticationInputRequired(str(error)) from None
        except Exception:
            raise AuthenticationInputRequired("The saved employer credentials could not be read securely.") from None
        workspace.store.job(job["id"], live_url=page["url"])
        parameters = {"snapshot_id": page["snapshot_id"], "username_id": username["id"],
                      "password_ids": [field["id"] for field in passwords], "submit_id": button["id"],
                      "username": account, "password": password, "intent": decision["mode"]}
        try:
            return _redact(session.call("agent_authenticate", parameters), password)
        except Exception:
            raise BrowserError("Authentication could not be confirmed. Inspect the account page before continuing.") from None


def _controls(page, decision):
    """Only observed, enabled controls in one native password form can receive credentials."""
    keys = {"action", "purpose", "mode", "username_id", "password_ids", "submit_id"}
    if not isinstance(decision, dict) or set(decision) - keys or decision.get("mode") not in {"sign_in", "create_account"}:
        raise ValueError("Choose authentication mode and observed control IDs, without credential values.")
    elements, selected = page.get("elements"), decision.get("password_ids")
    if any(not isinstance(decision.get(key), str) or not decision[key] for key in ("username_id", "submit_id")):
        raise ValueError("Choose observed account and submit control IDs.")
    if not page.get("snapshot_id") or page.get("elements_truncated") or not isinstance(elements, list):
        raise ValueError("Observe the complete account form before authenticating.")
    ids = [element.get("id") for element in elements if isinstance(element, dict)]
    if len(ids) != len(elements) or any(not isinstance(identity, str) or not identity for identity in ids) or len(set(ids)) != len(ids):
        raise ValueError("The observed account controls are ambiguous.")
    if not isinstance(selected, list) or not 1 <= len(selected) <= 2 or any(not isinstance(item, str) for item in selected) or len(set(selected)) != len(selected):
        raise ValueError("Choose the observed password field and its confirmation, if present.")
    indexed = {element["id"]: element for element in elements}
    username, button = indexed.get(decision.get("username_id"), {}), indexed.get(decision.get("submit_id"), {})
    passwords = [indexed.get(identity, {}) for identity in selected]
    controls = [username, *passwords, button]
    form = username.get("form_id")
    if (not form or any(not item or item.get("form_id") != form or item.get("disabled") or item.get("visible") is not True for item in controls)
            or username.get("type") not in {"text", "email"} or username.get("readonly")
            or any(item.get("type") != "password" or item.get("readonly") for item in passwords)
            or button.get("authentication_form") is not True
            or not (button.get("tag") == "button" or button.get("role") == "button" or button.get("type") == "submit")):
        raise ValueError("The selected controls do not belong to one editable account form.")
    observed = [item["id"] for item in elements if item.get("type") == "password" and not item.get("disabled")]
    if set(observed) != set(selected) or (decision["mode"] == "sign_in" and len(passwords) != 1):
        raise ValueError("The password fields are ambiguous; inspect the account form again.")
    if any(item.get("form_action") != button.get("form_action") or item.get("form_method") != button.get("form_method") for item in controls):
        raise ValueError("The account form destination changed.")
    return username, passwords, button


def _purpose(page, mode, username, passwords, button):
    """Check the account form, credentials and other required fields before authentication."""
    fields = [item for item in page["elements"] if item.get("form_id") == button["form_id"]]
    text = button.get("form_context") or " ".join(item.get("context", "") for item in fields)
    action_text = " ".join([button.get("label", ""), *(item.get("label", "") for item in passwords)])
    if re.search(r"\b(?:reset|change|update|recover)\b.{0,40}\bpassword\b|\bcurrent password\b", action_text, re.I):
        raise AuthenticationInputRequired("Password recovery or a password change needs your input.")
    if mode == "create_account" and not re.search(r"\b(?:creat\w*\s+(?:an?\s+)?account|sign[ -]?up|register|registration)\b", text + " " + button.get("label", ""), re.I):
        raise ValueError("The observed form does not establish account creation.")
    selected = {username["id"], button["id"], *(item["id"] for item in passwords)}
    for field in fields:
        if field["id"] in selected or field.get("disabled") or not field.get("required"):
            continue
        value = field.get("checked") if field.get("type") in {"checkbox", "radio"} else field.get("value")
        if value in (None, "", False, "false", []):
            raise ValueError("Complete the other required account details from confirmed facts before authenticating.")


def _scope(workspace, job, page, button):
    """Reuse the saved exact realm for SPA forms instead of guessing a provider's API endpoint."""
    try:
        origin = https_origin(page["url"])
        if https_origin(button.get("form_action", "")) != origin:
            raise ValueError("The account form sends credentials to a different website.")
        company = " ".join(job["company"].split()).casefold()
        visible = " ".join(page.get("text", "").split()).casefold()
        if not re.search(r"(?<!\w)" + re.escape(company) + r"(?!\w)", visible):
            raise ValueError("The sign-in page does not identify this employer.")
        record = workspace.store.get("employer_account", employer_key(job))
        if record and record.get("realm"):
            realm = record["realm"]
            if record["origin"] != origin or not all(_within_realm(url, realm) for url in (page["url"], button["form_action"])):
                raise ValueError("The employer's sign-in website or account scope changed.")
        else:
            realm = sign_in_scope(job, page, button)
        return origin, realm
    except (KeyError, TypeError, ValueError):
        raise AuthenticationInputRequired("Verify the employer's exact sign-in website and saved account scope before using credentials.") from None


def _within_realm(url, realm):
    """Directory boundaries and explicit tenant query values must remain within the saved scope."""
    target, known = urlsplit(url), urlsplit(realm)
    if https_origin(url) != https_origin(realm) or known.fragment:
        return False
    path, root = unquote(target.path), unquote(known.path)
    if "\\" in path or any(piece in {".", ".."} for piece in path.split("/")):
        return False
    if not (path.startswith(root) if root.endswith("/") else path == root):
        return False
    expected, actual = parse_qs(known.query), parse_qs(target.query)
    return all(actual.get(key) == values for key, values in expected.items())


def _redact(value, password):
    """Keep private credential echoes out of returned page text and nested action results."""
    if isinstance(value, str):
        return value.replace(password, "[redacted]")
    if isinstance(value, list):
        return [_redact(item, password) for item in value]
    if isinstance(value, dict):
        return {key: _redact(item, password) for key, item in value.items()}
    return value
