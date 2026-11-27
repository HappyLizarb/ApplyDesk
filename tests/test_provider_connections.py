"""Provider connections isolate credentials, catalogs, saved roles, and source changes."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from applydesk.agent_connections import AgentConnections
from applydesk.agent_settings import AgentSettings
from applydesk.model_catalog import catalog_models
from applydesk.orchestrator_http import orchestrator_action
from test_agent_connections import MemoryStore


class ProviderConnectionsTests(unittest.TestCase):
    def setUp(self):
        """A fake Keychain and provider catalogs keep tests completely offline."""
        self.secrets = {}
        secrets = SimpleNamespace(get=self.secrets.get, set=lambda name, key: self.secrets.__setitem__(name, key))
        self.w = SimpleNamespace(root=Path(tempfile.gettempdir()) / "provider-connections", store=MemoryStore(), secrets=secrets)
        self.accounts = AgentConnections(self.w)
        self.catalogs = {"xai": [{"id": "grok-4.7", "name": "Grok model"}], "openai": [{"id": "gpt-5.4", "name": "OpenAI model"}],
                         "anthropic": [{"id": "claude-sonnet-4-6", "name": "Claude model"}],
                         "google": [{"id": "gemini-3.8-flash", "name": "Gemini model"}]}
        self.catalog = patch("applydesk.agent_connections.list_models", side_effect=lambda provider, key: self.catalogs[provider])
        self.list_models = self.catalog.start()
        self.addCleanup(self.catalog.stop)
        self.w.orchestrator = SimpleNamespace(accounts=self.accounts, signin=SimpleNamespace(cancel=Mock()), control=Mock())
        self.w.start = lambda name, action, *args: {"task": name, "result": action(*args)}

    def save(self, provider):
        """Use the public request shape rather than bypassing the pause/source handling."""
        return orchestrator_action(self.w, "api-key", {"provider": provider, "key": provider + "-test-secret"})

    def test_each_provider_keeps_its_own_private_key_and_catalog(self):
        for provider in self.catalogs:
            result = self.save(provider)["result"]
            self.assertEqual(result["source"], provider + "_api")
            self.assertEqual(result["active"], provider + "_api")
            self.assertEqual(result["models"][0]["id"], self.catalogs[provider][0]["id"])
        for provider in self.catalogs:
            self.assertEqual(self.accounts.api_key(provider), provider + "-test-secret")
        self.assertEqual(self.accounts.state()["api_keys_saved"], dict.fromkeys(self.catalogs, True))
        self.assertTrue(self.accounts.state()["api_key_saved"])
        self.assertNotIn("test-secret", json.dumps(self.accounts.state()))
        self.assertNotIn("test-secret", str(self.w.store.records))
        self.assertEqual(self.w.orchestrator.control.call_count, 4)

    def test_removing_one_key_preserves_other_sources_and_keys(self):
        for provider in self.catalogs:
            self.save(provider)
        result = orchestrator_action(self.w, "api-key/remove", {"provider": "anthropic"})
        self.assertEqual(result["source"], "google_api")
        self.assertTrue(result["connected"])
        self.assertFalse(result["api_keys_saved"]["anthropic"])
        self.assertEqual(self.accounts.api_key("google"), "google-test-secret")
        self.assertEqual(self.accounts.api_key("openai"), "openai-test-secret")
        with self.assertRaisesRegex(ValueError, "Anthropic API key"):
            self.accounts.api_key("anthropic")

    def test_existing_openai_metadata_key_and_settings_survive_migration(self):
        self.w.store.put("agent_connection", "main", {"source": "openai_api", "api_key_saved": True})
        self.secrets["openai-api:" + self.accounts.namespace] = "existing-openai-secret"
        self.w.store.put("chatgpt_models", "openai_api", [{"id": "gpt-5.4", "name": "Existing", "thinking": ["high"]}])
        settings = AgentSettings(self.w, self.accounts)
        settings.save_role({"role": "master", "selection": {"model": "gpt-5.4", "thinking": "high"}})
        self.save("google")
        self.accounts.select_source("openai_api")
        self.assertEqual(self.accounts.token(), "existing-openai-secret")
        self.assertEqual(settings.read()["roles"]["master"]["thinking"], "high")
        self.assertTrue(self.accounts.state()["api_keys_saved"]["openai"])

    def test_missing_selected_provider_does_not_use_saved_other_keys(self):
        self.save("openai")
        self.accounts.select_source("google_api")
        self.assertFalse(self.accounts.state()["connected"])
        self.assertEqual(self.accounts.state()["models"], [])
        with self.assertRaisesRegex(ValueError, "Google API key"):
            self.accounts.token()

    def test_refresh_and_agent_settings_stay_scoped_to_selected_source(self):
        for provider in self.catalogs:
            self.save(provider)
            self.accounts.load_models()
            settings = AgentSettings(self.w, self.accounts)
            model = self.catalogs[provider][0]["id"]
            settings.save_role({"role": "master", "selection": {"model": model, "thinking": "high"}})
        for provider in self.catalogs:
            self.accounts.select_source(provider + "_api")
            self.assertEqual(AgentSettings(self.w, self.accounts).read()["roles"]["master"]["model"], self.catalogs[provider][0]["id"])
        self.assertEqual({call.args[0] for call in self.list_models.call_args_list}, set(self.catalogs))

    def test_stale_catalog_cannot_replace_key_or_switch_new_source(self):
        generation = self.accounts.generation
        self.accounts.select_source("google_api")
        with self.assertRaisesRegex(ValueError, "Connection changed"):
            self.accounts.save_api_key("stale-key", generation, "anthropic")
        self.assertFalse(self.secrets)
        self.assertEqual(self.accounts.state()["source"], "google_api")

    def test_invalid_provider_is_rejected_before_mutating_control_or_secret(self):
        for provider in ("custom", [], ""):
            with self.assertRaises(ValueError):
                orchestrator_action(self.w, "api-key", {"key": "test-secret", "provider": provider})
        self.w.orchestrator.control.assert_not_called()
        self.assertFalse(self.secrets)

    def test_catalog_exposes_only_documented_thinking_and_text_models(self):
        claude = catalog_models("anthropic", [{"id": "claude-haiku-4-5"}, {"id": "claude-sonnet-4-6"}])
        self.assertEqual(claude[0]["thinking"], ["low", "medium", "high", "max"])
        self.assertEqual(claude[1]["thinking"], [])
        gemini = catalog_models("google", [{"id": "gemini-2.5-flash"}, {"id": "gemini-3.8-flash"}, {"id": "gemini-3-image"}])
        self.assertEqual([row["id"] for row in gemini], ["gemini-3.8-flash", "gemini-2.5-flash"])
        self.assertEqual(gemini[0]["thinking"], ["low", "medium", "high"])
        self.assertEqual(gemini[1]["thinking"], [])


if __name__ == "__main__":
    unittest.main()
