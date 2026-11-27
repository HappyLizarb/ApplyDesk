"""Explicit ChatGPT-plan and provider-API connections share the single-agent workflow."""

from .chatgpt_accounts import ChatGPTAccounts
from .model_catalog import API_PROVIDERS, CONNECTION_SOURCES, catalog_models, list_models, validate_provider
from .providers import _validate_key


class AgentConnections(ChatGPTAccounts):
    """Keep OAuth registrations and each provider's workspace-scoped API key separate."""

    def connection(self):
        """Read public metadata, preserving pre-existing OpenAI connection records."""
        value = self.w.store.get("agent_connection", "main", {})
        saved = value.get("api_keys_saved", {})
        flags = {provider: bool(saved.get(provider, value.get("api_key_saved", False) if provider == "openai" else False))
                 for provider in API_PROVIDERS}
        return {"source": value.get("source", "chatgpt"), "api_keys_saved": flags, "api_key_saved": flags["openai"]}

    def state(self):
        """Expose the selected catalog and connection type, never API credentials."""
        oauth, connection = super().state(), self.connection()
        value = {**oauth, **connection, "chatgpt_active": oauth["active"]}
        if connection["source"] != "chatgpt":
            source = connection["source"]
            provider = validate_provider(source.removesuffix("_api"))
            connected = connection["api_keys_saved"][provider]
            value.update(active=source if connected else "", connected=connected,
                         models=self.w.store.get("chatgpt_models", source, []) if connected else [], notice="")
        return value

    def select_source(self, source):
        """Changing sources invalidates pending inference and never chooses a fallback."""
        if not isinstance(source, str) or source not in CONNECTION_SOURCES:
            raise ValueError("Choose ChatGPT, OpenAI API, Anthropic API, Google API, or Grok (xAI).")
        with self.lock:
            connection = self.connection()
            self.generation += 1
            self.w.store.put("agent_connection", "main", {**connection, "source": source})
            self.error = ""
        return self.state()

    def token(self):
        """Preserve OAuth/OpenAI runner compatibility without switching credential sources."""
        with self.lock:
            if self.connection()["source"] == "chatgpt":
                return super().token()
            return self.api_key()

    def api_key(self, provider=None):
        """Return exactly the requested provider's private key, with no cross-provider fallback."""
        with self.lock:
            provider = validate_provider(self.connection()["source"].removesuffix("_api") if provider is None else provider)
            key = self.w.secrets.get(provider + "-api:" + self.namespace)
            if not key:
                raise ValueError(f"Add a {API_PROVIDERS[provider]} API key in Account setup.")
            _validate_key(key)
            return key

    def save_api_key(self, key, generation, provider="openai"):
        """Verify model-list access before replacing a key; discard superseded requests."""
        provider = validate_provider(provider)
        _validate_key(key)
        models = catalog_models(provider, list_models(provider, key))
        with self.lock:
            if generation != self.generation:
                raise ValueError("Connection changed while checking the API key. Try again.")
            self.w.secrets.set(provider + "-api:" + self.namespace, key)
            self.w.store.put("chatgpt_models", provider + "_api", models)
            connection = self.connection()
            connection["api_keys_saved"][provider] = True
            connection.update(source=provider + "_api", api_key_saved=connection["api_keys_saved"]["openai"])
            self.w.store.put("agent_connection", "main", connection)
            self.generation += 1
            self.error = ""
        return self.state()

    def remove_api_key(self, provider="openai"):
        """Remove only the named provider's credential and catalog, preserving other sources."""
        provider = validate_provider(provider)
        with self.lock:
            connection = self.connection()
            self.generation += 1
            self.w.secrets.set(provider + "-api:" + self.namespace, "")
            self.w.store.put("chatgpt_models", provider + "_api", [])
            connection["api_keys_saved"][provider] = False
            connection["api_key_saved"] = connection["api_keys_saved"]["openai"]
            self.w.store.put("agent_connection", "main", connection)
            self.error = ""
        return self.state()

    def load_models(self):
        """Refresh only the selected catalog and reject results after a source switch."""
        with self.lock:
            source = self.connection()["source"]
            if source == "chatgpt":
                return super().load_models()
            provider = validate_provider(source.removesuffix("_api"))
            generation, key = self.generation, self.api_key(provider)
        models = catalog_models(provider, list_models(provider, key))
        with self.lock:
            if generation != self.generation:
                raise ValueError("Connection changed while loading models. Refresh again.")
            self.w.store.put("chatgpt_models", source, models)
            self.error = ""
        return models
