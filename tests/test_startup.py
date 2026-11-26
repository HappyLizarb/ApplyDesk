"""Window reuse tests isolate browser creation from the user's real windows."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from applydesk.startup import LocalInstance, focus_window, open_window


class StartupTests(unittest.TestCase):
    def test_window_focus_failure_keeps_service_available(self):
        """A locked or unavailable desktop must not kill the local application service."""
        import run
        args = SimpleNamespace(data_dir=Path("/tmp/fixture"), no_open=False, port=0)
        with patch.object(run.argparse.ArgumentParser, "parse_args", return_value=args), patch.object(run.sys, "platform", "darwin"):
            with patch.object(run, "LocalInstance"), patch.object(run, "Workspace"), patch.object(run, "AppServer") as server:
                with patch.object(run, "open_window", side_effect=RuntimeError("Window not visible")), patch.object(run, "close_workspace"), patch("builtins.print"):
                    run.main()
        server.return_value.serve_forever.assert_called_once()

    def setUp(self):
        # Each test owns its locks and simulated browser state.
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.url = "http://127.0.0.1:12345/start?token=synthetic"

    def tearDown(self):
        self.temp.cleanup()

    def test_existing_window_is_focused_without_launch_or_navigation(self):
        # Reopening preserves the user's current page and unsaved fields.
        with patch("applydesk.startup.focus_window", return_value=True), patch("applydesk.startup.subprocess.Popen") as launch:
            open_window(self.url, self.root)
            open_window(self.url, self.root)
        launch.assert_not_called()

    def test_closed_window_can_be_opened_again(self):
        # A window that was successfully opened does not leave a pending-launch marker.
        with patch("applydesk.startup.focus_window", side_effect=[False, True, False, True]):
            with patch("applydesk.startup.subprocess.Popen") as launch:
                open_window(self.url, self.root)
                open_window(self.url, self.root)
        self.assertEqual(launch.call_count, 2)
        self.assertEqual((self.root / "window.lock").read_text(), "")

    def test_concurrent_reopens_create_one_window(self):
        # Requests arriving during browser startup serialize on the workspace lock.
        created = threading.Event()
        with patch("applydesk.startup.focus_window", side_effect=lambda _: created.is_set()):
            with patch("applydesk.startup.subprocess.Popen", side_effect=lambda *a, **k: created.set()) as launch:
                with ThreadPoolExecutor(max_workers=6) as pool:
                    list(pool.map(lambda _: open_window(self.url, self.root), range(6)))
        launch.assert_called_once()

    def test_slow_start_does_not_spawn_duplicate_on_retry(self):
        # A timed-out launch remains pending briefly rather than opening more windows.
        with patch("applydesk.startup.focus_window", return_value=False), patch("applydesk.startup.time.sleep"):
            with patch("applydesk.startup.subprocess.Popen") as launch:
                with self.assertRaisesRegex(RuntimeError, "not appeared"):
                    open_window(self.url, self.root)
                open_window(self.url, self.root)
        launch.assert_called_once()

    def test_expired_pending_launch_can_retry(self):
        # A crashed first launch cannot permanently block opening the application.
        (self.root / "window.lock").write_text("1")
        with patch("applydesk.startup.focus_window", side_effect=[False, True]):
            with patch("applydesk.startup.subprocess.Popen") as launch:
                open_window(self.url, self.root)
        launch.assert_called_once()

    def test_focus_failure_does_not_create_another_window(self):
        # A denied activation is not evidence that no window exists.
        with patch("applydesk.startup.subprocess.run", return_value=Mock(returncode=2)):
            with patch("applydesk.startup.subprocess.Popen") as launch:
                with self.assertRaisesRegex(RuntimeError, "could not focus"):
                    open_window(self.url, self.root)
        launch.assert_not_called()

    def test_focus_helper_receives_only_the_private_profile(self):
        # Native focus never receives the capability URL or job platform profiles.
        with patch("applydesk.startup.subprocess.run", return_value=Mock(returncode=0)) as focus:
            self.assertTrue(focus_window(self.root))
        arguments = focus.call_args.args[0]
        self.assertEqual(arguments[1:], ["--focus-interface", str(self.root / "interface")])
        self.assertNotIn(self.url, arguments)

    def test_service_lock_is_released_on_close(self):
        # The existing service singleton still prevents concurrent writers.
        first, second = LocalInstance(self.root), LocalInstance(self.root)
        try:
            self.assertTrue(first.acquired)
            self.assertFalse(second.acquired)
            first.publish(self.url)
            self.assertEqual(second.existing_url(), self.url)
        finally:
            first.close()
            second.close()
        third = LocalInstance(self.root)
        self.assertTrue(third.acquired)
        third.close()

    def test_packaged_app_uses_its_own_focus_helper(self):
        # Moving the app must not send window activation back to the source checkout.
        launcher = "/Applications/ApplyDesk.app/Contents/MacOS/ApplyDesk"
        with patch.dict("os.environ", {"APPLYDESK_LAUNCHER": launcher}):
            with patch("applydesk.startup.subprocess.run", return_value=Mock(returncode=0)) as focus:
                self.assertTrue(focus_window(self.root))
        self.assertEqual(focus.call_args.args[0][0], launcher)
