"""Read saved recommendation sources and preserve their historical reports."""

import threading
import uuid

from .browser_bridge import BrowserSession
from .recommendation_source import PLATFORMS, known_identity_matches, normalize_job, validate_saved
from .recommendation_checks import retain_employer_link
from .storage import now

ACTIVE = {"connecting", "waiting_signin", "scanning"}

class Recommendations:
    """Track source reads so pause and shutdown can interrupt their browser sessions."""

    def __init__(self, workspace):
        self.w, self.active, self.closed = workspace, {}, False
        for job in self.w.store.all("job"):
            source = job.get("recommendation_source")
            if job["status"] == "new" and isinstance(source, dict) and source.get("complete") is False:
                self.w.store.job(job["id"], status="needs_review")
        for platform in PLATFORMS:
            report = self.w.store.get("recommendation", platform, {})
            if report.get("status") in ACTIVE:
                report.update(status="interrupted", message="The previous import stopped. Use public job search to find new listings.", finished_at=now())
                self.w.store.put("recommendation", platform, report)

    def state(self):
        """Display historic blocked captures truthfully without changing the saved operation records."""
        reports = {}
        for platform in sorted(PLATFORMS):
            report = dict(self.w.store.get("recommendation", platform, {}))
            if report.get("status") == "blocked" and report.get("message") == "Found 0 matching job(s).":
                issues = report.get("issues", [])
                issues = issues if isinstance(issues, list) else []
                report["message"] = next((item.strip() for item in issues if isinstance(item, str) and item.strip()),
                                         "The listings could not be read. Try the search again.")
            reports[platform] = report
        return reports

    def preferences(self):
        """Source selection is explicit and never inferred from past imports or profile facts."""
        preferences = {}
        for platform in sorted(PLATFORMS):
            saved = self.w.store.get("recommendation_preference", platform, {})
            preferences[platform] = {"enabled": isinstance(saved, dict) and saved.get("enabled") is True}
        return preferences

    def cancel(self, data):
        """Cancel only the requested generation, retaining the source's local sign-in cookies."""
        if not isinstance(data, dict):
            raise ValueError("Supply the active recommendation operation.")
        platform = data.get("platform")
        with self.w.lock:
            run = self.active.get(platform) if isinstance(platform, str) else None
            report = self.w.store.get("recommendation", platform, {}) if run else {}
            if not run or run["run_id"] != data.get("run_id") or report.get("run_id") != run["run_id"] or report.get("status") not in ACTIVE:
                raise ValueError("This recommendation operation is no longer active.")
            self._mark_cancelled(platform, run, "Import cancelled.")
        run["session"].cancel()
        return {"platform": platform, "run_id": run["run_id"], "status": "cancelled"}

    def cancel_all(self, message):
        """Pause current imports without disconnecting platforms or discarding imported jobs."""
        with self.w.lock:
            runs = list(self.active.items())
            for platform, run in runs:
                self._mark_cancelled(platform, run, message)
        for _, run in runs:
            run["session"].cancel()

    def _mark_cancelled(self, platform, run, message):
        """Call under the workspace lock so no late result can overwrite cancellation."""
        run["event"].set()
        report = self.w.store.get("recommendation", platform, {})
        if report.get("run_id") == run["run_id"] and report.get("status") in ACTIVE:
            self.w.store.put("recommendation", platform, {**report, "status": "cancelled", "message": message, "finished_at": now()})

    def read_job(self, source, *, application=False):
        """Read one fresh listing; changed descriptions require the caller to rerun every audit."""
        url = validate_saved(source)
        platform = source["platform"]
        with self.w.lock:
            if self.closed or platform in self.active:
                raise ValueError("Finish or cancel this source's active import before preparing a job.")
            run = {"run_id": uuid.uuid4().hex, "event": threading.Event(), "thread": threading.current_thread(),
                   "session": BrowserSession(self.w.root / "sources" / platform, source=platform, headless=application)}
            self.active[platform] = run
        try:
            result = run["session"].call("source_job", {"url": url, "id": source["job_id"]})
            if result.get("source") != platform or result.get("status") != "ready" or run["event"].is_set():
                raise ValueError("The saved source job could not be verified. Reconnect the source.")
            fresh = normalize_job(platform, result.get("job"))
            if fresh["job_id"] != source["job_id"] or not fresh["complete"]:
                raise ValueError("The fresh source job is different or incomplete. Review the listing.")
            if not known_identity_matches(source, fresh):
                raise ValueError("The listing company or title changed. Review its identity before continuing.")
            fresh = retain_employer_link(source, fresh)
            storage = run["session"].call("source_storage") if application else None
            with self.w.lock:
                if self.closed or run["event"].is_set():
                    raise ValueError("The recommendation read was cancelled.")
                if application:
                    self.w.application_browser.remember_source(platform, storage)
                return fresh
        finally:
            self._release(platform, run)

    def _release(self, platform, run):
        """Release the platform even if the browser process has already failed."""
        try:
            run["session"].close()
        finally:
            with self.w.lock:
                if self.active.get(platform) is run:
                    self.active.pop(platform)

    def close(self):
        """Stop all watchers before Workspace closes SQLite; cancelled generations cannot write."""
        with self.w.lock:
            self.closed = True
            runs = list(self.active.items())
            for platform, run in runs:
                run["event"].set()
                report = self.w.store.get("recommendation", platform, {})
                if report.get("status") in ACTIVE:
                    self.w.store.put("recommendation", platform, {**report, "status": "interrupted", "message": "Import stopped when the app closed.", "finished_at": now()})
        for _, run in runs:
            try:
                run["session"].cancel()
            except OSError:
                pass
        for _, run in runs:
            if run["thread"] is not threading.current_thread():
                run["thread"].join(timeout=6)
