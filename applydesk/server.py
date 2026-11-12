"""Loopback-only HTTP interface with session, origin, size, and path checks."""

import hmac
import json
import mimetypes
import secrets
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .agent_http import AgentRoutes, publish_connection
from .document_tasks import DocumentTasks
from .document_deletion import DocumentDeletion
from .orchestrator_http import orchestrator_action

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
MAX_BODY = 29 * 1024 * 1024


class AppServer(ThreadingHTTPServer):
    """One ephemeral port and session token per local app launch."""

    def __init__(self, workspace, port=0):
        self.workspace = workspace
        self.token = secrets.token_urlsafe(32)
        self.docs = DocumentTasks(workspace)
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.launch_url = self.origin + "/start?token=" + self.token
        publish_connection(self)
        workspace.orchestrator.start()


class Handler(AgentRoutes, BaseHTTPRequestHandler):
    """Serve only app assets and explicit actions; never expose arbitrary local files."""

    def log_message(self, *_):
        """URLs, document text, and user input do not belong in console logs."""

    def _headers(self, status, mime, size=0, extra=None):
        """Prevent cross-origin reads, framing, caching, and script injection."""
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-src 'self'; object-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def _json(self, value, status=200):
        """Encode API responses without interpreting imported content as markup."""
        body = json.dumps(value).encode()
        self._headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _authorized(self):
        """Require the launch cookie and an exact loopback Host header."""
        if self.headers.get("Host") != self.server.origin.removeprefix("http://"):
            return False
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
            token = cookies["applydesk"].value if "applydesk" in cookies else ""
        except Exception:
            return False
        return hmac.compare_digest(token, self.server.token)

    def do_GET(self):
        """All document previews and state requests share the same local session gate."""
        parsed = urlparse(self.path)
        if parsed.path.startswith("/agent/v1/"):
            return self.agent_request(parsed, "GET")
        if parsed.path == "/start":
            token = parse_qs(parsed.query).get("token", [""])[0]
            if hmac.compare_digest(token, self.server.token) and self.headers.get("Host") == self.server.origin.removeprefix("http://"):
                self._headers(303, "text/plain", extra={"Location": "/", "Set-Cookie": f"applydesk={token}; HttpOnly; SameSite=Strict; Path=/"})
                return
        if not self._authorized():
            return self._json({"error": "Open ApplyDesk using its launcher."}, 403)
        try:
            self._get(parsed)
        except (ValueError, KeyError) as exc:
            self._json({"error": str(exc)}, 400)

    def _get(self, parsed):
        """Map known read routes to data or contained asset paths."""
        query = parse_qs(parsed.query)
        if parsed.path == "/api/chat/task":
            return self._json(self.server.workspace.chat.get(query.get("id", [""])[0]))
        if parsed.path == "/api/chat/guide":
            from .agent_context import guide
            return self._json(guide())
        if parsed.path == "/api/state":
            return self._json(self.server.workspace.state())
        if parsed.path == "/api/document":
            document = self.server.workspace.docs.get(query.get("id", [""])[0])
            document["review"] = self.server.workspace.store.get("document_review", document["id"])
            return self._json(document)
        if parsed.path == "/api/profile/export":
            return self._json(self.server.workspace.profile())
        if parsed.path.startswith("/asset/"):
            return self._file(self.server.workspace.docs.asset(unquote(parsed.path[7:])))
        relative = "index.html" if parsed.path == "/" else unquote(parsed.path).lstrip("/")
        candidate = (WEB_ROOT / relative).resolve()
        if not candidate.is_relative_to(WEB_ROOT) or not candidate.is_file():
            return self._json({"error": "Not found."}, 404)
        self._file(candidate)

    def _file(self, path):
        """Serve an already-validated local asset with its real content type."""
        body = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._headers(200, mime, len(body))
        self.wfile.write(body)

    def do_POST(self):
        """Reject cross-site writes even if a browser happens to retain the cookie."""
        if urlparse(self.path).path.startswith("/agent/v1/"):
            return self.agent_request(urlparse(self.path), "POST")
        if not self._authorized() or self.headers.get("Origin") != self.server.origin or self.headers.get("X-ApplyDesk") != "1":
            return self._json({"error": "Invalid local app session or request origin."}, 403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY or self.headers.get("Content-Type") != "application/json":
                raise ValueError("Expected a bounded JSON request.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object.")
            path = urlparse(self.path).path
            control = path == "/api/shutdown" or path.startswith("/api/orchestrator/") or path == "/api/recommendations/cancel" or (path == "/api/recommendations/preference" and data.get("enabled") is False)
            if not control and any(task["status"] == "running" for task in self.server.workspace.tasks.values()):
                raise ValueError("A task is running. Wait before changing the workspace.")
            with self.server.workspace.lock:
                self._json(self._post(urlparse(self.path).path, data))
        except (ValueError, KeyError, TypeError, RuntimeError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception:
            self._json({"error": "The action failed. Your saved workspace is retained."}, 500)

    def _post(self, path, data):
        """Dispatch a small fixed API; long provider and browser work runs in a task."""
        w, docs = self.server.workspace, self.server.docs
        if path.startswith("/api/orchestrator/"):
            return orchestrator_action(w, path.removeprefix("/api/orchestrator/"), data)
        routes = {"/api/profile": lambda: w.save_profile(data), "/api/cover-template": lambda: w.save_settings(data),
                  "/api/chat/enqueue": lambda: w.chat.enqueue(data),
                  "/api/recommendations/begin": lambda: w.recommendations.begin(data),
                  "/api/recommendations/cancel": lambda: w.recommendations.cancel(data),
                  "/api/recommendations/preference": lambda: w.recommendations.preference(data),
                  "/api/job/add": lambda: w.add_job(data), "/api/jobs/remove": lambda: w.remove_jobs(data),
                  "/api/import": lambda: w.start("import", w.import_file, data),
                  "/api/template/confirm": lambda: w.docs.confirm_template(data["id"]),
                  "/api/resume/tag": lambda: w.resumes.tag(data),
                  "/api/document/delete": lambda: DocumentDeletion(w).delete(data),
                  "/api/document/save": lambda: w.start("save-document", docs.save, data),
                  "/api/job/account": lambda: self._account(data),
                  "/api/shutdown": self._shutdown}
        if path not in routes:
            raise ValueError("Unknown action.")
        return routes[path]()

    def _account(self, data):
        """Store the username in the row and the password solely in Keychain."""
        w = self.server.workspace
        if not w.store.get("job", data["id"]):
            raise ValueError("Application not found.")
        origin = urlparse(str(data.get("account_origin", "")))
        if origin.scheme != "https" or not origin.hostname or origin.username or origin.password or origin.path not in {"", "/"} or origin.query or origin.fragment:
            raise ValueError("Enter the exact HTTPS sign-in origin, without a path, credentials, or query.")
        normalized = f"https://{origin.netloc.lower()}"
        job = w.store.get("job", data["id"])
        account = str(data.get("account", ""))[:500]
        if job.get("has_password") and (job.get("account_origin") != normalized or job.get("account") != account) and not data.get("password"):
            raise ValueError("Enter the password again when changing the account or sign-in origin.")
        changes = {"account": account, "account_origin": normalized}
        if data.get("password"):
            w.secrets.set("account:" + data["id"], str(data["password"]))
            changes["has_password"] = True
        return w.store.job(data["id"], **changes)

    def _shutdown(self):
        """End the server after this response has been returned."""
        engine = self.server.workspace.orchestrator
        engine.stop.set()
        engine.cancelled.set()
        engine.runner.cancel()
        engine.wake.set()
        threading.Thread(target=self.server.shutdown, daemon=True).start()
        return {"closed": True}
