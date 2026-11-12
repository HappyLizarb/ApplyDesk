"""Cancellation checks surround every application browser operation, including slow page loads."""

import queue
import threading

from .browser_bridge import BrowserSession


class ApplicationSession:
    """A pause interrupts the owned browser worker instead of waiting for its next write."""

    def __init__(self, engine, root, **options):
        self.engine = engine
        self.browser = BrowserSession(root, **options)
        self.close = self.browser.close

    def call(self, method, params=None):
        """A late browser result cannot continue a cancelled application workflow."""
        self.engine.check_active()
        results = queue.Queue()
        def execute():
            try:
                results.put((True, self.browser.call(method, params)))
            except Exception as exc:
                results.put((False, exc))
        threading.Thread(target=execute, daemon=True).start()
        while True:
            try:
                self.engine.check_active()
            except InterruptedError:
                self.browser.cancel()
                raise
            try:
                success, result = results.get(timeout=.1)
            except queue.Empty:
                continue
            self.engine.check_active()
            if not success:
                raise result
            return result

