"""Exercise the real loopback draft API with separate UI and chat credentials."""

from http.client import HTTPConnection
import json
import threading
from unittest.mock import patch

from agent import AgentClient
from applydesk.server import AppServer
from chat_helpers import ChatFixture


class ChatHttpTests(ChatFixture):
    def setUp(self):
        """Start an isolated authenticated service and use the shipped client against it."""
        super().setUp()
        self.server = AppServer(self.w)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = AgentClient(self.w.root)

    def tearDown(self):
        """Stop only the fixture listener and release temporary files."""
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        super().tearDown()

    def request(self, method, path, data=None, headers=None):
        """Raw requests exercise identity confusion, origin checks, and removed routes."""
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, path, json.dumps(data) if data is not None else None,
                               headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_shipped_client_reads_guide_and_completes_only_a_draft(self):
        """The client keeps the local token private and the workflow performs no provider calls."""
        with patch.object(self.w, "client", side_effect=AssertionError("Model call")):
            guide = self.client.request("guide")
            self.assertTrue(guide["draft_only"])
            self.assertIn("Use one agent", guide["instructions"])
            tasks = self.client.request("tasks")
            self.assertEqual(tasks[0]["id"], self.task["id"])
            result = self.client.request("draft", self.payload())["task"]
        self.assertEqual(result["status"], "draft_ready")
        self.assertEqual(self.w.store.get("job", self.job["id"])["status"], "draft")
        self.assertNotIn(self.server.agent_token, json.dumps(self.w.state()))

    def test_chat_and_ui_capabilities_cannot_be_substituted(self):
        """A chat token cannot confirm the profile; an ordinary cookie cannot claim a review."""
        agent = {"Authorization": "Bearer " + self.server.agent_token, "Content-Type": "application/json"}
        ui = {"Cookie": "applydesk=" + self.server.token, "Origin": self.server.origin,
              "X-ApplyDesk": "1", "Content-Type": "application/json"}
        self.assertEqual(self.request("POST", "/api/profile", self.w.profile(), agent)[0], 403)
        self.assertEqual(self.request("POST", "/agent/v1/draft", self.payload(), ui)[0], 403)
        self.assertEqual(self.request("GET", "/agent/v1/tasks")[0], 403)
        for extra in [{"Host": "attacker.test"}, {"Origin": "https://attacker.test"}]:
            self.assertEqual(self.request("GET", "/agent/v1/tasks", headers=agent | extra)[0], 403)
        self.assertEqual(self.request("POST", "/agent/v1/submitted", {}, agent)[0], 400)

    def test_legacy_provider_and_application_mutation_routes_are_closed(self):
        """The UI token cannot reach old filling, submission, extraction, or model APIs either."""
        headers = {"Cookie": "applydesk=" + self.server.token, "Origin": self.server.origin,
                   "X-ApplyDesk": "1", "Content-Type": "application/json"}
        routes = ["job/prepare", "job/focus", "job/submitted", "models/refresh", "settings", "jobs/discover",
                  "document/chat", "document/review", "document/save", "cover-template", "template/confirm", "extract"]
        with patch.object(self.w, "client", side_effect=AssertionError("Provider access")):
            for route in routes:
                self.assertEqual(self.request("POST", "/api/" + route, {"id": self.job["id"]}, headers)[0], 400, route)
        self.assertEqual(self.w.store.get("job", self.job["id"])["status"], "draft")

    def test_task_bound_asset_and_payload_limits(self):
        """Preview access is scoped to the selected task and unknown write fields are rejected."""
        self.client.request("draft", self.payload())
        content = self.client.request("asset", query={"id": self.task["id"], "path": "resume/page.png"})
        self.assertEqual(content, b"synthetic PNG page")
        for path in ["../workspace.sqlite", "agent.connection.json", "unrelated/page.png"]:
            with self.assertRaises(ValueError):
                self.client.request("asset", query={"id": self.task["id"], "path": path})
        with self.assertRaises(ValueError):
            self.client.request("draft", {**self.payload(), "confirmed": True})
        headers = {"Authorization": "Bearer " + self.server.agent_token, "Content-Type": "application/json"}
        self.assertEqual(self.request("POST", "/agent/v1/draft", {}, headers | {"Content-Length": str(1024 * 1024 + 1)})[0], 400)

    def test_connection_is_private_and_readiness_is_not_in_client_actions(self):
        """Published local credentials are private and not a generic HTTP proxy."""
        self.assertEqual((self.w.root / "agent.connection.json").stat().st_mode & 0o777, 0o600)
        for action in ["../api/profile", "http://attacker.test", "submit", "fill", "review"]:
            with self.assertRaises(ValueError):
                self.client.request(action, {})
