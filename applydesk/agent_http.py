"""A separate local capability grants draft operations, never the browser/UI authority."""

import hmac
import json
import secrets
from urllib.parse import parse_qs

from .agent_context import guide


def publish_connection(server):
    """Rotate a private local token on launch without exposing it in URLs or UI state."""
    server.agent_token = secrets.token_urlsafe(32)
    path = server.workspace.root / "agent.connection.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"origin": server.origin, "token": server.agent_token}))
    temporary.chmod(0o600)
    temporary.replace(path)


class AgentRoutes:
    """Serve an explicit allowlist with independent authentication and bounded payloads."""

    def agent_request(self, parsed, method):
        """A chat token cannot authenticate any ordinary app route or cross-origin request."""
        token = self.headers.get("Authorization", "").removeprefix("Bearer ")
        same_host = self.headers.get("Host") == self.server.origin.removeprefix("http://")
        origin = self.headers.get("Origin")
        if not same_host or origin not in {None, self.server.origin} or not hmac.compare_digest(token, self.server.agent_token):
            return self._json({"error": "Invalid local chat connection."}, 403)
        try:
            with self.server.workspace.lock:
                if method == "GET":
                    return self.agent_read(parsed)
                if self.headers.get("Content-Type") != "application/json":
                    raise ValueError("Expected JSON.")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024 * 1024:
                    raise ValueError("Chat payload must be between 1 byte and 1 MiB.")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("Expected a JSON object.")
                if any(task["status"] == "running" for task in self.server.workspace.tasks.values()):
                    raise ValueError("A local import is running. Retry when it finishes.")
                return self._json(self.agent_write(parsed.path, data))
        except (ValueError, KeyError, TypeError, RuntimeError) as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception:
            return self._json({"error": "Chat action failed; the workspace is retained."}, 500)

    def agent_read(self, parsed):
        """Only the guide, task records, and task-bound page previews are readable."""
        chat = self.server.workspace.chat
        query = parse_qs(parsed.query)
        if parsed.path == "/agent/v1/guide":
            return self._json(guide())
        if parsed.path == "/agent/v1/tasks":
            return self._json(chat.list())
        if parsed.path == "/agent/v1/task":
            return self._json(chat.get(query.get("id", [""])[0]))
        if parsed.path == "/agent/v1/asset":
            context = chat.get(query.get("id", [""])[0])
            records = context["review"]["documents"] + [context["inputs"].get(key) for key in ("source", "document", "template")]
            paths = {path for record in records if record for key in ("image_paths", "source_image_paths") for path in record.get(key, [])}
            path = query.get("path", [""])[0]
            if path not in paths:
                raise ValueError("Choose a page preview attached to this task.")
            return self._file(self.server.workspace.docs.asset(path))
        raise ValueError("Unknown chat read operation.")

    def agent_write(self, path, data):
        """No operation exposes profile confirmation, application filling, or submission."""
        chat = self.server.workspace.chat
        if self.server.workspace.orchestrator.active:
            raise ValueError("The in-app agent is working. Pause it before external draft writes.")
        actions = {"enqueue": chat.enqueue, "draft": chat.submit,
                   "block": chat.block, "capture": chat.capture}
        action = path.removeprefix("/agent/v1/")
        if action not in actions:
            raise ValueError("Unknown chat action. This connection only supports unfinished drafts.")
        return actions[action](data)
