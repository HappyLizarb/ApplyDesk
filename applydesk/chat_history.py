"""Persist visible chat messages separately from private provider credentials and tool context."""

import uuid
from .agent_contract import string
from .storage import now


class ChatHistory:
    """Only completed turns enter conversation context; interrupted text stays visibly marked."""

    def __init__(self, workspace):
        self.w, self.live = workspace, None
        self.version = 0
        self.since = self.w.store.get("chat_control", "main", {}).get("cleared_at", "")
        for message in self.w.store.all("chat_message"):
            if message.get("status") == "running":
                message.update(status="interrupted", text=message.get("text") or "Interrupted when ApplyDesk closed.")
                self.w.store.put("chat_message", message["id"], message)

    def add(self, role, text, status="complete", **extra):
        """Messages get app-owned identities and cannot supply tool authority through text."""
        message = {"id": uuid.uuid4().hex, "role": role, "text": string(text, 200000, empty=True),
                   "status": status, "created_at": now(), **extra}
        self.w.store.put("chat_message", message["id"], message)
        return message

    def begin_turn(self, message):
        """Mark the request in progress and create its visible, streamable assistant reply."""
        if message:
            message["status"] = "running"
            self.w.store.put("chat_message", message["id"], message)
        assistant = self.add("assistant", "", "running", request_id=message["id"] if message else None)
        self.live = assistant.copy()
        return assistant

    def finish(self, message, text, status="complete"):
        """Persist the final visible result exactly once; partial text never becomes a result."""
        with self.w.lock:
            if not self.w.store.get("chat_message", message["id"]):
                return
            message.update(text=text, status=status)
            self.w.store.put("chat_message", message["id"], message)
            self.live = None

    def clear(self):
        """Invalidate in-flight chat turns before deleting visible conversation history."""
        with self.w.lock:
            self.version += 1
            self.live = None
            self.since = now()
            self.w.store.clear("chat_message")
            self.w.store.put("chat_control", "main", {"cleared_at": self.since})

    def read(self):
        """Expose the latest messages and an in-memory streamed draft without losing composer state."""
        messages = sorted(self.w.store.all("chat_message"), key=lambda item: item["created_at"])[-100:]
        if self.live:
            messages = [self.live.copy() if item["id"] == self.live["id"] else item for item in messages]
        return messages

    def application_runs(self):
        """Expose this chat's recent batches while keeping older receipts in the job tracker."""
        runs = (run for run in self.w.store.all("application_run") if run.get("conversation_id", "") == self.since)
        return sorted(runs, key=lambda run: run.get("updated_at", ""))[-10:]

    def context(self, current_id, include_ids=False):
        """Bound conversation history and exclude queued user messages from the current turn."""
        messages = sorted(self.w.store.all("chat_message"), key=lambda item: item["created_at"])
        prior = messages[:next((index for index, item in enumerate(messages) if item["id"] == current_id), len(messages))]
        prior = [item for item in prior if item["status"] == "complete" and item["role"] in {"user", "assistant"}]
        result, size = [], 0
        for item in reversed(prior[-30:]):
            if size + len(item["text"]) > 50000:
                break
            turn = {"role": item["role"], "content": item["text"]}
            if include_ids:
                turn["id"] = item["id"]
            result.insert(0, turn)
            size += len(item["text"])
        return result
