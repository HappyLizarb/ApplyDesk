"""Expose the current application window without opening another browser or taking control."""


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


def save_chrome_approval(workspace, data):
    """Persist explicit consent for this app's native Chrome connection prompt only."""
    if not isinstance(data, dict) or set(data) != {"enabled"} or type(data["enabled"]) is not bool:
        raise ValueError("Chrome connection approval needs an enabled boolean.")
    if workspace.orchestrator.active:
        raise ValueError("Stop the agent before changing Chrome connection approval.")
    workspace.store.put("settings", "main", {**workspace.settings(), "chrome_auto_approval": data["enabled"]})
    return {"enabled": data["enabled"]}
