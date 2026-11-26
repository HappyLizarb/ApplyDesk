"""One persistent timer retries only a proven usage-limit interruption of the latest request."""

import hashlib
from datetime import datetime, timedelta, timezone

from .agent_contract import object_fields
from .chatgpt_transport import AccountError
from .providers import ProviderError


def usage_limited(error):
    """Only typed provider status evidence can permit an automatic retry."""
    return isinstance(error, (AccountError, ProviderError)) and error.status == 429 and error.code != "insufficient_quota"


class AutoStart:
    """The existing worker checks the timer; it never creates a second worker or a new user grant."""

    def __init__(self, engine):
        self.engine, self.w = engine, engine.w
        self.clock = lambda: datetime.now(timezone.utc)

    def _settings(self):
        """Keep the pending retry binding private while preserving the user's one interval."""
        defaults = {"enabled": False, "interval": 5, "unit": "hours", "next_run_at": None,
                    "last_run_at": None, "pending": None}
        return {**defaults, **self.w.store.get("auto_start", "main", {})}

    def state(self):
        """Expose timer state without duplicating request text or connection identity."""
        settings = self._settings()
        return {**{key: settings[key] for key in ("enabled", "interval", "unit", "next_run_at", "last_run_at")},
                "pending": bool(settings["pending"])}

    def _next_time(self, settings, current):
        """Reject timestamp overflow rather than imposing an arbitrary short interval cap."""
        try:
            seconds = settings["interval"] * {"minutes": 60, "hours": 3600}[settings["unit"]]
            return (current + timedelta(seconds=seconds)).isoformat()
        except (OverflowError, ValueError):
            raise ValueError("Choose an interval that fits a valid future date.") from None

    def save(self, data):
        """Changing the interval starts a fresh countdown and never immediately resumes work."""
        object_fields(data, ["enabled", "interval", "unit"])
        if type(data["enabled"]) is not bool or type(data["interval"]) is not int or data["interval"] < 1:
            raise ValueError("Choose whether to enable automatic start and enter a positive whole interval.")
        if not isinstance(data["unit"], str) or data["unit"] not in {"minutes", "hours"}:
            raise ValueError("Choose minutes or hours.")
        with self.w.lock:
            settings = {**self._settings(), **data}
            next_time = self._next_time(settings, self.clock())
            settings["next_run_at"] = next_time if data["enabled"] else None
            if not data["enabled"]:
                settings["pending"] = None
            self.w.store.put("auto_start", "main", settings)
        self.engine.wake.set()
        return self.state()

    def disable(self):
        """Manual Stop, chat reset and connection changes revoke pending timer permission."""
        with self.w.lock:
            settings = self._settings()
            settings.update(enabled=False, next_run_at=None, pending=None)
            self.w.store.put("auto_start", "main", settings)
        return self.state()

    def forget(self, work=None):
        """A completed, superseded or otherwise blocked request cannot be retried later."""
        with self.w.lock:
            settings = self._settings()
            pending = settings["pending"]
            if pending and (work is None or pending["message_id"] == (work.get("message") or {}).get("id")):
                settings["pending"] = None
                self.w.store.put("auto_start", "main", settings)

    def binding(self, work):
        """A retry is bound to unchanged user instructions, conversation, account and profile."""
        message = work.get("message") or {}
        if message.get("role") != "user":
            return None
        account = self.engine.accounts.state()
        return {"message_id": message["id"], "message_hash": hashlib.sha256(message["text"].encode()).hexdigest(),
                "conversation_id": self.engine.history.since, "account_id": account["active"],
                "source": account.get("source", "chatgpt"), "profile_revision": self.w.profile()["revision"]}

    def failed(self, work, error, assistant):
        """Remember actual usage-limit evidence, never a generic failure or manual cancellation."""
        with self.w.lock:
            binding = work.get("retry_binding")
            if self.engine.stop.is_set():
                return  # Closing preserves an existing proven retry, but creates no new one.
            if not usage_limited(error) or not binding or self.engine.cancelled.is_set():
                self.forget(work)
                return
            settings, current = self._settings(), self.clock()
            settings["pending"] = {**binding, "status": 429, "error_message_id": assistant["id"]}
            if settings["enabled"] and (not settings["next_run_at"] or settings["next_run_at"] <= current.isoformat()):
                settings["next_run_at"] = self._next_time(settings, current)
            self.w.store.put("auto_start", "main", settings)

    def _pending_work(self, pending):
        """Existing message identity keeps application allowances and submission ledgers intact."""
        messages = self.w.store.all("chat_message")
        users = sorted((item for item in messages if item["role"] == "user"), key=lambda item: item["created_at"])
        message = self.w.store.get("chat_message", pending["message_id"])
        if not message or not users or users[-1]["id"] != message["id"] or pending.get("status") != 429:
            return None
        work = {"message": message, "history_version": self.engine.history.version, "auto_restart": True}
        binding = self.binding(work)
        if binding != {key: pending.get(key) for key in binding}:
            return None
        replies = sorted((item for item in messages if item.get("request_id") == message["id"]), key=lambda item: item["created_at"])
        if replies and replies[-1]["status"] == "complete" and replies[-1]["id"] != pending["error_message_id"]:
            return None
        return work

    def take(self):
        """Coalesce missed intervals into one idle retry, advancing the deadline before dispatch."""
        with self.w.lock:
            settings, current = self._settings(), self.clock()
            if not settings["enabled"] or not settings["next_run_at"] or settings["next_run_at"] > current.isoformat():
                return None
            if self.engine.active or any(task["status"] == "running" for task in self.w.tasks.values()):
                return None
            account = self.engine.accounts.state()
            if not account["connected"] or not account["models"] or self.engine.signin.status in {"waiting", "connecting"}:
                return None
            work = self._pending_work(settings["pending"]) if settings["pending"] else None
            settings["next_run_at"] = self._next_time(settings, current)
            if not work:
                settings["pending"] = None
            else:
                settings["last_run_at"] = current.isoformat()
            self.w.store.put("auto_start", "main", settings)
            if work:
                self.engine.control({"paused": False})
            return work
