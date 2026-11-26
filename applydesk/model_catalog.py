"""Explicit model discovery; listing a model never certifies task compatibility."""

import time
from urllib.parse import urlencode

from .providers import ProviderError, _request_json, _validate_key, validate_model_id

ENDPOINTS = {"openai": "https://api.openai.com/v1/models", "xai": "https://api.x.ai/v1/models",
             "anthropic": "https://api.anthropic.com/v1/models",
             "google": "https://generativelanguage.googleapis.com/v1beta/models"}
MAX_MODELS, MAX_PAGES, MAX_PAGE_BYTES = 1000, 10, 2 * 1024 * 1024


def list_models(provider, key):
    """Return sorted {id, name} rows using only bounded model-list GET requests."""
    if not isinstance(provider, str) or provider not in ENDPOINTS:
        raise ProviderError("Choose OpenAI, Anthropic, Google, or Grok (xAI).")
    _validate_key(key)
    headers = {"openai": {"Authorization": "Bearer " + key}, "xai": {"Authorization": "Bearer " + key},
               "anthropic": {"x-api-key": key, "anthropic-version": "2023-06-01"},
               "google": {"x-goog-api-key": key}}[provider]
    parameters = {"openai": {}, "xai": {}, "anthropic": {"limit": 100}, "google": {"pageSize": 100}}[provider]
    models, seen, count, deadline = {}, set(), 0, time.monotonic() + 60
    for _ in range(MAX_PAGES):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderError("Model listing timed out. Try again later or enter an ID manually.")
        url = ENDPOINTS[provider] + ("?" + urlencode(parameters) if parameters else "")
        page = _request_json(provider, url, headers, timeout=min(15, remaining), limit=MAX_PAGE_BYTES)
        entries = page.get("models" if provider == "google" else "data")
        if not isinstance(entries, list) or any(not isinstance(row, dict) for row in entries):
            raise ProviderError("Provider returned an invalid model list.")
        count += len(entries)
        if count > MAX_MODELS:
            raise ProviderError("Model list exceeded the limit. Enter a model ID manually.")
        models.update((row["id"], row) for row in _model_rows(provider, entries))
        cursor = _next_cursor(provider, page)
        if not cursor:
            return sorted(models.values(), key=lambda row: row["id"])
        if cursor in seen:
            raise ProviderError("Provider returned a repeated model-list cursor.")
        seen.add(cursor)
        parameters["after_id" if provider == "anthropic" else "pageToken"] = cursor
    raise ProviderError("Model list exceeded the page limit. Enter a model ID manually.")


def _model_rows(provider, entries):
    """Keep canonical IDs; Gemini advertises which models expose generateContent."""
    rows = []
    for entry in entries:
        if provider == "google":
            methods = entry.get("supportedGenerationMethods", [])
            if not isinstance(methods, list):
                raise ProviderError("Provider returned invalid model capabilities.")
            if "generateContent" not in methods:
                continue
        try:
            identity = validate_model_id(provider, entry.get("name" if provider == "google" else "id"))
        except ProviderError:
            raise ProviderError("Provider returned an invalid model identifier.") from None
        name = entry.get("displayName" if provider == "google" else "display_name", identity)
        if not isinstance(name, str) or len(name) > 500:
            raise ProviderError("Provider returned an invalid model name.")
        rows.append({"id": identity, "name": name or identity})
    return rows


def _next_cursor(provider, page):
    """Use opaque cursors only as encoded query values, never as destination URLs."""
    if provider in {"openai", "xai"}:
        if page.get("has_more"):
            raise ProviderError("Unexpected model-list pagination. Refresh the model catalog later.")
        return None
    if provider == "anthropic":
        if not isinstance(page.get("has_more"), bool):
            raise ProviderError("Provider returned invalid model-list pagination.")
        if not page["has_more"]:
            return None
        cursor = page.get("last_id")
    else:
        cursor = page.get("nextPageToken")
        if cursor is None or cursor == "":
            return None
    if not isinstance(cursor, str) or not cursor or len(cursor) > 4096:
        raise ProviderError("Provider returned an invalid model-list cursor.")
    return cursor
