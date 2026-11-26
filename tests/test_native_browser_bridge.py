"""Transport cancellation and privacy tests use only a temporary Python JSONL helper."""

from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from applydesk.browser_bridge import BrowserError
from applydesk.native_browser import NativeBrowser, command_timeout


class NativeBrowserBridgeTests(unittest.TestCase):
    def setUp(self):
        """The executable fixture accepts the native protocol without invoking any OS browser."""
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        launcher = Path(self.folder.name) / "ApplyDesk"
        fixture = Path(__file__).with_name("native_browser_fixture.py").read_text()
        launcher.write_text(f"#!{sys.executable}\n" + fixture)
        launcher.chmod(0o700)
        self.browser = NativeBrowser(launcher)
        self.addCleanup(self.browser.close)

    def test_native_only_launch_and_show_does_not_resume_after_stop(self):
        """No remote-debugging flags or Chrome process are involved, even after showing a stopped window."""
        self.assertEqual(self.browser.call("use_existing")["pid"], 42)
        self.assertEqual(self.browser.process.args[1:], ["--native-browser"])
        self.browser.interrupt()
        self.assertEqual(self.browser.call("show")["method"], "show")
        with self.assertRaisesRegex(BrowserError, "stopped"):
            self.browser.call("action", {"action": "click"})
        self.browser.call("attach", {"pid": 42, "window_id": 88})
        self.assertEqual(self.browser.call("action", {"action": "click"})["method"], "action")

    def test_private_values_and_errors_are_redacted_with_match_proof(self):
        """An approved chooser path is provably typed without entering the agent's response."""
        result = self.browser.call("action", {"action": "type", "text": "/private/fixture.pdf", "private": True})
        self.assertTrue(result["private_value_matches"])
        self.assertNotIn("/private/fixture.pdf", str(result))
        later = self.browser.call("observe", {"text": "echo /private/fixture.pdf"})
        self.assertEqual(later["text"], "echo [redacted]")
        with self.assertRaisesRegex(BrowserError, "private browser action") as caught:
            self.browser.call("action", {"key": "fail", "text": "dummy-password", "private": True})
        self.assertNotIn("dummy-password", str(caught.exception))

    def test_offscreen_error_identifies_live_reads_and_input_as_well_as_attach(self):
        """Use the actual message produced by the signed helper, including during private input."""
        reply = {"error": "Make the owned Chrome window visible before continuing."}
        for method in ("use_existing", "attach", "observe", "action"):
            with self.subTest(method=method), self.assertRaises(BrowserError) as caught:
                self.browser._result(reply, method, {"private": method == "action"}, 0)
            self.assertEqual(caught.exception.code, "window_offscreen")

    def test_long_visible_typing_has_bounded_proportional_timeout(self):
        """A valid multi-paragraph answer cannot time out merely because native typing is visible."""
        self.assertEqual(command_timeout("observe", {}), 60)
        self.assertEqual(command_timeout("stop", {}), 15)
        paired_reads = 2 * command_timeout("observe", {})
        self.assertEqual(command_timeout("action", {"action": "scroll"}), paired_reads)
        self.assertEqual(command_timeout("action", {"action": "click"}), paired_reads)
        self.assertEqual(command_timeout("action", {"action": "type", "text": "x" * 1000}), paired_reads + 150)
        self.assertEqual(command_timeout("action", {"action": "type", "text": "x" * 20000}), paired_reads + 1800)

    def test_closed_window_code_survives_sanitized_transport_errors(self):
        """The manager can discard an obsolete owned identity without guessing another Chrome window."""
        with self.assertRaises(BrowserError) as caught:
            self.browser.call("action", {"key": "window-closed"})
        self.assertEqual(caught.exception.code, "window_closed")

    def test_stop_interrupts_pending_action_and_queued_action(self):
        """The out-of-band Stop reaches a busy helper before a queued action can start."""
        self.browser.call("use_existing")
        sent, outcomes = threading.Event(), []
        send = self.browser._send
        def observe_send(request, generation):
            send(request, generation)
            if request["params"].get("key") == "wait":
                sent.set()
        with patch.object(self.browser, "_send", side_effect=observe_send):
            first = threading.Thread(target=self._record, args=(outcomes, "action", {"key": "wait"}))
            first.start()
            self.assertTrue(sent.wait(2))
            second = threading.Thread(target=self._record, args=(outcomes, "action", {"text": "must-not-type"}))
            second.start()
            time.sleep(0.03)
            self.browser.interrupt()
            first.join(2)
            second.join(2)
        self.assertEqual(len(outcomes), 2)
        self.assertTrue(all(isinstance(item, BrowserError) for item in outcomes))
        self.assertIsNone(self.browser.process.poll())

    def test_stop_during_helper_startup_invalidates_original_create(self):
        """Startup cannot forget an interruption just because the helper did not yet exist."""
        started, release, outcomes = threading.Event(), threading.Event(), []
        start = self.browser._start
        def delayed_start():
            started.set()
            release.wait(2)
            start()
        with patch.object(self.browser, "_start", side_effect=delayed_start):
            worker = threading.Thread(target=self._record, args=(outcomes, "use_existing", {}))
            worker.start()
            self.assertTrue(started.wait(1))
            self.browser.interrupt()
            release.set()
            worker.join(2)
        self.assertIsInstance(outcomes[0], BrowserError)
        self.assertTrue(self.browser.paused)
        self.assertEqual(self.browser.call("use_existing")["method"], "use_existing")

    def _record(self, results, method, parameters):
        """Capture thread outcomes without hiding asynchronous failures from unittest."""
        try:
            results.append(self.browser.call(method, parameters))
        except BrowserError as error:
            results.append(error)
