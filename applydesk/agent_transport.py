"""Bounded cancellable HTTPS requests to pinned native model endpoints, without redirects."""

import json
import ssl
import time
from http.client import HTTPSConnection, HTTPException

from .providers import ProviderError, _json_object, _validate_key, validate_model_id

HOSTS = {"anthropic": "api.anthropic.com", "google": "generativelanguage.googleapis.com"}
MAX_BODY, MAX_RESPONSE, TIME_LIMIT = 32 * 1024 * 1024, 12 * 1024 * 1024, 180


def check_request(runner, cancelled, generation):
    """Cancellation and source identity are checked before network use and final output."""
    if cancelled.is_set():
        raise InterruptedError("Paused. No further draft changes were saved.")
    if runner.accounts.generation != generation:
        raise InterruptedError("The selected account changed. No further draft changes were saved.")


def request_json(runner, provider, model, body, key, cancelled, generation):
    """Register the socket before waiting for headers so Stop interrupts native requests."""
    _validate_key(key)
    model = validate_model_id(provider, model)
    if provider not in HOSTS:
        raise ProviderError("Unsupported native model provider.")
    encoded = json.dumps(body, allow_nan=False).encode()
    if len(encoded) > MAX_BODY:
        raise ProviderError("The agent request exceeds the selected provider's input limit.")
    check_request(runner, cancelled, generation)
    connection = HTTPSConnection(HOSTS[provider], timeout=30, context=ssl.create_default_context())
    with runner.connection_lock:
        runner.connection = connection
    try:
        return _exchange(connection, runner, provider, model, encoded, key, cancelled, generation)
    except ProviderError:
        check_request(runner, cancelled, generation)
        raise
    except (OSError, HTTPException, ValueError):
        check_request(runner, cancelled, generation)
        raise ProviderError(f"{provider.title()} connection failed or timed out. Check connectivity and try again.") from None
    finally:
        with runner.connection_lock:
            if runner.connection is connection:
                runner.connection = None
        connection.close()


def _exchange(connection, runner, provider, model, body, key, cancelled, generation):
    """Credentials go only into headers for the explicitly selected official endpoint."""
    path = "/v1/messages" if provider == "anthropic" else f"/v1beta/models/{model}:generateContent"
    headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "ApplyDesk/1.0"}
    if provider == "anthropic":
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
    else:
        headers["x-goog-api-key"] = key
    deadline = time.monotonic() + TIME_LIMIT
    connection.connect()
    check_request(runner, cancelled, generation)
    sock = connection.sock
    if sock is None:
        check_request(runner, cancelled, generation)
        raise ProviderError("The provider connection closed before sending the request.")
    sock.settimeout(max(1, deadline - time.monotonic()))
    connection.request("POST", path, body, headers)
    check_request(runner, cancelled, generation)
    response = connection.getresponse()
    check_request(runner, cancelled, generation)
    if response.status != 200:
        hints = {401: "Check the saved API key.", 403: "Check model access.", 429: "Check API quota or try again later."}
        raise ProviderError(f"{provider.title()} API HTTP {response.status}. " + hints.get(response.status, "Check the model and API settings."))
    return _read_response(response, connection, runner, cancelled, generation, deadline)


def _read_response(response, connection, runner, cancelled, generation, deadline):
    """Bound bytes and wall time, and discard output after cancellation or source changes."""
    chunks, size = [], 0
    while True:
        check_request(runner, cancelled, generation)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderError("The model response timed out. Try a lower thinking strength.")
        sock = connection.sock
        if sock:
            sock.settimeout(remaining)
        chunk = response.read(min(65536, MAX_RESPONSE + 1 - size))
        if not chunk:
            break
        size += len(chunk)
        if size > MAX_RESPONSE:
            raise ProviderError("The model response exceeded the size limit.")
        chunks.append(chunk)
    check_request(runner, cancelled, generation)
    return _json_object(b"".join(chunks))
