"""Live catalog filtering and documented reasoning choices for agent connections."""

from .providers import MODEL_OPTIONS

API_PROVIDERS = {"openai": "OpenAI", "anthropic": "Anthropic", "google": "Google", "xai": "Grok (xAI)"}
CONNECTION_SOURCES = {"chatgpt", *(provider + "_api" for provider in API_PROVIDERS)}
# Only explicitly verified model capabilities appear; other live IDs use provider defaults.
# Claude: https://platform.claude.com/docs/en/build-with-claude/effort
# Gemini: https://aistudio.google.com/docs/thinking
THINKING = {
    "xai": {"grok-4.7": ["low", "medium", "high", "xhigh"],
            "grok-4.6": ["low", "medium", "high", "xhigh"], "grok-4.5": ["low", "medium", "high"]},
    "openai": {model: [value for value in values if value != "default"]
               for model, values in MODEL_OPTIONS["openai"].items()},
    "anthropic": {
        "claude-opus-5-5": ["low", "medium", "high", "xhigh", "max"],
        "claude-sonnet-5-5": ["low", "medium", "high", "xhigh", "max"],
        "claude-haiku-5-5": ["low", "medium", "high", "xhigh", "max"],
        "claude-fable-5-1": ["low", "medium", "high", "xhigh", "max"],
        "claude-opus-4-6": ["low", "medium", "high", "max"],
        "claude-sonnet-4-6": ["low", "medium", "high", "max"],
    },
    "google": {
        "gemini-3.8-flash": ["low", "medium", "high"],
        "gemini-3.6-flash": ["minimal", "low", "medium", "high"],
        "gemini-3.5-flash-lite": ["minimal", "low", "medium", "high"],
        "gemini-3.1-pro-preview": ["low", "medium", "high"],
        "gemini-3-flash-preview": ["minimal", "low", "medium", "high"],
        "gemini-3-pro-preview": ["low", "high"],
    },
}


def validate_provider(provider):
    """Reject unsupported provider names before choosing any credential or destination."""
    if not isinstance(provider, str) or provider not in API_PROVIDERS:
        raise ValueError("Choose OpenAI, Anthropic, Google, or Grok (xAI).")
    return provider


def text_model(provider, identity):
    """Omit known non-chat and legacy models without certifying unknown API capabilities."""
    if provider == "openai":
        if identity == "gpt-4" or identity.startswith(("gpt-3.5", "gpt-4-", "o1-mini", "o1-preview")):
            return False
        prefixes = ("gpt-", "o1", "o3", "o4", "codex-")
    else:
        prefixes = {"anthropic": ("claude-",), "google": ("gemini-",), "xai": ("grok-",)}[provider]
    excluded = ("realtime", "audio", "transcribe", "tts", "image", "video", "multi-agent", "embedding", "search", "computer-use")
    return identity.startswith(prefixes) and not any(part in identity for part in excluded)


def catalog_models(provider, rows):
    """Attach only known reasoning levels and prefer documented models actually returned."""
    validate_provider(provider)
    models = [{**row, "thinking": THINKING[provider].get(row["id"], [])}
              for row in rows if text_model(provider, row["id"])]
    if not models:
        kind = "Responses text" if provider == "openai" else "text"
        raise ValueError(f"This API key has no available {kind} models. Check its model permissions.")
    preferred = {model: index for index, model in enumerate(THINKING[provider])}
    return sorted(models, key=lambda model: (preferred.get(model["id"], len(preferred)), model["id"]))
