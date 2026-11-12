"""Bounded unattended application runs owned by the local orchestrator, not the language model."""

import functools
import uuid

from .application_client import ApplicationClient
from .application_policy import SCOPE_PROMPT, authorized_request, search_scope, validate_scope
from .application_session import ApplicationSession
from .application_submission import attempt_key, submit_ready
from .applications import Applications
from .chatgpt_transport import AccountError
from .recommendation_wait import find_jobs
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

    def start(self, limit, locations):
        """Derive permission from the current human turn, never a listing or automatic draft task."""
        self.engine.check_active()
        message = (self.engine.active or {}).get("message")
        if not message or message.get("role") != "user":
            raise ValueError("An unattended run needs a new chat request with its limit and target locations.")
        targets = authorized_request(message["text"], limit, locations)
        prior = next((run for run in self.w.store.all("application_run") if run["message_id"] == message["id"]), None)
        if prior:
            return prior
        profile = self.w.profile()
        scope = search_scope(profile, targets)
        if not self.w.resumes.list():
            raise ValueError("Upload a finished resume in Documents before starting unattended applications.")
        platforms = [key for key, value in self.w.recommendations.preferences().items() if value["enabled"]]
        if not platforms:
            raise ValueError("Connect a job platform in Profile first.")
        self.run = {"id": uuid.uuid4().hex, "message_id": message["id"], "instruction": message["text"], "limit": limit, "scope": scope,
                    "profile_revision": profile["revision"], "account_id": self.engine.accounts.state()["active"],
                    "status": "running", "started_at": now(), "results": [], "searches": [], "current_job": None}
        self._save()
        return self._execute(platforms)

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
            raise InterruptedError("Profile changed. Start a new application run after reviewing your facts.")
        if self.engine.accounts.state()["active"] != self.run["account_id"]:
            raise InterruptedError("Account changed. Start a new application run to continue.")

    def _execute(self, platforms):
        """Scan each connected source once, then process eligible saved jobs up to the requested cap."""
        available = []
        try:
            for platform in platforms:
                self.check_active()
                try:
                    report = find_jobs(self.engine, platform)
                    security = any(word in str(report.get("issues", [])).lower() for word in ("captcha", "security check", "challenge"))
                    if report.get("status") in {"complete", "no_results", "blocked"} and not security:
                        available.append(platform)
                    self.run["searches"].append({key: report.get(key) for key in ("platform", "status", "message", "issues", "added_count")})
                except InterruptedError:
                    raise
                except Exception as exc:
                    self.run["searches"].append({"platform": platform, "status": "error", "message": str(exc)[:1000]})
                self._save()
            for job in self._candidates(available):
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
            identity = attempt_key(job)
            if (source.get("platform") not in platforms or source.get("complete") is not True or job.get("removed_at")
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
        application = Applications(self.w, client, self.check_active, scope, functools.partial(ApplicationSession, self.engine))
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
        payload = {"allowed": self.run["scope"], "request": self.run["instruction"], "source_text": page["text"], "company": analysis["company"], "title": analysis["title"]}
        if any(requirement["text"] not in page["text"] for requirement in analysis["required"]):
            raise ValueError("Each mandatory qualification must quote the captured job description exactly.")
        decisions = []
        for role in ("application", "auditor"):
            decisions.append(validate_scope(client(role).ask(SCOPE_PROMPT, payload), self.run["scope"], page["text"]))
        if any(decisions[0][key] != decisions[1][key] for key in ("industry", "level", "location")):
            raise ValueError("The independent agents disagree about this job's search-preference match.")
        analysis["category"] = decisions[0]["industry"]
