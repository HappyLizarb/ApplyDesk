"""One chat agent with automatic queue routing and local draft validation."""

import json
import os
import subprocess
import sys
import threading

from .agent_contract import object_fields, string
from .agent_settings import AgentSettings
from .application_automation import ApplicationAutomation
from .auto_start import AutoStart
from .chat_history import ChatHistory
from .agent_connections import AgentConnections
from .chatgpt_auth import ChatGPTSignIn
from .chatgpt_inference import ChatGPTRunner, output_text
from .chatgpt_transport import AccountError
from .orchestrator_prompts import MASTER
from .orchestrator_tools import MasterTools, ToolProgress, master_tools, run_report
from .orchestrator_workflow import DraftWorkflow
from .application_session import status as application_browser_status


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
        self._sleep_lock = threading.Lock()
        self._sleep_assertion = None
        self.paused = workspace.store.get("agent_control", "main", {}).get("paused", False)
        self.generation, self.attempted = 0, {}
        self.error = ""
        self.applications = ApplicationAutomation(self)
        self.auto_start = AutoStart(self)

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
                "application_browser": application_browser_status(self.w), "auto_start": self.auto_start.state(),
                "settings": self.settings.read(), "paused": self.paused, "active": self.active,
                "current_run": current if current and current.get("started_at", "") >= self.history.since else None, "error": self.error, "messages": self.history.read(),
                "runs": runs[-40:], "application_runs": self.history.application_runs()}

    def send(self, data):
        """User text enters the chat queue; new input interrupts work before the next local write."""
        object_fields(data, ["message"])
        text = string(data["message"], 20000)
        if not self.accounts.state()["connected"]:
            raise ValueError("Connect ChatGPT or add an OpenAI API key in Account setup first.")
        self.settings.validate(self.settings.read()["roles"]["master"])
        self.auto_start.forget()
        message = self.history.add("user", text, "queued")
        if self.active:
            self.interrupt()
        self.wake.set()
        return {"message_id": message["id"], "status": "queued"}

    def control(self, data, *, automatic=False):
        """Pause is persistent; resuming is explicit and never bypasses task blockers."""
        object_fields(data, ["paused"])
        if type(data["paused"]) is not bool:
            raise ValueError("Choose Pause or Resume.")
        self.paused = data["paused"]
        self.w.store.put("agent_control", "main", {"paused": self.paused})
        if self.paused:
            if not automatic:
                self.auto_start.disable()
            self.interrupt()
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
        return {"cleared": True, "paused": True}

    def retry(self, data):
        """Only an explicit user action can retry a blocked draft after fixing its input."""
        object_fields(data, ["task_id"])
        context = self.w.chat.get(string(data["task_id"], 100))
        task = context["task"]
        if task["status"] not in {"needs_input", "stale"}:
            raise ValueError("Only a blocked or changed task needs a retry.")
        task.pop("blocked_version", None)
        task.update(status="queued", issues=[])
        self.w.store.put("chat_task", task["id"], task)
        self.attempted.pop(task["id"], None)
        self.wake.set()
        return {"status": "queued"}

    def check_active(self):
        """Run cancellation and account identity are checked again immediately before commits."""
        if self.stop.is_set() or self.paused or self.cancelled.is_set() or self.generation != self.accounts.generation:
            raise InterruptedError("Paused or account changed. No further draft changes were saved.")

    def prevent_idle_sleep(self):
        """Let the display sleep while macOS keeps the active agent turn running."""
        with self._sleep_lock:
            self.check_active()
            if sys.platform == "darwin" and self._sleep_assertion is None:
                self._sleep_assertion = subprocess.Popen(
                    ["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())], stdin=subprocess.DEVNULL)

    def allow_idle_sleep(self):
        """Release the process-owned assertion once, including when Pause races completion."""
        with self._sleep_lock:
            if self._sleep_assertion is not None:
                self._sleep_assertion.kill()
                self._sleep_assertion.wait()
                self._sleep_assertion = None

    def interrupt(self):
        """Withdraw active work and its sleep assertion before interrupting blocking I/O."""
        self.cancelled.set()
        self.allow_idle_sleep()
        self.runner.cancel()
        self.w.application_browser.interrupt()

    def run_agent(self, role, prompt, inputs, context=None, tools=None):
        """Record sequential steps of the same agent; only chat prose is streamed."""
        self.check_active()
        task = {"id": context["task"]["id"], "version": context["version"]} if context else {}
        task["turn_id"] = (self.history.live or {}).get("id") or ((self.active or {}).get("message") or {}).get("id")
        for attempt in range(2):
            live = self.history.live
            prefix = live.get("text", "") if tools and live else ""
            try:
                result = self.runner.run(role, prompt, inputs, self.cancelled, self._stream if tools else lambda _: None, tools, task)
                break
            except AccountError as error:
                if attempt or not error.retryable:
                    raise
                self.cancelled.wait(1)
                self.check_active()
                # Remove partial text from this inference only. Completed tools never run a second time.
                with self.w.lock:
                    if tools and live and self.history.live is live:
                        live["text"] = prefix
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
        timer = self.auto_start.state()
        if timer["enabled"] and timer["pending"]:
            return None  # The interrupted user request waits for its stored retry deadline.
        if not self.settings.read()["automatic"]:
            return None
        for row in self.w.chat.list():
            if row["status"] not in {"queued", "stale"}:
                continue
            context = self.w.chat.get(row["id"])
            if self.attempted.get(row["id"]) != context["version"]:
                self.attempted[row["id"]] = context["version"]
                return {"task_id": row["id"], "history_version": self.history.version}
        return None

    def _loop(self):
        """One worker owns routing and writes so task updates cannot race."""
        while not self.stop.is_set():
            self.wake.wait(1)
            self.wake.clear()
            if self.stop.is_set():
                break
            try:
                with self.w.lock:
                    work = self.auto_start.take() or self._next()
                    if work:
                        self.active = work
                        self.cancelled.clear()
                        self.generation = self.accounts.generation
                if work:
                    self.prevent_idle_sleep()
                    self._perform(work)
            except Exception as exc:
                if not self.stop.is_set():
                    self.error = str(exc)[:1000]
                    self.control({"paused": True}, automatic=True)
            finally:
                self.allow_idle_sleep()
                self.active = None

    def _perform(self, work):
        """Persist completed chat turns and visible interruption/error states without lost drafts."""
        with self.w.lock:
            if work.get("history_version", self.history.version) != self.history.version:
                return
            message = work.get("message")
            work.setdefault("retry_binding", self.auto_start.binding(work))
            assistant = self.history.begin_turn(message)
        text = message["text"] if message else f"Automatically prepare the already-authorized queued draft task {work['task_id']}. Use prepare_task and report actual results."
        if work.get("auto_restart"):
            text = "Continue this unfinished request after a usage-limit interruption. Reuse saved results; do not repeat completed work.\n\n" + text
        try:
            history = self.history.context(message["id"]) if message else []
            answer = self._master([*history, {"role": "user", "content": text}])
            self.history.finish(assistant, answer)
            self.auto_start.forget(work)
            if message:
                self.history.finish(message, message["text"])
            elif self.w.chat.get(work["task_id"])["task"]["status"] == "queued":
                context = self.w.chat.get(work["task_id"])
                self.w.chat.block({"task_id": work["task_id"], "version": context["version"], "issues": ["The agent did not prepare this task. Retry from the application details."]})
        except Exception as exc:
            if work.get("history_version", self.history.version) != self.history.version:
                return
            status = "interrupted" if isinstance(exc, InterruptedError) or self.cancelled.is_set() else "error"
            detail = self._run_error(exc, message) if status == "error" else str(exc)[:1000]
            self.history.finish(assistant, detail, status)
            self.auto_start.failed(work, exc, assistant)
            if message:
                self.history.finish(message, message["text"], "complete")
            if status == "error":
                raise

    def _run_error(self, error, message):
        """Show the last saved application outcome even if the model fails to write its summary."""
        detail = str(error)[:1000]
        if not isinstance(error, AccountError) or not message or not self.applications.run:
            return detail
        report = run_report(self.applications.run, message["id"])
        outcomes = report.get("results", [])
        if not outcomes:
            return detail
        last = outcomes[-1]
        job = self.w.store.get("job", last["job_id"]) or {}
        company = job.get("company") or "Last application"
        issue = next(iter(last.get("issues") or []), "")
        status = {"needs_review": "Needs input"}.get(last["status"], last["status"].replace("_", " "))
        return f"{company} · {status}. {issue}\nConfirmed submissions in this run: {report.get('submitted_count', 0)}.\n\n{detail}"

    def _master(self, history):
        """Keep working through tool results until the agent finishes or the user stops it."""
        text, progress = [], ToolProgress(self.tools.call)
        while True:
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
            for call in calls:
                self.check_active()
                result = progress.execute(call)
                history.append({"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(result)})

    def close(self):
        """Stop callbacks and inference before closing SQLite; unfinished tasks remain resumable."""
        self.stop.set()
        self.interrupt()
        self.wake.set()
        self.signin.cancel()
        with self.accounts.lock:
            pass  # Wait for any token renewal or model-list write before SQLite closes.
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join()
