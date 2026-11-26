"""Exercise window acquisition from job opens without touching a user's browser."""

import unittest
from itertools import count
from types import SimpleNamespace
from unittest.mock import patch

from applydesk.browser_bridge import BrowserError
from applydesk.native_application_browser import NativeApplicationBrowser
from applydesk.native_window_recovery import acquire_window, confirmed_empty
from native_window_fixture import NativeWindowFixture


class WindowFixture(NativeWindowFixture):
    """The OS may expose a new window late or refuse to identify an existing one."""

    def __init__(self):
        super().__init__()
        self.available, self.launches, self.delay = False, 0, 0

    def call(self, method, params=None, expected_generation=None):
        """A delayed window becomes identifiable after a chosen number of attachment probes."""
        if method == "use_existing" and (not self.available or self.delay):
            self.delay = max(0, self.delay - 1)
            error = BrowserError("The regular window is unavailable.")
            error.code = "window_unavailable"
            raise error
        return super().call(method, params, expected_generation)

    def open_public_url(self, url, generation):
        """Count window-opening requests separately from ordinary employer navigation."""
        if url == "about:blank":
            self.launches += 1
            self.available = True
            return
        super().open_public_url(url, generation)


class WindowRecoveryTests(unittest.TestCase):
    def setUp(self):
        """Run acquisition through the same manager shared by applications and research."""
        self.chrome = WindowFixture()
        self.manager = NativeApplicationBrowser(SimpleNamespace(store={"job": {"id": "one"}}))
        self.manager.browser = self.chrome
        self.inventory = patch("applydesk.native_window_recovery.confirmed_empty", return_value=True).start()
        patch("applydesk.native_application_browser.public_job_url", side_effect=lambda url: url).start()
        patch.object(NativeApplicationBrowser, "_settle", lambda m, g: m._wire("observe", {}, g)).start()
        patch("applydesk.native_window_recovery.time.sleep").start()
        self.addCleanup(patch.stopall)

    def test_one_launch_then_reuse_for_jobs_and_research(self):
        """A slow first appearance and later opens must all use the same regular window."""
        self.chrome.delay = 3
        url = {"url": "https://employer.test/one"}
        self.manager.call("one", "open", url)
        self.manager.call("one", "open", url)
        self.manager.call(self.manager.register_research(), "open", {"url": "https://employer.test/two"})
        self.assertEqual((self.chrome.launches, self.inventory.call_count), (1, 1))
        self.assertEqual(list(self.manager.tabs), [self.manager.research_id])

    def test_new_job_reuses_visible_window_after_retained_one_goes_offscreen(self):
        """Reacquire through the native helper before new navigation without replaying an action."""
        url = {"url": "https://employer.test/one"}
        self.chrome.available = True
        self.manager.call("one", "open", url)
        replacement = WindowFixture()
        replacement.available = True
        error = BrowserError("Make the owned Chrome window visible before continuing.")
        error.code = "window_offscreen"
        with patch.object(self.chrome, "call", side_effect=error) as previous, patch(
                "applydesk.native_application_browser.NativeBrowser", return_value=replacement):
            self.manager.call(self.manager.register_research(), "open", {"url": "https://employer.test/two"})
        self.assertEqual(previous.call_args.args[0], "attach")
        self.assertTrue(self.chrome.ended)
        self.assertNotIn("one", self.manager.tabs)
        self.assertEqual(replacement.calls[0][0], "use_existing")
        self.assertEqual(replacement.launches, 0)

    def test_reacquisition_waits_for_a_transition_and_stops_if_it_persists(self):
        """After losing a window, retry only presentation during a brief offscreen transition."""
        error = BrowserError("Make the owned Chrome window visible before continuing.")
        error.code = "window_offscreen"
        page = self.chrome.page()
        with patch.object(self.chrome, "call", side_effect=[error, page]) as probe:
            self.assertEqual(acquire_window(self.manager, 0), (page, 0))
        self.assertEqual([call.args[0] for call in probe.call_args_list], ["use_existing"] * 2)
        self.manager.owner = None
        with patch.object(self.chrome, "call", side_effect=error) as probe, patch(
                "applydesk.native_window_recovery.time.monotonic", side_effect=[0, 0.1, 20]):
            with self.assertRaisesRegex(BrowserError, "Make the owned Chrome window visible"):
                acquire_window(self.manager, 0)
        self.assertEqual([call.args[0] for call in probe.call_args_list], ["use_existing"] * 2)
        self.inventory.assert_not_called()

    def test_unknown_inventory_or_existing_window_never_launches(self):
        """Hidden windows and uncertain counts cannot authorize a launch."""
        self.inventory.return_value = False
        with self.assertRaisesRegex(BrowserError, "unavailable"):
            self.manager.call("one", "open", {"url": "https://employer.test/one"})
        self.chrome.available = True
        self.manager.call("one", "open", {"url": "https://employer.test/one"})
        self.assertEqual((self.chrome.launches, self.inventory.call_count), (0, 1))

    def test_timeout_retains_launch_across_jobs_until_window_is_bound(self):
        """After an uncertain launch, a second job can only attach, even following a timeout."""
        url = {"url": "https://employer.test/one"}
        self.chrome.delay = 9
        with patch("applydesk.native_window_recovery.time.monotonic", side_effect=count(0, 13)):
            with self.assertRaisesRegex(BrowserError, "not shown"):
                self.manager.call("one", "open", url)
        with self.assertRaisesRegex(BrowserError, "asked to open"):
            self.manager.call(self.manager.register_research(), "open", url)
        self.chrome.delay = 0
        self.manager.call("one", "open", url)
        self.assertEqual(self.chrome.launches, 1)
        self.assertFalse(self.manager.window_launch_pending)

    def test_stop_before_dispatch_allows_next_explicit_request(self):
        """An interruption before a launch must not leave a phantom pending window."""
        url = {"url": "https://employer.test/one"}
        with patch.object(self.chrome, "open_public_url", side_effect=BrowserError("interrupted")):
            with self.assertRaisesRegex(BrowserError, "interrupted"):
                self.manager.call("one", "open", url)
        self.manager.call("one", "open", url)
        self.assertEqual(self.chrome.launches, 1)

    def test_inventory_requires_permissions_and_successful_empty_result(self):
        """Errors, permission denials and unsuccessful probes never count as zero windows."""
        with patch.object(self.chrome, "call") as status, patch("applydesk.native_window_recovery.subprocess.run") as probe:
            status.return_value = {"accessibility": False, "input": True, "chrome_pid": 2}
            self.assertFalse(confirmed_empty(self.manager, 0))
            probe.assert_not_called()
            status.return_value["accessibility"] = True
            for code, output, expected in [(0, "occupied", False), (0, "unknown", False), (1, "empty", False), (0, "empty", True)]:
                probe.return_value = SimpleNamespace(returncode=code, stdout=output)
                self.assertEqual(confirmed_empty(self.manager, 0), expected)
