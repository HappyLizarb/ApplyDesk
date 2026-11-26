"""Structured usage-limit evidence survives streaming and native provider boundaries."""

import io
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

from chat_helpers import ChatFixture
from applydesk import agent_transport
from applydesk.auto_start import usage_limited
from applydesk.chatgpt_transport import AccountError
from applydesk.providers import ProviderError


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
