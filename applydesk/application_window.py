"""Expose the current application window without opening another browser or taking control."""


def active_browser(workspace):
    """Only the active run's existing live browser can be brought to the foreground."""
    run = workspace.orchestrator.applications.run or {}
    session = workspace.sessions.get(run.get("current_job"))
    browser = getattr(session, "browser", session)
    process = getattr(browser, "process", None)
    if (run.get("status") != "running" or getattr(browser, "ended", True)
            or process is None or process.poll() is not None or browser.options.get("headless")):
        raise ValueError("There is no application window open yet. It appears when the agent opens an application.")
    return run["current_job"], session


def status(workspace):
    """Expose availability and job identity without revealing browser internals or credentials."""
    try:
        job_id, _ = active_browser(workspace)
    except ValueError:
        return {"available": False, "job_id": None}
    return {"available": True, "job_id": job_id}


def show_application(workspace):
    """Queue a presentation-only action so the HTTP lock cannot delay Stop during navigation."""
    _, session = active_browser(workspace)
    task = workspace.tasks.get("show-application", {})
    if task.get("status") == "running":
        return {"task": "show-application"}
    return workspace.start("show-application", session.call, "show")
