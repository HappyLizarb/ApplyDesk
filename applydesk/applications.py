"""Screen public listings and prepare each application in its native Chrome tab."""

from .agent_context import screening_profile
from .application_policy import COVER_LETTER_REQUIRED
from .application_public_source import native_form_entry, public_recommendation, recently_screened_draft
from .application_session import ApplicationSession
from .discovery import require_public_listing
from .documents import DocumentTasks
from .employer_accounts import EmployerAccounts
from .intelligence import analyze_job, validate_eligibility
from .native_agent import NativeAgent
from .native_page import public_page
from .public_source import matches_saved_requisition_form, validate_public_analysis, validate_public_page, validate_public_source
from .recommendation_source import process_issues


class Applications:
    """Own per-job browser sessions and the readiness state machine."""

    def __init__(self, workspace, client=None, checkpoint=None, scope_check=None, session_factory=None, resume_choice=None):
        self.w = workspace
        self.client = client or (lambda role: self.w.client(role))
        self.checkpoint = checkpoint or (lambda: None)
        self.scope_check = scope_check
        self.session_factory = session_factory
        self.documents = DocumentTasks(workspace)
        self.resume_choice = dict(resume_choice) if resume_choice is not None else None

    def _session(self, job):
        """Reuse the same job tab across source and employer destinations."""
        existing = self.w.sessions.get(job["id"])
        if isinstance(existing, ApplicationSession):
            return existing
        if existing:
            existing.close()
        factory = self.session_factory or (lambda **options: ApplicationSession(self.w.orchestrator, **options))
        session = factory(job_id=job["id"])
        self.w.sessions[job["id"]] = session
        return session

    def prepare(self, job_id):
        """Turn any uncertain condition into an explicit review state, never success."""
        self.checkpoint()
        job = self.w.store.begin_preparation(job_id)
        try:
            return self._prepare(job)
        except Exception as exc:
            self.w.store.job(job_id, status="needs_review", exceptions=[str(exc)])
            self.w.store.event(job_id, "Paused: " + str(exc))
            raise

    def _prepare(self, job):
        """Review listing details and process prerequisites before interacting with a form."""
        profile = self.w.profile()
        if not profile["confirmed"]:
            raise ValueError("Review and confirm your profile first.")
        self.client("application")
        self.w.store.event(job["id"], "Reading the job description")
        session, page = self._description(job)
        analysis = self._screen(job, profile, page)
        if analysis is None:
            return self.w.store.get("job", job["id"])
        page = native_form_entry(session, page)
        job = self.w.store.get("job", job["id"])
        if job.get("public_source") and not (page.get("resumed_form") and matches_saved_requisition_form(job, page)):
            validate_public_page(job["public_source"], page, listing=False)
        self._resume(job, analysis)
        if job.get("attachments") and job.get("employer_account_id") and not page.get("resumed_form"):
            session.call("resume_account")
        resumed = page if page.get("resumed_form") else None
        return NativeAgent(self, session, job, profile, analysis, resumed_page=resumed).run()

    def _description(self, job):
        """Refresh a saved employer listing while retaining the identity of historical platform imports."""
        if resumed := recently_screened_draft(self, job):
            return resumed
        if "recommendation_source" in job:
            return public_recommendation(self, job)
        public = job.get("public_source")
        if public:
            validate_public_source(public)
        require_public_listing(job["url"])
        session = self._session(job)
        page = public_page(session, job["url"], listing_only=True)
        return session, validate_public_page(public, page) if public else page

    def _screen(self, job, profile, page):
        """Review listing completeness and prerequisites while accepting assumed qualifications."""
        context = screening_profile(self.w, profile, self.resume_choice)
        analysis = analyze_job(self.client("application"), context, page)
        if page.get("public_source"):
            validate_public_analysis(page["public_source"], analysis)
        eligibility = validate_eligibility(analysis, profile)
        source = page.get("public_source")
        if source:
            unresolved = process_issues(analysis, source, page["text"], check_on_form=True)
            eligibility["reasons"].extend(unresolved)
            eligibility["needs_input"] = eligibility.get("needs_input") or bool(unresolved)
            eligibility["eligible"] = eligibility["eligible"] and not eligibility["reasons"]
        requires_letter = analysis.get("cover_letter_required") is True
        if requires_letter:
            eligibility["reasons"].append(COVER_LETTER_REQUIRED)
            eligibility["eligible"] = False
        self.checkpoint()
        if eligibility["eligible"] and self.scope_check:
            self.scope_check(analysis, page)
        company = EmployerAccounts(self.w).listing_company(job, page, analysis["company"])
        self.w.store.job(job["id"], company=company, title=analysis["title"], details=page["text"], details_origin="captured",
                         analysis=analysis, profile_revision=profile["revision"], exceptions=eligibility["reasons"],
                         cover_state="required" if requires_letter else job.get("cover_state", "not_checked"))
        if not eligibility["eligible"]:
            status = "needs_review" if eligibility.get("needs_input") or requires_letter else "skipped"
            self.w.store.job(job["id"], status=status)
            self.w.store.event(job["id"], f"{status}: application details or process prerequisites need review")
            return None
        return analysis

    def _resume(self, job, analysis):
        """Honor a run's exact uploaded resume; only unspecified choices use category fallback."""
        record = (self.w.resumes.selected(self.resume_choice["id"], self.resume_choice["sha256"])
                  if self.resume_choice is not None else self.w.resumes.select(analysis["category"]))
        self.w.docs.source_file(record, for_upload=True)
        self.checkpoint()
        job["resume_id"] = record["id"]
        self.w.store.job(job["id"], resume_id=record["id"])
        self.w.store.event(job["id"], "Checking the uploaded resume")
        self.documents.require_review(job["resume_id"])
