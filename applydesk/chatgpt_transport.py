"""Bounded HTTPS transport for official ChatGPT account endpoints only."""

import json
import socket
import ssl
import time
from http.client import HTTPSConnection
from urllib.parse import urlencode, urlsplit


class AccountError(RuntimeError):
    """Expose actionable failures without echoing provider bodies or credentials."""

    def __init__(self, status=0):
        self.status = status
        messages = {401: "ChatGPT authorization expired. Reconnect your account.",
                    403: "ChatGPT plan access is unavailable for this account or model.",
                    429: "ChatGPT usage limit reached. Pause and try again later."}
        super().__init__(messages.get(status, "ChatGPT request failed. Check the connection and try again."))


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
            raise AccountError(response.status)
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
    except (OSError, ValueError) as exc:
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
