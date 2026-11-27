"""Process ownership and shutdown behavior are tested without touching real windows."""

import signal
import tempfile
import threading
import unittest
from applydesk.lifecycle import CHROME, LocalInstance, close_window, focus_window, interface_process, open_window
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


class LifecycleTests(unittest.TestCase):
    """Only ApplyDesk's exact private browser profile can be closed."""

    def setUp(self):
        """An isolated profile lock points to a simulated process."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'interface').mkdir()
        (self.root / 'interface/SingletonLock').symlink_to('test-machine-12345')

    def test_exact_owned_browser_is_recognized(self):
        """The executable and complete user-data-dir argument both identify the app window."""
        command = f'{CHROME} --app=http://127.0.0.1:1234/ --user-data-dir={self.root}/interface --disable-sync'
        with patch('applydesk.lifecycle.subprocess.run', return_value=Mock(returncode=0, stdout=command)):
            self.assertEqual(interface_process(self.root), 12345)

    def test_unrelated_browsers_and_similar_paths_are_untouched(self):
        """Neither personal Chrome nor a prefix-matching profile can be terminated."""
        for command in [f'{CHROME} --user-data-dir={self.root}/interface-other',
                        f'{CHROME} --user-data-dir=/Users/example/Personal',
                        f'/tmp/other --user-data-dir={self.root}/interface']:
            with patch('applydesk.lifecycle.subprocess.run', return_value=Mock(returncode=0, stdout=command)):
                with patch('applydesk.lifecycle.os.kill') as kill:
                    close_window(self.root)
                    kill.assert_not_called()

    def test_graceful_exit_needs_one_signal(self):
        """A normally exiting private browser never receives a force-kill signal."""
        with patch('applydesk.lifecycle.interface_process', side_effect=[12345, None]):
            with patch('applydesk.lifecycle.os.kill') as kill:
                close_window(self.root)
        kill.assert_called_once_with(12345, signal.SIGTERM)

    def test_unresponsive_owned_browser_has_bounded_fallback(self):
        """A still-owned process is reverified before the final termination signal."""
        with patch('applydesk.lifecycle.interface_process', return_value=12345):
            with patch('applydesk.lifecycle.time.sleep'), patch('applydesk.lifecycle.os.kill') as kill:
                close_window(self.root)
        self.assertEqual([call.args for call in kill.call_args_list], [(12345, signal.SIGTERM), (12345, signal.SIGKILL)])

    def test_changed_process_identity_cancels_fallback(self):
        """A reused PID or replaced lock cannot redirect the fallback to another process."""
        with patch('applydesk.lifecycle.interface_process', side_effect=[12345, 54321]):
            with patch('applydesk.lifecycle.os.kill') as kill:
                close_window(self.root)
        kill.assert_called_once_with(12345, signal.SIGTERM)


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
        with patch("applydesk.lifecycle.focus_window", return_value=True), patch("applydesk.lifecycle.subprocess.Popen") as launch:
            open_window(self.url, self.root)
            open_window(self.url, self.root)
        launch.assert_not_called()

    def test_closed_window_can_be_opened_again(self):
        # A window that was successfully opened does not leave a pending-launch marker.
        with patch("applydesk.lifecycle.focus_window", side_effect=[False, True, False, True]):
            with patch("applydesk.lifecycle.subprocess.Popen") as launch:
                open_window(self.url, self.root)
                open_window(self.url, self.root)
        self.assertEqual(launch.call_count, 2)
        self.assertEqual((self.root / "window.lock").read_text(), "")

    def test_concurrent_reopens_create_one_window(self):
        # Requests arriving during browser startup serialize on the workspace lock.
        created = threading.Event()
        with patch("applydesk.lifecycle.focus_window", side_effect=lambda _: created.is_set()):
            with patch("applydesk.lifecycle.subprocess.Popen", side_effect=lambda *a, **k: created.set()) as launch:
                with ThreadPoolExecutor(max_workers=6) as pool:
                    list(pool.map(lambda _: open_window(self.url, self.root), range(6)))
        launch.assert_called_once()

    def test_slow_start_does_not_spawn_duplicate_on_retry(self):
        # A timed-out launch remains pending briefly rather than opening more windows.
        with patch("applydesk.lifecycle.focus_window", return_value=False), patch("applydesk.lifecycle.time.sleep"):
            with patch("applydesk.lifecycle.subprocess.Popen") as launch:
                with self.assertRaisesRegex(RuntimeError, "not appeared"):
                    open_window(self.url, self.root)
                open_window(self.url, self.root)
        launch.assert_called_once()

    def test_expired_pending_launch_can_retry(self):
        # A crashed first launch cannot permanently block opening the application.
        (self.root / "window.lock").write_text("1")
        with patch("applydesk.lifecycle.focus_window", side_effect=[False, True]):
            with patch("applydesk.lifecycle.subprocess.Popen") as launch:
                open_window(self.url, self.root)
        launch.assert_called_once()

    def test_focus_failure_does_not_create_another_window(self):
        # A denied activation is not evidence that no window exists.
        with patch("applydesk.lifecycle.subprocess.run", return_value=Mock(returncode=2)):
            with patch("applydesk.lifecycle.subprocess.Popen") as launch:
                with self.assertRaisesRegex(RuntimeError, "could not focus"):
                    open_window(self.url, self.root)
        launch.assert_not_called()

    def test_focus_helper_receives_only_the_private_profile(self):
        # Native focus never receives the capability URL or job platform profiles.
        with patch("applydesk.lifecycle.subprocess.run", return_value=Mock(returncode=0)) as focus:
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
            with patch("applydesk.lifecycle.subprocess.run", return_value=Mock(returncode=0)) as focus:
                self.assertTrue(focus_window(self.root))
        self.assertEqual(focus.call_args.args[0][0], launcher)
