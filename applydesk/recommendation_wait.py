"""Return request-matched recommendations without leaving the master with a tracking ID."""

import time

from .recommendations import ACTIVE
from .recommendation_selection import finalize_search, request_context


def find_jobs(engine, platform):
    """Join the current request's scan or start once; unrelated scans cannot add tracker rows."""
    workspace, context = engine.w, request_context(engine)
    with workspace.lock:
        engine.check_active()
        if not workspace.recommendations.preferences().get(platform, {}).get("enabled"):
            raise ValueError("Connect that job platform in Profile first.")
        report = workspace.recommendations.state().get(platform, {})
        if report.get("status") in ACTIVE:
            if not report.get("connect_only") and report.get("request_id") != context["message_id"]:
                raise ValueError("Another search is running. Pause it before starting a different request.")
        elif report.get("request_id") != context["message_id"] or report.get("connect_only"):
            report = workspace.recommendations.begin({"platform": platform, "limit": 10}, request=context)
        run_id = report["run_id"]
    return wait_for_jobs(engine, platform, run_id)


def search_result(engine, platform, report):
    """Only filtered tracker rows are returned; pending sign-in never triggers model screening."""
    pending = report.get("status") in ACTIVE or platform in engine.w.recommendations.active
    if not pending and report.get("connect_only") and report.get("connected"):
        return find_jobs(engine, platform)
    if not pending and report.get("status") in {"complete", "no_results", "blocked"} and "candidate_count" in report and report.get("request_id"):
        report = finalize_search(engine, platform, report["run_id"])
    jobs = [engine.w.store.get("job", identity) for identity in report.get("job_ids", [])] if report.get("screened") else []
    jobs = [{key: job.get(key) for key in ("id", "company", "title", "url", "location", "status")}
            for job in jobs if job and not job.get("removed_at")]
    return {**report, "jobs": jobs, "pending": pending}


def wait_for_jobs(engine, platform, run_id, timeout=190):
    """Wait outside the workspace lock; Pause, account switches and reset interrupt it."""
    deadline = time.monotonic() + timeout
    while True:
        engine.check_active()
        report = engine.w.recommendations.state().get(platform, {})
        if report.get("run_id") != run_id:
            raise ValueError("This recommendation run was replaced. Read its current status.")
        released = platform not in engine.w.recommendations.active
        if (report.get("status") not in ACTIVE and released) or report.get("status") == "waiting_signin" or time.monotonic() >= deadline:
            return search_result(engine, platform, report)
        engine.cancelled.wait(.25)
