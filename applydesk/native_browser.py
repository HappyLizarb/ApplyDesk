"""Bounded JSONL transport to ApplyDesk's native Accessibility browser helper."""

import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import uuid

from .lifecycle import CHROME, launcher_path

METHODS = {"status", "use_existing", "attach", "observe", "action", "show", "stop", "close"}
MAX_RESPONSE = 32 * 1024 * 1024
OBSERVATION_TIMEOUT = 60


class BrowserError(RuntimeError):
    """A rejected action or unavailable browser session."""


def choose_resume():
    """Return only the path chosen in ApplyDesk's native panel, or None on cancellation."""
    try:
        result = subprocess.run([str(launcher_path()), "--choose-resume"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        raise BrowserError("The file picker could not finish. Reopen Documents and choose your resume again.") from None
    if result.returncode == 0 and not result.stdout:
        return None
    path = Path(os.fsdecode(result.stdout))
    if result.returncode or not path.is_absolute() or "\x00" in str(path):
        raise BrowserError("The file picker could not confirm the selected resume.")
    return path


class NativeBrowser:
    """Own only a native helper process; Chrome remains an ordinary user browser."""

    def __init__(self, launcher=None, redactions=None):
        self.launcher = launcher
        self.redactions = redactions if redactions is not None else set()
        self.process, self.ended, self.paused = None, False, False
        self.options = {"headless": False, "native": True}
        self.interrupt_generation = 0
        self.lock, self.write_lock, self.pending_lock = threading.RLock(), threading.Lock(), threading.Lock()
        self.pending, self.ignored = {}, set()
        self.reader = None

    def _start(self):
        """Use the installed launcher or an explicit development artifact, never Chrome debug flags."""
        executable = self.launcher or launcher_path()
        if not Path(executable).is_file():
            raise BrowserError("The native browser helper is unavailable. Open the installed ApplyDesk app.")
        self.process = subprocess.Popen([str(executable), "--native-browser"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def call(self, method, params=None, *, expected_generation=None):
        """Serialize regular commands while Stop can invalidate queued and in-flight input immediately."""
        if method not in METHODS or (params is not None and not isinstance(params, dict)):
            raise BrowserError("Unsupported native browser operation.")
        generation = self.interrupt_generation if expected_generation is None else expected_generation
        parameters = dict(params or {})
        with self.lock:
            if parameters.get("private") and isinstance(parameters.get("text"), str) and parameters["text"]:
                self.redactions.add(parameters["text"])
            if self.ended or (self.paused and method == "action"):
                raise BrowserError("Native browser input is stopped. Open the application again to continue.")
            if self.process is None:
                self._start()
            identity, responses = uuid.uuid4().hex, queue.Queue()
            with self.pending_lock:
                self.pending[identity] = responses
            try:
                self._send({"id": identity, "method": method, "params": parameters}, generation)
                reply = responses.get(timeout=command_timeout(method, parameters))
                return self._result(reply, method, parameters, generation)
            except (OSError, queue.Empty):
                self.terminate()
                raise BrowserError("The native browser did not respond. Reopen its application window.") from None
            finally:
                with self.pending_lock:
                    self.pending.pop(identity, None)

    def open_public_url(self, url, generation):
        """Open a public link or acquire a blank window in the user's ordinary Chrome profile."""
        profile = Path.home() / "Library/Application Support/Google/Chrome"
        with self.write_lock:
            if (self.ended or self.interrupt_generation != generation or (self.paused and url != "about:blank")
                    or not self.process or self.process.poll() is not None):
                raise BrowserError("The application browser was interrupted before navigation.")
            child = subprocess.Popen([CHROME, "--user-data-dir=" + str(profile), url],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            if child.wait(timeout=5):
                error = BrowserError("Chrome could not open the requested page.")
                error.code = "navigation_dispatched"
                raise error
        except subprocess.TimeoutExpired:
            # The link may already be open. Observe its document before taking any further action.
            threading.Thread(target=child.wait, daemon=True).start()

    def _result(self, reply, method, parameters, generation):
        """Private values are redacted before any response or error reaches the application agent."""
        if generation != self.interrupt_generation and method not in {"show", "status", "stop", "close"}:
            raise BrowserError("Native browser operation interrupted.")
        if reply.get("error"):
            message = "The private browser action could not be completed." if parameters.get("private") else str(reply["error"])[:1000]
            error = BrowserError(self._redact(message))
            error.code = reply.get("error_code") if reply.get("error_code") in {"window_closed", "stale_observation", "unverified_file_selection"} else None
            # The installed helper uses these two acquisition errors before it has bound any window.
            if method == "use_existing" and reply["error"] in {
                "Open ordinary Chrome and enable Accessibility/input permission.",
                "Bring an existing regular Chrome window forward, then retry the application.",
            }:
                error.code = "window_unavailable"
            # The same window can leave the screen during acquisition, observation or input.
            if method in {"use_existing", "attach", "observe", "action"} and reply["error"] == "Make the owned Chrome window visible before continuing.":
                error.code = "window_offscreen"
            # The signed helper emits this exact error before posting either mouse event.
            if (method == "action" and parameters.get("action") == "click" and parameters.get("final_permit")
                    and reply["error"] == "The visible control moved or is covered. Observe again."):
                error.code = "click_obstructed_before_dispatch"
            raise error
        result = reply.get("result")
        if not isinstance(result, dict):
            raise BrowserError("The native browser returned an invalid response.")
        details = result.get("action_result", {})
        if isinstance(details, dict) and isinstance(details.get("private_value_matches"), bool):
            result["private_value_matches"] = details["private_value_matches"]
        if parameters.get("private") and parameters.get("action") == "type" and "private_value_matches" not in result:
            field = next((item for item in result.get("controls", []) if item.get("id") == parameters.get("element_id")), {})
            if field and not field.get("secure"):
                result["private_value_matches"] = field.get("value") == parameters.get("text")
        if method in {"use_existing", "attach"}:
            self.paused = False
        if method == "stop":
            self.paused = True
        if method == "close":
            self.ended = True
        return self._redact(result)

    def _send(self, request, generation):
        """A command queued before Stop can never reach the native event loop afterward."""
        with self.write_lock:
            if generation != self.interrupt_generation:
                raise BrowserError("Native browser operation interrupted before dispatch.")
            self.process.stdin.write(json.dumps(request) + "\n")
            self.process.stdin.flush()

    def _read(self):
        """Demultiplex Stop acknowledgements without losing the response to a pending action."""
        try:
            while True:
                line = self.process.stdout.readline(MAX_RESPONSE + 1)
                if not line:
                    break
                if len(line) > MAX_RESPONSE:
                    raise ValueError("Oversized native response")
                reply = json.loads(line)
                if not isinstance(reply, dict) or not isinstance(reply.get("id"), str):
                    raise ValueError("Malformed native response")
                with self.pending_lock:
                    if reply["id"] in self.ignored:
                        self.ignored.discard(reply["id"])
                        continue
                    destination = self.pending.get(reply["id"])
                if destination:
                    destination.put(reply)
        except (OSError, ValueError):
            self.terminate()
        finally:
            self.ended = True
            with self.pending_lock:
                for destination in self.pending.values():
                    destination.put({"error": "The native browser helper closed. Reopen the application window."})

    def interrupt(self):
        """Stop bypasses the normal command lock and leaves the owned Chrome window intact."""
        with self.write_lock:
            self.interrupt_generation += 1
            self.paused = True
            process = self.process
            if not process or process.poll() is not None:
                return
            identity = uuid.uuid4().hex
            with self.pending_lock:
                self.ignored.add(identity)
            try:
                process.stdin.write(json.dumps({"id": identity, "method": "stop", "params": {}}) + "\n")
                process.stdin.flush()
            except (OSError, ValueError):
                self.ended = True

    def _redact(self, value):
        """Remember private typed values in memory so later native observations cannot echo them."""
        if isinstance(value, str):
            for private in self.redactions:
                value = value.replace(private, "[redacted]")
            return value
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, dict):
            return {key: self._redact(item) for key, item in value.items()}
        return value

    def terminate(self):
        """Terminate only this helper process; never terminate the user's Chrome process."""
        self.ended = True
        process = self.process
        if process and process.poll() is None:
            process.terminate()

    def close(self):
        """Revoke input and close the helper while retaining the ordinary Chrome workspace."""
        self.interrupt()
        if self.process and self.process.poll() is None and not self.ended:
            try:
                self.call("close")
            except BrowserError:
                self.terminate()
        if self.process:
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
            if self.reader and self.reader is not threading.current_thread():
                self.reader.join(timeout=1)
            self.process.stdin.close()
            self.process.stdout.close()
        self.ended = True


def command_timeout(method, parameters):
    """Allow large native option lists to finish a read while Stop bypasses the response wait."""
    if method in {"observe", "attach", "use_existing"}:
        return OBSERVATION_TIMEOUT
    if method != "action":
        return 15
    text = parameters.get("text") if parameters.get("action") == "type" else None
    characters = min(len(text), 12000) if isinstance(text, str) else 0
    # The helper reads the full visible page both before posting input and after it settles.
    return 2 * OBSERVATION_TIMEOUT + characters * 0.15
