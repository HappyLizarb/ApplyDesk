"""Automatic retry preserves the existing request identity and durable submission allowance."""

import unittest
from unittest.mock import patch

import test_public_applications as public_applications
from applydesk.ai_prompts import ANALYZE
from applydesk.chatgpt_transport import AccountError
from applydesk.orchestrator_prompts import SELF_CHECK
from applydesk.application_client import STEP_BOUNDARY
from applydesk.providers import ProviderError
from applydesk.orchestrator_tools import ToolProgress


class AutoStartApplicationTests(unittest.TestCase):
    def setUp(self):
        self.case = public_applications.PublicApplicationFixture()
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def test_tool_interrupted_before_submission_reports_no_attempt_or_receipt(self):
        """The chat agent sees the actual submission ledger when the draft model fails."""
        case = self.case
        with patch("applydesk.application_automation.resolve_intent", return_value=case.intent), \
             patch("applydesk.application_automation.Applications.prepare", side_effect=AccountError()) as prepare:
            report = ToolProgress(case.engine.tools.call).execute({"name": "run_applications", "arguments": "{}"})
        self.assertEqual(prepare.call_count, 1)
        self.assertEqual(case.w.store.all("submission_attempt"), [])
        self.assertEqual((report["status"], report["attempted_count"], report["uncertain_count"]), ("interrupted", 0, 0))
        self.assertEqual(report["interrupted_job"], {"job_id": prepare.call_args.args[0], "submission_status": "not_attempted",
                                                   "receipt_confirmed": False})
        self.assertEqual(report["submitted_count"], 0)
        self.assertIn("complete response", report["error"])

    def test_tool_reports_a_reserved_attempt_separately_from_an_interrupted_draft(self):
        """An interrupted receipt check retains the ledger's actual uncertainty."""
        case = self.case
        job = case.jobs[0]
        def receipt_failed(engine, ready, run, checkpoint):
            attempt = {"id": "attempt", "job_id": job["id"], "run_id": run["id"], "status": "submission_unknown", "receipt": {}}
            case.w.store.put("submission_attempt", "attempt", attempt)
            raise AccountError(503)
        with patch("applydesk.application_automation.resolve_intent", return_value=case.intent), \
             patch("applydesk.application_automation.Applications.prepare", return_value={**job, "status": "ready"}), \
             patch("applydesk.application_automation.submit_ready", side_effect=receipt_failed):
            report = ToolProgress(case.engine.tools.call).execute({"name": "run_applications", "arguments": "{}"})
        self.assertEqual((report["attempted_count"], report["uncertain_count"], report["submitted_count"]), (1, 1, 0))
        self.assertEqual(report["interrupted_job"], {"job_id": job["id"], "submission_status": "submission_unknown",
                                                   "receipt_confirmed": False})

    def test_interrupted_response_retries_only_inference_before_filling_and_sending_once(self):
        """One truncated stream reuses the same model input and never replays a browser operation."""
        case, analyses = self.case, []
        original = case.model
        def model(payload, cancelled, emit):
            if payload["instructions"] == SELF_CHECK + STEP_BOUNDARY + ANALYZE:
                analyses.append(payload["input"])
                if len(analyses) == 1:
                    raise AccountError(stream_ended=True)
            return original(payload, cancelled, emit)
        case.model = model
        run = case.run_public()
        self.assertEqual((len(analyses), run["attempted_count"], run["submitted_count"]), (2, 1, 1))
        self.assertEqual(analyses[0], analyses[1])
        self.assertEqual(sum(browser.clicks for browser in case.browsers), 1)

    def test_retry_continues_interrupted_run_without_a_new_grant_or_duplicate(self):
        case = self.case
        case.intent["limit"] = 2
        message = case.engine.active["message"]
        message["text"] = "Submit two matching applications in Boston."
        case.w.store.put("chat_message", message["id"], message)
        original, analyses = case.model, []
        def model(payload, cancelled, emit):
            if payload["instructions"] == SELF_CHECK + STEP_BOUNDARY + ANALYZE:
                analyses.append(payload)
                if len(analyses) == 2:
                    raise ProviderError("Usage limit", status=429)
            return original(payload, cancelled, emit)
        case.model = model
        with self.assertRaises(ProviderError):
            case.run_public()
        first = case.w.store.all("application_run")[0]
        self.assertEqual((first["status"], first["attempted_count"]), ("interrupted", 1))
        self.assertTrue(first["usage_limit_retry"])
        self.assertEqual(case.run_public()["status"], "interrupted")
        case.engine.active["auto_restart"] = True
        resumed = case.run_public()
        self.assertEqual((resumed["id"], resumed["message_id"]), (first["id"], message["id"]))
        self.assertEqual((resumed["limit"], resumed["attempted_count"], resumed["submitted_count"]), (2, 2, 2))
        self.assertEqual(len(case.w.store.all("application_run")), 1)
        self.assertEqual(sum(browser.clicks for browser in case.browsers), 2)
        self.assertEqual(case.run_public()["attempted_count"], 2)

    def test_timer_does_not_resume_a_non_limit_interrupted_application_run(self):
        case, original = self.case, self.case.model
        for status in (403, 0):
            case.w.store.clear("application_run")
            case.engine.active.pop("auto_restart", None)
            def model(payload, cancelled, emit):
                if payload["instructions"] == SELF_CHECK + STEP_BOUNDARY + ANALYZE:
                    raise AccountError(status)
                return original(payload, cancelled, emit)
            case.model = model
            with self.assertRaises(AccountError):
                case.run_public()
            first = case.w.store.all("application_run")[0]
            self.assertFalse(first.get("usage_limit_retry", False))
            opened = len(case.browsers)
            case.engine.active["auto_restart"] = True
            self.assertEqual(case.run_public(), first)
            self.assertEqual(len(case.browsers), opened)

    def test_shutdown_during_automatic_resume_retains_prior_run_limit_evidence(self):
        case, original = self.case, self.case.model
        def limited(payload, cancelled, emit):
            if payload["instructions"] == SELF_CHECK + STEP_BOUNDARY + ANALYZE:
                raise AccountError(429)
            return original(payload, cancelled, emit)
        case.model = limited
        with self.assertRaises(AccountError):
            case.run_public()
        first = case.w.store.all("application_run")[0]
        case.engine.active["auto_restart"] = True
        def closing(payload, cancelled, emit):
            case.engine.stop.set()
            case.engine.cancelled.set()
            raise InterruptedError("App closed")
        case.model = closing
        with self.assertRaises(InterruptedError):
            case.run_public()
        saved = case.w.store.get("application_run", first["id"])
        self.assertTrue(saved["usage_limit_retry"])
        self.assertEqual((saved["status"], saved["attempted_count"]), ("interrupted", 0))
