"""Shared provider input validation and bounded model-catalog transport."""

import json
import re
import socket
from urllib import error, request


class ProviderError(ValueError):
    """An actionable provider failure safe to display without credential leakage."""

    def __init__(self, message, status=0, code=""):
        self.status = status
        self.code = code
        super().__init__(message)


def validate_model_id(provider, model):
    """Allow provider IDs, never URLs, query strings, paths, or control characters."""
    if isinstance(model, str) and provider == "google" and model.startswith("models/"):
        model = model[7:]
    pattern = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}" if provider == "openai" else r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}"
    if not isinstance(model, str) or not re.fullmatch(pattern, model):
        raise ProviderError("Enter a plain provider model ID, without a URL, spaces, or path.")
    return model


def _validate_key(key):
    """Reject malformed headers before networking without echoing the supplied secret."""
    if not isinstance(key, str) or not key or len(key) > 1000 or not all(33 <= ord(char) <= 126 for char in key):
        raise ProviderError("Enter a valid API key for the selected provider.")


def _unique_object(pairs):
    """Duplicate JSON keys can silently replace safety decisions, so reject them."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProviderError("Provider returned duplicate JSON keys.")
        result[key] = value
    return result


def _invalid_constant(value):
    """JSON does not permit NaN or Infinity."""
    raise ProviderError("Provider returned a non-JSON number.")


def _json_object(text):
    """Parse one complete JSON object without repairing malformed model output."""
    try:
        result = json.loads(text, object_pairs_hook=_unique_object,
                            parse_constant=_invalid_constant)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        raise ProviderError("Provider returned invalid JSON; nothing was applied.") from None
    if not isinstance(result, dict):
        raise ProviderError("Provider must return a JSON object.")
    return result


class _NoRedirect(request.HTTPRedirectHandler):
    """Do not forward API credentials to a redirected host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Unexpected redirects are surfaced as provider errors."""
        return None


def _request_json(provider, url, headers, *, timeout, limit):
    """Bound catalog responses and keep credentials off redirected requests."""
    req = request.Request(url, headers=headers, method="GET")
    try:
        with request.build_opener(_NoRedirect()).open(req, timeout=timeout) as response:
            raw = response.read(limit + 1)
        if len(raw) > limit:
            raise ProviderError("Provider response exceeded the size limit.")
        return _json_object(raw)
    except error.HTTPError as exc:
        hints = {400: "Check model settings and request limits.", 401: "Check your API key.",
                 403: "Check model access.", 404: "This model may no longer be available.",
                 429: "Rate or account limit reached; try again later."}
        hint = hints.get(exc.code, "Provider request failed; try again later.")
        raise ProviderError(f"{provider} HTTP {exc.code}: {hint}", status=exc.code) from None
    except (error.URLError, socket.timeout, TimeoutError, OSError):
        raise ProviderError("Provider connection failed or timed out; check connectivity.") from None
