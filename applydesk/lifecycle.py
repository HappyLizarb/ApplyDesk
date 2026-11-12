"""Close only the Chrome instance owned by this ApplyDesk workspace."""

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
