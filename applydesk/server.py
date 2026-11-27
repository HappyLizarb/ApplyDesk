"""Loopback-only HTTP interface with session, origin, size, and path checks."""

import hmac
import json
import mimetypes
import secrets
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import PROGRAM_ROOT
from .document_deletion import DocumentDeletion
from .employer_accounts import EmployerAccounts
from .orchestrator_http import orchestrator_action

WEB_ROOT = PROGRAM_ROOT / "web"
MAX_BODY = 29 * 1024 * 1024


class AppServer(ThreadingHTTPServer):
    """One ephemeral port and session token per local app launch."""

    def __init__(self, workspace, port=0):
        self.workspace = workspace
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.launch_url = self.origin + "/start?token=" + self.token
        workspace.orchestrator.start()


class Handler(BaseHTTPRequestHandler):
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
            if path == "/api/document/delete":
                return self._json(DocumentDeletion(self.server.workspace).delete(data))
            if path == "/api/jobs/remove":
                return self._json(self.server.workspace.remove_jobs(data))
            if path == "/api/job/status":
                return self._json(self.server.workspace.store.set_board_status(data))
            control = path == "/api/shutdown" or path.startswith("/api/orchestrator/")
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
        w = self.server.workspace
        if path.startswith("/api/orchestrator/"):
            return orchestrator_action(w, path.removeprefix("/api/orchestrator/"), data)
        routes = {"/api/profile": lambda: w.save_profile(data),
                  "/api/job/add": lambda: w.add_job(data), "/api/jobs/remove": lambda: w.remove_jobs(data),
                  "/api/import": lambda: w.start("import", w.import_file, data),
                  "/api/resume/tag": lambda: w.resumes.tag(data),
                  "/api/document/delete": lambda: DocumentDeletion(w).delete(data),
                  "/api/job/account": lambda: EmployerAccounts(w).save(data),
                  "/api/shutdown": self._shutdown}
        if path not in routes:
            raise ValueError("Unknown action.")
        return routes[path]()

    def _shutdown(self):
        """End the server after this response has been returned."""
        engine = self.server.workspace.orchestrator
        engine.stop.set()
        engine.interrupt()
        engine.wake.set()
        threading.Thread(target=self.server.shutdown, daemon=True).start()
        return {"closed": True}
