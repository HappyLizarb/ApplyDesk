"""Prepare applications for review and separately authorized submission runs."""

from urllib.parse import urlparse

from .application_steps import attachment_plan, audit_snapshot, authenticate, cover_text_plan
from .agent_context import resume_sources, source_record
from .browser_bridge import BrowserSession
from .document_tasks import DocumentTasks
from .mapping import document_kind, normalize, plan_fields
from .profile import model_profile, public_job_url
from .readiness import verify_readiness
from .public_source import validate_public_analysis, validate_public_page, validate_public_source
from .application_submission import DECLARATION
from .recommendation_checks import employer_evidence, employer_target, process_issues, scoped_page, validate_employer_page, validate_source, validate_native_page


class Applications:
    """Own per-job browser sessions and the readiness state machine."""

    def __init__(self, workspace, client=None, checkpoint=None, scope_check=None, session_factory=None, resume_choice=None):
        self.w = workspace
        self.client = client or (lambda role: self.w.client(role))
        self.checkpoint = checkpoint or (lambda: None)
        self.scope_check = scope_check
        self.session_factory = session_factory or BrowserSession
        self.documents = DocumentTasks(workspace, client)
        self.resume_choice = dict(resume_choice) if resume_choice is not None else None

    def _session(self, job):
        """Separate persistent job sessions retain the exact live handoff tab."""
        existing = self.w.sessions.pop(job["id"], None)
        if existing:
            existing.close()
        platform = (job.get("recommendation_source") or {}).get("platform") if job.get("native_application") else None
        root = self.w.root / "sources" / platform if platform else self.w.root / "browser" / job["id"]
        options = {"application_source": platform} if platform else {}
        self.w.sessions[job["id"]] = self.session_factory(root, upload_roots=[self.w.docs.root], **options)
        return self.w.sessions[job["id"]]

    def prepare(self, job_id):
        """Turn any uncertain condition into an explicit review state, never success."""
        job = self.w.store.get("job", job_id)
        if not job or job.get("removed_at") or job["status"] == "submitted":
            raise ValueError("Choose an application that has not been removed or submitted.")
        self.checkpoint()
        self.w.store.job(job_id, status="preparing", exceptions=[])
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
        self.client("auditor")
        self.w.store.event(job["id"], "Reading the job description")
        session, page = self._description(job)
        source = page.get("recommendation_source")
        analysis = self._screen(job, profile, page)
        if analysis is None:
            return self.w.store.get("job", job["id"])
        if source:
            session, page, analysis = self._source_destination(job, profile, source, analysis)
            if analysis is None:
                return self.w.store.get("job", job["id"])
        job = self.w.store.get("job", job["id"])
        page = self._application_entry(session, page, job)
        if source and page.get("external_application_url"):
            source = {**source, "employer_url": page["external_application_url"]}
            self.w.store.job(job["id"], recommendation_source=source)
            session, page, analysis = self._source_destination(job, profile, source, analysis)
            if analysis is None:
                return self.w.store.get("job", job["id"])
            page = self._application_entry(session, page, job)
        if source and not source.get("employer_url"):
            validate_native_page(source, page)
        if source and source.get("employer_url"):
            validate_employer_page(source, page)
        job = self.w.store.get("job", job["id"])
        if job.get("public_source"):
            validate_public_page(job["public_source"], page, listing=False)
        self._resume(job, analysis)
        return self._walk(session, job, profile, analysis, page)

    def _source_destination(self, job, profile, source, analysis):
        """Reuse a platform sign-in only for that platform; employer links get an isolated session."""
        native = not source.get("employer_url")
        if native and not self.scope_check:
            employer_target(source)
        job["native_application"] = native
        target = source["source_url"] if native else public_job_url(employer_target(source))
        session = self._session(job)
        page = session.call("open", {"url": target})
        if native:
            validate_native_page(source, page)
        else:
            analysis = self._screen(job, profile, employer_evidence(source, page))
        return session, page, analysis

    def _description(self, job):
        """Read imported jobs through the scoped platform reader, not a generic feed snapshot."""
        source = job.get("recommendation_source")
        if "recommendation_source" in job:
            validate_source(source, require_complete=False)
            fresh = self.w.recommendations.read_job(source)
            page = scoped_page(fresh, source)
            self.w.store.job(job["id"], recommendation_source=fresh, details=fresh["description"],
                             details_origin="captured", qualification_status="unverified")
            return None, page
        host = (urlparse(job["url"]).hostname or "").lower()
        if not job.get("public_source") and any(host == domain or host.endswith("." + domain) for domain in ("linkedin.com", "joinhandshake.com")):
            raise ValueError("Import this LinkedIn or Handshake listing through Find jobs before preparing it. A scoped source capture is required.")
        public = job.get("public_source")
        if public:
            validate_public_source(public)
        session = self._session(job)
        page = session.call("open", {"url": job["url"]})
        return session, validate_public_page(public, page) if public else page

    def _screen(self, job, profile, page):
        """Review listing completeness and prerequisites while accepting assumed qualifications."""
        from .intelligence import analyze_job, audit_eligibility, validate_eligibility
        context = model_profile(profile)
        context["available_resume_categories"] = self.w.resumes.categories()
        if self.resume_choice is not None:
            selected = self.w.resumes.selected(self.resume_choice["id"], self.resume_choice["sha256"])
            context["resumes"] = [source_record(self.w, selected["id"])]
        else:
            context["resumes"] = resume_sources(self.w)
        analysis = analyze_job(self.client("application"), context, page)
        if page.get("public_source"):
            validate_public_analysis(page["public_source"], analysis)
        eligibility = validate_eligibility(analysis, profile)
        source = page.get("recommendation_source") or page.get("public_source")
        if source:
            eligibility["reasons"].extend(process_issues(analysis, source, page["text"]))
            eligibility["eligible"] = eligibility["eligible"] and not eligibility["reasons"]
        if eligibility["eligible"]:
            audit = audit_eligibility(self.client("auditor"), context, session_page(page), analysis)
            eligibility = {"eligible": audit["passed"], "reasons": audit["issues"]}
        self.checkpoint()
        if eligibility["eligible"] and self.scope_check:
            self.scope_check(analysis, page)
        self.w.store.job(job["id"], company=analysis["company"], title=analysis["title"], details=page["text"], details_origin="captured",
                         analysis=analysis, profile_revision=profile["revision"], exceptions=eligibility["reasons"])
        if not eligibility["eligible"]:
            status = "needs_review" if page.get("recommendation_source") else "skipped"
            self.w.store.job(job["id"], status=status)
            self.w.store.event(job["id"], f"{status}: application details or process prerequisites need review")
            return None
        return analysis

    def _resume(self, job, analysis):
        """Honor a run's exact uploaded resume; only unspecified choices use category fallback."""
        record = (self.w.resumes.selected(self.resume_choice["id"], self.resume_choice["sha256"])
                  if self.resume_choice is not None else self.w.resumes.select(analysis["category"]))
        self.checkpoint()
        job["resume_id"] = record["id"]
        self.w.store.job(job["id"], resume_id=record["id"])
        self.w.store.event(job["id"], "Checking the uploaded resume")
        self.documents.require_review(job["resume_id"])

    def _application_entry(self, session, page, job=None):
        """Follow an observed application link; ambiguous Apply buttons require handoff."""
        if any(document_kind(field) == "resume" for field in page["fields"]):
            return page
        if page.get("application_source"):
            return session.call("application_entry", {"snapshot_id": page["snapshot_id"], "company": (job or {}).get("company"), "title": (job or {}).get("title")})
        links = [link for link in page.get("links", []) if normalize(link["text"]) in {"apply", "apply now", "apply for this job", "apply for this position"}]
        destinations = list(dict.fromkeys(link["href"] for link in links))
        if len(destinations) == 1:
            return session.call("open", {"url": public_job_url(destinations[0])})
        return page

    def _walk(self, session, job, profile, analysis, page):
        """Advance only through verified fields and explicitly safe page transitions."""
        ledger, seen, step = {}, set(), 0
        while True:
            if page["snapshot_id"] in seen:
                raise ValueError("The application page did not advance. Review the current page before continuing.")
            seen.add(page["snapshot_id"])
            step += 1
            self.w.store.event(job["id"], f"Inspecting application step {step}")
            self.w.store.job(job["id"], live_url=page["url"])
            page = self._fill_step(session, job, profile, analysis, page, ledger)
            if page.get("intervention"):
                continue  # Reinspect newly revealed fields instead of continuing a stale batch.
            if any(button["action"] == "final_or_ambiguous" for button in page["buttons"]):
                return self._finish(job, profile, page, list(ledger.values()))
            next_buttons = [button for button in page["buttons"] if button["action"] == "advance" and not button["disabled"]]
            if len(next_buttons) == 1:
                if self.scope_check and DECLARATION.search(page.get("text", "")):
                    raise ValueError("This step includes an unapproved declaration. Review it before continuing.")
                page = session.call("next", {"snapshot_id": page["snapshot_id"], "field_id": next_buttons[0]["field_id"]})
                continue
            return self._finish(job, profile, page, list(ledger.values()))

    def _fill_step(self, session, job, profile, analysis, page, ledger):
        """Plan, fill, and audit a single page, retaining actual upload provenance."""
        self.checkpoint()
        if self.scope_check and any(field["type"] == "password" for field in page["fields"]):
            raise ValueError("Sign in manually before this application can continue.")
        page = authenticate(self.w, session, job, page)
        extra, handled = cover_text_plan(self.w, self.documents, job, page)
        answers, unresolved = plan_fields(page, profile, analysis["country"], analysis.get("country_name", ""), ignored_ids=handled)
        answers.extend(extra)
        uploads, missing = attachment_plan(self.w, self.documents, job, page)
        if unresolved or missing:
            raise ValueError("; ".join(unresolved + missing))
        if answers or uploads:
            page = session.call("fill", {"snapshot_id": page["snapshot_id"], "answers": answers, "uploads": uploads})
            completed = {item["field_id"] for item in page.get("readbacks", []) if item.get("files") and item.get("matches")}
            ledger.update({item["field_id"]: item for item in uploads if item["field_id"] in completed})
            self.w.store.job(job["id"], attachments=list(ledger.values()))
            if page.get("intervention"):
                return page
        return audit_snapshot(self.w, session, profile, analysis["country"], analysis.get("country_name", ""), uploads, extra_answers=extra, client=self.client)

    def _finish(self, job, profile, page, uploads):
        """Ready means reviewed live fields; it never means submitted."""
        import hashlib
        for item in uploads:
            item["expected_sha256"] = hashlib.sha256(self.w.docs.asset(item["path"]).read_bytes()).hexdigest()
        self.checkpoint()
        current_job = self.w.store.get("job", job["id"])
        readiness = verify_readiness(job, current_job, self.w.profile(), page, uploads)
        if not readiness["ready"]:
            raise ValueError("; ".join(readiness["issues"]))
        self.w.store.event(job["id"], "Paused for your final review and submission")
        return self.w.store.job(job["id"], status="ready", cover_state=readiness["cover_state"], exceptions=[],
                                live_url=page["url"], attachments=uploads, last_snapshot=page["snapshot_id"], final_audit=page.get("audit"))

    def focus(self, job_id):
        """The explicit handoff gives the user control of the exact paused browser tab."""
        session = self.w.sessions.get(job_id)
        if not session:
            raise ValueError("This live browser session is closed. Prepare the application to reopen it.")
        self.w.store.job(job_id, status="manual_review", progress="Browser under your control. Submit only after your review.")
        return session.call("focus", {"manual": True})


def session_page(page):
    """Pass actual inspected page data without worker interpretation to the auditor."""
    keys = ("url", "title", "text", "fields", "buttons", "recommendation_source", "public_source")
    return {key: page[key] for key in keys if key in page}
