"""Behavioral routing tests use independent synthetic model responses and real draft validation."""

import copy
import json
import time
from unittest.mock import patch

from chat_helpers import ChatFixture
from applydesk.chatgpt_transport import AccountError
from applydesk.orchestrator_prompts import MASTER, DRAFTER, REVIEWER, AUDITOR


def response(text="", calls=None):
    """Represent only a completed provider response; streamed partial text cannot pass."""
    output = calls or [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}]
    return {"id": "response-" + str(time.monotonic_ns()), "status": "completed", "output": output}


class OrchestratorTests(ChatFixture):
    def setUp(self):
        """Seed account metadata only; the test adapter must never request real credentials."""
        super().setUp()
        self.engine = self.w.orchestrator
        self.w.store.put("chatgpt", "accounts", {"active": "test", "host_id": "host", "accounts": [{"id": "test", "label": "Test", "connected": True, "client_id": "oaiapp_test", "subject": "subject"}]})
        self.w.store.put("chatgpt_models", "test", [{"id": "test-model", "name": "Test", "thinking": ["low", "high"]}])
        self.calls = []
        self.fail_review = False

    def model(self, payload, cancelled, emit):
        """Each role gets its own input; the master delegates and never supplies a verdict."""
        self.calls.append(copy.deepcopy(payload))
        self.assertFalse(payload["store"])
        self.assertTrue(payload["stream"])
        prompt = payload["instructions"]
        if prompt == MASTER:
            if any(item.get("type") == "function_call_output" for item in payload["input"]):
                emit("Your draft is prepared. Nothing was submitted.")
                return response("Your draft is prepared. Nothing was submitted.")
            return response(calls=[{"type": "function_call", "namespace": "applydesk", "name": "prepare_task", "call_id": "call-1", "arguments": json.dumps({"task_id": self.task["id"], "instruction": "Prepare this draft."})}])
        if prompt == DRAFTER:
            return response(json.dumps({"result": self.payload()["result"]}))
        role = "document_reviewer" if prompt == REVIEWER else "application_auditor"
        self.assertEqual(prompt, REVIEWER if role == "document_reviewer" else AUDITOR)
        self.assertEqual(len(payload["input"]), 1)
        material = json.loads(payload["input"][0]["content"][0]["text"])
        self.assertNotIn("master_instruction", material)
        self.assertNotIn("reviews", material)
        verdict = {"passed": not self.fail_review, "issues": ["Missing evidence"] if self.fail_review else [],
                   "checked_items": material["review"]["expected_checks"][role]}
        return response(json.dumps(verdict))

    def test_automatic_queue_runs_separate_agents_and_keeps_job_draft(self):
        """Connected idle app processes the queue without a Codex process or user chat turn."""
        original = self.w.profile()
        with patch.object(self.engine.runner, "_request", side_effect=self.model):
            self.engine.start()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and self.w.chat.get(self.task["id"])["task"]["status"] != "reviewed_draft":
                time.sleep(.02)
            self.engine.control({"paused": True})
        task = self.w.chat.get(self.task["id"])["task"]
        self.assertEqual(task["status"], "reviewed_draft")
        reviews = task["reviews"]
        self.assertTrue(all(item["identity_verified"] for item in reviews.values()))
        self.assertEqual(len({task["agent_id"], *(item["agent_id"] for item in reviews.values())}), 3)
        self.assertEqual(self.w.profile(), original)
        self.assertEqual(self.w.store.get("job", self.job["id"])["status"], "draft")
        layout = next(call for call in self.calls if call["instructions"] == REVIEWER)
        self.assertTrue(any(item["type"] == "input_image" for item in layout["input"][0]["content"]))

    def test_failed_reviews_block_retry_and_cannot_be_forged_by_external_chat(self):
        """A failed reviewer remains a blocker; a client cannot claim app-created run identity."""
        self.fail_review = True
        with patch.object(self.engine.runner, "_request", side_effect=self.model):
            result = self.engine.workflow.prepare(self.task["id"], "Prepare")
            count = len(self.calls)
            with self.assertRaises(ValueError):
                self.engine.workflow.prepare(self.task["id"], "Try again")
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(len(self.calls), count)
        review = self.reviewer("application_auditor", self.w.chat.get(self.task["id"])["task"]["reviews"]["application_auditor"]["agent_id"])
        with self.assertRaises(ValueError):
            self.w.chat.review(review)
        self.engine.retry({"task_id": self.task["id"]})
        self.assertEqual(self.w.chat.get(self.task["id"])["task"]["status"], "queued")

    def test_pause_discards_completed_but_uncommitted_model_output(self):
        """Cancellation between the provider result and local write leaves the task unchanged."""
        def delayed(payload, cancelled, emit):
            value = self.model(payload, cancelled, emit)
            self.engine.control({"paused": True})
            return value
        with patch.object(self.engine.runner, "_request", side_effect=delayed):
            with self.assertRaises(InterruptedError):
                self.engine.workflow.prepare(self.task["id"], "Prepare")
        self.assertIsNone(self.w.chat.get(self.task["id"])["task"]["result"])
        self.assertIsNone(self.w.store.get("job", self.job["id"])["resume_id"])

    def test_changed_profile_during_inference_rejects_stale_result(self):
        """Context snapshots cannot be committed against a different profile revision."""
        def changed(payload, cancelled, emit):
            value = self.model(payload, cancelled, emit)
            profile = self.w.profile();profile["skills"].append("SQL");self.w.save_profile(profile)
            return value
        with patch.object(self.engine.runner, "_request", side_effect=changed):
            with self.assertRaises(ValueError):
                self.engine.workflow.prepare(self.task["id"], "Prepare")
        self.assertIsNone(self.w.chat.get(self.task["id"])["task"]["result"])

    def test_account_changes_and_unavailable_settings_block_requests(self):
        """An account switch invalidates in-flight writes; unknown models do not silently fall back."""
        self.engine.accounts.generation += 1
        with self.assertRaises(InterruptedError):
            self.engine.workflow.prepare(self.task["id"], "Prepare")
        settings = self.engine.settings.read()
        settings["roles"]["master"]["model"] = "invented-model"
        with self.assertRaises(ValueError):
            self.engine.settings.save(settings)
        self.engine.generation = self.engine.accounts.generation
        with self.assertRaises(ValueError):
            self.engine.tools.call({"name": "submit_application", "arguments": "{}"})

    def test_chat_stream_history_and_user_message_survive_without_provider_keys(self):
        """The in-app composer queues user messages and persists final assistant text."""
        message = self.engine.send({"message": "Prepare my queued draft."})
        work = self.engine._next()
        self.assertEqual(work["message"]["id"], message["message_id"])
        with patch.object(self.engine.runner, "_request", side_effect=self.model):
            self.engine._perform(work)
        history = self.engine.history.read()
        self.assertEqual(history[-1]["role"], "assistant")
        self.assertIn("Nothing was submitted", history[-1]["text"])
        self.assertEqual(history[0]["status"], "complete")
        self.assertEqual(self.w.chat.get(self.task["id"])["task"]["status"], "reviewed_draft")

    def test_provider_failure_pauses_automatic_routing_instead_of_retry_loop(self):
        """Usage errors remain visible and require explicit resume, without paid fallback."""
        with patch.object(self.engine.runner, "_request", side_effect=AccountError(429)) as request:
            self.engine.start()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not self.engine.paused:
                time.sleep(.02)
            self.assertTrue(self.engine.paused)
            self.assertEqual(request.call_count, 1)
        self.assertIn("usage limit", self.engine.error)
        self.assertEqual(self.w.chat.get(self.task["id"])["task"]["status"], "queued")

    def test_app_rechecks_external_reported_reviews(self):
        """Reported passes from the bridge cannot satisfy app-controlled reviewer execution."""
        self.w.chat.submit(self.payload())
        self.w.chat.review(self.reviewer("document_reviewer", "external-reviewer"))
        self.w.chat.review(self.reviewer("application_auditor", "external-auditor"))
        with patch.object(self.engine.runner, "_request", side_effect=self.model):
            result = self.engine.workflow.prepare(self.task["id"], "Check this draft")
        reports = self.w.chat.get(self.task["id"])["task"]["reviews"]
        self.assertTrue(all(report["identity_verified"] for report in reports.values()))
        self.assertEqual(result["status"], "reviewed_draft")
        self.assertEqual([call["instructions"] for call in self.calls], [REVIEWER, AUDITOR])

    def test_empty_completed_reply_is_an_error_not_a_successful_chat_turn(self):
        """A successful transport cannot hide a missing answer behind a synthetic success message."""
        self.engine.send({"message": "Fill my profile."})
        work = self.engine._next()
        with patch.object(self.engine.runner, "_request", return_value=response(calls=[])):
            with self.assertRaisesRegex(ValueError, "no completed text"):
                self.engine._perform(work)
        self.assertEqual(self.engine.history.read()[-1]["status"], "error")
