"""One synthetic clock exercises the existing worker's usage-limit retry boundary."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from chat_helpers import ChatFixture
from test_orchestrator import response
from applydesk.chatgpt_transport import AccountError
from applydesk.orchestrator import Orchestrator
from applydesk.providers import ProviderError
from applydesk.orchestrator_tools import ToolProgress


class AutoStartTests(ChatFixture):
    def setUp(self):
        super().setUp()
        self.engine = self.w.orchestrator
        self.w.store.put("chatgpt", "accounts", {"active": "test", "accounts": [
            {"id": "test", "label": "Test", "connected": True, "client_id": "oaiapp_test", "subject": "test"}]})
        self.w.store.put("chatgpt_models", "test", [{"id": "test-model", "name": "Test", "thinking": []}])
        self.current = datetime.now(timezone.utc)
        self.timer = self.engine.auto_start
        self.timer.clock = lambda: self.current
        self.config = {"enabled": True, "interval": 5, "unit": "hours"}

    def fail_request(self, error=None):
        """Exercise actual message finishing before reproducing the outer worker's error pause."""
        self.engine.send({"message": "Continue my requested application work."})
        work = self.engine._next()
        self.engine.active = work
        self.engine.cancelled.clear()
        self.engine.generation = self.engine.accounts.generation
        with patch.object(self.engine, "_master", side_effect=error or AccountError(429)):
            with self.assertRaises(Exception):
                self.engine._perform(work)
        self.engine.control({"paused": True}, automatic=True)
        self.engine.active = None
        return work

    def test_default_off_is_read_only_and_save_validates_exact_fields(self):
        self.assertEqual(self.timer.state(), {**self.config, "enabled": False,
                         "next_run_at": None, "last_run_at": None, "pending": False})
        self.assertIsNone(self.w.store.get("auto_start", "main"))
        for change in ({"interval": 0}, {"interval": True}, {"interval": 1.5}, {"interval": "5"},
                       {"enabled": 1}, {"unit": "days"}, {"unit": []}, {"interval": 10 ** 100}, {"instruction": "Run"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.timer.save({**self.config, **change})
        self.assertIsNone(self.w.store.get("auto_start", "main"))

    def test_timer_reuses_one_user_turn_then_never_repeats_completion(self):
        saved = self.timer.save(self.config)
        work = self.fail_request()
        self.assertTrue(self.engine.paused)
        self.assertTrue(self.timer.state()["enabled"])
        self.assertIsNone(self.timer.take())
        self.current += timedelta(hours=5)
        resumed = self.timer.take()
        self.assertEqual(resumed["message"]["id"], work["message"]["id"])
        self.assertTrue(resumed["auto_restart"])
        self.assertFalse(self.engine.paused)
        self.assertIsNone(self.timer.take())
        with patch.object(self.engine, "_master", return_value="Finished.") as master:
            self.engine._perform(resumed)
            self.current += timedelta(hours=15)
            self.assertIsNone(self.timer.take())
        self.assertEqual(master.call_count, 1)
        self.assertFalse(self.timer.state()["pending"])
        self.assertEqual(len([row for row in self.engine.history.read() if row["role"] == "user"]), 1)
        self.assertNotEqual(self.timer.state()["next_run_at"], saved["next_run_at"])

    def test_manual_stop_and_reset_disable_timer_and_remove_pending_retry(self):
        for action in (lambda: self.engine.control({"paused": True}), lambda: self.engine.reset({})):
            self.engine.control({"paused": False})
            self.timer.save(self.config)
            self.fail_request()
            action()
            self.current += timedelta(hours=5)
            self.assertFalse(self.timer.state()["enabled"])
            self.assertFalse(self.timer.state()["pending"])
            self.assertIsNone(self.timer.take())

    def test_generic_errors_auth_failures_and_limit_text_do_not_authorize_retry(self):
        for error in (AccountError(401), AccountError(403), ProviderError("usage limit reached"),
                      ValueError("rate_limit_exceeded"), ProviderError("Invalid key", status=401)):
            self.engine.control({"paused": False})
            self.timer.save(self.config)
            self.fail_request(error)
            self.current += timedelta(hours=5)
            self.assertFalse(self.timer.state()["pending"])
            self.assertIsNone(self.timer.take())
            self.assertTrue(self.engine.paused)

    def test_busy_work_delays_without_interrupting_and_coalesces_old_ticks(self):
        self.timer.save(self.config)
        self.fail_request()
        self.current += timedelta(hours=24)
        deadline = self.timer.state()["next_run_at"]
        self.engine.active = {"task_id": "busy"}
        self.assertIsNone(self.timer.take())
        self.assertEqual(self.timer.state()["next_run_at"], deadline)
        self.engine.active = None
        self.w.tasks["import"] = {"status": "running"}
        self.assertIsNone(self.timer.take())
        self.w.tasks.clear()
        self.assertIsNotNone(self.timer.take())
        self.assertEqual(self.timer.state()["next_run_at"], (self.current + timedelta(hours=5)).isoformat())
        self.assertIsNone(self.timer.take())

    def test_profile_change_or_new_user_request_invalidates_old_retry(self):
        self.timer.save(self.config)
        self.fail_request()
        self.w.save_profile(self.w.profile())
        self.current += timedelta(hours=5)
        self.assertIsNone(self.timer.take())
        self.assertFalse(self.timer.state()["pending"])
        self.engine.control({"paused": False})
        self.fail_request()
        newer = self.engine.send({"message": "Keep these as drafts instead."})
        self.current += timedelta(hours=5)
        self.assertIsNone(self.timer.take())
        self.engine.control({"paused": False})
        self.assertEqual(self.engine._next()["message"]["id"], newer["message_id"])

    def test_changed_account_cannot_reuse_old_permission(self):
        self.timer.save(self.config)
        self.fail_request()
        self.current += timedelta(hours=5)
        state = self.engine.accounts.state()
        with patch.object(self.engine.accounts, "state", return_value={**state, "active": "other"}):
            self.assertIsNone(self.timer.take())
        self.assertFalse(self.timer.state()["pending"])

    def test_pending_binding_and_advanced_deadline_survive_restart(self):
        self.timer.save(self.config)
        original = self.fail_request()
        self.current += timedelta(hours=12)
        restarted = Orchestrator(self.w)
        self.w.orchestrator = restarted
        restarted.auto_start.clock = lambda: self.current
        work = restarted.auto_start.take()
        self.assertEqual(work["message"]["id"], original["message"]["id"])
        second = Orchestrator(self.w)
        self.w.orchestrator = second
        second.auto_start.clock = lambda: self.current
        self.assertIsNone(second.auto_start.take())

    def test_recorded_completed_reply_prevents_replay_after_crash_window(self):
        self.timer.save(self.config)
        original = self.fail_request()
        self.engine.history.add("assistant", "Finished before closing.", request_id=original["message"]["id"])
        self.current += timedelta(hours=5)
        self.assertIsNone(self.timer.take())
        self.assertFalse(self.timer.state()["pending"])

    def test_persistent_limit_waits_for_next_interval_and_does_not_spin(self):
        self.timer.save(self.config)
        self.fail_request()
        self.current += timedelta(hours=5)
        work = self.timer.take()
        self.engine.active = work
        self.engine.cancelled.clear()
        with patch.object(self.engine, "_master", side_effect=AccountError(429)), self.assertRaises(AccountError):
            self.engine._perform(work)
        self.engine.control({"paused": True}, automatic=True)
        self.engine.active = None
        self.assertTrue(self.timer.state()["pending"])
        self.assertIsNone(self.timer.take())
        self.current += timedelta(hours=5)
        self.assertIsNotNone(self.timer.take())

    def test_tools_propagate_typed_limits_without_guessing_from_error_text(self):
        call = {"name": "run_applications", "arguments": "{}"}
        message = self.engine.history.add("user", "Finish the saved applications.")
        self.engine.active = {"message": message}
        self.engine.applications.run = {"id": "run", "message_id": message["id"], "status": "interrupted", "results": []}
        for error in (AccountError(429), ProviderError("Quota", status=429), InterruptedError("Paused.")):
            with patch.object(self.engine.applications, "start", side_effect=error):
                with self.assertRaises(type(error)):
                    ToolProgress(self.engine.tools.call).execute(call)
        with patch.object(self.engine.applications, "start", side_effect=ValueError("usage limit reached")):
            self.assertIn("error", ToolProgress(self.engine.tools.call).execute(call))

    def test_timer_cannot_reset_a_needs_input_task(self):
        context = self.w.chat.get(self.task["id"])
        self.w.chat.block({"task_id": self.task["id"], "version": context["version"], "issues": ["Need a user answer"]})
        message = self.engine.history.add("user", "Prepare this draft.")
        self.engine.active = {"message": message, "auto_restart": True}
        call = {"name": "retry_task", "arguments": json.dumps({"task_id": self.task["id"]})}
        with self.assertRaisesRegex(ValueError, "Explicitly ask"):
            self.engine.tools.call(call)
        self.assertEqual(self.w.chat.get(self.task["id"])["task"]["status"], "needs_input")

    def test_usage_limited_queued_draft_resumes_without_retry_task(self):
        message = self.engine.history.add("user", "Prepare this draft.")
        self.engine.active = {"message": message}
        call = {"name": "prepare_task", "arguments": json.dumps({"task_id": self.task["id"], "instruction": "Prepare"})}
        with patch.object(self.engine.runner, "_request", side_effect=AccountError(429)), self.assertRaises(AccountError):
            self.engine.tools.call(call)
        self.assertEqual(self.w.chat.get(self.task["id"])["task"]["status"], "queued")
        self.engine.active["auto_restart"] = True
        with patch.object(self.engine.runner, "_request", return_value=response(json.dumps({"result": self.payload()["result"]}))):
            result = self.engine.tools.call(call)
        self.assertEqual(result["status"], "draft_ready")

    def test_closing_during_retry_preserves_proven_binding_and_next_deadline(self):
        self.timer.save(self.config)
        original = self.fail_request()
        ledger = {"id": "existing-run", "status": "interrupted", "message_id": original["message"]["id"],
                  "limit": 5, "attempted_count": 2, "submitted_count": 2, "results": []}
        self.w.store.put("application_run", ledger["id"], ledger)
        self.current += timedelta(hours=5)
        retry = self.timer.take()
        deadline = self.timer.state()["next_run_at"]
        self.engine.stop.set()
        self.engine.cancelled.set()
        with patch.object(self.engine, "_master", side_effect=InterruptedError("App closed")):
            self.engine._perform(retry)
        self.assertTrue(self.timer.state()["pending"])
        restarted = Orchestrator(self.w)
        self.w.orchestrator = restarted
        restarted.auto_start.clock = lambda: self.current
        self.assertEqual(restarted.auto_start.state()["next_run_at"], deadline)
        self.assertIsNone(restarted.auto_start.take())
        self.assertEqual(self.w.chat.get(self.task["id"])["task"]["status"], "queued")
        with patch.object(restarted.runner, "_request") as request:
            self.assertIsNone(restarted._next())
        request.assert_not_called()
        self.current += timedelta(hours=5)
        resumed = restarted.auto_start.take()
        self.assertEqual(resumed["message"]["id"], original["message"]["id"])
        self.assertEqual(self.w.store.get("application_run", ledger["id"]), ledger)
