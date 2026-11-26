"""Unattended application runs owned by the local orchestrator, not the language model."""

import functools
import uuid

from .auto_start import usage_limited

from .application_client import ApplicationClient
from .application_intent import resolve_intent
from .application_policy import SCOPE_PROMPT, search_scope, validate_scope
from .application_session import ApplicationSession
from .application_submission import attempt_key, submit_ready
from .applications import Applications
from .chatgpt_transport import AccountError
from .storage import now


class ApplicationAutomation:
    """Chat requests authorize applications with an optional count; restarting never resumes them."""

    def __init__(self, engine):
        self.engine, self.w, self.run = engine, engine.w, None
        self.selected_ids = []
        for run in self.w.store.all("application_run"):
            if run["status"] == "running":
                run.update(status="interrupted", finished_at=now())
                self.w.store.put("application_run", run["id"], run)
        for attempt in self.w.store.all("submission_attempt"):
            if attempt["status"] == "submitting":
                job = self.w.store.get("job", attempt["job_id"])
                from .application_submission import finish_attempt
                finish_attempt(self.w, job, attempt, "submission_unknown", {})

    def start(self, job_ids=None):
        """Continue the user's agreed plan across follow-ups without resetting its budget."""
        self.engine.check_active()
        self.selected_ids = self._selection(job_ids)
        message = (self.engine.active or {}).get("message")
        if not message or message.get("role") != "user":
            raise ValueError("Application runs need a user conversation; automatic draft tasks cannot submit.")
        platforms = None
        prior = next((run for run in self.w.store.all("application_run")
                      if message["id"] in [run.get("message_id"), *run.get("message_ids", [])]), None)
        if prior:
            return self._resume_current(prior, platforms)
        intent = resolve_intent(self.engine)
        profile = self.w.profile()
        scope = search_scope(profile, intent["locations"])
        if not self.w.resumes.list():
            raise ValueError("Upload a finished resume in Documents before starting unattended applications.")
        used = sum(item.get("run_id") == intent["run_id"] for item in self.w.store.all("submission_attempt")) if intent["action"] == "continue" else 0
        if (intent["limit"] is None or used < intent["limit"]) and not self._candidates(platforms):
            return {"status": "needs_jobs", "message": "Search and save matching job listings, then continue this run.",
                    "run_id": intent["run_id"], "remaining_count": None if intent["limit"] is None else intent["limit"] - used}
        self.engine.check_active()
        self.run = self._begin(intent, message, profile, scope)
        self._save()
        if self.run["limit"] is not None and self.run["attempted_count"] >= self.run["limit"]:
            self.run.update(status="complete", finished_at=now())
            self._save()
            return self.run
        return self._execute(self._candidates(platforms))

    def _resume_current(self, run, platforms):
        """Selected batches share one grant and never repeat a result from this user message."""
        used = sum(item.get("run_id") == run["id"] for item in self.w.store.all("submission_attempt"))
        message_id = self.engine.active["message"]["id"]
        start = run.get("message_result_starts", {}).get(message_id, 0)
        processed = {item["job_id"] for item in run["results"][start:]}
        jobs = [job for job in self._candidates(platforms) if job["id"] not in processed]
        resuming = run["status"] == "interrupted" and ((run.get("transport_retry") is True and not (self.engine.active or {}).get("auto_restart")) or
                    (run.get("usage_limit_retry") is True and (self.engine.active or {}).get("auto_restart")))
        if not (run["status"] == "complete" or resuming) or (run["limit"] is not None and used >= run["limit"]) or not jobs:
            return run
        self.run = run
        self.check_active()
        self.run.update(status="running", current_job=None)
        self.run.pop("transport_retry", None)
        self.run.pop("finished_at", None)
        self._save()
        return self._execute(jobs)

    def _begin(self, intent, message, profile, scope):
        """Reuse the original ledger, refreshing only the plan explicitly resolved from chat."""
        run = self.w.store.get("application_run", intent["run_id"]) if intent["action"] == "continue" else None
        if intent["action"] == "continue" and not run:
            raise ValueError("The previous application run is unavailable. Review its status before continuing.")
        previous_hash = run.get("resume_sha256") if run and run.get("resume_id") == intent["resume_id"] else None
        record = self.w.resumes.selected(intent["resume_id"], previous_hash) if intent["resume_id"] else None
        run = run or {"id": uuid.uuid4().hex, "message_id": message["id"], "started_at": now(), "results": [], "searches": []}
        run.update(instruction=intent["instruction"], limit=intent["limit"], scope=scope, status="running",
                   authorization_message_id=intent["authorization_message_id"], conversation_id=self.engine.history.since,
                   profile_revision=profile["revision"], account_id=self.engine.accounts.state()["active"], current_job=None,
                   resume_id=record["id"] if record else "", resume_sha256=record["source_sha256"] if record else "")
        run.pop("usage_limit_retry", None)
        run.setdefault("message_result_starts", {})[message["id"]] = len(run["results"])
        run["message_ids"] = list(dict.fromkeys([*run.get("message_ids", []), message["id"]]))
        run.pop("finished_at", None)
        return run

    def _save(self, interrupted_job_id=None):
        """Report counts and any interrupted job's outcome from durable submission attempts."""
        attempts = [item for item in self.w.store.all("submission_attempt") if item["run_id"] == self.run["id"]]
        if interrupted_job_id:
            attempt = next((item for item in attempts if item["job_id"] == interrupted_job_id), {})
            self.run["interrupted_job"] = {"job_id": interrupted_job_id, "submission_status": attempt.get("status", "not_attempted"),
                                           "receipt_confirmed": (attempt.get("receipt") or {}).get("confirmed") is True}
        else:
            self.run.pop("interrupted_job", None)
        self.run.update(attempted_count=len(attempts), submitted_count=sum(item["status"] == "submitted" for item in attempts),
                        uncertain_count=sum(item["status"] in {"submitting", "submission_unknown"} for item in attempts))
        self.run["remaining_count"] = None if self.run["limit"] is None else max(0, self.run["limit"] - len(attempts))
        self.run["updated_at"] = now()
        self.w.store.put("application_run", self.run["id"], self.run)

    def check_active(self):
        """Changed profile facts, account identity, or Pause revoke the current run's authority."""
        self.engine.check_active()
        if self.w.profile()["revision"] != self.run["profile_revision"]:
            raise InterruptedError("Profile changed. Confirm the updated profile, then continue this run.")
        if self.engine.accounts.state()["active"] != self.run["account_id"]:
            raise InterruptedError("Account changed. Start a new application run to continue.")
        if self.run.get("resume_id"):
            self.w.resumes.selected(self.run["resume_id"], self.run["resume_sha256"])

    def _execute(self, jobs):
        """Process saved jobs in tracker order; the agent chooses where and when to search."""
        try:
            for job in jobs:
                self.check_active()
                used = sum(attempt.get("run_id") == self.run["id"] for attempt in self.w.store.all("submission_attempt"))
                if self.run["limit"] is not None and used >= self.run["limit"]:
                    break
                self._job(job)
            self.run.update(status="complete", current_job=None, finished_at=now())
            self.run.pop("usage_limit_retry", None)
        except BaseException as error:
            self.run["transport_retry"] = isinstance(error, AccountError) and error.retryable
            if usage_limited(error):
                self.run["usage_limit_retry"] = True
            elif not (self.engine.stop.is_set() and (self.engine.active or {}).get("auto_restart")):
                self.run.pop("usage_limit_retry", None)
            interrupted_job = self.run.get("current_job")
            self.run.update(status="interrupted", current_job=None, finished_at=now())
            self._save(interrupted_job)
            raise
        self._save()
        return self.run

    def _candidates(self, platforms=None):
        """Only complete captures are eligible; attempted or removed listings never reenter the queue."""
        candidates, identities = [], set()
        saved = self.w.store.all("job")
        ordered = [next(job for job in saved if job["id"] == identity) for identity in self.selected_ids] if self.selected_ids else saved
        for job in ordered:
            source = job.get("recommendation_source") or {}
            public = job.get("public_source") or {}
            eligible_source = ((platforms is None or source.get("platform") in platforms) and source.get("complete") is True
                               if source else public.get("complete") is True)
            identity = attempt_key(job)
            if (not eligible_source or job.get("removed_at") or job.get("manual_status") in {"submitted", "skipped"}
                    or job["status"] in {"submitted", "submitting", "submission_unknown"} or identity in identities
                    or self.w.store.get("submission_attempt", identity)):
                continue
            identities.add(identity)
            candidates.append(job)
        return candidates

    def _selection(self, job_ids):
        """A tool may prioritize existing jobs but cannot expand the user's submission allowance."""
        if job_ids is None:
            return []
        known = {job["id"] for job in self.w.store.all("job") if not job.get("removed_at")}
        if not isinstance(job_ids, list) or any(not isinstance(identity, str) or identity not in known for identity in job_ids):
            raise ValueError("Choose existing, non-removed job IDs from the workspace.")
        return list(dict.fromkeys(job_ids))

    def _job(self, job):
        """Keep blockers local to one job; a transport or cancellation failure cannot trigger retries."""
        self.run["current_job"] = job["id"]
        self._save()
        client = functools.partial(ApplicationClient, self.engine, job_id=job["id"])
        scope = functools.partial(self._scope, client)
        choice = {"id": self.run["resume_id"], "sha256": self.run["resume_sha256"]} if self.run.get("resume_id") else None
        application = Applications(self.w, client, self.check_active, scope, functools.partial(ApplicationSession, self.engine), resume_choice=choice, agent_driven=True)
        try:
            ready = application.prepare(job["id"])
            if ready["status"] != "ready":
                result = {"job_id": job["id"], "status": ready["status"], "issues": ready.get("exceptions", [])}
            else:
                result = self._send_reviewed(application, ready)
        except (InterruptedError, AccountError):
            raise
        except Exception as exc:
            if usage_limited(exc):
                raise
            current = self.w.store.get("job", job["id"])
            result = {"job_id": job["id"], "status": current["status"], "issues": [str(exc)[:1000]]}
            if current["status"] not in {"submitted", "submission_unknown"}:
                result["status"] = "needs_review"
                self.w.store.job(job["id"], status="needs_review", exceptions=result["issues"])
        finally:
            session = self.w.sessions.get(job["id"])
            if session and hasattr(session, "park"):
                session.park()
        self.run["results"].append(result)
        self.run["current_job"] = None
        self._save()

    def _send_reviewed(self, application, ready):
        """Let the same agent recheck a changed final page in place after a proven unsent click."""
        from .native_agent import NativeAgent
        result = submit_ready(self.engine, ready, self.run, self.check_active)
        if not result.get("click_not_sent"):
            return result
        self.check_active()
        job = self.w.store.get("job", ready["id"])
        profile = self.w.profile()
        if job.get("removed_at") or job.get("status") != "needs_review" or profile.get("revision") != ready.get("profile_revision"):
            return result
        self.w.store.event(job["id"], "Rechecking the changed review page before sending.")
        agent = NativeAgent(application, self.w.sessions[job["id"]], job, profile, job.get("analysis", {}))
        agent.history.append({"review_blocker": result["issues"][0]})
        agent.evidence = list(ready["native_evidence"])
        agent.uploads = {item["document_id"]: item for item in ready["attachments"]}
        reviewed = agent.run()
        return submit_ready(self.engine, reviewed, self.run, self.check_active)

    def _scope(self, client, analysis, page):
        """Check fresh scope; let the same agent repair malformed evidence once."""
        self.check_active()
        checked = [item["text"] for item in analysis["required"] if item.get("met") is True]
        payload = {"preferences": self.run["scope"], "request": self.run["instruction"],
                   "source_text": page["text"], "company": analysis["company"], "title": analysis["title"],
                   "verified_requirements": checked}
        if any(requirement["text"] not in page["text"] for requirement in analysis["required"]):
            raise ValueError("Each mandatory qualification must quote the captured job description exactly.")
        result = client("application").ask(SCOPE_PROMPT, payload)
        try:
            validate_scope(result, self.run["scope"], page["text"])
        except ValueError as error:
            if result.get("matches") is not True or "source evidence" not in str(error):
                raise
            repair = {**payload, "previous_result": result, "validation_error": str(error)}
            validate_scope(client("application").ask(SCOPE_PROMPT, repair), self.run["scope"], page["text"])
