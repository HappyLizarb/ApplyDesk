"""Connection choices are local preferences, never inferred credentials or profile facts."""

from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from applydesk.server import AppServer
from applydesk.workspace import Workspace


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        # Every browser and secret access is mocked; preference changes need neither.
        self.temp = tempfile.TemporaryDirectory(prefix="applydesk-preferences-")
        self.secrets = Mock()
        self.secrets.get.side_effect = AssertionError("Preferences must not read secrets")
        self.w = Workspace(Path(self.temp.name), secrets=self.secrets)
        self.browser = patch("applydesk.recommendations.BrowserSession").start()

    def tearDown(self):
        # Synthetic active runs have mocked sessions and threads that close immediately.
        self.w.close()
        patch.stopall()
        self.temp.cleanup()

    def test_defaults_ignore_historic_reports_and_nonboolean_records(self):
        # A prior successful import cannot silently opt either platform into refreshes.
        self.w.store.put("recommendation", "linkedin", {"status": "complete", "added_count": 5})
        self.w.store.put("recommendation_preference", "handshake", {"enabled": 1})
        expected = {"handshake": {"enabled": False}, "linkedin": {"enabled": False}}
        self.assertEqual(self.w.state()["recommendation_preferences"], expected)
        self.assertEqual(self.w.recommendations.active, {})
        self.browser.assert_not_called()
        self.secrets.get.assert_not_called()

class PreferenceRouteTests(unittest.TestCase):
    def setUp(self):
        # The only real network traffic in these tests is the app's loopback HTTP API.
        self.temp = tempfile.TemporaryDirectory(prefix="applydesk-preference-route-")
        self.w = Workspace(Path(self.temp.name), secrets=Mock())
        self.server = AppServer(self.w)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        # Stop request handlers before closing their SQLite workspace.
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.w.close()
        self.temp.cleanup()

    def post(self, data, headers=None):
        # Call the actual route so the busy exception cannot skip origin/session checks.
        request_headers = {"Cookie": "applydesk=" + self.server.token, "Origin": self.server.origin,
                           "X-ApplyDesk": "1", "Content-Type": "application/json", **(headers or {})}
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request("POST", "/api/recommendations/preference", json.dumps(data), request_headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_retired_route_cannot_change_saved_preferences(self):
        # Historical preferences survive, but old UI requests cannot enable platform sessions.
        data = {"platform": "handshake", "enabled": True}
        self.assertEqual(self.post(data)[0], 400)
        self.assertEqual(self.w.state()["recommendation_preferences"]["handshake"], {"enabled": False})
        self.assertEqual(self.w.tasks, {})
        self.assertEqual(self.w.recommendations.active, {})

    def test_retired_route_stays_disabled_during_work_and_requires_authorization(self):
        # Removed controls cannot mutate a workspace through an old or unauthorized request.
        self.w.tasks["test"] = {"id": "test", "status": "running"}
        data = {"platform": "handshake", "enabled": False}
        self.assertEqual(self.post(data)[0], 400)
        self.assertEqual(self.post({**data, "enabled": True})[0], 400)
        self.assertEqual(self.post({**data, "enabled": 0})[0], 400)
        for headers in ({"Origin": "https://attacker.example"}, {"Cookie": ""}, {"X-ApplyDesk": ""}):
            with self.subTest(headers=headers):
                self.assertEqual(self.post(data, headers)[0], 403)


if __name__ == "__main__":
    unittest.main()
