"""Bounded unattended application runs owned by the local orchestrator, not the language model."""

import functools
import uuid

from .application_client import ApplicationClient
from .application_intent import resolve_intent
from .application_policy import SCOPE_PROMPT, search_scope, validate_scope
from .application_session import ApplicationSession
from .application_submission import attempt_key, submit_ready
from .applications import Applications
from .chatgpt_transport import AccountError
from .storage import now


class ApplicationAutomation:
    """Only explicit chat requests grant a bounded submission budget; restarting never resumes it."""

    def __init__(self, engine):
        self.engine, self.w, self.run = engine, engine.w, None
        for run in self.w.store.all("application_run"):
            if run["status"] == "running":
                run.update(status="interrupted", finished_at=now())
                self.w.store.put("application_run", run["id"], run)
        for attempt in self.w.store.all("submission_attempt"):
            if attempt["status"] == "submitting":
                job = self.w.store.get("job", attempt["job_id"])
                from .application_submission import finish_attempt
                finish_attempt(self.w, job, attempt, "submission_unknown", {})

    def start(self):
        """Continue the user's agreed plan across follow-ups without resetting its budget."""
        self.engine.check_active()
        message = (self.engine.active or {}).get("message")
        if not message or message.get("role") != "user":
            raise ValueError("Application runs need a user conversation; automatic draft tasks cannot submit.")
        platforms = [key for key, value in self.w.recommendations.preferences().items() if value["enabled"]]
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
        if used < intent["limit"] and not self._candidates(platforms):
            return {"status": "needs_jobs", "message": "Search and save matching job listings, then continue this run.",
                    "run_id": intent["run_id"], "remaining_count": intent["limit"] - used}
        self.engine.check_active()
        self.run = self._begin(intent, message, profile, scope)
        self._save()
        if self.run["attempted_count"] >= self.run["limit"]:
            self.run.update(status="complete", finished_at=now())
            self._save()
            return self.run
        return self._execute(self._candidates(platforms))

    def _resume_current(self, run, platforms):
        """Newly saved jobs share this turn's original grant; prior results are never retried."""
        used = sum(item.get("run_id") == run["id"] for item in self.w.store.all("submission_attempt"))
        processed = {item["job_id"] for item in run["results"]}
        jobs = [job for job in self._candidates(platforms) if job["id"] not in processed]
        if run["status"] != "complete" or used >= run["limit"] or not jobs:
            return run
        self.run = run
        self.check_active()
        self.run.update(status="running", current_job=None)
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
        run["message_ids"] = list(dict.fromkeys([*run.get("message_ids", []), message["id"]]))
        run.pop("finished_at", None)
        return run

    def _save(self):
        """Persist concise progress independently of the master's streamed explanation."""
        attempts = [item for item in self.w.store.all("submission_attempt") if item["run_id"] == self.run["id"]]
        self.run.update(attempted_count=len(attempts), submitted_count=sum(item["status"] == "submitted" for item in attempts),
                        uncertain_count=sum(item["status"] in {"submitting", "submission_unknown"} for item in attempts))
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
        """Process saved jobs in tracker order; the master chooses where and when to search."""
        try:
            for job in jobs:
                self.check_active()
                used = sum(attempt.get("run_id") == self.run["id"] for attempt in self.w.store.all("submission_attempt"))
                if used >= self.run["limit"]:
                    break
                self._job(job)
            self.run.update(status="complete", current_job=None, finished_at=now())
        except BaseException:
            self.run.update(status="interrupted", current_job=None, finished_at=now())
            raise
        finally:
            self._save()
        return self.run

    def _candidates(self, platforms):
        """Only complete captures are eligible; attempted or removed listings never reenter the queue."""
        candidates, identities = [], set()
        for job in self.w.store.all("job"):
            source = job.get("recommendation_source") or {}
            public = job.get("public_source") or {}
            eligible_source = (source.get("platform") in platforms and source.get("complete") is True
                               if source else public.get("complete") is True)
            identity = attempt_key(job)
            if (not eligible_source or job.get("removed_at")
                    or job["status"] in {"submitted", "submitting", "submission_unknown"} or identity in identities
                    or self.w.store.get("submission_attempt", identity)):
                continue
            identities.add(identity)
            candidates.append(job)
        return candidates

    def _job(self, job):
        """Keep blockers local to one job; a transport or cancellation failure cannot trigger retries."""
        self.run["current_job"] = job["id"]
        self._save()
        client = functools.partial(ApplicationClient, self.engine, job_id=job["id"])
        scope = functools.partial(self._scope, client)
        choice = {"id": self.run["resume_id"], "sha256": self.run["resume_sha256"]} if self.run.get("resume_id") else None
        application = Applications(self.w, client, self.check_active, scope, functools.partial(ApplicationSession, self.engine), resume_choice=choice)
        try:
            ready = application.prepare(job["id"])
            if ready["status"] != "ready":
                result = {"job_id": job["id"], "status": ready["status"], "issues": ready.get("exceptions", [])}
            else:
                result = submit_ready(self.engine, ready, self.run, self.check_active)
        except (InterruptedError, AccountError):
            raise
        except Exception as exc:
            current = self.w.store.get("job", job["id"])
            result = {"job_id": job["id"], "status": current["status"], "issues": [str(exc)[:1000]]}
            if current["status"] not in {"submitted", "submission_unknown"}:
                result["status"] = "needs_review"
                self.w.store.job(job["id"], status="needs_review", exceptions=result["issues"])
        finally:
            session = self.w.sessions.pop(job["id"], None)
            if session:
                session.close()
        self.run["results"].append(result)
        self._save()

    def _scope(self, client, analysis, page):
        """Independent scope reviews run on fresh source text before any personal fields are entered."""
        self.check_active()
        payload = {"preferences": self.run["scope"], "request": self.run["instruction"], "source_text": page["text"], "company": analysis["company"], "title": analysis["title"]}
        if any(requirement["text"] not in page["text"] for requirement in analysis["required"]):
            raise ValueError("Each mandatory qualification must quote the captured job description exactly.")
        for role in ("application", "auditor"):
            validate_scope(client(role).ask(SCOPE_PROMPT, payload), self.run["scope"], page["text"])
