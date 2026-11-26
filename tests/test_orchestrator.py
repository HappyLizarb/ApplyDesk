"""Behavioral routing tests use independent synthetic model responses and real draft validation."""

import copy
import json
import time
from unittest.mock import patch

from chat_helpers import ChatFixture
from applydesk.chatgpt_transport import AccountError
from applydesk.orchestrator_prompts import MASTER, DRAFTER, SELF_CHECK


def response(text="", calls=None):
    """Represent only a completed provider response; streamed partial text cannot pass."""
    output = calls or [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}]
    return {"id": "response-" + str(time.monotonic_ns()), "status": "completed", "output": output}


class OrchestratorTests(ChatFixture):
    def test_inventory_shows_current_resume_tags_and_excludes_replaced_upload(self):
        """The agent sees the same résumé choices as the application's attachment selector."""
        self.document("replacement", "resume")
        self.w.resumes.add("replacement")
        self.w.resumes.tag({"document_id": "replacement", "category": "General"})
        resumes = [row for row in self.engine.tools.overview()["documents"] if row["kind"] == "resume"]
        selected = self.w.resumes.select("Unlisted industry")
        self.assertEqual([(row["id"], row["category"]) for row in resumes], [(selected["id"], "General")])
        self.assertNotIn("source_path", resumes[0])

    def test_new_chat_excludes_old_submission_limits_and_retains_job_history(self):
        """Reset limits in both chat views without losing prior attempts or their saved jobs."""
        run = {"id": "prior", "limit": 5, "results": [], "conversation_id": self.engine.history.since}
        self.w.store.put("application_run", "prior", run)
        self.assertEqual(self.engine.tools.overview()["application_runs"][0]["limit"], 5)
        self.engine.reset({})
        self.assertEqual(self.engine.state()["application_runs"], [])
        self.assertEqual(self.engine.tools.overview()["application_runs"], [])
        self.assertEqual(self.w.store.get("application_run", "prior"), run)
        self.assertIn(self.job["id"], [job["id"] for job in self.engine.tools.overview()["jobs"]])

    def setUp(self):
        """Seed account metadata only; the test adapter must never request real credentials."""
        super().setUp()
        self.engine = self.w.orchestrator
        self.w.store.put("chatgpt", "accounts", {"active": "test", "host_id": "host", "accounts": [{"id": "test", "label": "Test", "connected": True, "client_id": "oaiapp_test", "subject": "subject"}]})
        self.w.store.put("chatgpt_models", "test", [{"id": "test-model", "name": "Test", "thinking": ["low", "high"]}])
        self.calls = []
        self.fail_draft = False

    def model(self, payload, cancelled, emit):
        """Sequential chat and draft steps share one model and self-check instruction."""
        self.calls.append(copy.deepcopy(payload))
        self.assertFalse(payload["store"])
        self.assertTrue(payload["stream"])
        self.assertTrue(payload["instructions"].startswith(SELF_CHECK))
        prompt = payload["instructions"].removeprefix(SELF_CHECK)
        if prompt == MASTER:
            if any(item.get("type") == "function_call_output" for item in payload["input"]):
                emit("Your draft is prepared. Nothing was submitted.")
                return response("Your draft is prepared. Nothing was submitted.")
            return response(calls=[{"type": "function_call", "namespace": "applydesk", "name": "prepare_task", "call_id": "call-1", "arguments": json.dumps({"task_id": self.task["id"], "instruction": "Prepare this draft."})}])
        self.assertEqual(prompt, DRAFTER)
        return response(json.dumps({"blocked": ["Missing application answer"]} if self.fail_draft else {"result": self.payload()["result"]}))

    def test_automatic_queue_uses_one_agent_and_keeps_job_draft(self):
        """Connected idle app processes the queue without a Codex process or user chat turn."""
        original = self.w.profile()
        with patch.object(self.engine.runner, "_request", side_effect=self.model):
            self.engine.start()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if self.w.chat.get(self.task["id"])["task"]["status"] == "draft_ready" and self.engine.active is None:
                    break
                time.sleep(.02)
            self.engine.control({"paused": True})
        task = self.w.chat.get(self.task["id"])["task"]
        self.assertEqual(task["status"], "draft_ready")
        self.assertEqual(task["reviews"], {})
        self.assertEqual(self.w.profile(), original)
        self.assertEqual(self.w.store.get("job", self.job["id"])["status"], "draft")
        self.assertEqual(self.engine.history.read()[-1]["status"], "complete")
        runs = self.w.store.all("agent_run")
        self.assertEqual({run["role"] for run in runs}, {"master"})
        self.assertEqual(len(runs), 3)
        self.assertEqual(len({run["turn_id"] for run in runs}), 1)
        self.assertTrue(runs[0]["turn_id"])

    def test_draft_blockers_and_reserved_execution_ids_remain_enforced(self):
        """Self-check findings block the draft; a client cannot impersonate app-owned execution."""
        self.fail_draft = True
        with patch.object(self.engine.runner, "_request", side_effect=self.model):
            result = self.engine.workflow.prepare(self.task["id"], "Prepare")
            with self.assertRaises(ValueError):
                self.engine.workflow.prepare(self.task["id"], "Try again")
        self.assertEqual(result["status"], "needs_input")
        job = self.w.store.get("job", self.job["id"])
        self.assertEqual(job["status"], "needs_review")
        self.assertEqual(job["exceptions"], ["Missing application answer"])
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(ValueError):
            self.w.chat.submit({**self.payload(), "agent_id": "local:invented"})
        self.engine.retry({"task_id": self.task["id"]})
        self.assertEqual(self.w.chat.get(self.task["id"])["task"]["status"], "queued")
        self.fail_draft = False
        with patch.object(self.engine.runner, "_request", side_effect=self.model):
            self.engine.workflow.prepare(self.task["id"], "Prepare the corrected draft")
        self.assertEqual(self.w.store.get("job", self.job["id"])["exceptions"], [])

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
        task = self.w.chat.get(self.task["id"])["task"]
        self.assertIsNone(task["result"])
        self.assertEqual(task["status"], "queued")

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
        self.assertEqual(self.w.chat.get(self.task["id"])["task"]["status"], "draft_ready")

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

    def test_summary_retry_never_repeats_an_executed_tool(self):
        """Recover one transient summary failure after a completed tool and discard its partial text."""
        self.engine.send({"message": "Check my workspace."})
        work = self.engine._next()
        call = {"name": "workspace_overview", "type": "function_call", "call_id": "once", "arguments": "{}"}
        answers = iter([response(calls=[call]), AccountError(520), response("Done.")])
        def model(payload, cancelled, emit):
            item = next(answers)
            if isinstance(item, AccountError):
                emit("partial text")
                raise item
            return item
        with patch.object(self.engine.runner, "_request", side_effect=model), patch.object(self.engine.tools, "call", return_value={}) as tool:
            self.engine._perform(work)
        tool.assert_called_once_with(call)
        self.assertEqual(self.engine.history.read()[-1]["text"], "Done.")

    def test_failed_summary_shows_only_this_turns_saved_outcomes(self):
        """Old blockers stay in the tracker without hiding the latest verified result or provider error."""
        from applydesk.orchestrator_tools import run_report
        prior = {"job_id": "older-job", "status": "needs_review", "issues": ["Old issue"]}
        current = {"job_id": self.job["id"], "status": "needs_review", "issues": ["Resume upload unavailable."]}
        run = {"id": "r", "message_result_starts": {"current": 1}, "results": [prior, current], "submitted_count": 0}
        self.engine.applications.run = run
        self.assertEqual(run_report(run, "current")["results"], [current])
        self.assertNotIn("results", run_report(run))
        detail = self.engine._run_error(AccountError(503), {"id": "current"})
        self.assertIn("Resume upload unavailable", detail)
        self.assertIn("HTTP 503", detail)
        self.assertNotIn("Old issue", detail)

    def test_prepared_draft_does_not_schedule_more_model_requests(self):
        """A valid existing draft is reusable without any reviewer or repeated draft call."""
        self.w.chat.submit(self.payload())
        with patch.object(self.engine.runner, "_request", side_effect=AssertionError("Unexpected request")):
            result = self.engine.workflow.prepare(self.task["id"], "Use this draft")
        self.assertEqual(result["status"], "draft_ready")

    def test_empty_completed_reply_is_an_error_not_a_successful_chat_turn(self):
        """A successful transport cannot hide a missing answer behind a synthetic success message."""
        self.engine.send({"message": "Fill my profile."})
        work = self.engine._next()
        with patch.object(self.engine.runner, "_request", return_value=response(calls=[])):
            with self.assertRaisesRegex(ValueError, "no completed text"):
                self.engine._perform(work)
        self.assertEqual(self.engine.history.read()[-1]["status"], "error")
