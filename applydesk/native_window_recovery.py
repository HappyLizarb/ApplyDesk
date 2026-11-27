"""Acquire an ordinary Chrome window once, including when its last window was closed."""

import subprocess
import time

from . import PROGRAM_ROOT
from .native_browser import BrowserError, NativeBrowser
from .public_source import matches_saved_requisition_form
from .recommendation_source import same_workday_application
from .lifecycle import interface_process


def observe_application(session, job, screenshot=False):
    """Recover a lost read by rebinding a verified saved draft before any more form input."""
    try:
        return session.call("native_observe", {"screenshot": screenshot})
    except BrowserError as error:
        lost = getattr(error, "code", None) == "window_closed" or str(error) in {
            "The application window is unavailable. Open the application again.",
            "The application's tab is not visible in its Chrome window. Reopen the application to continue.",
            "The native browser did not respond. Reopen its application window.",
        }
        if not lost or not job.get("live_url"):
            raise
        form = session.call("resume_visible")
        source = job.get("public_source") or {}
        workday = same_workday_application(source.get("url", ""), (form or {}).get("url", ""))
        if not form or form.get("native_modal") or not (workday or matches_saved_requisition_form(job, form)):
            raise error
        return {**form, "recovered_draft": True}


def click_browser_tab(manager, page, identity, generation):
    """Resume a retained tab whose title also names an account or submission button."""
    click = {"action": "click", "element_id": identity}
    try:
        return manager._action(click, page, generation)
    except BrowserError as error:
        if str(error) != "This control requires the protected account or reviewed submission operation.":
            raise
    # A rejected click refreshed the native snapshot. Rebind only the exact Chrome tab, never web content.
    binding = ("pid", "window_id", "generation", "active_tab_id", "document_url")
    for attempt in range(3):
        fresh = manager._wire("observe", {}, generation)
        tabs = [tab for tab in fresh.get("tabs", []) if tab.get("id") == identity]
        controls = [item for item in fresh.get("controls", []) if item.get("id") == identity]
        if (fresh.get("native_modal") or any(fresh.get(k) != page.get(k) for k in binding)
                or len(tabs) != 1 or len(controls) != 1 or controls[0].get("role") != "AXRadioButton"
                or controls[0].get("web_content") is not False):
            raise BrowserError("The intended Chrome tab could not be verified.")
        try:
            return manager._action({**click, "final_permit": True}, fresh, generation)
        except BrowserError as error:
            if getattr(error, "code", None) != "stale_observation" or attempt == 2:
                raise


def restore_visible_owner(manager, generation):
    """Present an existing Chrome window, then retain authority only if the original window returned."""
    owner = manager.owner
    if not owner or generation != manager.browser.interrupt_generation or manager.command_generation != manager.generation:
        return
    presenter = NativeBrowser(launcher=manager.browser.launcher)
    try:
        page = presenter.call("use_existing")
        return all(page.get(key) == value for key, value in owner.items())
    finally:
        presenter.close()


def confirmed_empty(manager, generation):
    """Require an unlocked desktop, native permissions and a conclusive window inventory."""
    status = manager.browser.call("status", expected_generation=generation)
    if not (status.get("accessibility") and status.get("input")):
        return False
    pid = status.get("chrome_pid")
    if type(pid) is not int or pid < 0:
        return False
    try:
        interface = 0 if pid else interface_process(manager.w.root) or 0
        probe = PROGRAM_ROOT / "native/chrome-window-inventory.cjs"
        result = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", str(probe), str(pid), str(interface)],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "empty"


def acquire_window(manager, generation):
    """Reuse the bound window or acquire another after a confirmed close, before any form input."""
    if manager.owner:
        try:
            return manager._wire("attach", {}, generation), generation
        except BrowserError as error:
            if getattr(error, "code", None) not in {"window_closed", "window_offscreen"}:
                raise
            manager._ensure_browser()
            generation = manager.browser.interrupt_generation
    try:
        page = wait_for_window(manager, generation)
    except BrowserError as error:
        if getattr(error, "code", None) != "window_unavailable":
            raise
        if manager.window_launch_pending:
            raise BrowserError("Chrome was asked to open a window. Bring that window forward to continue.") from error
        if not confirmed_empty(manager, generation):
            raise
        try:
            manager.browser.open_public_url("about:blank", generation)
        except BrowserError as launch_error:
            manager.window_launch_pending = getattr(launch_error, "code", None) == "navigation_dispatched"
            raise
        manager.window_launch_pending = True
        page = wait_for_window(manager, generation, launched=True)
    manager.window_launch_pending = False
    return page, generation


def wait_for_window(manager, generation, *, launched=False):
    """Allow a brief presentation transition or a longer startup without replaying any input."""
    deadline = time.monotonic() + (12 if launched else 1)
    while True:
        try:
            return manager._wire("use_existing", {}, generation)
        except BrowserError as error:
            code = getattr(error, "code", None)
            starting = launched and code == "window_unavailable"
            if code != "window_offscreen" and not starting:
                raise
            if time.monotonic() >= deadline:
                if starting:
                    raise BrowserError("Chrome has not shown its requested window yet. Bring it forward to continue.") from error
                raise
        time.sleep(0.25)
