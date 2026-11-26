"""Only the authenticated in-app UI can change the one automatic-start timer."""

import json
import unittest

import test_chat_http as chat_http


class AutoStartHttpTests(unittest.TestCase):
    def setUp(self):
        self.case = chat_http.ChatHttpTests()
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        server = self.case.server
        self.headers = {"Cookie": "applydesk=" + server.token, "Origin": server.origin,
                        "X-ApplyDesk": "1", "Content-Type": "application/json"}
        self.data = {"enabled": True, "interval": 5, "unit": "hours"}
        self.path = "/api/orchestrator/auto-start"

    def test_save_returns_timer_without_starting_work_and_rejects_added_authority(self):
        status, body = self.case.request("POST", self.path, self.data, self.headers)
        self.assertEqual(status, 200)
        saved = json.loads(body)
        self.assertEqual({key: saved[key] for key in self.data}, self.data)
        self.assertIsNotNone(saved["next_run_at"])
        self.assertFalse(saved["pending"])
        self.assertIsNone(self.case.w.orchestrator.active)
        self.assertEqual(self.case.request("POST", self.path, {**self.data, "message": "Apply now"}, self.headers)[0], 400)

    def test_external_draft_token_and_foreign_origin_cannot_enable_timer(self):
        agent = {"Authorization": "Bearer " + self.case.server.agent_token, "Content-Type": "application/json"}
        self.assertEqual(self.case.request("POST", self.path, self.data, agent)[0], 403)
        self.assertEqual(self.case.request("POST", self.path, self.data, {**self.headers, "Origin": "https://elsewhere.test"})[0], 403)
        self.assertIsNone(self.case.w.store.get("auto_start", "main"))
