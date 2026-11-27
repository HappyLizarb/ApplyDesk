"""One owned ordinary Chrome window, with native tab identities for jobs and public research."""

import threading
import time
import uuid

from .application_session import validate_bounds
from .native_browser import BrowserError, NativeBrowser
from .native_tab_workspace import check_current_job, forget_tab, resume_account_tab, track_action_tab
from .native_window_recovery import acquire_window, click_browser_tab
from .native_navigation import (_password_suggestion_close, adopt_clicked_tab, check_missing_window, clear_upload_picker, open_job_tab,
                                restore_job_tab, resume_visible_draft, return_from_notice, track_upload_picker, wait_for_tab)
from .profile import public_job_url
from .discovery import require_public_listing


class NativeApplicationBrowser:
    """Keep native window ownership private while exposing the existing application-session interface."""

    native = True

    def __init__(self, workspace):
        self.w, self.browser, self.owner = workspace, None, None
        self.workspace_id, self.last_job_id, self.active_job = "", None, None
        self.jobs, self.tabs, self.redactions = [], {}, set()
        self.upload_picker, self.created_tabs, self.pending_open = None, {}, None
        self.window_launch_pending, self.drafts, self.notice_forms = False, {}, {}
        self.research_id, self.research_registered, self.observation = "research-" + uuid.uuid4().hex, False, {}
        self.lock, self.generation, self.command_generation = threading.RLock(), 0, 0

    def register_research(self):
        """Only backend research sessions can obtain the reusable non-job tab identity."""
        with self.lock:
            self.research_registered = True
            return self.research_id

    def live(self):
        """A status read never creates a browser, raises a window or restores input authority."""
        browser = self.browser
        return bool(self.owner and browser and not browser.ended and browser.process
                    and browser.process.poll() is None)

    def _ensure_browser(self):
        """A restarted helper reuses private window ownership but never guesses old tab identities."""
        if self.browser and not self.browser.ended and (not self.browser.process or self.browser.process.poll() is None):
            return
        if self.browser:
            self.browser.close()
        self.browser = NativeBrowser(redactions=self.redactions)
        self.workspace_id = uuid.uuid4().hex
        self.tabs, self.jobs, self.observation = {}, [], {}
        self.drafts, self.notice_forms = {}, {}
        self.upload_picker, self.created_tabs, self.pending_open = None, {}, None

    def call(self, job_id, method, params=None, application_source=None, checkpoint=None):
        """Every operation stays within its registered job and the privately owned native window."""
        if application_source:
            raise BrowserError("Job-platform connections are no longer used. Open the public employer listing.")
        expected = self.generation
        if method == "pause":
            self.interrupt()
            return {"paused": True}
        with self.lock:
            if checkpoint:
                checkpoint()
            self._check_job(job_id)
            if expected != self.generation:
                raise BrowserError("The native browser operation was interrupted before it started.")
            if method in {"open", "resume_visible"}:
                self._ensure_browser()
            elif not self.live():
                raise BrowserError("The application window is unavailable. Open the application again.")
            generation = self.browser.interrupt_generation
            self.command_generation = expected
            self.active_job = job_id
            try:
                if method == "open":
                    result = self._open(job_id, params or {}, generation)
                else:
                    result = self._operate(job_id, method, params or {}, generation)
                notice = self.notice_forms.get(job_id)
                if notice and result.get("active_tab_id") == notice[2]:
                    result["reading_notice"] = True
                if checkpoint and method != "show":
                    checkpoint()
                return result
            finally:
                self.active_job = None

    def _check_job(self, job_id):
        """Removed jobs and model-invented research identifiers cannot acquire native browser control."""
        check_current_job(self, job_id)
        if self.research_registered and job_id == self.research_id:
            return
        job = self.w.store.get("job", job_id)
        if not job or job.get("removed_at"):
            raise InterruptedError("This application was removed. No browser actions are allowed.")

    def _wire(self, method, params, generation):
        """The native helper receives owner identity exclusively from backend state."""
        if self.command_generation != self.generation:
            raise BrowserError("The native browser operation was interrupted.")
        bound = {**(self.owner or {}), **params}
        try:
            result = self.browser.call(method, bound, expected_generation=generation)
        except BrowserError as error:
            result = check_missing_window(self, error, method, params, generation)
            if result is None:
                raise
        if "pid" in result and "window_id" in result:
            identity = {key: result[key] for key in ("pid", "window_id")}
            if self.owner and identity != self.owner:
                raise BrowserError("The native browser returned a different application window.")
            self.owner, self.observation = identity, result
        return result

    def _action(self, action, observation, generation):
        """Internal navigation uses the same fresh native snapshot and Stop fence as model actions."""
        parameters = {**action, "snapshot_id": observation["snapshot_id"], "generation": observation["generation"]}
        return self._wire("action", parameters, generation)

    def _open(self, job_id, params, generation):
        """Reuse the job’s rendered posting, or open the public URL in the same ordinary Chrome window."""
        target = public_job_url(require_public_listing(params.get("url", "")))
        page, generation = acquire_window(self, generation)
        page = clear_upload_picker(self, page, generation)
        self.last_job_id = job_id
        page = wait_for_tab(self, page, generation)
        open_job_tab(self, job_id, page, generation, target, params.get("listing_only") is True)
        if job_id not in self.jobs:
            self.jobs.append(job_id)
        settled = self._settle(generation)
        if settled.get("active_tab_id") != self.tabs[job_id]:
            # Chrome can surface another tab as loading ends. Restore the original tab by its observed identity.
            settled = self._select(job_id, settled, generation)
        return {**normalize_observation(settled), "has_open_draft": job_id in self.drafts}

    def _settle(self, generation):
        """Wait briefly for visible page content, stopping once repeated observations become quiet."""
        started, stable_since, prior, page = time.monotonic(), None, None, {}
        while time.monotonic() - started < 8:
            if self.command_generation != self.generation or generation != self.browser.interrupt_generation:
                raise BrowserError("The native browser operation was interrupted.")
            time.sleep(0.2)
            if time.monotonic() - started < 1:
                continue
            page = self._wire("observe", {}, generation)
            signature = (page.get("url"), page.get("text"), page.get("controls_truncated"))
            now = time.monotonic()
            stable_since = stable_since if signature == prior else now
            prior = signature
            if page.get("text", "").strip() and now - stable_since >= 0.6:
                return page
        return page

    def _find_tab(self, identity, page, generation):
        """Reobserve the tab strip across a brief repaint before deciding whether an owned tab disappeared."""
        for _ in range(5):
            if any(tab["id"] == identity for tab in page.get("tabs", [])):
                break
            time.sleep(0.1)
            page = self._wire("observe", {}, generation)
        return page

    def _select(self, job_id, page, generation):
        """Select only an observed job tab and keep clicks out of a covering native dialog."""
        identity = self.tabs.get(job_id)
        if not identity:
            raise BrowserError("Open this application before using its Chrome tab.")
        page = self._find_tab(identity, page, generation)
        if not any(tab["id"] == identity for tab in page.get("tabs", [])):
            raise BrowserError("The application's tab is not visible in its Chrome window. Reopen the application to continue.")
        if page.get("active_tab_id") == identity:
            return page
        if page.get("native_modal"):
            raise BrowserError("A native dialog covers the application tab.")
        page = click_browser_tab(self, page, identity, generation)
        return wait_for_tab(self, page, generation, expected=identity)

    def _operate(self, job_id, method, params, generation):
        """Presentation remains available after Stop; page input requires its exact current tab."""
        if method in {"resume_visible", "resume_account"}:
            page = {"resume_visible": resume_visible_draft, "resume_account": resume_account_tab}[method](self, job_id, generation)
            return normalize_observation(page) if page else {}
        if method == "return_from_notice":
            return normalize_observation(return_from_notice(self, job_id, self._wire("observe", {}, generation), generation))
        if method == "resume_form":
            return normalize_observation(restore_job_tab(self, job_id, self._wire("observe", {}, generation), generation))
        if method == "show":
            if params.get("bounds"):
                validate_bounds(params["bounds"])
            page = self._wire("show", {}, generation)
            active = next((key for key, tab in self.tabs.items() if tab == page.get("active_tab_id")), None)
            if not active:
                return {"shown": True, "job_id": None, "bounds_applied": False}
            return {**normalize_observation(page), "job_id": active, "bounds_applied": False}
        if method in {"snapshot", "native_observe"}:
            page = self._select(job_id, self._wire("observe", {}, generation), generation)
            if page.get("native_modal") and _password_suggestion_close(page):
                page = clear_upload_picker(self, page, generation)
            if params.get("screenshot") is True:
                page = self._wire("observe", {"screenshot": True}, generation)
                if page.get("active_tab_id") != self.tabs[job_id]:
                    raise BrowserError("The active tab changed while capturing the application.")
            self.last_job_id = job_id
            return normalize_observation(page)
        if method != "native_action":
            raise BrowserError("Use native observations and actions in this application window.")
        return self._native_action(job_id, params, generation)

    def _native_action(self, job_id, params, generation):
        """Dispatch input only in the owned tab, limiting linked notices to reading."""
        page = self.observation
        if page.get("active_tab_id") != self.tabs.get(job_id) or params.get("snapshot_id") != page.get("snapshot_id"):
            raise BrowserError("Observe the current application tab before acting.")
        action = {key: value for key, value in params.items() if key not in {"pid", "window_id", "generation"}}
        if action.get("action") == "key" and isinstance(action.get("key"), str):
            key_name = action["key"].casefold()
            if key_name in {"arrowup", "arrowdown", "arrowleft", "arrowright"}:
                action["key"] = key_name.removeprefix("arrow")
        if job_id in self.notice_forms and action.get("action") != "scroll":
            raise BrowserError("Return to the application before interacting with the form.")
        if any(tab.get("id") == action.get("element_id") for tab in page.get("tabs", [])):
            raise BrowserError("The action changed application tabs. Use the intended application tab.")
        if "point" in action:
            point = action.pop("point")
            if not isinstance(point, dict) or set(point) != {"x", "y"}:
                raise BrowserError("A native point needs window-relative x and y coordinates.")
            action.update(x=point["x"], y=point["y"])
        self.upload_picker = track_upload_picker(page, action, self.upload_picker)
        result = self._action(action, page, generation)
        adopt_clicked_tab(self, job_id, action, page, result)
        result = track_action_tab(self, job_id, page, result, generation, action)
        return normalize_observation(result)

    def park(self, job_id):
        """A finished job remains visible while its input lease is revoked."""
        if self.live() and job_id in self.tabs:
            self.interrupt()

    def interrupt(self):
        """Stop reaches the helper immediately without waiting for this manager's operation lock."""
        self.generation += 1
        if self.browser:
            self.browser.interrupt()

    def close_job(self, job_id):
        """Deleting a row closes only its identified tab and leaves other job tabs retained."""
        if self.browser and self.active_job == job_id:
            self.interrupt()
        with self.lock:
            self.drafts.pop(job_id, None)
            self.notice_forms.pop(job_id, None)
            if not self.live() or job_id not in self.tabs:
                return
            generation = self.browser.interrupt_generation
            self.command_generation = self.generation
            page = self._wire("attach", {}, generation)
            page = self._select(job_id, page, generation)
            tab = self.tabs[job_id]
            try:
                if self.created_tabs.get(tab) and self.created_tabs[tab] == page.get("document_url"):
                    self._action({"action": "key", "key": "cmd+w"}, page, generation)
            finally:
                forget_tab(self, tab)
                self.jobs = [identity for identity in self.jobs if identity != job_id]
                self.last_job_id = self.jobs[-1] if self.jobs else None
                self.interrupt()

    def close(self):
        """Closing ApplyDesk ends native control while preserving the ordinary Chrome window."""
        self.interrupt()
        with self.lock:
            if self.browser:
                self.browser.close()


def normalize_observation(page):
    """Use window-relative point geometry and expose only visible native link evidence."""
    bounds = page.get("bounds", {})
    result = {**page, "native": True, "url": page.get("url", ""), "title": page.get("title", ""), "text": page.get("text", "")}
    elements = []
    for control in page.get("controls", []):
        rect = control.get("bounds", {})
        relative = {**rect, "x": rect.get("x", 0) - bounds.get("x", 0), "y": rect.get("y", 0) - bounds.get("y", 0)}
        elements.append({**control, "value": None if control.get("secure") else control.get("value"), "bounds": relative})
    result["elements"] = elements
    result["links"] = [{"id": item["id"], "text": item.get("label", ""), "url": item["url"]}
                       for item in elements if item.get("role") == "AXLink" and item.get("url") and item.get("web_content", True)]
    result["dialogs"] = [{**item, "bounds": {**item.get("bounds", {}),
        "x": item.get("bounds", {}).get("x", 0) - bounds.get("x", 0),
        "y": item.get("bounds", {}).get("y", 0) - bounds.get("y", 0)}} for item in page.get("dialogs", [])]
    viewport = page.get("viewport") or page.get("web_bounds")
    if viewport:
        result["viewport"] = {**viewport, "x": viewport.get("x", 0) - bounds.get("x", 0), "y": viewport.get("y", 0) - bounds.get("y", 0)}
    result.pop("controls", None)
    result["elements_truncated"] = bool(page.get("controls_truncated"))
    return result
