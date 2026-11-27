"""Stream completion and authentication boundaries without any external network."""

import io
import json
import threading
from applydesk import agent_transport
from applydesk.auto_start import usage_limited
from applydesk.chatgpt_inference import output_text
from applydesk.chatgpt_transport import AccountError, connect, stream_events
from applydesk.orchestrator_http import orchestrator_action
from applydesk.providers import ProviderError
from chat_helpers import ChatFixture
from types import SimpleNamespace
from unittest.mock import Mock, patch


class StreamTests(ChatFixture):
    def test_partial_stream_never_becomes_completed_output(self):
        """A closed stream after visible text cannot create a completed agent result."""
        stream = io.BytesIO(b'data: {"type":"response.output_text.delta","delta":"Partial"}\n\n')
        connection = Mock()
        with patch.object(self.w.orchestrator.accounts, "token", return_value="synthetic"), patch("applydesk.chatgpt_inference.connect", return_value=(connection, stream)):
            with self.assertRaises(AccountError):
                self.w.orchestrator.runner._request({}, threading.Event(), lambda _: None)
        connection.close.assert_called_once()

    def test_sse_cancellation_and_complete_response(self):
        """Only an explicit completed event ends successfully; cancellation takes precedence."""
        value = {"type": "response.completed", "response": {"id": "r1", "status": "completed", "output": []}}
        raw = ("data: " + json.dumps(value) + "\n\n").encode()
        with patch.object(self.w.orchestrator.accounts, "token", return_value="synthetic"), patch("applydesk.chatgpt_inference.connect", return_value=(Mock(), io.BytesIO(raw))):
            result = self.w.orchestrator.runner._request({}, threading.Event(), lambda _: None)
        self.assertEqual(result["id"], "r1")
        cancelled = threading.Event();cancelled.set()
        with self.assertRaises(InterruptedError):
            list(stream_events(io.BytesIO(raw), cancelled))

    def test_untrusted_hosts_are_rejected_before_connecting(self):
        """The adapter cannot turn a model-supplied URL into an authenticated request."""
        for url in ("http://api.openai.com/v1/responses", "https://evil.test/v1/responses", "https://api.openai.com:444/v1/responses", "https://user@api.openai.com/v1/responses"):
            with self.assertRaises(ValueError):
                connect(url, "GET", token="synthetic")

    def test_control_contract_rejects_extra_authority(self):
        """UI controls cannot accept a supplied model response or application approval."""
        for action, data in (("send", {"message": "Hello", "approved": True}), ("control", {"paused": False, "submit": True}), ("approve", {})):
            with self.assertRaises(ValueError):
                orchestrator_action(self.w, action, data)

    def run_stream(self, events):
        """Exercise the real stream adapter with synthetic SSE frames and no remote credentials."""
        raw = "".join("data: " + json.dumps(event) + "\n\n" for event in events).encode()
        emitted = []
        with patch.object(self.w.orchestrator.accounts, "token", return_value="synthetic"), patch("applydesk.chatgpt_inference.connect", return_value=(Mock(), io.BytesIO(raw))):
            result = self.w.orchestrator.runner._request({}, threading.Event(), emitted.append)
        return result, emitted

    def test_terminal_empty_output_preserves_completed_message(self):
        """The live ChatGPT response shape must retain the answer after streaming finishes."""
        item = {"id": "message-1", "type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": "Your profile draft is ready."}]}
        result, emitted = self.run_stream([
            {"type": "response.output_text.delta", "delta": "Your profile draft is ready."},
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {"type": "response.completed", "response": {"id": "r1", "status": "completed", "output": []}}])
        self.assertEqual(output_text(result), "Your profile draft is ready.")
        self.assertEqual(emitted, ["Your profile draft is ready."])

    def test_completed_tool_calls_and_reasoning_retain_provider_order(self):
        """Delegation calls must survive an empty final envelope, with encrypted reasoning intact."""
        call = {"id": "fc1", "type": "function_call", "namespace": "applydesk", "name": "workspace_overview", "call_id": "call-1", "arguments": "{}"}
        reasoning = {"id": "r0", "type": "reasoning", "encrypted_content": "synthetic"}
        result, _ = self.run_stream([
            {"type": "response.output_item.done", "output_index": 1, "item": call},
            {"type": "response.output_item.done", "output_index": 0, "item": reasoning},
            {"type": "response.completed", "response": {"id": "r1", "status": "completed", "output": []}}])
        self.assertEqual(result["output"], [reasoning, call])

    def test_complete_envelope_is_not_duplicated_and_partial_items_are_not_promoted(self):
        """Prefer a populated final output; deltas and added items are never final evidence."""
        item = {"type": "function_call", "name": "read_profile", "arguments": "{}", "call_id": "call-1"}
        result, _ = self.run_stream([
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {"type": "response.completed", "response": {"id": "r1", "status": "completed", "output": [item]}}])
        self.assertEqual(result["output"], [item])
        with self.assertRaises(AccountError):
            self.run_stream([{"type": "response.output_item.done", "output_index": 0, "item": item}])
        partial, _ = self.run_stream([
            {"type": "response.output_item.added", "output_index": 0, "item": item},
            {"type": "response.completed", "response": {"id": "r1", "status": "completed", "output": []}}])
        self.assertEqual(partial["output"], [])

    def test_malformed_completed_item_is_rejected(self):
        """Invalid stream indexes cannot masquerade as complete tool output."""
        with self.assertRaises(AccountError):
            self.run_stream([{"type": "response.output_item.done", "output_index": True, "item": {}}])

    def test_http_and_stream_errors_expose_only_a_known_reason(self):
        """An oversized request, exhausted quota and an incomplete reply need different recovery."""
        raw = {"error": {"code": "context_length_exceeded", "message": "private applicant data"}}
        with patch("applydesk.chatgpt_transport.HTTPSConnection") as https:
            response = https.return_value.getresponse.return_value
            response.status, response.read.return_value = 400, json.dumps(raw).encode()
            with self.assertRaisesRegex(AccountError, "HTTP 400.*context window") as failure:
                connect("https://api.openai.com/v1/responses", "POST", {})
        self.assertFalse(failure.exception.retryable)
        self.assertNotIn("private applicant", str(failure.exception))
        with self.assertRaisesRegex(AccountError, "output limit"):
            self.run_stream([{"type": "response.incomplete", "response": {"incomplete_details": {"reason": "max_output_tokens"}}}])
        with self.assertRaisesRegex(AccountError, "insufficient API quota") as quota:
            self.run_stream([{"type": "error", "status": 429, "code": "insufficient_quota"}])
        self.assertFalse(usage_limited(quota.exception))


class UsageLimitTransportTests(ChatFixture):
    def stream_error(self, event, source="chatgpt"):
        """Only controlled SSE error objects enter this fixture; there is no remote request."""
        raw = ("data: " + json.dumps(event) + "\n\n").encode()
        runner = self.w.orchestrator.runner
        with patch.object(runner.accounts, "token", return_value="synthetic"), \
                patch.object(runner.accounts, "state", return_value={"source": source}), \
                patch("applydesk.chatgpt_inference.connect", return_value=(Mock(), io.BytesIO(raw))):
            with self.assertRaises((AccountError, ProviderError)) as caught:
                runner._request({}, threading.Event(), lambda _: None)
        return caught.exception

    def test_subscription_usage_codes_are_typed_limits(self):
        events = [{"type": "error", "code": "usage_limit_reached"},
                  {"type": "error", "error": {"code": "rate_limit_exceeded"}},
                  {"type": "response.failed", "response": {"error": {"type": "rate_limit_error"}}},
                  {"type": "response.incomplete", "response": {"error": {"code": "usage_limit_exceeded"}}}]
        for event in events:
            with self.subTest(event=event):
                self.assertTrue(usage_limited(self.stream_error(event)))
                self.assertTrue(usage_limited(self.stream_error(event, "openai_api")))

    def test_messages_and_malformed_or_other_codes_never_authorize_retry(self):
        for error in ({"message": "usage limit reached"}, {"code": "authentication_error"},
                      {"code": []}, {"code": {}}, {"type": []}, {"type": {}},
                      {"status": "429"}, {"status": True}, {"code": "max_output_tokens"}):
            with self.subTest(error=error):
                self.assertFalse(usage_limited(self.stream_error({"type": "error", "error": error})))

    def test_native_http_status_is_preserved_without_reading_error_body(self):
        runner = SimpleNamespace(connection_lock=threading.Lock(), connection=None,
                                 accounts=SimpleNamespace(generation=1))
        connection, response = Mock(), Mock(status=429)
        connection.getresponse.return_value = response
        with patch.object(agent_transport, "HTTPSConnection", return_value=connection):
            with self.assertRaises(ProviderError) as caught:
                agent_transport.request_json(runner, "xai", "grok-test", {}, "synthetic-key", threading.Event(), 1)
        self.assertTrue(usage_limited(caught.exception))
        response.read.assert_not_called()
