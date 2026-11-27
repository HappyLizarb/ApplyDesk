"""Removing a job keeps Stop responsive while the native browser is busy."""

from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
import json
import threading
from unittest.mock import patch

from chat_helpers import ChatFixture
from native_window_fixture import NativeWindowFixture
from applydesk.native_application_browser import NativeApplicationBrowser
from applydesk.applications import Applications
from applydesk.native_browser import BrowserError
from applydesk.server import AppServer


class BusyTab(NativeWindowFixture):
    """Hold one native field action until the browser receives Stop."""

    def __init__(self, **options):
        super().__init__(**options)
        self.fill_started, self.interrupted = threading.Event(), threading.Event()

    def _action(self, params):
        if params.get("element_id") == "fixture-field":
            self.fill_started.set()
            if not self.interrupted.wait(3):
                raise AssertionError("Stop did not reach the active helper")
            raise BrowserError("Native input stopped")
        super()._action(params)

    def interrupt(self):
        super().interrupt()
        self.interrupted.set()


class ApplicationBrowserLifecycleTests(ChatFixture):
    def setUp(self):
        """Two synthetic job tabs share a browser without accessing any actual account."""
        super().setUp()
        patch("applydesk.native_application_browser.NativeBrowser", side_effect=BusyTab).start()
        patch("applydesk.native_application_browser.public_job_url", side_effect=lambda url: url).start()
        patch("applydesk.native_navigation.time.sleep").start()
        patch.object(NativeApplicationBrowser, "_settle", lambda m, generation: m._wire("observe", {}, generation)).start()
        self.addCleanup(patch.stopall)
        app = Applications(self.w)
        self.first = app._session(self.job)
        self.first.call("open", {"url": self.job["url"]})
        self.other_job = self.w.store.add_job("https://other.example.test/job")
        self.second = app._session(self.other_job)
        self.second.call("open", {"url": self.other_job["url"]})
        self.browser = self.first.manager.browser
        self.manager = self.w.application_browser

    def fill(self, session):
        """Dispatch a normal native field click on the job's freshly observed tab."""
        page = session.call("native_observe")
        session.call("native_action", {"snapshot_id": page["snapshot_id"], "action": "click", "element_id": "fixture-field"})

    def test_removing_an_active_job_interrupts_its_fill_and_rejects_stale_reopen(self):
        """Deleting the active row interrupts input and preserves the user's other Chrome tab."""
        errors = []
        def fill():
            try:
                self.fill(self.second)
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=fill)
        thread.start()
        self.assertTrue(self.browser.fill_started.wait(1))
        self.w.remove_jobs({"ids": [self.other_job["id"]]})
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertTrue(errors)
        self.assertTrue(self.browser.interrupted.is_set())
        self.assertIn("tab-1", self.browser.tabs)
        self.assertNotIn(self.other_job["id"], self.manager.tabs)
        self.assertFalse(self.browser.ended)
        before = len(self.browser.calls)
        with self.assertRaisesRegex(InterruptedError, "removed"):
            self.second.call("open", {"url": self.other_job["url"]})
        self.assertEqual(len(self.browser.calls), before)

    def _post(self, server, path, data):
        """Send a genuine local UI request with an independent connection and bounded wait."""
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=1)
        headers = {"Cookie": "applydesk=" + server.token, "Origin": server.origin,
                   "X-ApplyDesk": "1", "Content-Type": "application/json"}
        try:
            connection.request("POST", path, json.dumps(data), headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_http_stop_remains_responsive_while_an_unrelated_tab_close_waits(self):
        """The removal endpoint cannot hold the UI lock while waiting for another job's fill."""
        with patch.object(self.w.orchestrator, "start"):
            server = AppServer(self.w)
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        self.addCleanup(server.server_close)
        self.addCleanup(serving.join, 2)
        self.addCleanup(server.shutdown)
        entered = threading.Event()
        original = self.manager.close_job
        def close_job(identity):
            """Signal when deletion has reached the browser serialization boundary."""
            entered.set()
            result = original(identity)
            return result
        with ThreadPoolExecutor(max_workers=2) as workers, patch.object(self.manager, "close_job", side_effect=close_job):
            filling = workers.submit(self.fill, self.second)
            self.assertTrue(self.browser.fill_started.wait(1))
            removing = workers.submit(self._post, server, "/api/jobs/remove", {"ids": [self.job["id"]]})
            self.assertTrue(entered.wait(1))
            self.assertFalse(removing.done())
            status, result = self._post(server, "/api/orchestrator/control", {"paused": True})
            self.assertEqual(status, 200, result)
            self.assertTrue(self.browser.interrupted.is_set())
            with self.assertRaises(Exception):
                filling.result(timeout=1)
            self.assertEqual(removing.result(timeout=1)[0], 200)
