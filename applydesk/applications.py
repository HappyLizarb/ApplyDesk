"""Prepare applications for review and separately authorized submission runs."""

from urllib.parse import urlparse

from .application_answers import with_application_source
from .application_public_source import native_form_entry, public_recommendation, recently_screened_draft
from .application_steps import attachment_plan, audit_snapshot, authenticate, fill_current_form, enter_current_form
from .application_session import ApplicationSession
from .application_interest import interest_answers
from .agent_context import screening_profile
from .browser_bridge import BrowserSession
from .document_tasks import DocumentTasks
from .mapping import COVER_LETTER_REQUIRED, document_kind, normalize, plan_fields
from .profile import public_job_url
from .readiness import verify_readiness
from .public_source import matches_saved_requisition_form, validate_public_analysis, validate_public_page, validate_public_source
from .application_terms import unresolved_declarations
from .application_submission import DECLARATION
from .greenhouse_answers import disclosure_issues
from .employer_accounts import EmployerAccounts
from .recommendation_checks import employer_evidence, employer_target, process_issues, scoped_page, validate_employer_page, validate_source, validate_native_page, same_workday_job


class Applications:
    """Own per-job browser sessions and the readiness state machine."""

    def __init__(self, workspace, client=None, checkpoint=None, scope_check=None, session_factory=None, resume_choice=None, agent_driven=False):
        self.w = workspace
        self.client = client or (lambda role: self.w.client(role))
        self.checkpoint = checkpoint or (lambda: None)
        self.scope_check = scope_check
        self.session_factory = session_factory
        self.documents = DocumentTasks(workspace)
        self.resume_choice = dict(resume_choice) if resume_choice is not None else None
        self.agent_driven = agent_driven

    def _session(self, job):
        """Reuse the same job tab across source and employer destinations."""
        platform = (job.get("recommendation_source") or {}).get("platform") if job.get("native_application") and not self.agent_driven else None
        existing = self.w.sessions.get(job["id"])
        if isinstance(existing, ApplicationSession):
            existing.application_source = platform
            existing.agent_driven = self.agent_driven
            return existing
        if existing:
            existing.close()
        root = self.w.root / "sources" / platform if platform else self.w.root / "browser" / job["id"]
        options = {"application_source": platform} if platform else {}
        if self.agent_driven:
            options["agent_driven"] = True
        if self.session_factory:
            session = self.session_factory(root, upload_roots=[self.w.docs.root], job_id=job["id"], **options)
        elif hasattr(self.w, "application_browser"):
            session = ApplicationSession(self.w.orchestrator, root, job_id=job["id"], **options)
        else:
            session = BrowserSession(root, upload_roots=[self.w.docs.root], **options)
        self.w.sessions[job["id"]] = session
        return self.w.sessions[job["id"]]

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
        source = page.get("recommendation_source")
        analysis = self._screen(job, profile, page, listing_only=bool(source))
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
            validate_native_page(source, page, agent_driven=self.agent_driven)
        if source and source.get("employer_url"):
            validate_employer_page(source, page)
        job = self.w.store.get("job", job["id"])
        if job.get("public_source") and not (page.get("resumed_form") and matches_saved_requisition_form(job, page)):
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
            validate_native_page(source, page, agent_driven=self.agent_driven)
            evidence = {**page, "text": source["description"] + "\n\n" + page["text"], "recommendation_source": source}
            analysis = self._screen(job, profile, evidence)
        else:
            analysis = self._screen(job, profile, employer_evidence(source, page))
        return session, page, analysis

    def _description(self, job):
        """Read imported jobs through the scoped platform reader, not a generic feed snapshot."""
        source = job.get("recommendation_source")
        if self.agent_driven:
            if resumed := recently_screened_draft(self, job):
                return resumed
            if source:
                return public_recommendation(self, job)
        if "recommendation_source" in job:
            validate_source(source, require_complete=False)
            fresh = self.w.recommendations.read_job(source, application=True)
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
        if self.agent_driven:
            from .public_discovery import require_public_listing
            require_public_listing(job["url"])
        session = self._session(job)
        from .native_page import public_page
        page = public_page(session, job["url"], listing_only=True)
        return session, validate_public_page(public, page) if public else page

    def _screen(self, job, profile, page, listing_only=False):
        """Review listing completeness and prerequisites while accepting assumed qualifications."""
        from .intelligence import analyze_job, validate_eligibility
        context = screening_profile(self.w, profile, self.resume_choice)
        analysis = analyze_job(self.client("application"), context, page)
        if page.get("public_source"):
            validate_public_analysis(page["public_source"], analysis)
        eligibility = validate_eligibility(analysis, profile)
        source = page.get("recommendation_source") or page.get("public_source")
        if source and not listing_only:
            check_on_form = bool(self.agent_driven and page.get("native") and page.get("public_source"))
            unresolved = process_issues(analysis, source, page["text"], check_on_form=check_on_form)
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
            status = "needs_review" if eligibility.get("needs_input") or page.get("recommendation_source") or requires_letter else "skipped"
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
        if self.agent_driven:
            return native_form_entry(session, page)
        if any(document_kind(field) == "resume" for field in page["fields"]):
            return page
        if page.get("application_source"):
            return enter_current_form(session, page, job or {})
        links = [link for link in page.get("links", []) if normalize(link["text"]) in {"apply", "apply now", "apply for this job", "apply for this position"}
                 or (normalize(link["text"]) == "apply manually" and same_workday_job(page["url"], link["href"]))]
        destinations = list(dict.fromkeys(link["href"] for link in links))
        if len(destinations) == 1 and urlparse(page["url"]).hostname == "jobs.supermicro.com":
            return session.call("application_entry", {"snapshot_id": page["snapshot_id"], "link_id": links[0]["id"],
                                                     "company": job["company"], "title": job["title"]})
        if len(destinations) == 1:
            opened = session.call("open", {"url": public_job_url(destinations[0])})
            if same_workday_job(page["url"], opened["url"]) and opened["url"] != page["url"] and "/apply/applyManually" not in opened["url"] and any(normalize(link["text"]) == "apply manually" for link in opened.get("links", [])):
                return self._application_entry(session, opened, job)
            return opened
        return page

    def _walk(self, session, job, profile, analysis, page):
        """Advance only through verified fields and explicitly safe page transitions."""
        if self.agent_driven:
            if page.get("native"):
                from .native_agent import NativeAgent
                if (job.get("attachments") and job.get("employer_account_id") and not page.get("resumed_form")
                        and getattr(getattr(session, "manager", None), "native", None) is True):
                    session.call("resume_account")
                return NativeAgent(self, session, job, profile, analysis, resumed_page=page if page.get("resumed_form") else None).run()
            from .agent_browser import AgentBrowser
            return AgentBrowser(self, session, job, profile, analysis).run()
        profile = {**with_application_source(profile, job), "application_company": analysis.get("company", job.get("company", ""))}
        ledger, seen, step, stale_count = {}, set(), 0, 0
        while True:
            if page["snapshot_id"] in seen:
                raise ValueError("The application page did not advance. Review the current page before continuing.")
            seen.add(page["snapshot_id"])
            step += 1
            self.w.store.event(job["id"], f"Inspecting application step {step}")
            self.w.store.job(job["id"], live_url=page["url"])
            page = self._fill_step(session, job, profile, analysis, page, ledger)
            if page.get("intervention"):
                stale_count += page["intervention"] in {"stale_before_fill", "field_replaced_during_fill"}
                if stale_count > 3:
                    raise ValueError("The application keeps changing before filling. Its current form needs review.")
                page.pop("intervention", None)
                continue  # Reinspect newly revealed fields instead of continuing a stale batch.
            if any(button["action"] == "final_or_ambiguous" for button in page["buttons"]):
                return self._finish(job, profile, page, list(ledger.values()))
            next_buttons = [button for button in page["buttons"] if button["action"] == "advance" and not button["disabled"]]
            if len(next_buttons) == 1:
                if self.scope_check and unresolved_declarations(page.get("declaration_text", page.get("text", "")), DECLARATION):
                    raise ValueError("This step includes an unapproved declaration. Review it before continuing.")
                page = session.call("next", {"snapshot_id": page["snapshot_id"], "field_id": next_buttons[0]["field_id"], "audit": page.get("audit")})
                continue
            return self._finish(job, profile, page, list(ledger.values()))

    def _fill_step(self, session, job, profile, analysis, page, ledger):
        """Plan, fill, and audit a single page, retaining actual upload provenance."""
        self.checkpoint()
        page = authenticate(self.w, session, job, page)
        if page.get("intervention"):
            return page
        covers = [field for field in page["fields"] if document_kind(field) == "cover" and not field.get("disabled")]
        if any(field.get("required") for field in covers):
            self.w.store.job(job["id"], cover_state="required")
            raise ValueError(COVER_LETTER_REQUIRED)
        profile = {**profile, "interest_answers": interest_answers(self.w, self.client, job, profile, page)}
        answers, unresolved = plan_fields(page, profile, analysis["country"], analysis.get("country_name", ""))
        uploads, missing = attachment_plan(self.w, self.documents, job, page)
        if unresolved or missing:
            raise ValueError("; ".join(unresolved + missing))
        if answers or uploads:
            page = fill_current_form(session, {"snapshot_id": page["snapshot_id"], "answers": answers, "uploads": uploads})
            completed = {item["field_id"] for item in page.get("readbacks", []) if item.get("files") and item.get("matches")}
            ledger.update({item["field_id"]: item for item in uploads if item["field_id"] in completed})
            self.w.store.job(job["id"], attachments=list(ledger.values()))
            if page.get("intervention"):
                return page
        return audit_snapshot(self.w, session, profile, analysis["country"], analysis.get("country_name", ""), uploads)

    def _finish(self, job, profile, page, uploads):
        """Ready means reviewed live fields; it never means submitted."""
        import hashlib
        for item in uploads:
            item["expected_sha256"] = hashlib.sha256(self.w.docs.asset(item["path"]).read_bytes()).hexdigest()
        self.checkpoint()
        issues = disclosure_issues(profile, page)
        if issues:
            raise ValueError("; ".join(issues))
        readiness = verify_readiness(job, self.w.store.get("job", job["id"]), self.w.profile(), page, uploads)
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
