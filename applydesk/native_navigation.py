"""Bounded native tab creation and address-bar navigation, without repeating uncertain input."""

import re
import time
from urllib.parse import urlsplit

from .browser_bridge import BrowserError
from .discovery import canonical_url
from .native_application_io import _dialog_controls, _picker
from .native_window_recovery import acquire_window, click_browser_tab, restore_visible_owner
from .native_tab_workspace import open_public_tab, retire_spare_tab
from .public_source import matches_saved_requisition_form, saved_form_tab
from .recommendation_checks import same_workday_application, same_workday_job


def adopt_clicked_tab(manager, job_id, action, before, after):
    """Track a newly opened destination, preserving the application behind a linked notice."""
    selected, parent = after.get("active_tab_id"), manager.tabs.get(job_id)
    if selected == parent:
        return
    added = {tab["id"] for tab in after.get("tabs", [])} - {tab["id"] for tab in before.get("tabs", [])}
    if not selected or action.get("action") != "click" or added != {selected}:
        raise BrowserError("The action changed application tabs. Observe the intended job again.")
    control = next((c for c in before.get("controls", []) if c.get("id") == action.get("element_id")), {})
    label = (control.get("label") or "").strip().casefold()
    notice = label in {"privacy", "terms"} or re.search(
        r"\b(privacy (notice|statement|policy)|terms (of (service|use)|and conditions)|(?:recruitment|applicant) notice)\b", label)
    if notice and control.get("role") == "AXLink" and control.get("web_content", True) and before.get("document_url"):
        manager.notice_forms[job_id] = (parent, before["document_url"], selected)
    manager.tabs[job_id] = selected


def return_from_notice(manager, job_id, page, generation):
    """Restore only the previously observed form tab at its original address."""
    saved = manager.notice_forms.get(job_id)
    if not saved or manager.tabs.get(job_id) != saved[2] or page.get("native_modal"):
        raise BrowserError("No application form is available behind this notice.")
    form, url, child = saved
    if form not in {tab["id"] for tab in page.get("tabs", [])} or form in manager.tabs.values():
        raise BrowserError("The original application tab is unavailable.")
    manager.tabs[job_id] = form
    try:
        current = manager._select(job_id, page, generation)
        if current.get("active_tab_id") != form or current.get("document_url") != url:
            raise BrowserError("The original application tab changed while the notice was open.")
    except Exception:
        manager.tabs[job_id] = child
        raise
    del manager.notice_forms[job_id]
    return retire_spare_tab(manager, current, job_id, generation)


def resume_visible_draft(manager, job_id, generation):
    """Attach to an existing job-specific form without opening a tab or replaying an action."""
    job = manager.w.store.get("job", job_id) or {}
    source = job.get("public_source") or {}
    target, expected = source.get("url", ""), job.get("live_url", "")
    if not target or not expected.startswith("https://"):
        return {}
    page, generation = acquire_window(manager, generation)
    if job_id in manager.notice_forms:
        page = return_from_notice(manager, job_id, page, generation)
    elif page.get("document_url") != expected:
        other_tabs = [tab for owner, tab in manager.tabs.items() if owner != job_id]
        if candidate := saved_form_tab(job, page, other_tabs):
            selected = click_browser_tab(manager, page, candidate, generation)
            page = wait_for_tab(manager, selected, generation, expected=candidate)
    selected, url = page.get("active_tab_id"), page.get("document_url") or ""
    if not selected or page.get("native_modal") or (url != expected and not same_workday_application(target, url)):
        return {}
    known_tab = manager.tabs.get(job_id) == selected
    matching_job = (url != target and urlsplit(url).path.rstrip("/").endswith(("/apply", "/application"))
                    and canonical_url(url) == canonical_url(target)) or same_workday_job(target, url) or matches_saved_requisition_form(job, page)
    if not job.get("attachments") and (url == target or not matching_job):
        return {}
    if not known_tab and (selected in manager.tabs.values() or not matching_job):
        return {}
    manager.tabs[job_id] = selected
    if job_id not in manager.jobs:
        manager.jobs.append(job_id)
    manager.last_job_id = job_id
    return retire_spare_tab(manager, page, job_id, generation)


def open_job_tab(manager, job_id, page, generation, target, listing_only):
    """Keep an already-open employer form while the application refreshes its listing."""
    if saved := manager.notice_forms.get(job_id):
        if saved[0] in {tab["id"] for tab in page.get("tabs", [])}:
            page = return_from_notice(manager, job_id, page, generation)
        else:
            del manager.notice_forms[job_id]
            manager.tabs.pop(job_id, None)
    retained = manager.tabs.get(job_id)
    if retained:
        page = manager._find_tab(retained, page, generation)
        if any(tab["id"] == retained for tab in page.get("tabs", [])):
            page = manager._select(job_id, page, generation)
    selected = page.get("active_tab_id")
    # Chrome can bring an already-open listing forward without adding a tab.
    if (selected and page.get("document_url") == target and not isinstance(manager.pending_open, tuple) and
            (selected == retained or selected not in manager.tabs.values())):
        manager.tabs[job_id] = selected
        return page
    if listing_only:
        job = manager.w.store.get("job", job_id) or {}
        url, selected = page.get("document_url") or "", page.get("active_tab_id")
        # After restarting, a job-specific application URL can identify the already-open, selected form.
        apply_path = urlsplit(url).path.rstrip("/").endswith(("/apply", "/application"))
        known_form = selected not in manager.tabs.values() and (
            (apply_path and canonical_url(target) == canonical_url(url) and url != target) or matches_saved_requisition_form(job, page))
        if (selected and (selected == retained or known_form) and url == job.get("live_url")
                and url.startswith("https://") and job.get("attachments")):
            manager.drafts[job_id] = (selected, url)
    page = open_public_tab(manager, page, generation, target, job_id)
    manager.tabs[job_id] = page["active_tab_id"]
    return page


def restore_job_tab(manager, job_id, page, generation):
    """Resume only this job's retained tab at its observed form URL, otherwise use the checked listing."""
    draft = manager.drafts.pop(job_id, None)
    if not draft or not any(tab["id"] == draft[0] for tab in page.get("tabs", [])):
        return manager._select(job_id, page, generation)
    listing = manager.tabs[job_id]
    manager.tabs[job_id] = draft[0]
    try:
        page = manager._select(job_id, page, generation)
        if page.get("active_tab_id") == draft[0] and page.get("document_url") == draft[1]:
            return {**retire_spare_tab(manager, page, job_id, generation), "resumed_form": True}
    except Exception:
        manager.tabs[job_id] = listing
        raise
    manager.tabs[job_id] = listing
    return manager._select(job_id, page, generation)


def check_missing_window(manager, error, method, params, generation):
    """Probe the original window after a missing-window report without repeating the failed input."""
    code = getattr(error, "code", None)
    if code == "window_offscreen" and method in {"observe", "action"} and manager.owner:
        if restore_visible_owner(manager, generation):
            options = {"screenshot": True} if method == "observe" and params.get("screenshot") is True else {}
            page = manager.browser.call("observe", {**manager.owner, **options}, expected_generation=generation)
            if any(page.get(key) != value for key, value in manager.owner.items()):
                raise BrowserError("The native browser returned a different application window.")
            if method == "observe":
                return page
            manager.observation = page
            raise BrowserError("The application window returned. Check the form to see whether the last action took effect.")
        return
    if code != "window_closed" and not (method == "attach" and code == "window_offscreen"):
        return
    if method in {"action", "observe"} and manager.owner:
        for delay in (0.15, 0.35, 0.65):
            time.sleep(delay)
            try:
                options = {"screenshot": True} if method == "observe" and params.get("screenshot") is True else {}
                page = manager.browser.call("observe", {**manager.owner, **options}, expected_generation=generation)
            except BrowserError as probe:
                if getattr(probe, "code", None) != "window_closed":
                    raise
                continue
            if any(page.get(key) != value for key, value in manager.owner.items()):
                raise BrowserError("The native browser returned a different application window.")
            if method == "observe":
                return page
            manager.observation = page
            raise BrowserError("The application window returned. Check the form to see whether the last action took effect.")
    manager.owner, manager.tabs, manager.jobs, manager.observation = None, {}, [], {}
    manager.last_job_id = None
    manager.browser.close()


def track_upload_picker(page, action, previous):
    """Keep only the tab and native dialog identities observed during the private upload operation."""
    if not page.get("native_modal"):
        return None
    if action.get("key") == "cmd+shift+g":
        picker = _picker(page, set())
        return (page.get("active_tab_id"), picker["id"], None) if picker else None
    if previous and action.get("action") == "type" and action.get("private") is True:
        tab, picker_id, _ = previous
        for sheet in page.get("dialogs", []):
            if sheet.get("native") and sheet.get("parent_dialog_id") == picker_id and any(
                c["id"] == action.get("element_id") for c in _dialog_controls(page, sheet["id"])
            ):
                return tab, picker_id, sheet["id"]
    return previous


def clear_upload_picker(manager, page, generation):
    """Clear a password-save suggestion or a verified unfinished upload before navigation."""
    if not page.get("native_modal"):
        manager.upload_picker = None
        return page
    message = "A dialog is open over Chrome. Close it before continuing to another posting."
    suggestion = _password_suggestion_close(page)
    if suggestion:
        dialog_id, close_id = suggestion
        tab = page["active_tab_id"]
        page = manager._action({"action": "click", "element_id": close_id}, page, generation)
        page = _wait_dialog_closed(manager, page, dialog_id, tab, generation)
        if page.get("native_modal"):
            raise BrowserError(message)
        manager.upload_picker = None
        return page
    tab, picker_id, child = manager.upload_picker or (None, None, None)
    if not tab or page.get("active_tab_id") != tab or tab not in manager.tabs.values():
        raise BrowserError(message)
    dialogs = {d["id"]: d for d in page.get("dialogs", []) if d.get("native")}
    if child and set(dialogs) == {picker_id, child} and dialogs[child].get("parent_dialog_id") == picker_id:
        if not any(c.get("role") == "AXTextField" and c.get("focused") for c in _dialog_controls(page, child)):
            raise BrowserError(message)
        page = manager._action({"action": "key", "key": "escape"}, page, generation)
        page = _wait_dialog_closed(manager, page, child, tab, generation)
    if {d["id"] for d in page.get("dialogs", [])} != {picker_id} or not _picker(page, set()):
        raise BrowserError(message)
    cancel = [c for c in _dialog_controls(page, picker_id) if c.get("enabled") and
              c.get("role") == "AXButton" and c.get("label") == "Cancel"]
    if len(cancel) != 1:
        raise BrowserError(message)
    page = manager._action({"action": "click", "element_id": cancel[0]["id"]}, page, generation)
    page = _wait_dialog_closed(manager, page, picker_id, tab, generation)
    if page.get("native_modal"):
        raise BrowserError(message)
    manager.upload_picker = None
    return page


def _password_suggestion_close(page):
    """Identify Chrome's own optional save prompt and its Close button without reading credentials."""
    dialogs = page.get("dialogs", [])
    if not page.get("active_tab_id") or len(dialogs) != 1 or not dialogs[0].get("native"):
        return None
    dialog = dialogs[0]
    if str(dialog.get("label", "")).casefold() != "save password?":
        return None
    buttons = {item.get("label"): item["id"] for item in page.get("controls", [])
               if item.get("parent_dialog_id") == dialog["id"] and item.get("web_content") is False
               and item.get("role") == "AXButton" and item.get("enabled") and not item.get("clipped")}
    if {"Close", "Save", "No, thanks"}.issubset(buttons):
        return dialog["id"], buttons["Close"]
    return None


def _wait_dialog_closed(manager, page, identity, tab, generation):
    """Reobserve a closing dialog without sending another keystroke or click."""
    for _ in range(8):
        if page.get("active_tab_id") != tab:
            raise BrowserError("The Chrome tab changed while closing the dialog.")
        if identity not in {d["id"] for d in page.get("dialogs", [])}:
            return page
        time.sleep(0.1)
        page = manager._wire("observe", {}, generation)
    raise BrowserError("The Chrome dialog is still open. Close it to continue.")


def wait_for_tab(manager, page, generation, *, before=None, expected=None):
    """Chrome may paint a new tab after its shortcut returns; bind only one observed active identity."""
    previous = None
    for _ in range(24):
        if page.get("native_modal"):
            raise BrowserError("A dialog is open over Chrome. Close it before continuing to another posting.")
        tabs = {item["id"] for item in page.get("tabs", [])}
        selected = page.get("active_tab_id")
        added = tabs - before if before is not None else None
        if added is not None and len(added) > 1:
            raise BrowserError("More than one new browser tab appeared. Show the intended application tab.")
        valid = selected in tabs and (expected is None or selected == expected)
        valid = valid and (added is None or added == {selected})
        if valid and selected == previous:
            return page
        previous = selected if valid else None
        time.sleep(0.1)
        page = manager._wire("observe", {}, generation)
    raise BrowserError("The application tab did not become identifiable. Show its Chrome window and try again.")
