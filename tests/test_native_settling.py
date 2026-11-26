"""A transient native read can settle in place without repeating scroll input or accepting missing evidence."""

import unittest
from unittest.mock import Mock, patch
from applydesk.browser_bridge import BrowserError

from applydesk.native_page import _move_view, _record_readability, public_page
from test_native_research import viewport


class NativeSettlingTests(unittest.TestCase):
    def setUp(self):
        """All observations are synthetic; the native helper and browser are never started."""
        patch("applydesk.native_page.time.sleep").start()
        self.addCleanup(patch.stopall)
        self.initial = {**viewport("Initial viewport"), "pid": 42, "window_id": 88,
            "generation": 1, "active_tab_id": "tab-1"}
        self.unstable = [{**self.initial, "text": f"Changing visible fragment {index}", "snapshot_id": str(index)}
                         for index in range(6)]
        self.settled = {**self.initial, "text": "Complete visible requirements", "snapshot_id": "settled"}

    def test_second_observation_pass_recovers_without_another_scroll(self):
        """The exact current viewport becomes complete only after two successive quiet observations."""
        session = Mock()
        session.call.side_effect = [self.initial, {**self.initial, "url": ""}, *self.unstable[1:], *([self.settled] * 3)]
        result = _move_view(session, self.initial, scroll=True)
        self.assertEqual(result, self.settled)
        self.assertNotIn("render_unsettled", result)
        methods = [call.args[0] for call in session.call.call_args_list]
        self.assertEqual(methods, ["native_action", *(["native_observe"] * 9)])
        self.assertEqual(session.call.call_args_list[0].args[1]["action"], "scroll")

    def test_persistent_changes_still_report_incomplete_after_twelve_observations(self):
        """A continuously changing viewport cannot turn the extra wait into an unbounded or permissive scan."""
        samples = [{**self.initial, "text": str(index)} for index in range(12)]
        session = Mock()
        session.call.side_effect = [self.initial, *samples]
        result = _move_view(session, self.initial)
        self.assertTrue(result["render_unsettled"])
        self.assertTrue(_record_readability(result, set(), set()))
        self.assertEqual(session.call.call_count, 13)
        self.assertEqual(session.call.call_args_list[0].args[1]["key"], "home")

    def test_listing_rescans_only_same_tab_once_after_transient_rendering(self):
        """Never mix a prior partial scan with the retry or repeat input on an application form."""
        partial = {**self.initial, "text_truncated": True, "capture_diagnostics": {
            "hard_flags": ["render_unsettled"], "stop_reason": "bottom_reached"}}
        for listing, fresh, final, expected in (
                (True, self.initial, self.settled, self.settled), (True, self.initial, partial, partial),
                (True, {**self.initial, "active_tab_id": "other"}, self.settled, partial),
                (False, self.initial, self.settled, partial)):
            session = Mock()
            session.call.side_effect = [self.initial, fresh]
            with self.subTest(listing=listing, fresh=fresh), patch("applydesk.native_page.capture_page",
                    side_effect=[partial, final]) as scan:
                self.assertEqual(public_page(session, self.initial["url"], listing_only=listing), expected)
                self.assertEqual(scan.call_count, 2 if listing and fresh == self.initial else 1)

    def test_switched_tab_restarts_only_the_same_public_listing(self):
        error = BrowserError("The action changed application tabs. Observe the intended job again.")
        for restore in (self.initial, dict(self.initial, active_tab_id="another")):
            session = Mock()
            session.call.side_effect = [self.initial, restore]
            with patch("applydesk.native_page.capture_page", side_effect=[error, self.settled]) as scan:
                if restore == self.initial:
                    self.assertEqual(public_page(session, self.initial["url"], listing_only=True), self.settled)
                    self.assertEqual(scan.call_count, 2)
                else:
                    with self.assertRaises(BrowserError):
                        public_page(session, self.initial["url"], listing_only=True)

    def test_quiet_capped_or_unreadable_content_remains_blocked(self):
        """Stable rendering is independent of whether all visible evidence was actually readable."""
        partial = {"id": "n42", "web_content": True, "text_incomplete": True, "clipped": True}
        for change in ({"text_capped": True}, {"controls_truncated": True},
                       {"text_truncated": True, "elements": [partial]}):
            with self.subTest(change=change):
                page = {**self.settled, **change}
                session = Mock()
                session.call.side_effect = [self.initial, *self.unstable, *([page] * 3)]
                result = _move_view(session, self.initial)
                unreadable = set()
                self.assertTrue(_record_readability(result, unreadable, set()) or unreadable)
                self.assertEqual(result, page)

    def test_second_pass_cannot_mix_pages_windows_tabs_or_control_generations(self):
        """Changed identity aborts before the next settling sample, without echoing page URLs."""
        changes = ({"url": "https://another.test/private-page"}, {"pid": 99}, {"window_id": 99},
                   {"active_tab_id": "tab-2"}, {"generation": 2})
        for change in changes:
            with self.subTest(change=change):
                session = Mock()
                session.call.side_effect = [self.initial, *self.unstable, {**self.settled, **change}]
                with self.assertRaisesRegex(ValueError, "changed while its visible description was settling") as failure:
                    _move_view(session, self.initial, scroll=True)
                self.assertNotIn("private-page", str(failure.exception))
                self.assertEqual(session.call.call_count, 8)
