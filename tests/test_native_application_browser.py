"""Native workspace contracts use simulated windows and never touch the user's browser."""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from applydesk.native_browser import BrowserError
from applydesk.native_application_browser import NativeApplicationBrowser, normalize_observation
from applydesk.native_evidence import public_page
from native_window_fixture import NativeWindowFixture

SETTLE = NativeApplicationBrowser._settle


class ObservedLock:
    """Expose the exact moment a queued call reaches its lock after capturing the interruption epoch."""

    def __init__(self):
        self.lock, self.entered = threading.RLock(), threading.Event()

    def __enter__(self):
        self.entered.set()
        self.lock.acquire()
        return self

    def __exit__(self, *unused):
        self.lock.release()


class NativeApplicationBrowserTests(unittest.TestCase):
    def setUp(self):
        """DNS and native helper construction are replaced with deterministic local fixtures."""
        jobs = {key: {"id": key} for key in ("one", "two")}
        self.workspace = SimpleNamespace(store=SimpleNamespace(get=lambda kind, key: jobs.get(key)))
        self.factory = patch("applydesk.native_application_browser.NativeBrowser", side_effect=NativeWindowFixture).start()
        patch("applydesk.native_application_browser.public_job_url", side_effect=lambda value: value).start()
        patch.object(NativeApplicationBrowser, "_settle", lambda manager, generation: manager._wire("observe", {}, generation)).start()
        patch("applydesk.native_navigation.time.sleep").start()
        self.addCleanup(patch.stopall)
        self.manager = NativeApplicationBrowser(self.workspace)

    def test_finished_jobs_and_research_reuse_one_work_tab(self):
        """The public listing slot is reused without touching the personal tab or retaining old input authority."""
        self.manager.call("one", "open", {"url": "https://employer.test/one"})
        first = self.manager.tabs["one"]
        self.manager.browser.calls.clear()
        self.manager.call("one", "open", {"url": "https://employer.test/one"})
        self.assertEqual(self.manager.tabs["one"], first)
        self.assertFalse(any(method == "action" for method, _ in self.manager.browser.calls))
        self.manager.call("two", "open", {"url": "https://employer.test/two"})
        research = self.manager.register_research()
        self.manager.call(research, "open", {"url": "https://google.com/search?q=roles"})
        self.assertEqual(self.manager.tabs, {research: first})
        self.manager.browser.tabs.reverse()
        self.manager.call("one", "open", {"url": "https://employer.test/one/apply"})
        self.assertEqual(self.manager.tabs, {"one": first})
        self.assertEqual(self.manager.browser.active, self.manager.tabs["one"])
        self.assertEqual(self.factory.call_count, 1)
        self.assertEqual(len(self.manager.browser.tabs), 2)
        self.assertNotIn("tab-1", self.manager.tabs.values())

    def test_stop_show_and_explicit_resume_preserve_authority_boundary(self):
        """Showing a retained window does not re-enable typing, whereas a new authorized open does."""
        self.manager.call("one", "open", {"url": "https://employer.test/one"})
        self.manager.interrupt()
        page = self.manager.call("one", "show", {"bounds": {"left": 500, "top": 10, "width": 700, "height": 700}})
        self.assertFalse(page["bounds_applied"])
        self.assertTrue(self.manager.browser.paused)
        with self.assertRaisesRegex(BrowserError, "stopped"):
            self.manager.call("one", "native_action", {"snapshot_id": page["snapshot_id"], "action": "click", "element_id": "address"})
        self.manager.call("one", "open", {"url": "https://employer.test/one"})
        self.assertFalse(self.manager.browser.paused)

    def test_active_application_keeps_its_browser_until_parked(self):
        """A second employer or a research request cannot navigate away from an active application."""
        self.manager.call("one", "open", {"url": "https://employer.test/one"})
        jobs = {"one": {"status": "preparing"}, "two": {"status": "not_started"}}
        self.workspace.store.get = lambda kind, identity: jobs.get(identity)
        for identity in ("two", self.manager.register_research()):
            with self.assertRaisesRegex(BrowserError, "Finish the current application"):
                self.manager.call(identity, "open", {"url": "https://employer.test/other"})
        self.assertEqual(self.manager.browser.url, "https://employer.test/one")
        jobs["one"]["status"] = "submitted"
        self.manager.call("two", "open", {"url": "https://employer.test/two"})
        self.assertEqual(len(self.manager.browser.tabs), 2)

    def test_queued_open_is_invalidated_by_stop_even_before_first_helper(self):
        """A request waiting for the manager lock cannot silently resume after Stop."""
        finished, errors = threading.Event(), []
        observed = ObservedLock()
        self.manager.lock = observed
        def run():
            try:
                self.manager.call("one", "open", {"url": "https://employer.test/one"})
            except BrowserError as error:
                errors.append(error)
            finished.set()
        with self.manager.lock:
            observed.entered.clear()
            thread = threading.Thread(target=run)
            thread.start()
            self.assertTrue(observed.entered.wait(1))
            self.manager.interrupt()
        self.assertTrue(finished.wait(1))
        thread.join()
        self.assertEqual(len(errors), 1)
        self.assertEqual(self.factory.call_count, 0)

    def test_snapshot_selects_identified_tab_before_capturing_its_screenshot(self):
        """The screenshot belongs to the requested job, even when another retained tab is selected."""
        self.manager.call("one", "open", {"url": "https://employer.test/one"})
        self.manager.browser.active = "tab-1"
        page = self.manager.call("one", "native_observe", {"screenshot": True})
        self.assertEqual(page["screenshot"]["data"], self.manager.tabs["one"])
        self.assertTrue(page["native"])
        self.assertEqual(page["viewport"]["y"], 100)

    def test_observing_already_active_tab_reads_the_native_page_once(self):
        """A fresh observation already proves the retained tab is selected; no second AX scan is needed."""
        self.manager.call("one", "open", {"url": "https://employer.test/one"})
        self.manager.browser.calls.clear()
        page = self.manager.call("one", "native_observe")
        self.assertEqual([method for method, _ in self.manager.browser.calls], ["observe"])
        self.assertEqual(page["active_tab_id"], self.manager.tabs["one"])
        self.assertEqual(page["snapshot_id"], self.manager.observation["snapshot_id"])

    def test_observation_waits_for_actual_tab_switch_to_settle(self):
        """A delayed selected-tab update still needs repeated proof before returning another job's page."""
        self.manager.call("one", "open", {"url": "https://employer.test/one"})
        self.manager.browser.active = "tab-1"
        browser, original = self.manager.browser, self.manager.browser.call
        before = browser.page()
        def delayed_selection(method, params=None, expected_generation=None):
            result = original(method, params, expected_generation)
            return before if method == "action" else result
        browser.calls.clear()
        with patch.object(browser, "call", side_effect=delayed_selection):
            page = self.manager.call("one", "native_observe")
        self.assertEqual([method for method, _ in browser.calls], ["observe", "action", "observe", "observe"])
        self.assertEqual(page["active_tab_id"], self.manager.tabs["one"])
        self.assertEqual(browser.calls[1][1]["element_id"], self.manager.tabs["one"])

    def test_backgrounded_loading_tab_is_reselected_without_reopening_the_job(self):
        """When Chrome changes tabs during loading, use the original tab before returning the page."""
        target = "https://employer.test/one"
        self.manager.call("one", "open", {"url": target})
        browser, intended = self.manager.browser, self.manager.tabs["one"]
        def switch_during_load(generation):
            browser.active = next(tab for tab in browser.tabs if tab != intended)
            return self.manager._wire("observe", {}, generation)
        tab_count = len(browser.tabs)
        with patch.object(self.manager, "_settle", side_effect=switch_during_load):
            page = self.manager.call("one", "open", {"url": target})
        self.assertEqual((page["active_tab_id"], page["url"], len(browser.tabs)), (intended, target, tab_count))

    def test_closed_tab_never_falls_through_to_another_job(self):
        """A manually closed job tab must be explicitly reopened before input can resume."""
        self.manager.call("one", "open", {"url": "https://employer.test/one"})
        closed = self.manager.tabs["one"]
        self.manager.browser.tabs.remove(closed)
        self.manager.browser.active, self.manager.browser.url = "tab-1", "about:blank"
        with self.assertRaisesRegex(BrowserError, "tab is not visible"):
            self.manager.call("one", "native_observe")
        self.manager.call("one", "open", {"url": "https://employer.test/one"})
        self.assertNotEqual(self.manager.tabs["one"], closed)

    def test_helper_restart_retains_owned_window_but_discards_tab_guesses(self):
        """Native identities are helper-local, so restarting attaches the window then creates a fresh job tab."""
        self.manager.call("one", "open", {"url": "https://employer.test/one"})
        old = self.manager.browser
        old.ended = True
        self.manager.call("one", "open", {"url": "https://employer.test/one"})
        self.assertEqual(self.manager.owner, {"pid": 42, "window_id": 88})
        self.assertEqual(self.manager.browser.calls[0][0], "attach")
        self.assertTrue(any(method == "open_public_url" for method, _ in self.manager.browser.calls))
        self.assertEqual(old.tabs, ["tab-1", "tab-2"])

    def test_transient_window_loss_keeps_the_form_without_repeating_input(self):
        """An interruption after possible input allows observation only until the agent inspects the form."""
        page = self.manager.call("one", "open", {"url": "https://employer.test/one"})
        browser = self.manager.browser
        closed = BrowserError("Window disappeared")
        closed.code = "window_closed"
        with patch.object(browser, "call", side_effect=[closed, browser.page()]) as calls:
            with self.assertRaisesRegex(BrowserError, "whether the last action took effect"):
                self.manager.call("one", "native_action", {"snapshot_id": page["snapshot_id"],
                                  "action": "key", "key": "tab"})
        self.assertEqual([call.args[0] for call in calls.call_args_list], ["action", "observe"])
        self.assertTrue(self.manager.live())
        self.assertEqual(self.manager.call("one", "native_observe")["active_tab_id"], page["active_tab_id"])

    def test_closed_owned_window_clears_its_identity_before_explicit_reopen(self):
        """A missing owned window is never replaced by attaching an unrelated existing window."""
        self.manager.call("one", "open", {"url": "https://employer.test/one"})
        error = BrowserError("The owned window closed.")
        error.code = "window_closed"
        with patch.object(self.manager.browser, "call", side_effect=error):
            with self.assertRaises(BrowserError):
                self.manager.call("one", "show")
        self.assertIsNone(self.manager.owner)
        self.assertFalse(self.manager.live())
        self.manager.call("one", "open", {"url": "https://employer.test/one"})
        self.assertEqual(self.manager.browser.calls[0][0], "use_existing")

    def test_unknown_jobs_and_retired_platform_connections_cannot_open_browser(self):
        """Only known jobs and the backend-registered research sentinel can own tabs."""
        with self.assertRaises(InterruptedError):
            self.manager.call("invented", "open", {"url": "https://employer.test/one"})
        with self.assertRaisesRegex(BrowserError, "no longer used"):
            self.manager.call("one", "open", {}, application_source="linkedin")
        self.assertEqual(self.factory.call_count, 0)

    def test_open_uses_existing_chrome_after_previous_window_disappears_between_jobs(self):
        """An explicit new job opens normally after its first window attachment reports a closed window."""
        self.manager.call("one", "open", {"url": "https://employer.test/one"})
        old = self.manager.browser
        gone = BrowserError("The previous Chrome window has closed.")
        gone.code = "window_closed"
        with patch.object(old, "call", side_effect=gone):
            self.manager.call("two", "open", {"url": "https://employer.test/two"})
        self.assertTrue(old.ended)
        self.assertEqual(self.manager.browser.calls[0][0], "use_existing")
        self.assertEqual(self.manager.browser.url, "https://employer.test/two")
        self.assertNotIn("one", self.manager.tabs)

    def test_clicked_new_tab_is_adopted_but_preexisting_switch_is_not(self):
        """A normal target-blank employer link can continue without giving another job's tab to this job."""
        page = self.manager.call("one", "open", {"url": "https://employer.test/one"})
        original = self.manager.tabs["one"]
        page = self.manager.call("one", "native_action", {"snapshot_id": page["snapshot_id"],
            "action": "click", "element_id": "new-application-link"})
        self.assertNotEqual(self.manager.tabs["one"], original)
        self.assertNotIn(original, self.manager.browser.tabs)
        with self.assertRaisesRegex(BrowserError, "changed application tabs"):
            self.manager.call("one", "native_action", {"snapshot_id": page["snapshot_id"],
                "action": "click", "element_id": "tab-1"})
        self.assertNotEqual(self.manager.tabs["one"], original)

    def test_dialogs_controls_and_links_share_window_relative_geometry(self):
        """Chooser containment and screenshot points use one coordinate system and hide secure values."""
        page = NativeWindowFixture().page()
        page["dialogs"] = [{"id": "picker", "bounds": {"x": 700, "y": 220, "width": 300, "height": 200}}]
        page["controls"].extend([{"id": "secret", "secure": True, "value": "unreadable", "bounds": {"x": 710, "y": 230}},
            {"id": "notice", "role": "AXLink", "label": "Privacy", "url": "https://employer.test/privacy", "web_content": True}])
        normalized = normalize_observation(page)
        self.assertEqual(normalized["dialogs"][0]["bounds"]["x"], 100)
        self.assertEqual(normalized["elements"][1]["bounds"]["x"], 110)
        self.assertIsNone(normalized["elements"][1]["value"])
        self.assertEqual(normalized["links"][0]["id"], "notice")

    def test_opened_recruitment_notice_returns_to_the_original_form(self):
        """Linked terms can be inspected but never become the editable application."""
        form = self.manager.call("one", "open", {"url": "https://employer.test/apply"})
        self.manager.observation["controls"].append({"id": "new-application-link", "role": "AXLink",
            "web_content": True, "label": "Recruitment Notice", "url": "https://employer.test/notice"})
        notice = self.manager.call("one", "native_action", {"snapshot_id": form["snapshot_id"],
            "action": "click", "element_id": "new-application-link"})
        self.assertTrue(notice["reading_notice"])
        self.assertTrue(self.manager.call("one", "native_observe")["reading_notice"])
        with self.assertRaisesRegex(BrowserError, "Return to the application"):
            self.manager.call("one", "native_action", {"snapshot_id": notice["snapshot_id"],
                "action": "type", "text": "personal answer"})
        restored = self.manager.call("one", "return_from_notice")
        self.assertEqual(restored["active_tab_id"], form["active_tab_id"])
        self.assertEqual(restored["url"], form["url"])
        self.assertNotIn("reading_notice", restored)
        self.assertNotIn(notice["active_tab_id"], self.manager.browser.tabs)

    def test_observed_required_metadata_reaches_agent_without_defaulting_unknown(self):
        """Native true/false evidence survives both boundaries; an absent attribute remains unknown."""
        page = NativeWindowFixture().page()
        page["controls"] = [
            {"id": "major", "role": "AXComboBox", "label": "Field of study", "required": True},
            {"id": "optional", "role": "AXTextField", "label": "Additional information", "required": False},
            {"id": "unknown", "role": "AXTextField", "label": "Other field"}]
        exposed = public_page(normalize_observation(page))
        controls = {item["id"]: item for item in exposed["elements"]}
        self.assertIs(controls["major"]["required"], True)
        self.assertIs(controls["optional"]["required"], False)
        self.assertNotIn("required", controls["unknown"])
        self.assertNotIn("bounds", controls["major"])
        self.assertNotIn("window_id", exposed)

    def test_render_settling_waits_for_quiet_visible_text_and_honors_stop(self):
        """Generic settling has a minimum delay, observes visible content, and aborts while waiting."""
        self.manager._ensure_browser()
        self.manager._wire("use_existing", {}, 0)
        clock = [0.0]
        def advance(seconds):
            clock[0] += seconds
        with patch("applydesk.native_application_browser.time.monotonic", side_effect=lambda: clock[0]), \
             patch("applydesk.native_application_browser.time.sleep", side_effect=advance):
            result = SETTLE(self.manager, 0)
        self.assertEqual(result["text"], "Visible fixture listing")
        self.assertTrue(1.6 <= clock[0] < 8, clock[0])
        with patch("applydesk.native_application_browser.time.sleep", side_effect=lambda seconds: self.manager.interrupt()):
            with self.assertRaisesRegex(BrowserError, "interrupted"):
                SETTLE(self.manager, 0)
