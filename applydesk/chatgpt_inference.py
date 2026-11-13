"""One observable, cancellable Responses request per independently configured agent run."""

import json
import socket
import threading
import time
import uuid

from .chatgpt_accounts import RESOURCE
from .chatgpt_transport import AccountError, connect, stream_events
from .storage import now
from .providers import ProviderError
from . import anthropic_agent, google_agent
from .agent_transport import request_json as native_request, check_request


def output_text(response):
    """Read final assistant text only, never hidden reasoning or incomplete deltas."""
    parts = [content.get("text", "") for item in response.get("output", []) if item.get("type") == "message"
             for content in item.get("content", []) if content.get("type") == "output_text"]
    return "\n".join(parts)


class ChatGPTRunner:
    """One role run uses exactly the selected provider and credential, without fallback."""

    def __init__(self, workspace, accounts, settings):
        self.w, self.accounts, self.settings = workspace, accounts, settings
        self.connection = None
        self.connection_lock = threading.Lock()
        self.current = None

    def run(self, role, instructions, inputs, cancelled, emit, tools=None, task=None):
        """Bind role, model, task version, and provider response to an app-created run ID."""
        selection = self.settings.validate(self.settings.read()["roles"][role]).copy()
        identity = "local:" + uuid.uuid4().hex
        record = {"id": identity, "role": role, "model": selection["model"], "thinking": selection["thinking"],
                  "source": self.accounts.state().get("source", "chatgpt"),
                  "turn_id": (task or {}).get("turn_id"),
                  "task_id": (task or {}).get("id"), "version": (task or {}).get("version"), "status": "running", "started_at": now()}
        self.w.store.put("agent_run", identity, record)
        self.current = record
        try:
            payload = {"model": selection["model"], "instructions": instructions, "input": inputs, "store": False, "stream": True, "include": ["reasoning.encrypted_content"]}
            if selection["thinking"] != "default":
                payload["reasoning"] = {"effort": selection["thinking"]}
            if tools:
                payload.update(tools=tools, parallel_tool_calls=False)
            result = self._request(payload, cancelled, emit)
            record.update(status="completed", response_id=result["id"], finished_at=now())
            self.w.store.put("agent_run", identity, record)
            return identity, result
        except Exception as exc:
            record.update(status="cancelled" if cancelled.is_set() else "error", finished_at=now(), error=str(exc)[:500])
            self.w.store.put("agent_run", identity, record)
            raise
        finally:
            self.current = None

    def _request(self, payload, cancelled, emit):
        """Stop safely on incomplete streams, provider refusal, cancellation, or account change."""
        source = self.accounts.state().get("source", "chatgpt")
        if source in {"anthropic_api", "google_api"}:
            return self._native_request(source.removesuffix("_api"), payload, cancelled, emit)
        if source not in {"chatgpt", "openai_api"}:
            raise ProviderError("Select a supported agent connection source.")
        return self._openai_request(payload, cancelled, emit)

    def _openai_request(self, payload, cancelled, emit):
        """Keep the existing streamed OpenAI request path and account-specific errors."""
        generation = self.accounts.generation
        token = self.accounts.token()
        if cancelled.is_set():
            raise InterruptedError("Paused.")
        connection = None
        try:
            connection, response = connect(RESOURCE + "/responses", "POST", payload, token)
            with self.connection_lock:
                self.connection = connection
            return self._consume(response, cancelled, emit, generation)
        except AccountError:
            if cancelled.is_set():
                raise InterruptedError("Paused.") from None
            if self.accounts.state().get("source") == "openai_api":
                raise ValueError("OpenAI API request failed. Check the API key, model access, and API usage limits.") from None
            raise
        except (OSError, ValueError):
            if cancelled.is_set():
                raise InterruptedError("Paused.") from None
            raise AccountError() from None
        finally:
            with self.connection_lock:
                self.connection = None
            if connection:
                connection.close()

    def _native_request(self, provider, payload, cancelled, emit):
        """Native providers return bounded final text while retaining cancellable sockets."""
        generation = self.accounts.generation
        check_request(self, cancelled, generation)
        adapter = anthropic_agent if provider == "anthropic" else google_agent
        body = adapter.request_body(payload)
        key = self.accounts.api_key(provider)
        value = native_request(self, provider, payload["model"], body, key, cancelled, generation)
        result = adapter.parse_response(value)
        check_request(self, cancelled, generation)
        text = output_text(result)
        if text:
            emit(text)
        return result

    def _consume(self, response, cancelled, emit, generation):
        """Collect completed items; ChatGPT may omit them from the terminal response envelope."""
        items = {}
        for event in stream_events(response, cancelled):
            if cancelled.is_set() or generation != self.accounts.generation:
                raise InterruptedError("Account changed or work paused.")
            if event.get("type") == "response.output_text.delta":
                emit(str(event.get("delta", "")))
            if event.get("type") == "response.output_item.done":
                index, item = event.get("output_index"), event.get("item")
                if type(index) is not int or index < 0 or not isinstance(item, dict):
                    raise AccountError()
                items[index] = item
            if event.get("type") == "response.completed":
                result = event.get("response", {})
                if result.get("status") != "completed" or not isinstance(result.get("id"), str):
                    raise AccountError()
                if not result.get("output"):
                    result["output"] = [items[index] for index in sorted(items)]
                return result
            if event.get("type") in {"error", "response.failed", "response.incomplete"}:
                raise AccountError()
        raise AccountError()

    def cancel(self):
        """Closing the active socket interrupts a streamed response without granting writes."""
        with self.connection_lock:
            connection = self.connection
            if connection and connection.sock:
                try:
                    connection.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            if connection:
                connection.close()
