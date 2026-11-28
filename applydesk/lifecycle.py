"""Manage the local service lock and the Chrome window owned by this workspace."""

import fcntl
import os
import re
import signal
import subprocess
import time
from pathlib import Path


CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def interface_process(root):
    """A private lock and exact browser/profile arguments must agree before signalling a process."""
    profile = Path(root).resolve() / "interface"
    lock = profile / "SingletonLock"
    if not lock.is_symlink():
        return None
    try:
        pid = int(os.readlink(lock).rsplit("-", 1)[1])
    except (OSError, ValueError):
        return None
    if pid <= 1:
        return None
    process = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=3)
    command = process.stdout.strip()
    argument = re.escape("--user-data-dir=" + str(profile)) + r"(?=\s--|$)"
    return pid if process.returncode == 0 and command.startswith(CHROME + " ") and re.search(argument, command) else None


def close_window(root):
    """Quit the private app window, allowing a short graceful exit before a verified fallback."""
    pid = interface_process(root)
    if pid is None:
        return
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(40):
            if interface_process(root) != pid:
                return
            time.sleep(.05)
        if interface_process(root) == pid:
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # The private window already exited between identity verification and signalling.


class LocalInstance:
    """An OS lock dies with the process, so a crash never leaves a permanent lockout."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.handle = (self.root / "session.lock").open("a+")
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.acquired = True
        except BlockingIOError:
            self.acquired = False

    def existing_url(self):
        """Use only the local launch URL saved by the running instance."""
        self.handle.seek(0)
        url = self.handle.read().strip()
        return url if url.startswith("http://127.0.0.1:") else None

    def publish(self, url):
        """Keep the temporary launch token in the private app data directory."""
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(url)
        self.handle.flush()

    def close(self):
        """Closing the file releases any owned advisory lock."""
        self.handle.close()


def launcher_path():
    """Find this installation's native executable or the configured development build."""
    if override := os.environ.get("APPLYDESK_LAUNCHER"):
        return Path(override)
    module = Path(__file__).resolve()
    bundle = next((parent for parent in module.parents if parent.suffix == ".app"), None)
    return (bundle or module.parent.parent / "ApplyDesk.app") / "Contents/MacOS/ApplyDesk"


def focus_window(root):
    """Ask macOS to activate only the browser using this workspace's interface profile."""
    result = subprocess.run([str(launcher_path()), "--focus-interface", str(root / "interface")],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    if result.returncode not in {0, 1}:
        raise RuntimeError("ApplyDesk could not focus its existing window. Select it from your open windows.")
    return result.returncode == 0


def open_window(url, root):
    """Serialize window launches and reuse an existing window without reloading its draft."""
    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not chrome.is_file():
        raise RuntimeError("Google Chrome is required for the application browser.")
    with (root / "window.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if focus_window(root):
            return
        # A slow first launch must not become multiple windows on repeated clicks.
        lock.seek(0)
        pending = lock.read().strip()
        if pending and time.time() - float(pending) < 30:
            return
        lock.seek(0)
        lock.truncate()
        lock.write(str(time.time()))
        lock.flush()
        subprocess.Popen([str(chrome), "--app=" + url, "--user-data-dir=" + str(root / "interface"),
                          "--no-first-run", "--disable-sync"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if focus_window(root):
                lock.seek(0)
                lock.truncate()
                return
            time.sleep(.1)
        raise RuntimeError("ApplyDesk's window has not appeared yet. Wait a moment before opening it again.")
