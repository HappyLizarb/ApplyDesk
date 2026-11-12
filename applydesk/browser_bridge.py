"""Guarded, serialized communication with the dedicated Playwright process."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import uuid


class BrowserError(RuntimeError):
    """A rejected action or unavailable browser session."""


class BrowserSession:
    """Own one persistent browser profile without exposing arbitrary browser tools."""

    def __init__(self, data_root, *, headless=False, allow_local=False, upload_roots=None, source=None, application_source=None):
        if source not in {None, "linkedin", "handshake"}:
            raise BrowserError("Unknown recommendation source.")
        if application_source not in {None, "linkedin", "handshake"} or (source and application_source):
            raise BrowserError("Choose a separate recommendation or application session.")
        self.data_root = Path(data_root).resolve()
        self.options = {"dataRoot": str(self.data_root), "headless": headless,
                        "allowLocal": allow_local, "source": source, "applicationSource": application_source,
                        "uploadRoots": [str(Path(p).resolve()) for p in (upload_roots or [data_root])]}
        self.process = None
        self.messages = queue.Queue()
        self.lock = threading.RLock()
        self.ended = False

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
        worker = Path(__file__).resolve().parent.parent / "browser/worker.cjs"
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
    def call(self, method, params=None):
        methods = {"source_login", "source_status", "source_scan", "source_job", "close"} if self.options["source"] else {"open", "snapshot", "fill", "next", "authenticate", "application_entry", "submit", "focus", "close"}
        if method not in methods:
            raise BrowserError("Unsupported browser method.")
        with self.lock:
            if self.ended:
                raise BrowserError("Browser session ended. Create a new session before resuming automation.")
            if self.process is None:
                self._start()
            request_id = uuid.uuid4().hex
            request = {"id": request_id, "method": method, "params": params or {}}
            try:
                self.process.stdin.write(json.dumps(request) + "\n")
                self.process.stdin.flush()
                response = self.messages.get(timeout=180 if method == "source_scan" else 65)
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
            return response["result"]

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

    # Graceful close is used by app shutdown; failed workers still receive termination.
    def close(self):
        if self.process and self.process.poll() is None:
            try:
                self.call("close")
            except BrowserError:
                self.terminate()
