"""User-initiated browser sign-in with single-use state, nonce, and PKCE."""

import base64
import hashlib
import hmac
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

from .chatgpt_accounts import AUTH, RESOURCE, TOKEN
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


class CallbackHandler(BaseHTTPRequestHandler):
    """Never log OAuth codes, state, tokens, or callback query strings."""

    def log_message(self, *_):
        """Suppress the standard request logger because callback URLs contain credentials."""

    def do_GET(self):
        """Require the exact callback host/path; consume valid state only once."""
        parsed = urlsplit(self.path)
        owner = self.server.owner
        host = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host") != host or parsed.path != "/auth/callback":
            self.send_error(404)
            return
        try:
            query = parse_qs(parsed.query, max_num_fields=12)
            if any(len(values) != 1 for values in query.values()):
                raise ValueError("Invalid callback.")
            owner.complete({key: values[0] for key, values in query.items()})
            message, status = "Connected. Return to ApplyDesk; you can close this tab.", 200
        except Exception:
            message, status = "Sign-in was not completed. Return to ApplyDesk for details.", 400
        body = ("<!doctype html><title>ApplyDesk</title><p>" + message + "</p>").encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)


def start_callback(owner):
    """Bind before opening the system browser; a fresh ephemeral port is supported."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), CallbackHandler)
    server.daemon_threads = True
    server.owner = owner
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
