"""API connection behavior is checked with fake credentials and no network calls."""

import copy
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from applydesk.agent_connections import AgentConnections
from applydesk.agent_settings import AgentSettings, ROLES
from applydesk.chatgpt_accounts import ChatGPTAccounts
from applydesk.chatgpt_inference import ChatGPTRunner
from applydesk.chatgpt_transport import AccountError
from applydesk.orchestrator_http import orchestrator_action


class MemoryStore:
    """Independent in-memory records detect credentials accidentally stored as app data."""

    def __init__(self):
        self.records = {}

    def get(self, kind, identity, default=None):
        return copy.deepcopy(self.records.get((kind, identity), default))

    def put(self, kind, identity, value):
        self.records[kind, identity] = copy.deepcopy(value)


class ConnectionsTests(unittest.TestCase):
    def setUp(self):
        """Use synthetic OAuth metadata and a separate fake Keychain."""
        self.secrets = {}
        secrets = SimpleNamespace(get=self.secrets.get, set=lambda key, value: self.secrets.__setitem__(key, value))
        self.w = SimpleNamespace(root=Path(tempfile.gettempdir()) / "connection-tests", store=MemoryStore(), secrets=secrets)
        self.accounts = AgentConnections(self.w)
        self.w.store.put("chatgpt", "accounts", {"active": "oauth", "host_id": "host", "accounts": [
            {"id": "oauth", "client_id": "client", "subject": "subject", "email": "preview@example.invalid", "connected": True}]})
        self.w.store.put("chatgpt_models", "oauth", [{"id": "oauth-model", "name": "Plan model", "thinking": ["high"]}])
        self.models = [{"id": "gpt-5.4", "name": "gpt-5.4"}, {"id": "gpt-new-preview", "name": "Preview"},
                       {"id": "text-embedding-3-small", "name": "Embedding"}]
        self.catalog = patch("applydesk.agent_connections.list_models", return_value=self.models)
        self.list_models = self.catalog.start()
        self.addCleanup(self.catalog.stop)

    def save(self, key="sk-test-only-secret"):
        """Call the same guarded save method used by the background HTTP task."""
        return self.accounts.save_api_key(key, self.accounts.generation)

    def test_key_is_private_and_api_catalog_is_explicit(self):
        result = self.save()
        self.assertTrue(result["connected"])
        self.assertEqual(result["source"], "openai_api")
        self.assertEqual(result["active"], "openai_api")
        self.assertEqual(result["chatgpt_active"], "oauth")
        self.assertNotIn("sk-test-only-secret", str(self.w.store.records))
        self.assertNotIn("sk-test-only-secret", json.dumps(result))
        self.assertEqual(self.accounts.token(), "sk-test-only-secret")
        self.assertEqual([row["id"] for row in result["models"]], ["gpt-5.4", "gpt-new-preview"])
        self.assertEqual(result["models"][1]["thinking"], [])

    def test_failed_validation_keeps_old_key_and_connection(self):
        self.save()
        self.list_models.side_effect = ValueError("Invalid API key.")
        with self.assertRaises(ValueError):
            self.save("sk-new-test-key")
        self.assertEqual(self.accounts.token(), "sk-test-only-secret")
        self.assertTrue(self.accounts.state()["connected"])

    def test_source_switch_never_falls_back(self):
        self.accounts.select_source("openai_api")
        with patch.object(ChatGPTAccounts, "token", return_value="oauth-token") as oauth:
            with self.assertRaisesRegex(ValueError, "API key"):
                self.accounts.token()
            oauth.assert_not_called()
            self.accounts.select_source("chatgpt")
            self.assertEqual(self.accounts.token(), "oauth-token")

    def test_stale_key_check_cannot_override_newer_source(self):
        generation = self.accounts.generation
        self.accounts.select_source("chatgpt")
        with self.assertRaisesRegex(ValueError, "Connection changed"):
            self.accounts.save_api_key("sk-test-secret", generation)
        self.assertFalse(self.secrets)
        self.assertEqual(self.accounts.state()["source"], "chatgpt")

    def test_remove_key_leaves_oauth_registration_and_no_fallback(self):
        self.save()
        result = self.accounts.remove_api_key()
        self.assertFalse(result["connected"])
        self.assertFalse(result["api_key_saved"])
        self.assertEqual(result["source"], "openai_api")
        self.assertEqual(result["accounts"][0]["id"], "oauth")
        self.accounts.select_source("chatgpt")
        self.assertTrue(self.accounts.state()["connected"])

    def test_agent_settings_are_scoped_to_selected_source(self):
        self.save()
        settings = AgentSettings(self.w, self.accounts)
        data = {"automatic": False, "roles": {role: {"model": "gpt-5.4", "thinking": "high"} for role in ROLES}}
        settings.save(data)
        self.assertEqual(settings.read(), data)
        self.assertIsNone(self.w.store.get("agent_settings", "oauth"))
        self.accounts.select_source("chatgpt")
        self.assertEqual(settings.read()["roles"]["master"]["model"], "oauth-model")

    def test_each_unavailable_role_can_be_repaired_without_changing_other_choices(self):
        self.save()
        data = {"automatic": False, "roles": {role: {"model": "unavailable-model", "thinking": "ultra"} for role in ROLES}}
        self.w.store.put("agent_settings", "openai_api", data)
        settings = AgentSettings(self.w, self.accounts)
        engine = SimpleNamespace(settings=settings, active=None)
        self.w.orchestrator = engine
        selection = {"model": "gpt-5.4", "thinking": "high"}
        result = orchestrator_action(self.w, "role-settings", {"role": "master", "selection": selection})
        self.assertEqual(result["roles"]["master"], selection)
        self.assertEqual(result["roles"]["application"], data["roles"]["application"])
        self.assertFalse(result["automatic"])
        result = orchestrator_action(self.w, "role-settings", {"role": "application", "selection": selection})
        self.assertEqual(result["roles"]["application"], selection)
        self.assertEqual(result["roles"]["document_reviewer"], data["roles"]["document_reviewer"])

    def test_invalid_role_edits_never_change_saved_settings(self):
        self.save()
        settings = AgentSettings(self.w, self.accounts)
        prior = settings.read()
        invalid = [{"role": "unknown", "selection": {"model": "gpt-5.4", "thinking": "high"}},
                   {"role": "master", "selection": {"model": "unavailable-model", "thinking": "default"}},
                   {"role": "master", "selection": {"model": "gpt-5.4", "thinking": "invented"}}]
        for value in invalid:
            with self.assertRaises(ValueError):
                settings.save_role(value)
            self.assertEqual(settings.read(), prior)
        self.w.orchestrator = SimpleNamespace(settings=settings, active={"message": "running"})
        with self.assertRaisesRegex(ValueError, "Stop the agents"):
            orchestrator_action(self.w, "role-settings", {"role": "master", "selection": {"model": "gpt-5.4", "thinking": "high"}})
        self.assertEqual(settings.read(), prior)

    def test_http_key_save_pauses_and_returns_only_public_state(self):
        engine = SimpleNamespace(accounts=self.accounts, signin=SimpleNamespace(cancel=Mock()), control=Mock())
        self.w.orchestrator = engine
        self.w.start = lambda name, action, *args: {"task": name, "result": action(*args)}
        result = orchestrator_action(self.w, "api-key", {"key": "sk-test-only-secret"})
        engine.control.assert_called_once_with({"paused": True})
        engine.signin.cancel.assert_called_once_with()
        self.assertEqual(result["task"], "openai-api-key")
        self.assertNotIn("sk-test-only-secret", json.dumps(result))

    def test_source_switch_rejects_extra_fields_before_mutation(self):
        self.w.orchestrator = SimpleNamespace(accounts=self.accounts, control=Mock())
        with self.assertRaises(ValueError):
            orchestrator_action(self.w, "source", {"source": "openai_api", "key": "unexpected"})
        self.w.orchestrator.control.assert_not_called()

    def test_disconnect_chatgpt_preserves_api_key_and_selected_source(self):
        self.save()
        engine = SimpleNamespace(accounts=self.accounts, signin=SimpleNamespace(cancel=Mock()), control=Mock())
        self.w.orchestrator = engine
        self.w.start = lambda name, action, *args: {"task": name, "result": action(*args)}
        with patch.object(self.accounts, "revoke", return_value=True):
            orchestrator_action(self.w, "disconnect", {})
        self.assertEqual(self.accounts.token(), "sk-test-only-secret")
        self.assertEqual(self.accounts.state()["source"], "openai_api")
        self.assertFalse(self.accounts.state()["chatgpt_active"])

    def test_default_prefers_available_responses_models_and_excludes_legacy_chat(self):
        ids = ["gpt-3.5-turbo", "gpt-4", "gpt-4-turbo", "gpt-4-0613", "o1-mini", "o1-preview",
               "gpt-4o", "gpt-5.4-mini", "gpt-new-preview", "gpt-5.4"]
        self.list_models.return_value = [{"id": identity, "name": identity} for identity in ids]
        result = self.save()
        models = [row["id"] for row in result["models"]]
        self.assertEqual(models, ["gpt-5.4", "gpt-5.4-mini", "gpt-4o", "gpt-new-preview"])
        settings = AgentSettings(self.w, self.accounts).read()
        self.assertTrue(all(role["model"] == "gpt-5.4" for role in settings["roles"].values()))

    def test_legacy_only_catalog_does_not_create_a_connection(self):
        self.list_models.return_value = [{"id": "gpt-3.5-turbo", "name": "Legacy chat model"}]
        with self.assertRaisesRegex(ValueError, "Responses text models"):
            self.save()
        self.assertFalse(self.secrets)
        self.assertEqual(self.accounts.state()["source"], "chatgpt")

    def test_api_inference_uses_selected_key_and_returns_completed_response(self):
        self.save()
        runner = ChatGPTRunner(self.w, self.accounts, AgentSettings(self.w, self.accounts))
        response = {"id": "response-test", "status": "completed", "output": []}
        events = [{"type": "response.completed", "response": response}]
        with patch("applydesk.chatgpt_inference.connect", return_value=(Mock(), Mock())) as connect:
            with patch("applydesk.chatgpt_inference.stream_events", return_value=events):
                _, result = runner.run("master", "Test", [], threading.Event(), Mock())
        args = connect.call_args.args
        self.assertEqual(args[0], "https://api.openai.com/v1/responses")
        self.assertEqual(args[3], "sk-test-only-secret")
        self.assertFalse(args[2]["store"])
        self.assertEqual(result["id"], "response-test")
        self.assertNotIn("sk-test-only-secret", str(self.w.store.records))

    def test_api_inference_error_is_safe_and_source_specific(self):
        self.save()
        runner = ChatGPTRunner(self.w, self.accounts, AgentSettings(self.w, self.accounts))
        with patch("applydesk.chatgpt_inference.connect", side_effect=AccountError(401)):
            with self.assertRaisesRegex(ValueError, "OpenAI API request failed") as caught:
                runner.run("master", "Test", [], threading.Event(), Mock())
        self.assertNotIn("sk-test-only-secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
