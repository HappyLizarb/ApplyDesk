"""Bounded HTTPS transport for official ChatGPT account endpoints only."""

import json
import ssl
import time
from http.client import HTTPException, HTTPSConnection
from urllib.parse import urlencode, urlsplit


# Only fixed provider categories may reach the local history, never response bodies.
ERROR_REASONS = {
    "context_length_exceeded": "The conversation exceeds the model's context window.",
    "input_too_large": "The request exceeds the model's input limit.",
    "invalid_request_error": "The model rejected the request format.",
    "invalid_value": "The model rejected a request parameter.",
    "model_not_found": "The selected model is unavailable to this connection.",
    "server_error": "The model service reported a temporary error.",
    "insufficient_quota": "The account has insufficient API quota.",
    "max_output_tokens": "The model reached its output limit before completing the response.",
    "content_filter": "The provider stopped the response during content filtering.",
}
LIMIT_CODES = {"usage_limit_reached", "usage_limit_exceeded", "rate_limit_exceeded", "rate_limit_error"}


class AccountError(RuntimeError):
    """Expose actionable failures without echoing provider bodies or credentials."""

    def __init__(self, status=0, *, code="", connection_failed=False, stream_ended=False):
        self.status = status
        self.code = code if code in ERROR_REASONS or code in LIMIT_CODES else ""
        self.retryable = (connection_failed or stream_ended or status in {408, 500, 502, 503, 504, 520}
                          or (not status and self.code == "server_error"))
        messages = {401: "ChatGPT authorization expired. Reconnect your account.",
                    403: "ChatGPT plan access is unavailable for this account or model.",
                    429: "ChatGPT usage limit reached. Pause and try again later."}
        fallback = "The connection to ChatGPT failed." if connection_failed else "ChatGPT did not return a complete response."
        reason = ERROR_REASONS.get(self.code) or messages.get(status)
        if status:
            reason = f"ChatGPT returned HTTP {status}." + (" " + reason if reason else "")
        super().__init__(reason or fallback)


def provider_failure(event, status=0):
    """Retain only recognized error codes and numeric statuses from JSON or streamed responses."""
    if not isinstance(event, dict):
        return AccountError(status)
    response = event.get("response") if isinstance(event.get("response"), dict) else {}
    parts = [part for part in (event.get("error"), response.get("error"), response.get("incomplete_details"), event)
             if isinstance(part, dict)]
    if not status:
        status = next((part["status"] for part in parts if type(part.get("status")) is int and 400 <= part["status"] <= 599), 0)
    for part in parts:
        for key in ("code", "reason", "type"):
            code = part.get(key)
            if isinstance(code, str) and (code in ERROR_REASONS or code in LIMIT_CODES):
                return AccountError(status or (429 if code in LIMIT_CODES else 0), code=code)
    return AccountError(status)


def connect(url, method, body=None, token=None, form=False):
    """Never follow redirects or send credentials outside the two official HTTPS hosts."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {"auth.openai.com", "api.openai.com"} or parsed.port not in {None, 443} or parsed.username or parsed.fragment:
        raise ValueError("Invalid ChatGPT endpoint.")
    connection = HTTPSConnection(parsed.hostname, timeout=30, context=ssl.create_default_context())
    headers = {"Accept": "application/json", "User-Agent": "ApplyDesk/1.0"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        body = urlencode(body).encode() if form else json.dumps(body).encode()
    try:
        connection.request(method, parsed.path + ("?" + parsed.query if parsed.query else ""), body, headers)
        response = connection.getresponse()
        if response.status != 200:
            try:
                details = json.loads(response.read(16385))
            except (OSError, HTTPException, ValueError, RecursionError):
                details = {}
            raise provider_failure(details, response.status)
        return connection, response
    except Exception:
        connection.close()
        raise


def request_json(url, body=None, token=None, form=False):
    """Account metadata and token responses have a strict size bound and no raw error logs."""
    try:
        connection, response = connect(url, "POST" if body is not None else "GET", body, token, form)
        try:
            content = response.read(2 * 1024 * 1024 + 1)
            if len(content) > 2 * 1024 * 1024:
                raise AccountError()
            return json.loads(content) if content else {}
        finally:
            connection.close()
    except (OSError, HTTPException):
        raise AccountError(connection_failed=True) from None
    except ValueError:
        raise AccountError() from None


def stream_events(response, cancelled):
    """Accept bounded SSE messages through completion, checking cancellation between reads."""
    data, total, deadline = [], 0, time.monotonic() + 600
    while not cancelled.is_set() and time.monotonic() < deadline:
        line = response.readline(2 * 1024 * 1024 + 1)
        if not line:
            break
        total += len(line)
        if len(line) > 2 * 1024 * 1024 or total > 12 * 1024 * 1024:
            raise AccountError()
        line = line.decode("utf-8").rstrip("\r\n")
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line and data:
            value = "\n".join(data)
            data = []
            if value == "[DONE]":
                break
            yield json.loads(value)
    if cancelled.is_set():
        raise InterruptedError("Paused. No further draft changes were saved.")
