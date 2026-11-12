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

    def active_run(self, platform="handshake", identity="current"):
        # Seed one known generation without opening a browser or starting a worker.
        run = {"run_id": identity, "event": threading.Event(), "session": Mock(), "thread": Mock()}
        self.w.recommendations.active[platform] = run
        self.w.store.put("recommendation", platform, {"run_id": identity, "status": "scanning"})
        return run

    def test_defaults_ignore_historic_reports_and_nonboolean_records(self):
        # A prior successful import cannot silently opt either platform into refreshes.
        self.w.store.put("recommendation", "linkedin", {"status": "complete", "added_count": 5})
        self.w.store.put("recommendation_preference", "handshake", {"enabled": 1})
        expected = {"handshake": {"enabled": False}, "linkedin": {"enabled": False}}
        self.assertEqual(self.w.state()["recommendation_preferences"], expected)
        self.assertEqual(self.w.recommendations.active, {})
        self.browser.assert_not_called()
        self.secrets.get.assert_not_called()

    def test_validation_rejects_unknown_sources_and_coerced_booleans(self):
        # Indeed remains unsupported, and strings/numbers cannot authorize a connection.
        cases = [None, [], {}, {"platform": "indeed", "enabled": True}, {"platform": [], "enabled": False}]
        cases += [{"platform": "handshake", "enabled": value} for value in (None, 0, 1, "false", "true")]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.w.recommendations.preference(data)
        self.assertEqual(self.w.store.all("recommendation_preference"), [])
        self.browser.assert_not_called()

    def test_preference_persists_without_changing_profile_or_starting_work(self):
        # Restart retains the choice but never starts a browser or agent automatically.
        profile = self.w.profile()
        result = self.w.recommendations.preference({"platform": "handshake", "enabled": True})
        self.assertEqual(result, {"platform": "handshake", "enabled": True})
        self.w.close()
        self.w = Workspace(Path(self.temp.name), secrets=self.secrets)
        self.assertTrue(self.w.state()["recommendation_preferences"]["handshake"]["enabled"])
        self.assertFalse(self.w.state()["recommendation_preferences"]["linkedin"]["enabled"])
        self.assertEqual(self.w.profile(), profile)
        self.assertEqual(self.w.tasks, {})
        self.assertEqual(self.w.recommendations.active, {})
        self.browser.assert_not_called()

    def test_explicit_begin_enables_only_the_chosen_source(self):
        # A successful begin is authorization to select that source for later refreshes.
        with patch("applydesk.recommendations.threading.Thread") as thread:
            result = self.w.recommendations.begin({"platform": "handshake", "limit": 1})
        self.assertEqual(result["platform"], "handshake")
        thread.return_value.start.assert_called_once()
        self.assertEqual(self.w.recommendations.preferences(),
                         {"handshake": {"enabled": True}, "linkedin": {"enabled": False}})

    def test_failed_begin_does_not_enable_a_disabled_source(self):
        # A duplicate request cannot silently reverse an explicit disabled preference.
        self.active_run()
        with self.assertRaises(ValueError):
            self.w.recommendations.begin({"platform": "handshake"})
        self.assertFalse(self.w.recommendations.preferences()["handshake"]["enabled"])
        self.browser.assert_not_called()

    def test_disable_stops_exact_active_generation_and_rejects_late_publish(self):
        # Cancellation and saved selection change under the same workspace lock.
        current = self.active_run()
        other = self.active_run("linkedin", "other")
        self.w.recommendations.preference({"platform": "handshake", "enabled": False})
        self.assertTrue(current["event"].is_set())
        current["session"].cancel.assert_called_once()
        self.assertFalse(other["event"].is_set())
        other["session"].cancel.assert_not_called()
        self.assertEqual(self.w.recommendations.state()["handshake"]["status"], "cancelled")
        self.assertFalse(self.w.recommendations._publish("handshake", current, status="complete"))
        self.assertFalse(self.w.recommendations.preferences()["handshake"]["enabled"])

    def test_disable_fresh_read_does_not_overwrite_an_older_import_report(self):
        # A preparation read has no matching import report but still must stop on disable.
        current = self.active_run(identity="fresh-read")
        prior = {"run_id": "older-import", "status": "complete", "added_count": 2}
        self.w.store.put("recommendation", "handshake", prior)
        self.w.recommendations.preference({"platform": "handshake", "enabled": False})
        self.assertTrue(current["event"].is_set())
        current["session"].cancel.assert_called_once()
        self.assertEqual(self.w.recommendations.state()["handshake"], prior)


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

    def test_route_persists_and_exports_selection_without_a_task(self):
        # Enabling is a preference change, not a browser connection or paid agent call.
        data = {"platform": "handshake", "enabled": True}
        self.assertEqual(self.post(data), (200, data))
        self.assertEqual(self.w.state()["recommendation_preferences"]["handshake"], {"enabled": True})
        self.assertEqual(self.w.tasks, {})
        self.assertEqual(self.w.recommendations.active, {})

    def test_only_disable_bypasses_busy_and_still_requires_authorization(self):
        # A disabled checkbox remains available during work, without opening other writes.
        self.w.tasks["test"] = {"id": "test", "status": "running"}
        data = {"platform": "handshake", "enabled": False}
        self.assertEqual(self.post(data), (200, data))
        self.assertEqual(self.post({**data, "enabled": True})[0], 400)
        self.assertEqual(self.post({**data, "enabled": 0})[0], 400)
        for headers in ({"Origin": "https://attacker.example"}, {"Cookie": ""}, {"X-ApplyDesk": ""}):
            with self.subTest(headers=headers):
                self.assertEqual(self.post(data, headers)[0], 403)


if __name__ == "__main__":
    unittest.main()
