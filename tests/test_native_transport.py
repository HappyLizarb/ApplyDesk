"""Offline network fixtures prove provider pinning, cancellation, and safe error output."""

import io
import json
import socket
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from applydesk import agent_transport
from applydesk.chatgpt_inference import ChatGPTRunner
from applydesk.providers import ProviderError


class NativeTransportTests(unittest.TestCase):
    def setUp(self):
        """A mock HTTPS connection never opens a real socket or reads real credentials."""
        self.cancelled = threading.Event()
        self.runner = SimpleNamespace(connection_lock=threading.Lock(), connection=None,
                                      accounts=SimpleNamespace(generation=7))
        self.connection = Mock()
        self.response = SimpleNamespace(status=200, read=io.BytesIO(b'{"ok":true}').read)
        self.connection.getresponse.return_value = self.response
        self.factory = patch.object(agent_transport, 'HTTPSConnection', return_value=self.connection).start()
        self.addCleanup(patch.stopall)

    def test_requests_use_only_the_selected_providers_endpoint_and_credentials(self):
        """Each provider receives its own headers; keys never enter the URL or body."""
        cases = [
            ('anthropic', 'claude-sonnet-4-6', 'api.anthropic.com', '/v1/messages',
             {'x-api-key': 'private-key', 'anthropic-version': '2023-06-01'}),
            ('google', 'gemini-3.8-flash', 'generativelanguage.googleapis.com', '/v1beta/models/gemini-3.8-flash:generateContent',
             {'x-goog-api-key': 'private-key'}),
            ('xai', 'grok-4.7', 'api.x.ai', '/v1/responses', {'Authorization': 'Bearer private-key'}),
        ]
        provider_headers = {'x-api-key', 'anthropic-version', 'x-goog-api-key', 'Authorization'}
        for provider, model, host, endpoint, expected in cases:
            with self.subTest(provider=provider):
                self.connection.reset_mock()
                self.response.read = io.BytesIO(b'{"ok":true}').read
                result = agent_transport.request_json(self.runner, provider, model, {}, 'private-key', self.cancelled, 7)
                self.assertEqual(result, {'ok': True})
                method, path, body, headers = self.connection.request.call_args.args
                self.assertEqual((self.factory.call_args.args[0], method, path), (host, 'POST', endpoint))
                self.assertEqual({name: headers[name] for name in provider_headers & headers.keys()}, expected)
                self.assertNotIn(b'private-key', body)
                self.assertIsNone(self.runner.connection)
                self.connection.close.assert_called_once()
        with self.assertRaises(ProviderError):
            agent_transport.request_json(self.runner, 'google', 'https://elsewhere/model', {}, 'key', self.cancelled, 7)

    def test_http_errors_never_read_or_expose_provider_error_bodies(self):
        """Error responses may echo secrets, so only status-specific local text is returned."""
        self.response.status = 401
        self.response.read = Mock(return_value=b'private-key supplied in upstream error')
        with self.assertRaises(ProviderError) as caught:
            agent_transport.request_json(self.runner, 'anthropic', 'claude-sonnet-4-6', {}, 'private-key', self.cancelled, 7)
        self.assertNotIn('private-key', str(caught.exception))
        self.assertIn('401', str(caught.exception))
        self.response.read.assert_not_called()
        self.connection.close.assert_called_once()

    def test_cancellation_before_request_opens_no_socket(self):
        """An already-paused operation cannot start a provider request."""
        self.cancelled.set()
        with self.assertRaises(InterruptedError):
            agent_transport.request_json(self.runner, 'google', 'gemini-3.8-flash', {}, 'key', self.cancelled, 7)
        self.factory.assert_not_called()

    def test_stop_interrupts_socket_while_waiting_for_response_headers(self):
        """The runner owns the socket before getresponse begins its blocking wait."""
        def stop_while_waiting():
            self.assertIs(self.runner.connection, self.connection)
            self.cancelled.set()
            ChatGPTRunner.cancel(self.runner)
            raise OSError('private-key should never escape')
        self.connection.getresponse.side_effect = stop_while_waiting
        with self.assertRaises(InterruptedError) as caught:
            agent_transport.request_json(self.runner, 'anthropic', 'claude-sonnet-4-6', {}, 'private-key', self.cancelled, 7)
        self.connection.sock.shutdown.assert_called_with(socket.SHUT_RDWR)
        self.assertNotIn('private-key', str(caught.exception))
        self.assertIsNone(self.runner.connection)

    def test_source_change_during_read_discards_output(self):
        """A valid response from a superseded account cannot be returned to the workflow."""
        def change_source(_size):
            self.runner.accounts.generation += 1
            return b'{"ok":true}'
        self.response.read = change_source
        with self.assertRaises(InterruptedError):
            agent_transport.request_json(self.runner, 'google', 'gemini-3.8-flash', {}, 'key', self.cancelled, 7)

    def test_response_byte_limit_and_invalid_json_fail_closed(self):
        """Oversized, truncated, and duplicate-key JSON never becomes an agent response."""
        with patch.object(agent_transport, 'MAX_RESPONSE', 4), self.assertRaises(ProviderError):
            agent_transport.request_json(self.runner, 'google', 'gemini-3.8-flash', {}, 'key', self.cancelled, 7)
        for raw in [b'{"unfinished":', b'{"a":1,"a":2}', b'not-json-private-key']:
            self.response.read = io.BytesIO(raw).read
            with self.subTest(raw=raw), self.assertRaises(ProviderError) as caught:
                agent_transport.request_json(self.runner, 'google', 'gemini-3.8-flash', {}, 'private-key', self.cancelled, 7)
            self.assertNotIn('private-key', str(caught.exception))

    def test_request_byte_limit_prevents_network_use(self):
        """Oversized document payloads are rejected before constructing a connection."""
        with patch.object(agent_transport, 'MAX_BODY', 4), self.assertRaises(ProviderError):
            agent_transport.request_json(self.runner, 'google', 'gemini-3.8-flash', {'large': 'request'}, 'key', self.cancelled, 7)
        self.factory.assert_not_called()

    def test_network_exception_is_sanitized(self):
        """Transport implementation details and credentials never enter user error records."""
        self.connection.connect.side_effect = OSError('private-key from network implementation')
        with self.assertRaises(ProviderError) as caught:
            agent_transport.request_json(self.runner, 'anthropic', 'claude-sonnet-4-6', {}, 'private-key', self.cancelled, 7)
        self.assertNotIn('private-key', str(caught.exception))
        self.assertIn('connection failed', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
