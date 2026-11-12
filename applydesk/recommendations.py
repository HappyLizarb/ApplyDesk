"""Local recommendation imports with independent sign-in waits and cancellable writes."""

import threading
import time
import uuid

from .browser_bridge import BrowserSession
from .recommendation_source import PLATFORMS, employer_url, known_identity_matches, normalize_job, source_identity, validate_saved
from .storage import now

ACTIVE = {"connecting", "waiting_signin", "scanning"}

class Recommendations:
    """Own source-specific workers without reserving the workspace's foreground task."""

    def __init__(self, workspace):
        self.w, self.active, self.closed = workspace, {}, False
        for job in self.w.store.all("job"):
            source = job.get("recommendation_source")
            if job["status"] == "new" and isinstance(source, dict) and source.get("complete") is False:
                self.w.store.job(job["id"], status="needs_review")
        for platform in PLATFORMS:
            report = self.w.store.get("recommendation", platform, {})
            if report.get("status") in ACTIVE:
                report.update(status="interrupted", message="The previous import stopped. Connect again to continue.", finished_at=now())
                self.w.store.put("recommendation", platform, report)

    def state(self):
        """Expose only persisted operation summaries, never browser processes or cookies."""
        return {platform: self.w.store.get("recommendation", platform, {}) for platform in sorted(PLATFORMS)}

    def preferences(self):
        """Source selection is explicit and never inferred from past imports or profile facts."""
        preferences = {}
        for platform in sorted(PLATFORMS):
            saved = self.w.store.get("recommendation_preference", platform, {})
            preferences[platform] = {"enabled": isinstance(saved, dict) and saved.get("enabled") is True}
        return preferences

    def preference(self, data):
        """Disabling a source stops its exact active generation without erasing sign-in cookies."""
        if not isinstance(data, dict):
            raise ValueError("Supply a recommendation source and an enabled boolean.")
        platform, enabled = data.get("platform"), data.get("enabled")
        if not isinstance(platform, str) or platform not in PLATFORMS or type(enabled) is not bool:
            raise ValueError("Choose a supported source and an enabled boolean.")
        with self.w.lock:
            if self.closed:
                raise ValueError("The workspace is closing.")
            self.w.store.put("recommendation_preference", platform, {"enabled": enabled})
            run = self.active.get(platform) if not enabled else None
            if run:
                self._mark_cancelled(platform, run, "Connection disabled. Import cancelled.")
        if run:
            run["session"].cancel()
        return {"platform": platform, "enabled": enabled}

    def begin(self, data, request=None):
        """Return immediately while the user signs in within a persistent source profile."""
        if not isinstance(data, dict):
            raise ValueError("Supply a recommendation source and limit.")
        platform, limit = data.get("platform"), data.get("limit", 5)
        connect_only = data.get("connect_only", False)
        if type(connect_only) is not bool:
            raise ValueError("Choose whether to connect or search.")
        if not isinstance(platform, str) or platform not in PLATFORMS or type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("Choose a supported source and a limit from 1 to 10.")
        with self.w.lock:
            if self.closed or platform in self.active:
                raise ValueError("This source is already active or the workspace is closing.")
            run = {"run_id": uuid.uuid4().hex, "event": threading.Event(), "request": request, "connect_only": connect_only, "session": BrowserSession(self.w.root / "sources" / platform, source=platform)}
            run["thread"] = threading.Thread(target=self._run, args=(platform, run, limit), daemon=True)
            self.active[platform] = run
            self.w.store.put("recommendation", platform, {"platform": platform, "run_id": run["run_id"], "status": "connecting", "connected": False,
                             "message": "Opening the source browser…", "limit": limit, "added_count": 0,
                             "connect_only": connect_only, "request_id": request.get("message_id") if request else None,
                             "added_job_ids": [], "job_ids": [], "excluded_count": 0, "existing_count": 0,
                             "updated_count": 0, "issues": [], "started_at": now()})
            run["thread"].start()
            self.w.store.put("recommendation_preference", platform, {"enabled": True})
        return {"platform": platform, "run_id": run["run_id"]}

    def _publish(self, platform, run, **changes):
        """Generation and cancellation checks prevent stale workers from publishing state."""
        with self.w.lock:
            if self.closed or run["event"].is_set() or self.active.get(platform) is not run:
                return False
            report = self.w.store.get("recommendation", platform)
            self.w.store.put("recommendation", platform, {**report, **changes})
            return True

    def _wait_ready(self, platform, run):
        """Observe sign-in for at most ten minutes without blocking another workspace action."""
        deadline = time.monotonic() + 600
        result = run["session"].call("source_login")
        while result.get("status") == "waiting_signin" and time.monotonic() < deadline:
            message = result.get("message")
            self._publish(platform, run, status="waiting_signin", message=message[:600] if isinstance(message, str) and message.strip() else "Sign in in the source browser to connect.")
            if run["event"].wait(2):
                return {"status": "cancelled"}
            result = run["session"].call("source_status")
        if result.get("source") != platform or result.get("status") not in {"ready", "blocked"}:
            return {"source": platform, "status": "blocked", "jobs": [], "message": "Sign-in was not completed. Connect again.",
                    "issues": ["Sign-in was not completed or the source could not be read. Connect again."]}
        return result

    def _run(self, platform, run, limit):
        """Scan once after sign-in; cancelled operations can never commit later job rows."""
        try:
            result = self._wait_ready(platform, run)
            if not self._publish(platform, run, status="scanning", connected=result.get("status") == "ready", message="Reading recommended jobs…"):
                return
            if result.get("status") == "ready" and run["connect_only"]:
                self._publish(platform, run, status="complete", message="Connected.", finished_at=now())
                return
            if result.get("status") == "ready":
                result = run["session"].call("source_scan", {"limit": limit})
            if result.get("source") != platform or result.get("status") not in {"ready", "blocked"}:
                self._publish(platform, run, status="blocked", message="The source could not be read. Open it and try again.", issues=["The source did not confirm readable recommendations."], finished_at=now())
                return
            jobs = result.get("jobs")
            if not isinstance(jobs, list) or len(jobs) > limit:
                raise ValueError("The source returned an invalid or excessive job list.")
            records, issues = self._records(platform, jobs, result["status"] == "blocked")
            reported = result.get("issues", [])
            issues.extend([item[:600] for item in reported if isinstance(item, str) and item.strip()][:20] if isinstance(reported, list) else ["The source report details were invalid."])
            self._stage(platform, run, records)
            status = "blocked" if result["status"] == "blocked" or issues else "complete" if records else "no_results"
            message = "Some recommendations could not be read." if status == "blocked" else "Recommendations ready to check against your request." if records else "No recommendations were found."
            if status == "blocked" and isinstance(result.get("message"), str) and result["message"].strip():
                message = result["message"][:600]
            self._publish(platform, run, status=status, message=message, issues=list(dict.fromkeys(issues))[:20], finished_at=now())
        except Exception as error:
            self._publish(platform, run, status="error", message=str(error)[:600], issues=[str(error)[:600]], finished_at=now())
        finally:
            self._release(platform, run)

    def _records(self, platform, jobs, blocked):
        """Keep valid captures when another record fails, recovering only observed valid identities."""
        records, issues = [], []
        for item in jobs:
            try:
                source = normalize_job(platform, item)
            except (TypeError, ValueError):
                issue = "A recommendation could not be fully verified; its observed link requires review."
                issues.append(issue)
                if not isinstance(item, dict):
                    continue
                try:
                    source = normalize_job(platform, {"id": item.get("id"), "url": item.get("url"), "complete": False, "issues": [issue]})
                except (TypeError, ValueError):
                    continue
            if blocked:
                source["complete"] = False
                source["issues"] = list(dict.fromkeys([*source["issues"], "The source reader was blocked; this listing requires review."]))
            if not source["complete"]:
                detail = "; ".join(source["issues"] or ["The complete description was not verified."])
                issues.append(("Job " + source["job_id"] + ": " + detail)[:600])
            records.append(source)
        return records, list(dict.fromkeys(issues))[:20]

    def _stage(self, platform, run, records):
        """Capture listings separately; no tracker write occurs before relevance selection."""
        with self.w.lock:
            if self.closed or run["event"].is_set() or self.active.get(platform) is not run:
                return
            complete = [source for source in records if source["complete"] is True]
            self.w.store.put("recommendation_candidates", platform, {"run_id": run["run_id"],
                             "request": run["request"], "records": complete})
            self._publish(platform, run, candidate_count=len(complete))

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

    def read_job(self, source):
        """Read one fresh listing; changed descriptions require the caller to rerun every audit."""
        url = validate_saved(source)
        platform = source["platform"]
        with self.w.lock:
            if self.closed or platform in self.active:
                raise ValueError("Finish or cancel this source's active import before preparing a job.")
            run = {"run_id": uuid.uuid4().hex, "event": threading.Event(), "thread": threading.current_thread(),
                   "session": BrowserSession(self.w.root / "sources" / platform, source=platform)}
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
            with self.w.lock:
                if self.closed or run["event"].is_set():
                    raise ValueError("The recommendation read was cancelled.")
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
