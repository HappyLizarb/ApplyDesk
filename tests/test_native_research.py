"""Native public discovery integration uses visible-page fixtures, never Chrome or network."""

import copy
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from applydesk.application_session import ApplicationSession, status
from applydesk.native_application_browser import NativeApplicationBrowser
from applydesk.native_page import capture_page
from applydesk.storage import Store
from applydesk.web_research import WebResearch
from applydesk.workspace import Workspace
from native_window_fixture import NativeWindowFixture


def viewport(text, **changes):
    """Each fixture contains only the text and links visible in one short viewport."""
    return {"native": True, "url": "https://employer.test/jobs/one", "text": text,
        "snapshot_id": text, "viewport": {"height": 200, "width": 400}, "links": [], **changes}


class ViewportSession:
    """Wheel scrolling advances a known viewport sequence; observations never reveal hidden sections."""

    def __init__(self, pages):
        self.pages, self.index, self.calls = pages, 0, []

    def call(self, method, params=None):
        self.calls.append((method, params))
        if method == "native_action" and params.get("action") == "scroll":
            self.index = min(self.index + 1, len(self.pages) - 1)
        if method == "native_action" and params.get("key") == "home":
            self.index = 0
        return copy.deepcopy(self.pages[self.index])


class NativePageTests(unittest.TestCase):
    def setUp(self):
        """Native delays are tested elsewhere; these tests inspect capture content and page boundaries."""
        patch("applydesk.native_page.time.sleep").start()
        self.addCleanup(patch.stopall)

    def test_short_viewports_overlap_and_nonadjacent_repetition_is_not_the_end(self):
        """Repeated headings in later sections cannot hide the final requirements below them."""
        pages = [viewport("Title\nFirst"), viewport("First\nSecond"), viewport("Title\nFirst"),
                 viewport("Last requirements", links=[{"id": "end", "text": "Apply", "url": "https://employer.test/apply"}])]
        session = ViewportSession(pages)
        result = capture_page(session, pages[0])
        self.assertIn("First\nSecond", result["text"])
        self.assertIn("Last requirements", result["text"])
        self.assertFalse(result["text_truncated"])
        self.assertEqual(result["links"][0]["id"], "end")
        scrolls = [params for _, params in session.calls if params and params.get("action") == "scroll"]
        self.assertTrue(all(params["delta_y"] == 140 for params in scrolls))
        self.assertEqual(session.index, 0)

    def test_capture_limit_and_navigation_do_not_claim_complete_evidence(self):
        """A long page stays incomplete and a navigation abandons evidence from the former listing."""
        pages = [viewport("Section " + str(index)) for index in range(8)]
        self.assertTrue(capture_page(ViewportSession(pages), pages[0], limit=3)["text_truncated"])
        moved = [pages[0], viewport("Another listing", url="https://employer.test/jobs/two")]
        with self.assertRaisesRegex(ValueError, "navigated"):
            capture_page(ViewportSession(moved), moved[0])

    def test_retained_page_at_bottom_is_read_from_top_and_left_at_top(self):
        """Reloading a retained page cannot silently omit the initial role and qualification paragraphs."""
        pages = [viewport("Role and degree requirements"), viewport("Experience requirements"), viewport("Footer")]
        session = ViewportSession(pages)
        session.index = 2
        result = capture_page(session, pages[2])
        self.assertTrue(result["text"].startswith("Role and degree requirements"))
        self.assertIn("Experience requirements", result["text"])
        self.assertFalse(result["text_truncated"])
        self.assertEqual(session.calls[0][1]["key"], "home")
        self.assertEqual(session.index, 0)


class NativeResearchIntegrationTests(unittest.TestCase):
    def setUp(self):
        """Use real store and ApplicationSession ownership with a synthetic native helper only."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.addCleanup(self.store.close)
        self.w = SimpleNamespace(store=self.store, sessions={}, lock=threading.RLock())
        self.w.application_browser = NativeApplicationBrowser(self.w)
        self.engine = SimpleNamespace(w=self.w, check_active=Mock(), active={"message": {"role": "user", "id": "turn"}},
            history=SimpleNamespace(version=1, since=""), accounts=SimpleNamespace(generation=1, state=lambda: {"active": "fixture"}))
        self.factory = patch("applydesk.native_application_browser.NativeBrowser", side_effect=NativeWindowFixture).start()
        patch("applydesk.native_application_browser.public_job_url", side_effect=lambda url: url).start()
        patch("applydesk.web_research.public_job_url", side_effect=lambda url: url).start()
        patch.object(NativeApplicationBrowser, "_settle", lambda manager, generation: manager._wire("observe", {}, generation)).start()
        patch("applydesk.native_page.time.sleep").start()
        self.addCleanup(patch.stopall)

    def test_research_reuses_visible_tab_and_cached_show_after_agent_stops(self):
        """Public reads keep one inspectable tab and cannot silently create a standalone headless browser."""
        research = WebResearch(self.engine)
        research.read_page("https://google.com/search?q=hardware")
        manager = self.w.application_browser
        first = manager.tabs[manager.research_id]
        research.read_page("https://employer.test/jobs/one")
        self.assertEqual(manager.tabs[manager.research_id], first)
        self.assertEqual(self.factory.call_count, 1)
        self.assertTrue(manager.browser.paused)
        self.assertEqual(status(self.w)["job_id"], manager.research_id)
        self.engine.check_active.side_effect = InterruptedError("Stopped")
        result = self.w.sessions[manager.research_id].call("show")
        self.assertEqual(result["active_tab_id"], manager.tabs[manager.research_id])
        self.assertTrue(manager.browser.paused)

    def test_cancel_reaches_manager_before_any_browser_exists(self):
        """A queued research open is revoked even during first-helper startup."""
        session = ApplicationSession(self.engine, research=True)
        self.assertIsNone(session.manager.browser)
        session.cancel()
        self.assertEqual(self.w.application_browser.generation, 1)

    def test_workspace_shutdown_parks_native_tabs_instead_of_deleting_them(self):
        """App shutdown preserves the same tabs the user can still inspect in ordinary Chrome."""
        session = Mock()
        workspace = SimpleNamespace(orchestrator=Mock(), recommendations=Mock(), store=Mock(),
            sessions={"one": session}, application_browser=Mock(native=True))
        session.manager = workspace.application_browser
        Workspace.close(workspace)
        session.park.assert_called_once()
        session.close.assert_not_called()
        workspace.application_browser.close.assert_called_once()
