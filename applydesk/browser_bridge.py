"""Guarded, serialized communication with the dedicated Playwright process."""
from __future__ import annotations

import json
import os
from pathlib import Path
from . import PROGRAM_ROOT
import queue
import shutil
import subprocess
import threading
import uuid


class BrowserError(RuntimeError):
    """A rejected action or unavailable browser session."""


class BrowserSession:
    """Own one persistent browser profile without exposing arbitrary browser tools."""

    def __init__(self, data_root, *, headless=False, allow_local=False, upload_roots=None, source=None, application_source=None, research=False, application_workspace=False, existing_chrome=False, auto_approve_chrome=False):
        if source not in {None, "linkedin", "handshake"}:
            raise BrowserError("Unknown recommendation source.")
        if application_source not in {None, "linkedin", "handshake"} or (source and application_source):
            raise BrowserError("Choose a separate recommendation or application session.")
        if research and (source or application_source):
            raise BrowserError("Web research uses its own browser session.")
        if application_workspace and (source or application_source or research):
            raise BrowserError("An application workspace cannot use a source or research profile.")
        if existing_chrome and (not application_workspace or headless):
            raise BrowserError("Existing Chrome requires a visible application workspace.")
        if type(auto_approve_chrome) is not bool or (auto_approve_chrome and not existing_chrome):
            raise BrowserError("Chrome approval requires an explicitly authorized existing-Chrome workspace.")
        self.data_root = Path(data_root).resolve()
        self.options = {"dataRoot": str(self.data_root), "headless": headless,
                        "allowLocal": allow_local, "source": source, "applicationSource": application_source, "research": research,
                        "applicationWorkspace": application_workspace,
                        "existingChrome": existing_chrome,
                        "autoApproveChrome": auto_approve_chrome,
                        "uploadRoots": [str(Path(p).resolve()) for p in (upload_roots or [data_root])]}
        self.process = None
        self.messages = queue.Queue()
        self.lock = threading.RLock()
        self.write_lock = threading.Lock()
        self.interrupt_generation = 0
        self.ended = False
        self.connection_ready = False

    # The bundled browser runtime is reused; no dependency installation occurs here.
    def _start(self):
        runtime = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node"
        node = os.environ.get("APPLYDESK_NODE") or str(runtime / "bin/node")
        if not Path(node).is_file():
            node = shutil.which("node")
        if not node:
            raise BrowserError("Node.js is unavailable. Install it or set APPLYDESK_NODE.")
        self.data_root.mkdir(parents=True, exist_ok=True)
        environment = dict(os.environ)
        environment["APPLYDESK_BROWSER_OPTIONS"] = json.dumps(self.options)
        if (runtime / "node_modules/playwright").is_dir():
            environment.setdefault("APPLYDESK_NODE_MODULES", str(runtime / "node_modules"))
        worker = PROGRAM_ROOT / "browser/worker.cjs"
        self.process = subprocess.Popen([node, str(worker)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=True, bufsize=1, env=environment)
        if self.ended:
            self.process.terminate()
        threading.Thread(target=self._read, daemon=True).start()

    # A reader thread keeps malformed browser output from blocking the HTTP server forever.
    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    self.messages.put(json.loads(line))
                except json.JSONDecodeError:
                    self.messages.put({"error": "Invalid response from browser worker."})
        finally:
            self.messages.put({"error": "Browser closed or could not start. Check Chrome and Playwright availability."})

    # Every browser request is serialized and paired with its own response identifier.
    def call(self, method, params=None, *, expected_generation=None):
        methods = {"source_login", "source_status", "source_scan", "source_job", "source_storage", "close"} if self.options["source"] else {"open", "snapshot", "read_choices", "fill", "next", "authenticate", "application_entry", "submit", "focus", "show", "close"}
        if self.options["applicationWorkspace"]:
            methods |= {"import_source", "pause", "close_job", "verify_application", "agent_observe",
                        "agent_action", "agent_authenticate", "agent_submit"}
        if self.options["research"]:
            methods = {"open", "snapshot", "close"}
        if method not in methods:
            raise BrowserError("Unsupported browser method.")
        generation = self.interrupt_generation if expected_generation is None else expected_generation
        with self.lock:
            if self.ended:
                raise BrowserError("Browser session ended. Create a new session before resuming automation.")
            if self.process is None:
                self._start()
            request_id = uuid.uuid4().hex
            request = {"id": request_id, "method": method, "params": params or {}}
            try:
                self._send(request, generation)
                response = self.messages.get(timeout=self._response_timeout(method, request["params"]))
            except (BrokenPipeError, OSError, queue.Empty) as error:
                self.terminate()
                raise BrowserError("Browser did not respond. Reopen the application browser.") from error
            if response.get("error"):
                if self.process.poll() is not None:
                    self.ended = True
                raise BrowserError(response["error"])
            if response.get("id") != request_id:
                self.terminate()
                raise BrowserError("Browser response mismatch; session stopped for safety.")
            if method == "close":
                self.ended = True
            if method == "open":
                self.connection_ready = True
            return response["result"]

    def _response_timeout(self, method, params):
        """Allow visible field pacing on long forms while keeping waits bounded and interruptible."""
        if method == "source_scan":
            return 180
        if method == "open" and self.options["existingChrome"] and not self.connection_ready:
            return 245  # Chrome approval can take three minutes before normal navigation begins.
        if not self.options["applicationWorkspace"] or method != "fill":
            return 65
        operations = sum(len(params.get(key, [])) for key in ("answers", "uploads"))
        return min(900, 65 + operations * 2)

    def _send(self, request, generation):
        """Keep JSON lines intact while rejecting writes queued before an interruption."""
        with self.write_lock:
            if generation != self.interrupt_generation:
                raise BrowserError("Browser action interrupted before it could start.")
            self.process.stdin.write(json.dumps(request) + "\n")
            self.process.stdin.flush()

    # Process shutdown preserves local browser state while preventing orphan automation.
    def terminate(self):
        with self.lock:
            self.ended = True
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()

    # Cancellation must interrupt an active source scan rather than wait for its IPC lock.
    def cancel(self):
        self.ended = True
        process = self.process
        if process and process.poll() is None:
            process.terminate()
            def finish_cancel():
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
            threading.Thread(target=finish_cancel, daemon=True).start()

    def interrupt(self):
        """Freeze workspace commands immediately while retaining Chrome and its network guards."""
        if not self.options["applicationWorkspace"]:
            return self.cancel()
        with self.write_lock:
            self.interrupt_generation += 1
            process = self.process
            if process and process.poll() is None:
                try:
                    process.stdin.write(json.dumps({"method": "interrupt"}) + "\n")
                    process.stdin.flush()
                except (BrokenPipeError, OSError):
                    self.ended = True

    # Graceful close is used by app shutdown; failed workers still receive termination.
    def close(self):
        if self.process and self.process.poll() is None:
            try:
                self.call("close")
            except BrowserError:
                self.terminate()
