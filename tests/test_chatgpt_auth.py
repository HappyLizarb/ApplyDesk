"""OAuth validation and credential lifecycle tests use generated keys and fake endpoints."""

import base64
import copy
import hashlib
import json
import threading
import time
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from chat_helpers import ChatFixture
from applydesk.chatgpt_accounts import AUTH, RESOURCE, SCOPES, TOKEN
from applydesk.chatgpt_identity import validate_identity


def encode(value):
    """Encode synthetic signing material with the same public JWT format as real tokens."""
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


class ChatGPTAuthTests(ChatFixture):
    def setUp(self):
        """Create a local signing authority; no real account credential is read."""
        super().setUp()
        self.accounts = self.w.orchestrator.accounts
        self.signin = self.w.orchestrator.signin
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = self.key.public_key().public_numbers()
        self.jwks = {"keys": [{"kty": "RSA", "kid": "test", "alg": "RS256", "use": "sig",
            "n": encode(numbers.n.to_bytes(256, "big")), "e": encode(numbers.e.to_bytes(3, "big"))}]}
        self.accounts.discovery = {"issuer": AUTH, "jwks_uri": AUTH + "/.well-known/jwks.json",
                                   "token_endpoint": TOKEN, "revocation_endpoint": AUTH + "/revoke"}

    def tokens(self, client="oaiapp_test", nonce="nonce", **claims):
        """Return a signed identity, opaque tokens, and explicit plan scopes."""
        identity = {"iss": AUTH, "aud": client, "sub": "subject-1", "email": "test@example.test",
                    "nonce": nonce, "iat": time.time(), "exp": time.time() + 3600, **claims}
        header = encode(json.dumps({"alg": "RS256", "kid": "test"}).encode())
        body = encode(json.dumps(identity).encode())
        signature = self.key.sign((header + "." + body).encode(), padding.PKCS1v15(), hashes.SHA256())
        return {"id_token": header + "." + body + "." + encode(signature), "access_token": "private-access",
                "refresh_token": "private-refresh", "expires_in": 3600, "scope": " ".join(SCOPES)}

    def accept(self):
        """Seed one valid registration without contacting OpenAI."""
        with patch("applydesk.chatgpt_accounts.request_json", return_value=self.jwks):
            return self.accounts.accept("oaiapp_test", self.tokens(), "nonce")

    def test_signature_claims_and_plan_scope_are_required(self):
        """Reject tampering, wrong audience, expiry, issuer, nonce, and identity-only consent."""
        for claims in ({"aud": "wrong"}, {"exp": 1}, {"iss": "https://evil.test"}, {"nonce": "wrong"}, {"sub": ""}):
            with self.assertRaises(ValueError):
                validate_identity(self.tokens(**claims)["id_token"], "oaiapp_test", "nonce", self.jwks)
        tampered = self.tokens()["id_token"].split(".")
        tampered[1] = encode(b'{"sub":"attacker"}')
        with self.assertRaises(ValueError):
            validate_identity(".".join(tampered), "oaiapp_test", "nonce", self.jwks)
        with patch("applydesk.chatgpt_accounts.request_json", return_value=self.jwks):
            with self.assertRaises(ValueError):
                self.accounts.accept("oaiapp_test", {**self.tokens(), "scope": "openid email"}, "nonce")
        self.assertFalse(self.accounts.state()["connected"])

    def test_pkce_callback_is_single_use_and_tokens_never_enter_ui_state(self):
        """A real loopback listener is bound before official sign-in opens."""
        with patch("applydesk.chatgpt_auth.webbrowser.open", return_value=True) as browser:
            self.signin.begin()
        pending = self.signin.pending
        parameters = parse_qs(urlsplit(browser.call_args.args[0]).query)
        self.assertEqual(parameters["client_id"], ["dynamic_agent_client"])
        challenge = encode(hashlib.sha256(pending["verifier"].encode()).digest())
        self.assertEqual(parameters["code_challenge"], [challenge])
        with self.assertRaises(ValueError):
            self.signin.complete({"state": "wrong", "code": "code", "client_id": "oaiapp_test"})
        response = self.tokens(nonce=pending["parameters"]["nonce"])
        with patch("applydesk.chatgpt_auth.request_json", return_value=response), patch("applydesk.chatgpt_accounts.request_json", return_value=self.jwks), patch.object(self.accounts, "load_models"):
            callback = {"state": pending["parameters"]["state"], "code": "code", "client_id": "oaiapp_test"}
            self.signin.complete(callback)
            with self.assertRaises(ValueError):
                self.signin.complete(callback)
        self.assertTrue(self.accounts.state()["connected"])
        self.assertNotIn("private-access", json.dumps(self.w.state()))
        self.assertNotIn("private-refresh", json.dumps(self.accounts.metadata()))
        self.assertEqual(self.accounts.token(), "private-access")

    def test_failed_reauthorization_retains_existing_account(self):
        """A new or mismatched account cannot overwrite a working registration."""
        row = self.accept()
        before = self.accounts.metadata()
        with patch("applydesk.chatgpt_accounts.request_json", return_value=self.jwks):
            with self.assertRaises(ValueError):
                self.accounts.accept("oaiapp_test", self.tokens(sub="different-user"), "nonce", row["subject"])
        self.assertEqual(self.accounts.metadata(), before)
        self.assertEqual(self.accounts.token(), "private-access")

    def test_refresh_rotates_once_under_concurrent_requests(self):
        """The refresh token cannot race between workers or revert to a previous value."""
        self.accept()
        tokens = self.accounts.credentials("oaiapp_test")
        tokens["expires_at"] = 1
        self.accounts.save_tokens("oaiapp_test", tokens)
        response = {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}
        results = []
        with patch("applydesk.chatgpt_accounts.request_json", return_value=response) as request:
            threads = [threading.Thread(target=lambda: results.append(self.accounts.token())) for _ in range(4)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
        self.assertEqual(results, ["new-access"] * 4)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(self.accounts.credentials("oaiapp_test")["refresh_token"], "new-refresh")

    def test_disconnect_clears_tokens_even_if_revocation_fails(self):
        """Remote failure stays visible; the account and its secrets disappear from public state."""
        self.accept()
        with patch("applydesk.chatgpt_accounts.request_json", side_effect=OSError("offline")):
            result = self.accounts.disconnect()
        self.assertFalse(result["connected"])
        self.assertEqual(result["accounts"], [])
        self.assertEqual(result["active"], "")
        self.assertEqual(result["models"], [])
        self.assertIn("ChatGPT Settings", result["notice"])
        self.assertIn("not confirmed", result["error"])
        self.assertIsNone(self.accounts.credentials("oaiapp_test"))
        self.assertEqual(self.accounts.metadata()["accounts"][0]["client_id"], "oaiapp_test")

    def test_catalog_is_account_specific_and_no_inference_on_connect(self):
        """Only visible live catalog rows become settings choices; reasoning stays default if unknown."""
        self.accept()
        catalog = {"models": [{"slug": "test-model", "display_name": "Test Model", "visibility": "list", "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}]},
                              {"slug": "hidden", "visibility": "hide"}]}
        with patch("applydesk.chatgpt_accounts.request_json", return_value=catalog) as request:
            rows = self.accounts.load_models()
        self.assertEqual([row["id"] for row in rows], ["test-model"])
        self.assertEqual(rows[0]["thinking"], ["low", "high"])
        self.assertEqual(request.call_args.args[0], RESOURCE + "/models")
        self.assertEqual(self.w.orchestrator.settings.read()["roles"]["master"], {"model": "test-model", "thinking": "default"})

    def test_disconnect_revokes_only_selected_account_and_keeps_others(self):
        """Successful revocation clears local access without auto-selecting another billing account."""
        first = self.accept()
        with patch("applydesk.chatgpt_accounts.request_json", return_value=self.jwks):
            other = self.accounts.accept("oaiapp_other", self.tokens(client="oaiapp_other", sub="other", email="other@example.test"), "nonce")
        self.accounts.select(first["id"])
        with patch("applydesk.chatgpt_accounts.request_json", return_value={}) as request:
            result = self.accounts.disconnect()
        self.assertEqual(request.call_args.args[0], self.accounts.discovery["revocation_endpoint"])
        self.assertEqual(request.call_args.args[1], {"token": "private-refresh", "token_type_hint": "refresh_token", "client_id": "oaiapp_test"})
        self.assertTrue(request.call_args.kwargs["form"])
        self.assertEqual([row["id"] for row in result["accounts"]], [other["id"]])
        self.assertFalse(result["connected"])
        self.assertIsNone(self.accounts.credentials("oaiapp_test"))
        self.assertIsNotNone(self.accounts.credentials("oaiapp_other"))
        self.assertEqual(self.accounts.state()["notice"], result["notice"])

    def test_legacy_disconnected_accounts_are_hidden(self):
        """Existing signed-out rows cannot reappear in the picker or expose stale model choices."""
        row = self.accept()
        value = self.accounts.metadata()
        value["accounts"][0]["connected"] = False
        self.w.store.put("chatgpt", "accounts", value)
        self.w.store.put("chatgpt_models", row["id"], [{"id": "stale"}])
        result = self.accounts.state()
        self.assertEqual(result["accounts"], [])
        self.assertEqual(result["active"], "")
        self.assertEqual(result["models"], [])

    def test_duplicate_email_is_rejected_without_replacing_existing_credentials(self):
        """A different verified subject with the same email cannot create a second connection."""
        first = self.accept()
        before = self.accounts.metadata()
        tokens = self.tokens(client="oaiapp_duplicate", sub="different-subject", email="TEST@example.test")
        with patch("applydesk.chatgpt_accounts.request_json", side_effect=[self.jwks, {}]) as request:
            with self.assertRaisesRegex(ValueError, "already connected"):
                self.accounts.accept("oaiapp_duplicate", tokens, "nonce")
        self.assertEqual(request.call_args.args[1]["client_id"], "oaiapp_duplicate")
        self.assertEqual(self.accounts.metadata(), before)
        self.assertIsNone(self.accounts.credentials("oaiapp_duplicate"))
        self.assertEqual(self.accounts.token(), "private-access")
        self.assertEqual(self.accounts.state()["active"], first["id"])

    def test_duplicate_subject_and_failed_revocation_are_reported(self):
        """Identity matches also block changed email, without hiding failed remote cleanup."""
        self.accept()
        tokens = self.tokens(client="oaiapp_duplicate", email="renamed@example.test")
        with patch("applydesk.chatgpt_accounts.request_json", side_effect=[self.jwks, OSError(), OSError(), OSError()]), patch("applydesk.chatgpt_accounts.time.sleep"):
            with self.assertRaisesRegex(ValueError, "not confirmed"):
                self.accounts.accept("oaiapp_duplicate", tokens, "nonce")
        self.assertEqual(len(self.accounts.state()["accounts"]), 1)
        self.assertIsNone(self.accounts.credentials("oaiapp_duplicate"))

    def test_reauthorizing_existing_registration_preserves_its_identity(self):
        """Normal reauthorization uses the existing row and rotates its own credentials."""
        first = self.accept()
        with patch("applydesk.chatgpt_accounts.request_json", return_value=self.jwks) as request:
            second = self.accounts.accept("oaiapp_test", {**self.tokens(), "access_token": "rotated"}, "nonce")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.accounts.state()["accounts"]), 1)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(self.accounts.token(), "rotated")

    def test_legacy_duplicates_show_once_and_disconnect_all_related_sessions(self):
        """Old duplicate rows collapse in the picker and all their grants are revoked on removal."""
        first = self.accept()
        value = self.accounts.metadata()
        duplicate = {**first, "id": "duplicate", "client_id": "oaiapp_duplicate", "subject": "another-subject"}
        value["accounts"].append(duplicate)
        value["active"] = duplicate["id"]
        self.w.store.put("chatgpt", "accounts", value)
        self.accounts.save_tokens("oaiapp_duplicate", self.accounts.credentials("oaiapp_test"))
        self.assertEqual(self.accounts.state()["accounts"], [duplicate])
        with patch("applydesk.chatgpt_accounts.request_json", return_value={}) as request:
            result = self.accounts.disconnect()
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result["accounts"], [])
        self.assertIsNone(self.accounts.credentials("oaiapp_duplicate"))
        self.assertIsNone(self.accounts.credentials("oaiapp_test"))

    def test_revocation_retries_transient_failure(self):
        """A temporary network failure gets a bounded retry while tokens remain available."""
        self.accept()
        with patch("applydesk.chatgpt_accounts.request_json", side_effect=[OSError(), {}]) as request, patch("applydesk.chatgpt_accounts.time.sleep") as wait:
            result = self.accounts.disconnect()
        self.assertEqual(request.call_count, 2)
        wait.assert_called_once_with(0.25)
        self.assertEqual(result["error"], "")
        self.assertEqual(result["accounts"], [])

    def test_reconnecting_hidden_registration_cannot_create_duplicate(self):
        """A remembered signed-out registration must still respect the one-email rule."""
        self.accept()
        value = self.accounts.metadata()
        value["accounts"].append({**value["accounts"][0], "id": "old", "client_id": "oaiapp_old", "connected": False})
        self.w.store.put("chatgpt", "accounts", value)
        with patch("applydesk.chatgpt_accounts.request_json", side_effect=[self.jwks, {}]):
            with self.assertRaisesRegex(ValueError, "already connected"):
                self.accounts.accept("oaiapp_old", self.tokens(client="oaiapp_old"), "nonce")
        self.assertIsNone(self.accounts.credentials("oaiapp_old"))
        self.assertEqual(len(self.accounts.state()["accounts"]), 1)
