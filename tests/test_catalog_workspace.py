"""Legacy settings migration and read-only source routes in chat-only mode."""

import copy
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from applydesk.server import AppServer
from applydesk.workspace import Workspace


class WorkspaceFixture:
    def setUp(self):
        # Mock secrets make accidental operating-system Keychain access impossible.
        self.temporary = tempfile.TemporaryDirectory(prefix="applydesk-catalog-test-")
        self.secrets = Mock(spec=["get", "set"])
        self.secrets.get.return_value = None
        self.workspace = Workspace(Path(self.temporary.name), secrets=self.secrets)

    def tearDown(self):
        # Close the database before removing each test's isolated workspace.
        self.workspace.close()
        self.temporary.cleanup()


class CatalogWorkspaceTests(WorkspaceFixture, unittest.TestCase):
    def test_every_provider_role_is_disabled_without_reading_credentials(self):
        # Saved subscriptions and model settings cannot trigger a legacy API fallback.
        previous = copy.deepcopy(self.workspace.settings())
        self.workspace.store.put("settings", "main", previous)
        for role in ("application", "formatting", "auditor", "discovery"):
            with self.assertRaisesRegex(ValueError, "Provider calls are disabled"):
                self.workspace.client(role)
        self.secrets.get.assert_not_called()
        self.assertEqual(self.workspace.settings(), previous)
        self.assertNotIn("model_catalog", self.workspace.state())

    def test_document_preference_preserves_unused_legacy_settings(self):
        # Removing the UI does not silently delete the user's old configuration or secrets.
        original = self.workspace.settings()
        original["keys"] = {"openai": True}
        self.workspace.store.put("settings", "main", original)
        self.assertEqual(self.workspace.save_settings({"cover_template_id": ""}), {"cover_template_id": ""})
        self.assertEqual(self.workspace.settings(), original)
        self.secrets.get.assert_not_called()
        self.secrets.set.assert_not_called()



    def test_restart_marks_interrupted_search_without_losing_criteria_or_jobs(self):
        # A terminated search cannot remain visibly running after a relaunch.
        criteria = {"keywords": "engineer", "location": "US"}
        self.workspace.store.put("discovery", "main", {"status": "searching", "criteria": criteria, "added_count": 1})
        job = self.workspace.store.add_job("https://example.test/jobs/1")
        self.workspace.close()
        self.workspace = Workspace(Path(self.temporary.name), secrets=self.secrets)
        report = self.workspace.state()["discovery"]
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["criteria"], criteria)
        self.assertEqual(report["added_count"], 1)
        self.assertIn("finished_at", report)
        self.assertEqual(self.workspace.store.all("job"), [job])
        self.secrets.get.assert_not_called()






class CatalogRouteTests(WorkspaceFixture, unittest.TestCase):
    def setUp(self):
        # Only the local HTTP listener is real; background actions remain mocked.
        super().setUp()
        self.server = AppServer(self.workspace)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        # Stop incoming requests before closing the shared SQLite connection.
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        super().tearDown()

    def post(self, route, data=None, headers=None, raw=None):
        # Defaults match the app; each attack changes one exact session boundary.
        request_headers = {"Cookie": "applydesk=" + self.server.token,
                           "Origin": self.server.origin, "X-ApplyDesk": "1",
                           "Content-Type": "application/json"}
        request_headers.update(headers or {})
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            body = json.dumps(data) if raw is None else raw
            connection.request("POST", route, body=body, headers=request_headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_old_provider_routes_cannot_start_background_work(self):
        # Retired controls remain disabled even if an old browser tab calls their URLs.
        with patch.object(self.workspace, "start") as start:
            for route in ("/api/models/refresh", "/api/jobs/discover"):
                self.assertEqual(self.post(route, {"provider": "openai"})[0], 400)
            start.assert_not_called()
        self.secrets.get.assert_not_called()

    def test_retired_recommendation_connection_cannot_start_a_browser(self):
        # Old UI tabs cannot restart the removed job-platform connection flow.
        data = {"platform": "linkedin", "limit": 5}
        self.assertEqual(self.post("/api/recommendations/begin", data)[0], 400)
        self.assertEqual(self.workspace.tasks, {})

    def test_recommendation_cancel_works_during_an_agent_task(self):
        # Cancellation bypasses only the busy gate, never session authorization.
        self.workspace.tasks["test"] = {"id": "test", "status": "running"}
        data = {"platform": "handshake", "run_id": "current-run"}
        with patch.object(self.workspace.recommendations, "cancel", return_value={"cancelled": True}) as cancel:
            self.assertEqual(self.post("/api/recommendations/cancel", data), (200, {"cancelled": True}))
            self.assertEqual(self.post("/api/recommendations/cancel", data, {"Origin": "https://elsewhere.example"})[0], 403)
            self.assertEqual(self.post("/api/shutdown", {})[0], 200)
            cancel.assert_called_once_with(data)

    def test_both_routes_require_exact_local_session_host_and_origin(self):
        # Cookie possession alone must not authorize cross-site or alternate-host work.
        attacks = [{"Cookie": ""}, {"Cookie": "applydesk=incorrect"}, {"Origin": "https://attacker.example"},
                   {"Origin": self.server.origin + "/"}, {"Host": f"localhost:{self.server.server_port}"},
                   {"Host": "attacker.example"}, {"X-ApplyDesk": ""}]
        with patch.object(self.workspace, "start") as start:
            for route in ("/api/models/refresh", "/api/jobs/discover", "/api/recommendations/begin", "/api/recommendations/cancel"):
                for headers in attacks:
                    with self.subTest(route=route, headers=headers):
                        self.assertEqual(self.post(route, {"provider": "openai"}, headers)[0], 403)
            start.assert_not_called()
        self.secrets.get.assert_not_called()

    def test_malformed_requests_never_start_background_work(self):
        # Parsing errors leave both catalogs and discovery state untouched.
        before = self.workspace.state()
        with patch.object(self.workspace, "start") as start:
            for route in ("/api/models/refresh", "/api/jobs/discover", "/api/recommendations/begin", "/api/recommendations/cancel"):
                for raw in ("{", "[]", "null"):
                    with self.subTest(route=route, raw=raw):
                        self.assertEqual(self.post(route, raw=raw)[0], 400)
            start.assert_not_called()
        self.assertEqual(self.workspace.state(), before)
        self.secrets.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
