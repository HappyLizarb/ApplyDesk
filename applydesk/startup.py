"""Prevent concurrent app processes from editing one local document manifest."""

import fcntl
import os
import subprocess
import time
from pathlib import Path


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


def focus_window(root):
    """Ask macOS to activate only the browser using this workspace's interface profile."""
    launcher = Path(__file__).resolve().parent.parent / "ApplyDesk.app/Contents/MacOS/ApplyDesk"
    launcher = os.environ.get("APPLYDESK_LAUNCHER", str(launcher))
    result = subprocess.run([str(launcher), "--focus-interface", str(root / "interface")],
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
