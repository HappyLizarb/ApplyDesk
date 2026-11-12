"""Local master chat, automatic queue routing, and mandatory independent draft reviews."""

import json
import threading

from .agent_contract import object_fields, string
from .agent_settings import AgentSettings
from .application_automation import ApplicationAutomation
from .chat_history import ChatHistory
from .agent_connections import AgentConnections
from .chatgpt_auth import ChatGPTSignIn
from .chatgpt_inference import ChatGPTRunner, output_text
from .orchestrator_prompts import MASTER
from .orchestrator_tools import MasterTools, master_tools
from .orchestrator_workflow import DraftWorkflow


class Orchestrator:
    """Only this controller assigns trusted run identities and commits model-generated drafts."""

    def __init__(self, workspace):
        self.w = workspace
        self.accounts = AgentConnections(workspace)
        self.signin = ChatGPTSignIn(self.accounts)
        self.settings = AgentSettings(workspace, self.accounts)
        self.history = ChatHistory(workspace)
        self.runner = ChatGPTRunner(workspace, self.accounts, self.settings)
        self.workflow = DraftWorkflow(self)
        self.tools = MasterTools(self, self.workflow)
        self.stop, self.cancelled, self.wake = threading.Event(), threading.Event(), threading.Event()
        self.thread, self.active = None, None
        self.paused = workspace.store.get("agent_control", "main", {}).get("paused", False)
        self.generation, self.attempted = 0, {}
        self.error = ""
        self.applications = ApplicationAutomation(self)

    def start(self):
        """Start a local scheduler; disconnected accounts cause no network or model requests."""
        if self.thread is None:
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()

    def state(self):
        """Keep credentials private while exposing connection, stream, queue, and role progress."""
        current = self.runner.current
        runs = [run for run in self.w.store.all("agent_run") if run.get("started_at", "") >= self.history.since]
        return {"account": self.accounts.state(), "signin": {"status": self.signin.status, "error": self.signin.error},
                "settings": self.settings.read(), "paused": self.paused, "active": self.active,
                "current_run": current if current and current.get("started_at", "") >= self.history.since else None, "error": self.error, "messages": self.history.read(),
                "runs": runs[-40:], "application_runs": self.w.store.all("application_run")[-10:]}

    def send(self, data):
        """User text enters the chat queue; new input interrupts work before the next local write."""
        object_fields(data, ["message"])
        text = string(data["message"], 20000)
        if not self.accounts.state()["connected"]:
            raise ValueError("Connect ChatGPT or add an OpenAI API key in Account setup first.")
        self.settings.validate(self.settings.read()["roles"]["master"])
        message = self.history.add("user", text, "queued")
        if self.active:
            self.cancelled.set()
            self.runner.cancel()
        self.wake.set()
        return {"message_id": message["id"], "status": "queued"}

    def control(self, data):
        """Pause is persistent; resuming is explicit and never bypasses task blockers."""
        object_fields(data, ["paused"])
        if type(data["paused"]) is not bool:
            raise ValueError("Choose Pause or Resume.")
        self.paused = data["paused"]
        self.w.store.put("agent_control", "main", {"paused": self.paused})
        if self.paused:
            self.cancelled.set()
            self.runner.cancel()
            self.w.recommendations.cancel_all("Paused by the user.")
        else:
            self.error = ""
        self.wake.set()
        return {"paused": self.paused}

    def reset(self, data):
        """Clear only the current conversation, cancel active work, and leave saved drafts paused."""
        object_fields(data, [])
        with self.w.lock:
            self.control({"paused": True})
            self.history.clear()
            self.error = ""
            self.w.recommendations.cancel_all("Stopped when chat was reset.")
        return {"cleared": True, "paused": True}

    def retry(self, data):
        """Only an explicit user action can retry a blocked draft after fixing its input."""
        object_fields(data, ["task_id"])
        context = self.w.chat.get(string(data["task_id"], 100))
        task = context["task"]
        if task["status"] not in {"needs_input", "stale"}:
            raise ValueError("Only a blocked or changed task needs a retry.")
        task.update(status="queued", result=None, reviews={}, issues=[], document_ids=[], input_version=None)
        self.w.store.put("chat_task", task["id"], task)
        self.attempted.pop(task["id"], None)
        self.wake.set()
        return {"status": "queued"}

    def check_active(self):
        """Run cancellation and account identity are checked again immediately before commits."""
        if self.stop.is_set() or self.paused or self.cancelled.is_set() or self.generation != self.accounts.generation:
            raise InterruptedError("Paused or account changed. No further draft changes were saved.")

    def run_agent(self, role, prompt, inputs, context=None, tools=None):
        """Each delegated role receives a fresh request and its own app-owned execution record."""
        self.check_active()
        task = {"id": context["task"]["id"], "version": context["version"]} if context else None
        result = self.runner.run(role, prompt, inputs, self.cancelled, self._stream if role == "master" else lambda _: None, tools, task)
        self.check_active()
        return result

    def _stream(self, delta):
        """Stream visible assistant text only, with a bounded local message size."""
        with self.w.lock:
            if self.history.live and not self.cancelled.is_set():
                self.history.live["text"] = (self.history.live["text"] + delta)[:200000]

    def _next(self):
        """Chat messages take priority; automatic dispatch only considers actionable queued drafts."""
        account = self.accounts.state()
        if self.paused or self.signin.status in {"waiting", "connecting"} or not account["connected"] or not account["models"]:
            return None
        if any(task["status"] == "running" for task in self.w.tasks.values()):
            return None
        message = next((item for item in self.w.store.all("chat_message") if item["role"] == "user" and item["status"] == "queued"), None)
        if message:
            return {"message": message, "history_version": self.history.version}
        if not self.settings.read()["automatic"]:
            return None
        for row in self.w.chat.list():
            if row["status"] not in {"queued", "stale", "awaiting_review"}:
                continue
            context = self.w.chat.get(row["id"])
            if self.attempted.get(row["id"]) != context["version"]:
                self.attempted[row["id"]] = context["version"]
                return {"task_id": row["id"], "history_version": self.history.version}
        return None

    def _loop(self):
        """One worker owns routing and reviews; no second process can race draft writes."""
        while not self.stop.is_set():
            self.wake.wait(1)
            self.wake.clear()
            if self.stop.is_set():
                break
            try:
                with self.w.lock:
                    work = self._next()
                    if work:
                        self.active = work
                        self.cancelled.clear()
                        self.generation = self.accounts.generation
                if work:
                    self._perform(work)
            except Exception as exc:
                if not self.stop.is_set():
                    self.error = str(exc)[:1000]
                    self.control({"paused": True})
            finally:
                self.active = None

    def _perform(self, work):
        """Persist completed chat turns and visible interruption/error states without lost drafts."""
        with self.w.lock:
            if work.get("history_version", self.history.version) != self.history.version:
                return
            message = work.get("message")
            if message:
                message["status"] = "running"
                self.w.store.put("chat_message", message["id"], message)
            assistant = self.history.add("assistant", "", "running")
            self.history.live = assistant.copy()
        text = message["text"] if message else f"Automatically prepare the already-authorized queued draft task {work['task_id']}. Delegate with prepare_task and report actual results."
        try:
            history = self.history.context(message["id"]) if message else []
            answer = self._master([*history, {"role": "user", "content": text}])
            self.history.finish(assistant, answer)
            if message:
                self.history.finish(message, message["text"])
            elif self.w.chat.get(work["task_id"])["task"]["status"] == "queued":
                context = self.w.chat.get(work["task_id"])
                self.w.chat.block({"task_id": work["task_id"], "version": context["version"], "issues": ["The master did not delegate this task. Retry from the application details."]})
        except Exception as exc:
            if work.get("history_version", self.history.version) != self.history.version:
                return
            status = "interrupted" if isinstance(exc, InterruptedError) or self.cancelled.is_set() else "error"
            self.history.finish(assistant, str(exc)[:1000], status)
            if message:
                self.history.finish(message, message["text"], "complete")
            if status == "error":
                raise

    def _master(self, history):
        """Bound tool rounds; preserve function outputs locally for the current planning turn."""
        text = []
        for _ in range(8):
            _, response = self.run_agent("master", MASTER, history, tools=master_tools())
            output = response.get("output", [])
            text.append(output_text(response))
            calls = [item for item in output if item.get("type") == "function_call"]
            if not calls:
                answer = "\n\n".join(part for part in text if part)
                if not answer:
                    raise ValueError("ChatGPT returned no completed text or tool calls. Please retry your message.")
                return answer
            history.extend(output)
            for call in calls[:8]:
                self.check_active()
                try:
                    result = self.tools.call(call)
                except InterruptedError:
                    raise
                except Exception as exc:
                    result = {"error": str(exc)[:1000]}
                history.append({"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(result)})
        raise ValueError("The master reached its task-step limit. Continue with a narrower request.")

    def close(self):
        """Stop callbacks and inference before closing SQLite; unfinished tasks remain resumable."""
        self.stop.set()
        self.cancelled.set()
        self.wake.set()
        self.runner.cancel()
        self.signin.cancel()
        self.w.recommendations.cancel_all("Stopped when ApplyDesk closed.")
        with self.accounts.lock:
            pass  # Wait for any token renewal or model-list write before SQLite closes.
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join()
