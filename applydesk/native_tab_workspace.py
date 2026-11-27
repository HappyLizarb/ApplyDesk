"""Keep a single job's browser work within two tabs in the user's ordinary window."""

import re
import time

from .account_scope import employer_key, https_origin
from .native_browser import BrowserError
from .discovery import canonical_url
from .native_window_recovery import click_browser_tab


def select_tab(manager, page, tab, generation):
    """Require the intended tab to be active before using its address bar or closing it."""
    for step in range(12):
        if page.get("native_modal") or tab not in {item["id"] for item in page.get("tabs", [])}:
            break
        if page.get("active_tab_id") == tab:
            return page
        if step == 0:
            page = click_browser_tab(manager, page, tab, generation)
        else:
            time.sleep(.1)
            page = manager._wire("observe", {}, generation)
    raise BrowserError("The intended Chrome tab could not be verified.")


def forget_tab(manager, tab):
    """Drop old job bindings when a tab closes, changes ownership, or is reused."""
    manager.created_tabs.pop(tab, None)
    manager.tabs = {key: value for key, value in manager.tabs.items() if value != tab}
    manager.drafts = {key: value for key, value in manager.drafts.items() if value[0] != tab}
    manager.notice_forms = {key: value for key, value in manager.notice_forms.items() if tab not in (value[0], value[2])}


def available_tab(manager, page, generation, keep):
    """Select an ApplyDesk-created tab only while it still displays its last observed address."""
    for tab, address in list(manager.created_tabs.items()):
        if tab in keep:
            continue
        if tab in {item["id"] for item in page.get("tabs", [])}:
            page = select_tab(manager, page, tab, generation)
            if address and address == page.get("document_url"):
                return tab, page
        forget_tab(manager, tab)
    return None, page


def address_field(page, tab):
    """Locate Chrome's focused address bar separately from the employer's form."""
    fields = [c for c in page.get("controls", []) if c.get("web_content") is False and c.get("focused")
              and c.get("role") in {"AXTextField", "AXComboBox"} and "address" in (c.get("label") or "").casefold()]
    if page.get("active_tab_id") != tab or page.get("native_modal") or len(fields) != 1:
        raise BrowserError("The address bar of the intended Chrome tab was not focused.")
    return fields[0]


def find_open_listing(manager, page, generation, job_id, url):
    """Find an already-open saved posting by its tab title, then verify its exact public address."""
    job = manager.w.store.get("job", job_id) or {}
    source = job.get("public_source") if isinstance(job, dict) else None
    if not source or source.get("url") != url or not source.get("title") or page.get("native_modal"):
        return None, page
    role = " ".join(source["title"].casefold().split())
    original = page.get("active_tab_id")
    excluded = {original, *(tab for owner, tab in manager.tabs.items() if owner != job_id)}
    current = page
    for tab in page.get("tabs", []):
        if tab.get("id") in excluded or role not in " ".join((tab.get("label") or "").casefold().split()):
            continue
        current = select_tab(manager, current, tab["id"], generation)
        if current.get("document_url") == url:
            return current, current
    if current.get("active_tab_id") != original:
        current = select_tab(manager, current, original, generation)
    return None, current


def reuse_for_listing(manager, page, generation, target, job_id):
    """Navigate an existing work tab without reusing the populated application behind a listing."""
    keep = {row[0] for key, row in manager.drafts.items() if key == job_id}
    tab, page = available_tab(manager, page, generation, keep)
    if not tab:
        return None
    if canonical_url(page.get("document_url") or "") != canonical_url(target):
        page = manager._action({"action": "key", "key": "cmd+l"}, page, generation)
        field = address_field(page, tab)
        page = manager._action({"action": "type", "element_id": field["id"], "text": target, "replace": True}, page, generation)
        field = address_field(page, tab)
        if field.get("value") != target:
            raise BrowserError("The requested public address was not confirmed in Chrome.")
        page = manager._action({"action": "key", "element_id": field["id"], "key": "enter"}, page, generation)
        for _ in range(32):
            if page.get("native_modal") or page.get("active_tab_id") != tab:
                raise BrowserError("Chrome switched tabs while opening the public listing.")
            if canonical_url(page.get("document_url") or "") == canonical_url(target):
                break
            time.sleep(.2)
            page = manager._wire("observe", {}, generation)
        else:
            raise BrowserError("The requested listing did not appear in the existing work tab.")
    forget_tab(manager, tab)
    manager.created_tabs[tab] = page["document_url"]
    return page


def retire_spare_tab(manager, page, job_id, generation):
    """Keep a form and its linked notice or retained draft, retiring other verified work tabs."""
    active = page.get("active_tab_id")
    if not active or page.get("native_modal"):
        return page
    keep = {active}
    if job_id in manager.notice_forms:
        keep.add(manager.notice_forms[job_id][0])
    if job_id in manager.drafts:
        keep.add(manager.drafts[job_id][0])
    spare, current = available_tab(manager, page, generation, keep)
    if spare:
        if len(current.get("tabs", [])) < 2:
            raise BrowserError("The application tab changed during cleanup.")
        current = manager._action({"action": "key", "key": "cmd+w"}, current, generation)
        if spare in {tab["id"] for tab in current.get("tabs", [])}:
            raise BrowserError("Chrome has not closed the previous work tab yet.")
        forget_tab(manager, spare)
    return current if current.get("active_tab_id") == active else select_tab(manager, current, active, generation)


def track_action_tab(manager, job_id, before, after, generation, action):
    """Retain only the current job destination and any form needed for a return from a notice."""
    tab = after.get("active_tab_id")
    opened = tab and tab != before.get("active_tab_id")
    if opened or tab in manager.created_tabs and manager.created_tabs[tab] == before.get("document_url"):
        manager.created_tabs[tab] = after.get("document_url")
    # Read the employer receipt before any housekeeping after a final click.
    if isinstance(action.get("final_permit"), str):
        return after
    return retire_spare_tab(manager, after, job_id, generation) if opened else after


def check_current_job(manager, job_id):
    """Keep other job and research calls out while an application is in progress."""
    for owner in set(manager.tabs) - {job_id, manager.research_id}:
        if (manager.w.store.get("job", owner) or {}).get("status") in {"preparing", "ready", "submitting"}:
            raise BrowserError("Finish the current application before working on another job.")


def resume_account_tab(manager, job_id, generation):
    """Resume the existing employer login at its exact registered address after screening the posting."""
    job = manager.w.store.get("job", job_id) or {}
    account_id = job.get("employer_account_id")
    saved = manager.w.store.get("employer_account", account_id) if account_id else None
    if not (job.get("attachments") and saved and saved.get("id") == account_id == employer_key(job)
            and saved.get("owner_job_id") == job_id and saved.get("has_password") and saved.get("realm")
            and saved.get("origin") == job.get("account_origin") == https_origin(saved["realm"])):
        return {}
    page = manager._wire("observe", {}, generation)
    listing = manager.tabs.get(job_id)
    if (not listing or page.get("active_tab_id") != listing or page.get("native_modal")
            or page.get("document_url") != (job.get("public_source") or {}).get("url")):
        return {}
    current = page
    for tab in page.get("tabs", []):
        if tab["id"] in manager.tabs.values() or not re.search(r"\b(?:sign[ -]?in|log[ -]?in)\b", tab.get("label") or "", re.I):
            continue
        current = select_tab(manager, current, tab["id"], generation)
        if current.get("document_url") != saved["realm"]:
            continue
        controls = [item for item in current.get("controls", []) if item.get("web_content") and item.get("enabled")]
        if any(item.get("role") == "AXButton" and re.fullmatch(r"(?:sign|log)[ -]?in", (item.get("label") or "").strip(), re.I)
               for item in controls) and any(item.get("secure") or item.get("role") == "AXTextField" for item in controls):
            manager.tabs[job_id] = tab["id"]
            return current
    if current.get("active_tab_id") != listing:
        select_tab(manager, current, listing, generation)
    return {}


def open_public_tab(manager, page, generation, url, job_id):
    """Reuse an identified work tab or dispatch a single new public link and verify its destination."""
    if not manager.pending_open:
        listing, page = find_open_listing(manager, page, generation, job_id, url)
        if listing:
            return listing
        if existing := reuse_for_listing(manager, page, generation, url, job_id):
            return existing
        before = {tab["id"] for tab in page.get("tabs", [])}
        manager.pending_open = (job_id, url, before)
        manager.browser.open_public_url(url, generation)
    previous_job, previous_url, before = manager.pending_open
    if (previous_job, previous_url) != (job_id, url):
        raise BrowserError("Chrome is still opening the previous work tab. Check its destination first.")
    target = canonical_url(url)
    for _ in range(32):
        current = manager._wire("observe", {}, generation)
        if current.get("native_modal"):
            raise BrowserError("A dialog is open over Chrome. Close it before continuing to another posting.")
        selected = current.get("active_tab_id")
        added = {tab["id"] for tab in current.get("tabs", [])} - before
        if len(added) > 1:
            raise BrowserError("More than one new browser tab appeared. Show the intended application tab.")
        if selected and added == {selected} and canonical_url(current.get("document_url") or "") == target:
            manager.created_tabs[selected] = current["document_url"]
            manager.pending_open = None
            return current
        time.sleep(.2)
    raise BrowserError("Chrome did not show the requested posting in a new tab in the application window.")
