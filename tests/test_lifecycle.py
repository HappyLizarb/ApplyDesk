"""Process ownership and shutdown behavior are tested without touching real windows."""

import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from applydesk.lifecycle import CHROME, close_window, interface_process


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
