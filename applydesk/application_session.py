"""Own application browser sessions and expose their visible window to the local UI."""

import queue
import threading


class ApplicationSession:
    """Bind one job to the shared browser while checking every application operation."""

    def __init__(self, engine, *, job_id=None, research=False):
        """Register a research tab or attach to the saved job in the shared native window."""
        self.engine = engine
        self.manager = engine.w.application_browser
        self.research = research
        self.job_id = self.manager.register_research() if research else job_id
        if not self.job_id:
            raise ValueError("An application session requires a saved job.")
        if research:
            engine.w.sessions[self.job_id] = self

    def call(self, method, params=None):
        """A late browser result cannot continue a cancelled application workflow."""
        if method == "show":
            return self.manager.call(self.job_id, method, params)
        self.engine.check_active()
        results = queue.Queue()
        def execute():
            try:
                result = self.manager.call(self.job_id, method, params, checkpoint=self.engine.check_active)
                results.put((True, result))
            except Exception as exc:
                results.put((False, exc))
        threading.Thread(target=execute, daemon=True).start()
        while True:
            try:
                self.engine.check_active()
            except InterruptedError:
                self.manager.interrupt()
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
        self.manager.park(self.job_id)

    def cancel(self):
        """Explicit Stop revokes browser writes without waiting for an in-flight call."""
        self.manager.interrupt()

    def close(self):
        """Retain the reusable research tab; deletion closes only an explicitly selected job tab."""
        if self.research:
            self.park()
        else:
            self.manager.close_job(self.job_id)


def active_browser(workspace):
    """Retained tabs stay inspectable after completion or Stop without restoring application authority."""
    manager = workspace.application_browser
    job_id = manager.last_job_id
    session = workspace.sessions.get(job_id)
    if not manager.live() or not job_id or not session:
        raise ValueError("There is no application window open yet. It appears when the agent opens an application.")
    return job_id, session


def status(workspace):
    """Expose availability and job identity without revealing browser internals or credentials."""
    try:
        job_id, _ = active_browser(workspace)
    except ValueError:
        return {"available": False, "job_id": None}
    return {"available": True, "job_id": job_id, "workspace_id": workspace.application_browser.workspace_id}


def show_application(workspace, bounds=None):
    """Queue a presentation-only action so the HTTP lock cannot delay Stop during navigation."""
    _, session = active_browser(workspace)
    task = workspace.tasks.get("show-application", {})
    if task.get("status") == "running":
        return {"task": "show-application"}
    return workspace.start("show-application", session.call, "show", {"bounds": bounds} if bounds else {})


def validate_bounds(bounds):
    """Only bounded integer window geometry is accepted; it never supplies URLs or browser actions."""
    if not isinstance(bounds, dict) or set(bounds) != {"left", "top", "width", "height"}:
        raise ValueError("Window bounds need left, top, width and height.")
    for key, value in bounds.items():
        low, high = (-32768, 32768) if key in {"left", "top"} else (320, 16384)
        if type(value) is not int or not low <= value <= high:
            raise ValueError("Window bounds must use valid integer positions and dimensions.")
    return bounds
