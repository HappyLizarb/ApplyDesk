"""Small provider adapters; credentials stay in memory and errors omit payloads."""

import base64
import json
import re
import socket
from urllib import error, request


# Supported presets are documented and request-tested, not live API-certified.
MODEL_OPTIONS = {
    "openai": {
        "gpt-5.4": ["none", "low", "medium", "high", "xhigh", "default"],
        "gpt-5.4-mini": ["none", "low", "medium", "high", "xhigh", "default"],
        "gpt-6.1-sol": ["default", "low", "medium", "high", "xhigh", "max"],
        "gpt-6-astra": ["default", "low", "medium", "high", "xhigh", "max"],
        "gpt-6-sol": ["default", "none", "low", "medium", "high", "xhigh", "max"],
        "gpt-6-luna": ["default", "none", "low", "medium", "high", "xhigh", "max"],
        "gpt-5.6-terra": ["default", "none", "low", "medium", "high", "xhigh", "max"],
        "gpt-5.5": ["default", "none", "low", "medium", "high", "xhigh"],
    },
    "anthropic": {
        "claude-sonnet-4-6": ["none", "low", "medium", "high", "max", "default"],
        "claude-haiku-4-5": ["none", "low", "medium", "high", "default"],
        "claude-opus-5-5": ["default", "low", "medium", "high", "xhigh", "max"],
        "claude-sonnet-5-5": ["default", "low", "medium", "high", "xhigh", "max"],
        "claude-haiku-5-5": ["default", "low", "medium", "high", "xhigh", "max"],
        "claude-fable-5-1": ["default", "low", "medium", "high", "xhigh", "max"],
    },
    "google": {
        "gemini-3.8-flash": ["low", "medium", "high", "default"],
        "gemini-3.6-flash": ["default", "minimal", "low", "medium", "high"],
        "gemini-3.5-flash-lite": ["default", "minimal", "low", "medium", "high"],
        "gemini-3.1-flash-lite": ["default", "minimal", "low", "medium", "high"],
    },
}
JSON_RULE = "Return exactly one JSON object. No Markdown, fences, or commentary."
MAX_RESPONSE = 8 * 1024 * 1024


class ProviderError(ValueError):
    """An actionable provider failure safe to display without credential leakage."""

    def __init__(self, message, status=0, code=""):
        self.status = status
        self.code = code
        super().__init__(message)


def validate_settings(settings):
    """Custom IDs retain provider defaults; explicit controls require a documented preset."""
    if not isinstance(settings, dict):
        raise ProviderError("Model settings must be an object.")
    provider = settings.get("provider")
    if not isinstance(provider, str) or provider not in MODEL_OPTIONS:
        raise ProviderError("Choose OpenAI, Anthropic, or Google.")
    model = validate_model_id(provider, settings.get("model"))
    options = MODEL_OPTIONS[provider].get(model, ["default"])
    thinking = settings.get("thinking", options[0])
    if thinking not in options:
        raise ProviderError("Unsupported thinking setting. Custom models require provider default thinking.")
    return {"provider": provider, "model": model, "thinking": thinking}


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


def _validate_images(images):
    """Accept only bounded inline images, never URLs or filesystem references."""
    if not isinstance(images, list) or len(images) > 20:
        raise ProviderError("Supply at most 20 image objects per request.")
    for item in images:
        if not isinstance(item, dict) or item.get("mime_type") not in {
            "image/png", "image/jpeg", "image/webp"
        }:
            raise ProviderError("Images must be PNG, JPEG, or WebP.")
        try:
            data = item["data"]
            if not isinstance(data, str) or len(data) > 7_000_000:
                raise ValueError()
            raw = base64.b64decode(data, validate=True)
            if not raw or len(raw) > 5_000_000:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise ProviderError("Image data must be valid base64 under 5 MB.") from None


class _NoRedirect(request.HTTPRedirectHandler):
    """Do not forward API credentials to a redirected host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Unexpected redirects are surfaced as provider errors."""
        return None


def _request_json(provider, url, headers, body=None, timeout=60, limit=MAX_RESPONSE):
    """Bound authenticated GET/POST responses and never expose provider error bodies."""
    data = None if body is None else json.dumps(body).encode()
    headers = headers if data is None else {**headers, "Content-Type": "application/json"}
    req = request.Request(url, data, headers, method="GET" if body is None else "POST")
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


class ProviderClient:
    """One-shot JSON requests; browser execution remains outside this class."""

    def __init__(self, settings: dict, key: str):
        """Hold validated settings and an API key supplied by secure storage."""
        self.settings = validate_settings(settings)
        _validate_key(key)
        self._key = key

    def ask(self, system: str, payload: dict, images: list[dict] | None = None) -> dict:
        """Request and strictly decode JSON, raising without exposing private data."""
        if not isinstance(system, str) or not isinstance(payload, dict):
            raise ProviderError("Supply a system instruction and an object payload.")
        images = [] if images is None else images
        _validate_images(images)
        try:
            text = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError):
            raise ProviderError("Request payload is not valid JSON.") from None
        builder = getattr(self, "_" + self.settings["provider"])
        url, headers, body = builder(system + "\n" + JSON_RULE, text, images)
        data = self._post(url, headers, body)
        return _json_object(self._response_text(data))

    def _post(self, url, headers, body):
        """Send one bounded HTTPS request; failures do not automatically spend again."""
        return _request_json(self.settings["provider"], url, headers, body)

    def _openai(self, system, text, images):
        """Build Responses API content including inline screenshot inputs."""
        content = [{"type": "input_text", "text": text}]
        content += [{"type": "input_image", "image_url":
                     f"data:{item['mime_type']};base64,{item['data']}"} for item in images]
        body = {"model": self.settings["model"], "instructions": system,
                "input": [{"role": "user", "content": content}], "store": False,
                "text": {"format": {"type": "json_object"}}, "max_output_tokens": 16384}
        if self.settings["thinking"] != "default":
            body["reasoning"] = {"effort": self.settings["thinking"]}
        return "https://api.openai.com/v1/responses", {"Authorization": "Bearer " + self._key}, body

    def _anthropic(self, system, text, images):
        """Apply only each preset's documented thinking API; default sends no override."""
        content = [{"type": "text", "text": text}]
        content += [{"type": "image", "source": {"type": "base64",
                     "media_type": item["mime_type"], "data": item["data"]}} for item in images]
        body = {"model": self.settings["model"], "system": system,
                "messages": [{"role": "user", "content": content}], "max_tokens": 16384}
        thinking = self.settings["thinking"]
        if thinking == "none":
            body["thinking"] = {"type": "disabled"}
        elif self.settings["model"] == "claude-sonnet-4-6" and thinking != "default":
            body.update(thinking={"type": "adaptive"}, output_config={"effort": thinking})
        elif self.settings["model"] == "claude-haiku-4-5" and thinking != "default":
            body["thinking"] = {"type": "enabled", "budget_tokens":
                                {"low": 1024, "medium": 4096, "high": 8192}[thinking]}
        elif thinking != "default":
            body["output_config"] = {"effort": thinking}
        headers = {"x-api-key": self._key, "anthropic-version": "2023-06-01"}
        return "https://api.anthropic.com/v1/messages", headers, body

    def _google(self, system, text, images):
        """Use generateContent with model-supported thinking levels and JSON output."""
        parts = [{"text": text}]
        parts += [{"inlineData": {"mimeType": item["mime_type"],
                                  "data": item["data"]}} for item in images]
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 16384}}
        if self.settings["thinking"] != "default":
            body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": self.settings["thinking"]}
        url = "https://generativelanguage.googleapis.com/v1beta/models/"
        return url + self.settings["model"] + ":generateContent", {"x-goog-api-key": self._key}, body

    def _response_text(self, data):
        """Reject interrupted/refused responses before looking for answer text."""
        provider = self.settings["provider"]
        try:
            if provider == "openai":
                if data.get("status") != "completed" or data.get("error"):
                    raise ProviderError("OpenAI response was incomplete or refused.")
                blocks = [part for item in data["output"] if item.get("type") == "message"
                          for part in item.get("content", [])]
                texts = [part["text"] for part in blocks if part.get("type") == "output_text"]
                if any(part.get("type") == "refusal" for part in blocks):
                    raise ProviderError("OpenAI declined the request.")
            elif provider == "anthropic":
                if data.get("stop_reason") != "end_turn":
                    raise ProviderError("Anthropic response was incomplete or refused.")
                texts = [part["text"] for part in data["content"] if part.get("type") == "text"]
            else:
                candidates = data.get("candidates", [])
                if len(candidates) != 1 or candidates[0].get("finishReason") != "STOP":
                    raise ProviderError("Gemini response was incomplete or blocked.")
                texts = [part["text"] for part in candidates[0]["content"]["parts"]
                         if "text" in part and not part.get("thought")]
            if not texts or not all(isinstance(text, str) for text in texts):
                raise ProviderError("Provider returned no usable answer.")
            return "".join(texts)
        except (KeyError, TypeError, AttributeError):
            raise ProviderError("Provider response had an unexpected format.") from None
