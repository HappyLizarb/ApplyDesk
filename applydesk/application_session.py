"""Cancellation checks surround every application browser operation, including slow page loads."""

import queue
import threading

from .browser_bridge import BrowserSession


class ApplicationSession:
    """Bind one job to the shared browser while checking every application operation."""

    def __init__(self, engine, root, *, job_id=None, **options):
        self.engine = engine
        self.agent_driven = options.pop("agent_driven", False)
        self.job_id, self.application_source = job_id, options.get("application_source")
        manager = getattr(getattr(engine, "w", None), "application_browser", None)
        research = options.pop("research", False) and getattr(manager, "native", False)
        self.manager = manager if job_id or research else None
        if research:
            self.job_id = manager.register_research()
            engine.w.sessions[self.job_id] = self
        self.research = research
        self.owned_browser = None if self.manager else BrowserSession(root, **options)

    @property
    def browser(self):
        """Cancellation and status always target the manager's current browser generation."""
        return self.manager.browser if self.manager else self.owned_browser

    def call(self, method, params=None):
        """A late browser result cannot continue a cancelled application workflow."""
        if method == "open" and self.agent_driven:
            params = {**(params or {}), "agent_driven": True}
        if method == "show" and self.manager:
            return self.manager.call(self.job_id, method, params)
        self.engine.check_active()
        results = queue.Queue()
        def execute():
            try:
                result = (self.manager.call(self.job_id, method, params, self.application_source, self.engine.check_active)
                          if self.manager else self.browser.call(method, params))
                results.put((True, result))
            except Exception as exc:
                results.put((False, exc))
        threading.Thread(target=execute, daemon=True).start()
        while True:
            try:
                self.engine.check_active()
            except InterruptedError:
                target = self.manager or self.browser
                if target:
                    target.interrupt()
                raise
            try:
                success, result = results.get(timeout=.1)
            except queue.Empty:
                continue
            self.engine.check_active()
            if not success:
                raise result
            return result

    def park(self):
        """Keep the last form visible after completion or a job-specific blocker."""
        if self.manager:
            self.manager.park(self.job_id)
        elif self.browser:
            self.browser.interrupt()

    def cancel(self):
        """Explicit Stop revokes browser writes without waiting for an in-flight call."""
        target = self.manager or self.browser
        if target:
            target.interrupt()

    def close(self):
        """Retain the reusable research tab; deletion closes only an explicitly selected job tab."""
        if self.research:
            self.park()
        elif self.manager:
            self.manager.close_job(self.job_id)
        else:
            self.browser.close()

