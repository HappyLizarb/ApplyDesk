"""Account registrations, private token renewal, and account-specific model choices."""

import hashlib
import json
import threading
import time
import uuid

from .chatgpt_identity import validate_identity
from .chatgpt_transport import AccountError, request_json

AUTH = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
TOKEN = AUTH + "/api/accounts/oauth/token"
SCOPES = {"resource.invoke", "chatgpt.tokens.use.direct"}


def account_key(row):
    """One visible connection per email; never use email to transfer credentials or identity."""
    email = row.get("email", row.get("label", "").rsplit(" · ", 1)[0]).strip().casefold()
    return ("email", email) if "@" in email else ("subject", row["subject"])


class ChatGPTAccounts:
    """Keep account metadata in SQLite and all OAuth credentials in macOS Keychain."""

    def __init__(self, workspace):
        self.w, self.lock = workspace, threading.RLock()
        self.namespace = hashlib.sha256(str(workspace.root.resolve()).encode()).hexdigest()[:20]
        self.error = ""
        self.generation = 0
        self.discovery = None

    def metadata(self):
        """Registrations survive sign-out so later logins reuse their issued client IDs."""
        return self.w.store.get("chatgpt", "accounts", {"active": "", "accounts": [], "host_id": ""})

    def state(self):
        """Public state never contains OAuth secrets or unvalidated account claims."""
        value = self.metadata()
        connected = [row for row in value["accounts"] if row.get("connected")]
        accounts = {}
        for row in connected:
            key = account_key(row)
            if key not in accounts or row["id"] == value["active"]:
                accounts[key] = row
        accounts = list(accounts.values())
        active = next((row for row in accounts if row["id"] == value["active"]), None)
        identity = active["id"] if active else ""
        return {"accounts": accounts, "active": identity, "connected": bool(active),
                "models": self.w.store.get("chatgpt_models", identity, []) if active else [],
                "notice": value.get("notice", ""), "error": self.error}

    def endpoints(self):
        """Discovery is accepted only from the pinned issuer and official auth host."""
        if not self.discovery:
            result = request_json(AUTH + "/.well-known/openid-configuration")
            if result.get("issuer") != AUTH or result.get("token_endpoint") != TOKEN:
                raise ValueError("Unexpected ChatGPT authentication configuration.")
            for key in ("jwks_uri", "revocation_endpoint"):
                if not str(result.get(key, "")).startswith(AUTH + "/"):
                    raise ValueError("Unexpected ChatGPT authentication endpoint.")
            self.discovery = result
        return self.discovery

    def host(self):
        """A stable opaque installation identity is separate from authentication secrets."""
        with self.lock:
            value = self.metadata()
            if not value["host_id"]:
                value["host_id"] = "urn:uuid:" + str(uuid.uuid4())
                self.w.store.put("chatgpt", "accounts", value)
            return value["host_id"]

    def credentials(self, client_id):
        """Read one registration's credentials without mixing accounts or workspaces."""
        raw = self.w.secrets.get("chatgpt:" + self.namespace + ":" + client_id)
        return json.loads(raw) if raw else None

    def save_tokens(self, client_id, tokens):
        """Persist rotating credentials as one secret; no token is stored in app records."""
        self.w.secrets.set("chatgpt:" + self.namespace + ":" + client_id, json.dumps(tokens) if tokens else "")

    def accept(self, client_id, response, nonce, expected=None):
        """Validate the identity and inference scope before replacing the active connection."""
        keys = request_json(self.endpoints()["jwks_uri"])
        identity = validate_identity(response.get("id_token", ""), client_id, nonce, keys, expected)
        if not SCOPES <= set(response.get("scope", "").split()) or not response.get("access_token") or not response.get("refresh_token"):
            raise ValueError("Allow ChatGPT plan usage during sign-in, then reconnect.")
        tokens = {**response, "expires_at": time.time() + float(response["expires_in"])}
        value = self.metadata()
        prior = next((row for row in value["accounts"] if row["client_id"] == client_id), None)
        if prior and prior["subject"] != identity["sub"]:
            raise ValueError("The selected account does not match this sign-in.")
        if not prior or not prior.get("connected"):
            self.reject_duplicate(client_id, identity, response)
        row = {"id": prior["id"] if prior else uuid.uuid4().hex, "client_id": client_id, "subject": identity["sub"], "email": str(identity.get("email") or ""),
               "label": str(identity.get("email") or identity.get("name") or "ChatGPT") + " · " + client_id[-6:], "connected": True}
        self.save_tokens(client_id, tokens)
        value["accounts"] = [item for item in value["accounts"] if item["id"] != row["id"]] + [row]
        value["active"] = row["id"]
        self.w.store.put("chatgpt", "accounts", value)
        self.error = ""
        return row

    def token(self):
        """Serialize token rotation and stop on expiry failures without API-key fallback."""
        with self.lock:
            value = self.metadata()
            row = next((item for item in value["accounts"] if item["id"] == value["active"] and item.get("connected")), None)
            tokens = self.credentials(row["client_id"]) if row else None
            if not tokens:
                raise ValueError("Connect ChatGPT in Agent settings first.")
            if tokens["expires_at"] <= time.time() + 60:
                try:
                    refreshed = request_json(TOKEN, {"grant_type": "refresh_token", "client_id": row["client_id"],
                        "refresh_token": tokens["refresh_token"], "resource": RESOURCE}, form=True)
                    scopes = refreshed.get("scope", tokens.get("scope", ""))
                    if not SCOPES <= set(scopes.split()) or not refreshed.get("refresh_token"):
                        raise ValueError("ChatGPT plan permission is missing. Reconnect.")
                    if refreshed.get("id_token"):
                        validate_identity(refreshed["id_token"], row["client_id"], None, request_json(self.endpoints()["jwks_uri"]), row["subject"])
                    tokens = {**tokens, **refreshed, "scope": scopes, "expires_at": time.time() + float(refreshed["expires_in"])}
                    self.save_tokens(row["client_id"], tokens)
                except Exception:
                    self.error = "ChatGPT authorization could not be renewed. Reconnect your account."
                    raise ValueError(self.error) from None
            return tokens["access_token"]

    def load_models(self):
        """The account's live catalog determines choices; no model ID is invented."""
        with self.lock:
            active = self.metadata()["active"]
            result = request_json(RESOURCE + "/models", token=self.token())
            entries = result.get("models")
            if not isinstance(entries, list) or len(entries) > 1000:
                raise ValueError("ChatGPT returned an invalid model catalog.")
            models = []
            for entry in entries:
                if entry.get("visibility") != "list":
                    continue
                slug, name = entry.get("slug"), entry.get("display_name")
                if not isinstance(slug, str) or not slug or len(slug) > 200:
                    raise ValueError("ChatGPT returned an invalid model ID.")
                efforts = entry.get("supported_reasoning_levels", [])
                efforts = [item.get("effort") if isinstance(item, dict) else item for item in efforts]
                models.append({"id": slug, "name": str(name or slug)[:200], "thinking": [item for item in efforts if item in {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}]})
            if not models:
                raise ValueError("No ChatGPT models are available for this account.")
            self.w.store.put("chatgpt_models", active, models)
            self.error = ""
            return models

    def select(self, identity):
        """Explicit account switches invalidate running requests before any further work."""
        with self.lock:
            value = self.metadata()
            row = next((item for item in value["accounts"] if item["id"] == identity), None)
            if not row or not row.get("connected"):
                raise ValueError("Reconnect this account first.")
            value["active"] = identity
            self.w.store.put("chatgpt", "accounts", value)
            self.generation += 1
            self.error = ""
        return self.state()

    def revoke(self, client_id, tokens):
        """Retry transient revocation failures before discarding a renewable session."""
        if not tokens:
            return False
        for attempt in range(3):
            try:
                request_json(self.endpoints()["revocation_endpoint"], {"token": tokens["refresh_token"],
                    "token_type_hint": "refresh_token", "client_id": client_id}, form=True)
                return True
            except Exception as exc:
                retryable = not isinstance(exc, AccountError) or exc.status == 0 or exc.status >= 500
                if not retryable or attempt == 2:
                    return False
                time.sleep(0.25 * 2 ** attempt)

    def reject_duplicate(self, client_id, identity, response):
        """Reject a second registration without ever signing into the first using its email."""
        candidate = {"email": str(identity.get("email") or ""), "subject": identity["sub"]}
        duplicate = next((row for row in self.metadata()["accounts"] if row.get("connected")
                          and row["client_id"] != client_id and (row["subject"] == identity["sub"]
                          or account_key(row) == account_key(candidate))), None)
        if not duplicate:
            return
        revoked = self.revoke(client_id, response)
        message = "This account is already connected. Choose its existing entry in ApplyDesk. "
        message += "The extra session was revoked. " if revoked else "Revocation of the extra session was not confirmed. "
        raise ValueError(message + "Remove the extra ApplyDesk authorization in ChatGPT Settings → Security and login → Sign in with ChatGPT.")

    def disconnect(self):
        """Remove every saved session for the selected email without activating another account."""
        with self.lock:
            value = self.metadata()
            row = next((item for item in value["accounts"] if item["id"] == value["active"]), None)
            if not row:
                return self.state()
            self.generation += 1
            self.error = ""
            for item in value["accounts"]:
                if account_key(item) != account_key(row) or not item.get("connected"):
                    continue
                tokens = self.credentials(item["client_id"])
                if not self.revoke(item["client_id"], tokens):
                    self.error = "Disconnected locally. Remote revocation was not confirmed; disconnect ApplyDesk in ChatGPT Settings."
                self.save_tokens(item["client_id"], None)
                item["connected"] = False
            value["active"] = ""
            value["notice"] = ("Removed from ApplyDesk. To remove its ChatGPT authorization, open ChatGPT Settings → "
                               "Security and login → Sign in with ChatGPT → ApplyDesk → Disconnect.")
            self.w.store.put("chatgpt", "accounts", value)
        return self.state()
