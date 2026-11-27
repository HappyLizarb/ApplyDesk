"""Request one recovery email from the saved employer's visible Workday form."""

import re
import time

from .account_scope import employer_key, https_origin, is_signin_switch
from .application_session import ApplicationSession
from .native_application_io import _act
from .native_evidence import page_text
from .storage import now
from .workday_skill import tenant_key


def _fields(page, roles, pattern):
    """Find visible employer controls by their displayed label."""
    return [item for item in page.get("elements", []) if item.get("role") in roles
            and item.get("enabled") and item.get("web_content", True) and not item.get("clipped")
            and re.fullmatch(pattern, str(item.get("label") or "").strip(" *:"), re.I)]


def _scope(page, account):
    """Follow the same saved Workday tenant across its sign-in and recovery pages."""
    url = page.get("url") or ""
    if (page.get("native_modal") or not tenant_key(account["realm"]) or tenant_key(url) != tenant_key(account["realm"])
            or https_origin(url) != account["origin"]):
        raise ValueError("The visible recovery page does not belong to the saved Workday account.")


def _email_fields(page):
    """Workday sometimes displays the email label separately above its only text field."""
    roles = {"AXTextField", "AXComboBox"}
    named = _fields(page, roles, r"e-?mail(?: address)?")
    if named:
        return named
    fields = [c for c in _fields(page, roles, "") if not c.get("secure")]
    if len(fields) != 1:
        return []
    box = fields[0].get("bounds") or {}
    for c in page.get("elements", []):
        label = str(c.get("value") or c.get("label") or "").strip(" *:")
        area = c.get("bounds") or {}
        if (c.get("web_content", True) and c.get("role") == "AXStaticText" and re.fullmatch(r"e-?mail(?: address)?", label, re.I)
                and "y" in area and "x" in area and "x" in box and "y" in box
                and abs(box["x"] - area["x"]) <= 20 and 0 <= box["y"] - area["y"] - area.get("height", 0) < 20):
            return fields
    return []


def _reset_form(session, account):
    """Follow visible account links until the employer asks where to send its reset email."""
    page = session.call("open", {"url": account["realm"]})
    visited = set()
    for index in range(15):
        _scope(page, account)
        fields = _email_fields(page)
        buttons = _fields(page, {"AXButton"}, r"reset password|send(?: password)? reset (?:email|link)|submit")
        secret = any(item.get("secure") and item.get("web_content", True) for item in page.get("elements", []))
        if (len(fields) == len(buttons) == 1 and not secret
                and re.search(r"forgot (?:your )?password|reset (?:your )?password", page_text(page), re.I)):
            return page, fields[0], buttons[0]
        forgot = _fields(page, {"AXLink", "AXButton"}, r"forgot (?:your )?password\??")
        signin = _fields(page, {"AXLink", "AXButton"}, r"sign in")
        switch = len(signin) == 1 and is_signin_switch(page, {"action": "click", "element_id": signin[0]["id"]}, account, recovery=True)
        route = forgot or (signin if switch or not secret else [])
        if len(route) == 1 and route[0]["label"] not in visited:
            visited.add(route[0]["label"])
            page = _act(session, page, action="click", element_id=route[0]["id"], **({"final_permit": True} if switch and not forgot else {}))
        elif index in (2, 5, 8) and page.get("viewport", {}).get("height", 0) >= 80:
            from .native_page import _viewport_action
            page = _viewport_action(session, page, scroll=True)
        else:
            time.sleep(.4)
            page = session.call("native_observe")
    raise ValueError("The saved employer did not show an identifiable password-reset email form.")


def _dispatch(engine, session, account):
    """Reserve before sending so an interruption or another request cannot send duplicate email."""
    page, field, button = _reset_form(session, account)
    page = _act(session, page, action="type", element_id=field["id"], text=account["account"], replace=True)
    _scope(page, account)
    controls = {c.get("id"): c for c in page["elements"]}
    if (controls.get(field["id"], {}).get("value") != account["account"]
            or controls.get(button["id"], {}).get("label") != button["label"]
            or any(item.get("secure") for item in controls.values())):
        raise ValueError("The recovery form changed before the email request.")
    engine.check_active()
    account["reset_email"] = {"status": "requested_unconfirmed", "requested_at": now()}
    engine.w.store.put("employer_account", account["id"], account)
    before = set(line.strip() for line in page_text(page).splitlines())
    page = _act(session, page, action="click", element_id=button["id"], final_permit=True)
    for _ in range(10):
        _scope(page, account)
        lines = [line.strip() for line in page_text(page).splitlines() if 0 < len(line.strip()) <= 550]
        response = next((line for line in lines if line not in before and re.search(
            r"(?:email|e-mail).{0,100}(?:has been sent|will be sent|was sent)|(?:sent|send) (?:you |an? )?.{0,15}email (?:with|to)|"
            r"check your (?:e-?mail|inbox)|you will receive an e-?mail with instructions to reset your password", line, re.I)), None)
        if response:
            account["reset_email"].update(status="acknowledged", response=response)
            engine.w.store.put("employer_account", account["id"], account)
            break
        time.sleep(.5)
        page = session.call("native_observe")
    return account["reset_email"]


def request_reset_email(engine, job_id):
    """Act only on an explicit recovery request for this job's already bound employer account."""
    engine.check_active()
    active = engine.active or {}
    message = active.get("message", {})
    text = message.get("text", "")
    job = engine.w.store.get("job", job_id) or {}
    if (message.get("role") != "user" or active.get("auto_restart") or job_id not in text
            or not re.search(r"(?:^|[.!?]\s+|\bplease |\byou (?:may|can) )\s*(?:request|send)\b[^.!?\n]{0,75}\b(?:password[- ]reset|reset)[- ](?:email|link)\b", text, re.I)
            or job.get("removed_at") or not job.get("employer_account_id")):
        raise ValueError("Request a password-reset email for the specific saved job and its existing account.")
    key = employer_key(job)
    with engine.w.store.lease(("job", job_id), ("employer_account", key)):
        account = engine.w.store.get("employer_account", key) or {}
        if (key != job["employer_account_id"] or account.get("owner_job_id") != job_id or not account.get("account")
                or not tenant_key(account.get("realm", "")) or not job.get("public_source", {}).get("complete")
                or tenant_key(job["public_source"]["url"]) != tenant_key(account["realm"])):
            raise ValueError("The saved employer account and Workday tenant could not be verified.")
        if account.get("reset_email"):
            return account["reset_email"]
        session = ApplicationSession(engine, job_id=job_id)
        try:
            return _dispatch(engine, session, account)
        finally:
            session.park()
