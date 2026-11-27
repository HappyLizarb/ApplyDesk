"""Capture failure evidence stays useful without retaining page text, labels or field values."""

import hashlib
import json
import unittest
from applydesk.native_browser import BrowserError
from applydesk.native_page import HARD_FLAGS, _move_view, _record_readability, _viewport_action, capture_page, incomplete_capture_message, public_page
from applydesk.public_source import validate_public_page
from applydesk.web_research import WebResearch
from test_native_research import ViewportSession, viewport
from types import SimpleNamespace
from unittest.mock import Mock, patch


class NativeCaptureDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        """All viewport data is fictional; these checks never invoke Chrome, network or a model."""
        patch("applydesk.native_page.time.sleep").start()
        patch("applydesk.web_research.public_job_url", side_effect=lambda url: url).start()
        self.addCleanup(patch.stopall)
        self.partial = {"id": "n42", "role": "AXStaticText", "web_content": True,
            "text_incomplete": True, "clipped": True, "label": "private-label", "value": "private-value",
            "bounds": {"x": 12, "y": 160, "width": 350, "height": 40, "unexpected": "private-bounds"}}

    def test_unreadable_geometry_and_bottom_reason_exclude_page_content(self):
        """Reaching the bottom retains the actual unresolved control without leaking its partial contents."""
        pages = [viewport("private-page", elements=[self.partial], text_truncated=True), viewport("Footer")]
        result = capture_page(ViewportSession(pages), pages[0])
        diagnostics = result["capture_diagnostics"]
        self.assertTrue(result["text_truncated"])
        self.assertEqual(diagnostics["stop_reason"], "bottom_reached")
        self.assertEqual(diagnostics["hard_flags"], [])
        self.assertEqual(diagnostics["unreadable_controls"], [{"id": "n42", "role": "AXStaticText",
            "bounds": {"x": 12, "y": 160, "width": 350, "height": 40}, "clipped": True, "viewport": 1}])
        self.assertNotIn("private-", json.dumps(diagnostics))

    def test_recovered_control_disappears_but_earlier_hard_flags_remain(self):
        """Diagnostics follow existing readability rules instead of silently relaxing completeness."""
        whole = {**self.partial, "text_incomplete": False, "clipped": False, "value": "Complete paragraph"}
        pages = [viewport("Top", elements=[self.partial], text_truncated=True),
                 viewport("Complete paragraph", elements=[whole]), viewport("Footer")]
        complete = capture_page(ViewportSession(pages), pages[0])
        self.assertFalse(complete["text_truncated"])
        self.assertEqual(complete["capture_diagnostics"]["unreadable_controls"], [])
        for flag in HARD_FLAGS:
            with self.subTest(flag=flag):
                first = {**pages[0], flag: True}
                result = capture_page(ViewportSession([first, *pages[1:]]), first)
                self.assertTrue(result["text_truncated"])
                self.assertEqual(result["capture_diagnostics"]["hard_flags"], [flag])
                self.assertEqual(result["capture_diagnostics"]["unreadable_controls"], [])

    def test_limit_and_unattributed_truncation_reasons_are_distinct(self):
        """A bounded scan, text cap and missing native evidence remain distinguishable after failure."""
        cases = [(viewport("Top"), 1, "viewport_limit", []),
                 (viewport("x" * 60001), 32, "text_limit", []),
                 (viewport("Incomplete", text_truncated=True), 32, "bottom_reached", ["text_truncated_without_control"])]
        for page, limit, reason, flags in cases:
            with self.subTest(reason=reason):
                result = capture_page(ViewportSession([page]), page, limit=limit)
                self.assertTrue(result["text_truncated"])
                self.assertEqual(result["capture_diagnostics"]["stop_reason"], reason)
                self.assertEqual(result["capture_diagnostics"]["hard_flags"], flags)
                self.assertGreater(result["capture_diagnostics"]["viewports"], 0)

    def test_public_research_and_preparation_preserve_failure_metadata(self):
        """The caller receives the same diagnostic reason instead of losing it behind a generic pause."""
        text = "Example Co Engineer. " + "Visible public role requirements. " * 8
        page = viewport(text, title="Example Co Engineer", elements=[self.partial], text_truncated=True)
        result = capture_page(ViewportSession([page]), page)
        web = WebResearch(SimpleNamespace(w=Mock()))
        owner = ("current-turn",)
        web._binding = Mock(return_value=owner)
        capture = web._capture(result, owner)
        self.assertEqual(capture["capture_diagnostics"], result["capture_diagnostics"])
        stored = web.captures[capture["capture_id"]]
        self.assertEqual(stored["capture_diagnostics"], result["capture_diagnostics"])
        with self.assertRaisesRegex(ValueError, "Some visible text could not be read") as saving:
            web.save_job({"capture_id": capture["capture_id"]})
        source = {"url": page["url"], "company": "Example Co", "title": "Engineer", "description": text,
            "description_sha256": hashlib.sha256(text.encode()).hexdigest(), "captured_at": "2026-11-17", "complete": True}
        with self.assertRaisesRegex(ValueError, "Read the complete job description") as preparing:
            validate_public_page(source, result)
        for error in (saving.exception, preparing.exception):
            self.assertIn("Some visible text could not be read", str(error))
            self.assertNotIn("AXStaticText", str(error))
            self.assertNotIn("n42", str(error))
            self.assertNotIn('"x":12', str(error))
            self.assertNotIn("private-", str(error))
            self.assertNotIn(text, str(error))

    def test_product_message_explains_change_or_limit_without_dumping_diagnostics(self):
        """User-visible history remains concise while detailed capture evidence stays structured."""
        cases = [("bottom_reached", ["render_unsettled"], "The page was still changing while being read."),
                 ("viewport_limit", [], "The page could not be read completely within this scan."),
                 ("text_limit", [], "The page could not be read completely within this scan.")]
        for stop, flags, reason in cases:
            with self.subTest(stop=stop):
                page = {"capture_diagnostics": {"stop_reason": stop, "hard_flags": flags,
                    "viewports": 7, "unreadable_controls": [self.partial]}}
                self.assertEqual(incomplete_capture_message(page, "Read this job again."), "Read this job again. " + reason)


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
        session.call.assert_called_with("open", {"url": listing, "listing_only": True})
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
