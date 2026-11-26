"""Source reads and browser IPC use isolated synthetic sessions."""

import io
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

from chat_helpers import ChatFixture
from recommendation_helpers import listing
from applydesk.browser_bridge import BrowserError, BrowserSession
from applydesk.recommendation_source import normalize_job


class SourceReaderTests(ChatFixture):
    def test_private_source_transfer_is_not_requested_by_ordinary_reads(self):
        """Find-job source reads keep their visible browser and never request sign-in transfer."""
        item = listing("100", "handshake")
        reader = Mock()
        reader.call.return_value = {"source": "handshake", "status": "ready", "job": item}
        with patch("applydesk.recommendations.BrowserSession", return_value=reader) as factory:
            self.w.recommendations.read_job(normalize_job("handshake", item))
        self.assertFalse(factory.call_args.kwargs["headless"])
        self.assertEqual([call.args[0] for call in reader.call.call_args_list], ["source_job"])


class BrowserInterruptTests(ChatFixture):
    def test_existing_chrome_allows_time_for_approval_only_before_first_open(self):
        """Permission waiting does not enlarge ordinary navigation deadlines after connection."""
        browser = BrowserSession(self.w.root, application_workspace=True, existing_chrome=True)
        browser.process = SimpleNamespace(stdin=io.StringIO(), poll=lambda: None)
        browser.messages = Mock()
        browser.messages.get.return_value = {"id": "request", "result": {}}
        with patch("applydesk.browser_bridge.uuid.uuid4", return_value=SimpleNamespace(hex="request")):
            browser.call("open", {"job_id": "job", "url": "https://example.test"})
            self.assertEqual(browser.messages.get.call_args.kwargs["timeout"], 245)
            browser.call("open", {"job_id": "job", "url": "https://example.test"})
            self.assertEqual(browser.messages.get.call_args.kwargs["timeout"], 65)

    def test_long_visible_fill_has_time_for_pacing_without_changing_other_timeouts(self):
        """A 200-field visible form must not be terminated by the ordinary one-minute response limit."""
        cases = [(True, None, "fill", 200, 165, 900), (False, None, "fill", 200, 65, 65),
                 (False, "linkedin", "source_scan", 0, 180, 180)]
        for workspace, source, method, count, minimum, maximum in cases:
            browser = BrowserSession(self.w.root, application_workspace=workspace, source=source)
            browser.process = SimpleNamespace(stdin=io.StringIO(), poll=lambda: None)
            browser.messages = Mock()
            browser.messages.get.return_value = {"id": "request", "result": {}}
            with patch("applydesk.browser_bridge.uuid.uuid4", return_value=SimpleNamespace(hex="request")):
                browser.call(method, {"answers": [{}] * count})
            timeout = browser.messages.get.call_args.kwargs["timeout"]
            self.assertGreaterEqual(timeout, minimum)
            self.assertLessEqual(timeout, maximum)

    def test_interrupt_does_not_wait_for_response_lock_or_mark_browser_ended(self):
        """The short control write reaches a worker that is busy answering another request."""
        browser = BrowserSession(self.w.root, application_workspace=True)
        browser.process = SimpleNamespace(stdin=io.StringIO(), poll=lambda: None)
        with browser.lock:
            thread = threading.Thread(target=browser.interrupt)
            thread.start(); thread.join(.5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(json.loads(browser.process.stdin.getvalue()), {"method": "interrupt"})
        self.assertFalse(browser.ended)
        with self.assertRaises(BrowserError):
            browser._send({"method": "open"}, 0)

    def test_cancellation_during_startup_prevents_the_pending_open(self):
        """A browser launch cannot turn an older cancelled request into a fresh authorized open."""
        browser = BrowserSession(self.w.root, application_workspace=True)
        def start():
            browser.process = SimpleNamespace(stdin=io.StringIO(), poll=lambda: None)
            browser.interrupt()
        with patch.object(browser, "_start", side_effect=start), self.assertRaises(BrowserError):
            browser.call("open", {"job_id": "job", "url": "https://example.test"})
        self.assertEqual(json.loads(browser.process.stdin.getvalue()), {"method": "interrupt"})
        self.assertFalse(browser.ended)
