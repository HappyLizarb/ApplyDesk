"""User-initiated browser sign-in with single-use state, nonce, and PKCE."""

import base64
import hashlib
import hmac
import secrets
import threading
import time
import webbrowser
from urllib.parse import urlencode

from .chatgpt_accounts import AUTH, RESOURCE, TOKEN
from .chatgpt_callback import start_callback
from .chatgpt_transport import request_json


class ChatGPTSignIn:
    """A pending sign-in never replaces the existing account until it is validated."""

    def __init__(self, accounts):
        self.accounts, self.pending, self.server = accounts, None, None
        self.lock = threading.RLock()
        self.status, self.error = "idle", ""
        self.timer = None

    def begin(self, identity=""):
        """Open official authorization in the system browser without exposing tokens to the UI."""
        with self.lock:
            self.cancel()
            self.server = start_callback(self)
            try:
                self.pending = self._transaction(identity)
            except Exception:
                self._close_listener()
                raise
            self.status, self.error = "waiting", ""
            self.timer = threading.Timer(600, self.expire)
            self.timer.daemon = True
            self.timer.start()
            url = AUTH + "/api/accounts/authorize?" + urlencode(self.pending["parameters"])
        if not webbrowser.open(url):
            self.cancel()
            raise ValueError("Could not open the browser. Try Connect ChatGPT again.")
        return {"status": "waiting"}

    def _transaction(self, identity):
        """Bind issued client, redirect URI, and identity to the exact sign-in attempt."""
        value = self.accounts.metadata()
        selected = next((row for row in value["accounts"] if row["id"] == identity), None)
        if identity and not selected:
            raise ValueError("Choose a saved ChatGPT account.")
        verifier, state, nonce = secrets.token_urlsafe(48), secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        redirect = f"http://127.0.0.1:{self.server.server_port}/auth/callback"
        client = selected["client_id"] if selected else "dynamic_agent_client"
        parameters = {"client_id": client, "ext_agent_host_id": self.accounts.host(), "response_type": "code", "redirect_uri": redirect,
                      "scope": "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct", "resource": RESOURCE,
                      "state": state, "nonce": nonce, "code_challenge_method": "S256",
                      "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")}
        if not selected:
            parameters["agent_name_hint"] = "ApplyDesk"
        return {"parameters": parameters, "verifier": verifier, "subject": selected["subject"] if selected else None,
                "expires": time.monotonic() + 600, "generation": self.accounts.generation}

    def complete(self, query):
        """Validate and consume callback state before code exchange; reject stale callbacks."""
        with self.lock:
            pending = self.pending
            if not pending or time.monotonic() > pending["expires"] or not hmac.compare_digest(query.get("state", ""), pending["parameters"]["state"]):
                raise ValueError("Invalid or expired sign-in state.")
            self.pending = None
            self.status = "connecting"
            try:
                self._exchange(pending, query)
                self.status, self.error = "connected", ""
                try:
                    self.accounts.load_models()
                except Exception as exc:
                    self.accounts.error = str(exc)
            except Exception as exc:
                self.status, self.error = "error", str(exc)
                raise
            finally:
                self._close_listener()

    def _exchange(self, pending, query):
        """Never accept a changed returning client or an unapproved plan scope."""
        if query.get("error"):
            raise ValueError("ChatGPT sign-in was cancelled or plan usage was not allowed.")
        original = pending["parameters"]["client_id"]
        client = query.get("client_id", original)
        if not isinstance(client, str) or not client.startswith("oaiapp_") or len(client) > 200 or (original != "dynamic_agent_client" and original != client):
            raise ValueError("ChatGPT registration did not return the expected client ID.")
        code = query.get("code", "")
        if not code or len(code) > 10000:
            raise ValueError("ChatGPT did not return a valid authorization code.")
        response = request_json(TOKEN, {"grant_type": "authorization_code", "client_id": client, "code": code,
            "code_verifier": pending["verifier"], "redirect_uri": pending["parameters"]["redirect_uri"], "resource": RESOURCE}, form=True)
        with self.accounts.lock:
            if pending["generation"] != self.accounts.generation:
                raise ValueError("Account changed during sign-in. Connect again.")
            self.accounts.accept(client, response, pending["parameters"]["nonce"], pending["subject"])
            self.accounts.generation += 1

    def _close_listener(self):
        """Close the callback listener without blocking its current request thread."""
        if self.timer:
            self.timer.cancel()
            self.timer = None
        if self.server:
            server, self.server = self.server, None
            threading.Thread(target=self._stop_listener, args=(server,), daemon=True).start()

    @staticmethod
    def _stop_listener(server):
        """Release the ephemeral port after the callback has returned its response."""
        server.shutdown()
        server.server_close()

    def expire(self):
        """Expired sign-ins cannot remain falsely displayed as connected or keep a port open."""
        with self.lock:
            self.cancel()
            self.status, self.error = "error", "Sign-in timed out. Connect ChatGPT again."

    def cancel(self):
        """Cancellation invalidates pending state, including a previous browser tab."""
        with self.lock:
            self.pending = None
            self.status, self.error = "idle", ""
            self._close_listener()
