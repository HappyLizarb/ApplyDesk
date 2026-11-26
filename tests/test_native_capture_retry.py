"""Viewport retry fixtures prove stale input is unsent and uncertain input is never repeated."""

import unittest
from unittest.mock import Mock, patch

from applydesk.browser_bridge import BrowserError
from applydesk.native_page import _viewport_action, capture_page, public_page
from test_native_research import viewport


class NativeCaptureRetryTests(unittest.TestCase):
    def setUp(self):
        """All page observations and actions are synthetic; no native browser is launched."""
        patch("applydesk.native_page.time.sleep").start()
        self.addCleanup(patch.stopall)
        self.initial = viewport("Loading requirements", snapshot_id="before")
        self.fresh = viewport("Loaded requirements", snapshot_id="after")
        self.stale = BrowserError("The visible page changed (text, controls). Observe again before acting.")
        self.stale.code = "stale_observation"

    def test_loading_text_retry_uses_fresh_snapshot_and_new_viewport_size(self):
        """A structural change before input can be reread without skipping content in a resized viewport."""
        self.fresh["viewport"]["height"] = 100
        session = Mock()
        session.call.side_effect = [self.stale, self.fresh, self.fresh]
        result = _viewport_action(session, self.initial, scroll=True)
        self.assertEqual(result, self.fresh)
        self.assertEqual(session.call.call_args_list[1].args, ("native_observe",))
        retried = session.call.call_args_list[2].args[1]
        self.assertEqual((retried["snapshot_id"], retried["delta_y"]), ("after", 70))

    def test_home_retries_only_known_pre_input_staleness(self):
        """Resetting a capture can recover a changed page snapshot without repeating uncertain keys."""
        session = Mock()
        session.call.side_effect = [self.stale, self.fresh, self.fresh]
        _viewport_action(session, self.initial)
        self.assertEqual(session.call.call_args_list[-1].args[1],
            {"snapshot_id": "after", "action": "key", "key": "home"})
        session.reset_mock()
        session.call.side_effect = BrowserError("Response failed after input.")
        with self.assertRaisesRegex(BrowserError, "after input"):
            _viewport_action(session, self.initial)
        self.assertEqual(session.call.call_count, 1)

    def test_form_link_opens_the_same_jobs_description_only_for_screening(self):
        """Reading requirements from a Lever form previously yielded an empty qualification list."""
        listing = "https://jobs.lever.co/example/12345678-1111-2222-3333-444444444444"
        session = Mock()
        session.call.return_value = {"native": False}
        public_page(session, listing + "/apply", listing_only=True)
        session.call.assert_called_with("open", {"url": listing})
        public_page(session, listing + "/apply")
        session.call.assert_called_with("open", {"url": listing + "/apply"})

    def test_failed_return_to_top_preserves_only_a_complete_same_tab_capture(self):
        """A cosmetic Home key cannot invalidate readable requirements or transfer them to another tab."""
        page = dict(self.fresh, pid=4, window_id=5, generation=6, active_tab_id="tab")
        for other in (False, True):
            session = Mock()
            session.call.return_value = {**page, "active_tab_id": "other" if other else "tab"}
            with self.subTest(other=other), patch("applydesk.native_page._move_view", side_effect=[page, page, page, self.stale]):
                if other:
                    with self.assertRaisesRegex(ValueError, "posting changed"):
                        capture_page(session, page)
                else:
                    result = capture_page(session, page)
                    self.assertFalse(result["text_truncated"])
                    self.assertEqual(result["text"], page["text"])

    def test_navigation_during_stale_reread_stops_before_any_second_input(self):
        """A retry cannot turn one listing capture into input on a different destination."""
        session = Mock()
        moved = {**self.fresh, "url": "https://employer.test/jobs/two"}
        session.call.side_effect = [self.stale, moved]
        with self.assertRaisesRegex(ValueError, "navigated"):
            _viewport_action(session, self.initial, scroll=True)
        self.assertEqual(session.call.call_count, 2)

    def test_repeated_pre_input_changes_are_bounded(self):
        """A continuously changing structure cannot cause an unbounded native action loop."""
        session = Mock()
        session.call.side_effect = [self.stale, self.fresh, self.stale, self.fresh, self.stale]
        with self.assertRaises(BrowserError):
            _viewport_action(session, self.initial, scroll=True)
        actions = [call for call in session.call.call_args_list if call.args[0] == "native_action"]
        self.assertEqual(len(actions), 3)
