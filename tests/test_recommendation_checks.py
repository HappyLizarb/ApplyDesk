"""Recommendation gates use scoped synthetic captures and never contact providers."""

import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from applydesk.applications import Applications
from applydesk.intelligence import audit_eligibility
from applydesk.recommendation_checks import employer_evidence, employer_target, process_issues, scoped_page, validate_employer_page, validate_source


def source(description=None):
    """Represent a complete selected detail panel rather than an entire job feed."""
    description = description or "Example Co\nAnalyst Intern\nBoston\nPython required."
    return {"platform": "linkedin", "source_url": "https://www.linkedin.com/jobs/view/12345/", "job_id": "12345",
            "company": "Example Co", "title": "Analyst Intern", "description": description, "complete": True,
            "captured_at": "2026-10-18T10:00:00+00:00", "description_sha256": hashlib.sha256(description.encode()).hexdigest(),
            "employer_url": "https://employer.example.test/jobs/req-456", "issues": []}


def analysis():
    """Only this complete, supported baseline may reach an independent audit."""
    return {"company": "Example Co", "title": "Analyst Intern", "requirements_complete": True,
            "required": [{"text": "Python required", "evidence_ids": ["skills.0"], "met": True}],
            "process_complete": True, "identity_consistent": True, "employment_type_consistent": True,
            "consistency_issues": [], "process_restrictions": [], "passed": True, "issues": []}


class RecommendationChecksTests(unittest.TestCase):
    """Unknown source context, process rules, and destinations remain explicit pauses."""

    def test_incomplete_capture_and_modified_bytes_fail(self):
        for changes in ({"complete": False}, {"description": "Replacement job"}, {"issues": ["Show more is collapsed"]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_source({**source(), **changes})

    def test_fresh_capture_must_retain_identity_but_can_have_new_requirements(self):
        old = source()
        fresh = source(old["description"] + "\nSQL required.")
        self.assertEqual(scoped_page(fresh, old)["text"], fresh["description"])
        for key in ("job_id", "title", "company"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "different job"):
                validate_source({**fresh, key: "Different"}, old)

    def test_scoped_page_has_no_feed_navigation_or_application_controls(self):
        captured = source()
        page = scoped_page(captured, captured)
        self.assertEqual(page["text"], captured["description"])
        self.assertEqual(page["links"], [])
        self.assertEqual(page["buttons"], [])
        self.assertEqual(page["recommendation_source"], captured)

    def test_bad_platform_source_cannot_claim_a_valid_capture(self):
        for url in ("https://evil.test/jobs/12345", "https://linkedin.com.evil.test/jobs/12345", "http://linkedin.com/jobs/12345"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_source({**source(), "source_url": url})

    def test_unknown_process_and_contradictory_type_cannot_pass(self):
        self.assertEqual(process_issues(analysis(), source()), [])
        for key in ("process_complete", "identity_consistent", "employment_type_consistent", "process_restrictions", "consistency_issues"):
            result = analysis()
            result.pop(key)
            with self.subTest(key=key):
                self.assertTrue(process_issues(result, source()))
        result = {**analysis(), "employment_type_consistent": False,
                  "consistency_issues": ["The description is a permanent role but the card says internship."]}
        self.assertTrue(process_issues(result, source()))

    def test_application_cap_and_availability_always_require_manual_evidence(self):
        rules = [("application_limit", "Apply to at most two roles across our affiliates."),
                 ("availability_statement", "State your availability dates on your resume."),
                 ("other", "Complete the prerequisite assessment before applying.")]
        for kind, quote in rules:
            result = analysis()
            result["process_restrictions"] = [{"kind": kind, "text": quote, "quote": quote, "met": True, "count": 0}]
            captured = source(source()["description"] + "\n" + quote)
            with self.subTest(kind=kind):
                self.assertTrue(any("Manual verification" in issue for issue in process_issues(result, captured)))

    def test_independent_auditor_cannot_approve_a_previously_omitted_rule(self):
        quote = "Apply to at most two roles across all affiliates."
        captured = source(source()["description"] + "\n" + quote)
        audited = analysis()
        audited["process_restrictions"] = [{"kind": "application_limit", "text": quote, "quote": quote}]
        profile = {"confirmed": True, "facts": [{"id": "skills.0", "text": "Python", "kind": "skill", "confirmed": True}]}
        result = audit_eligibility(SimpleNamespace(ask=Mock(return_value=audited)), profile, scoped_page(captured, captured), analysis())
        self.assertFalse(result["passed"])
        self.assertTrue(any("Manual verification" in issue for issue in result["issues"]))

    def test_missing_or_platform_employer_link_pauses(self):
        for target in (None, "", "https://www.linkedin.com/jobs/view/12345", "http://employer.example.test/jobs/12345"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                employer_target({**source(), "employer_url": target})

    def test_employer_page_requires_visible_identity_and_observed_destination(self):
        captured = source()
        page = {"url": captured["employer_url"], "title": "Example Co", "text": "Analyst Intern — Python required."}
        self.assertEqual(validate_employer_page(captured, page), page)
        with self.assertRaisesRegex(ValueError, "company and title"):
            validate_employer_page(captured, {**page, "text": "Senior Manager"})
        with self.assertRaisesRegex(ValueError, "redirected"):
            validate_employer_page(captured, {**page, "url": "https://ats.example.test/apply"})

    def test_redirect_needs_an_employer_id_observed_in_original_link(self):
        captured = {**source(), "employer_job_id": "req-456"}
        page = {"url": "https://ats.example.test/job/req-456", "title": "Example Co", "text": "Analyst Intern"}
        self.assertEqual(validate_employer_page(captured, page), page)
        for changes in ({"url": "https://ats.example.test/job/req-4567"}, {"url": "https://ats.example.test/job/req-999"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_employer_page(captured, {**page, **changes})
        with self.assertRaises(ValueError):
            validate_employer_page({**captured, "employer_job_id": "12345"}, page)

    def test_imported_prepare_uses_fresh_scoped_reader_and_stores_new_description(self):
        stored = source()
        fresh = source(stored["description"] + "\nSQL required.")
        workspace = SimpleNamespace(recommendations=SimpleNamespace(read_job=Mock(return_value=fresh)), store=Mock())
        app = Applications(workspace)
        app._session = Mock()
        session, page = app._description({"id": "job-1", "url": "https://www.linkedin.com/jobs/", "recommendation_source": stored})
        self.assertIsNone(session)
        self.assertEqual(page["text"], fresh["description"])
        workspace.recommendations.read_job.assert_called_once_with(stored)
        workspace.store.job.assert_called_once_with("job-1", recommendation_source=fresh, details=fresh["description"],
                                                   details_origin="captured", qualification_status="unverified")
        app._session.assert_not_called()

    def test_invalid_import_never_opens_a_feed_or_calls_reader(self):
        workspace = SimpleNamespace(recommendations=SimpleNamespace(read_job=Mock()), store=Mock())
        app = Applications(workspace)
        app._session = Mock()
        for captured in ({}, None):
            with self.subTest(captured=captured), self.assertRaises(ValueError):
                app._description({"id": "job-1", "url": "https://www.linkedin.com/jobs/", "recommendation_source": captured})
        app._session.assert_not_called()
        workspace.recommendations.read_job.assert_not_called()

    def test_incomplete_saved_capture_can_refresh_but_fresh_incomplete_still_pauses(self):
        saved = {**source(), "complete": False, "company": "", "title": "", "description": "",
                 "description_sha256": hashlib.sha256(b"").hexdigest(), "issues": ["Description was collapsed."]}
        workspace = SimpleNamespace(recommendations=SimpleNamespace(read_job=Mock(return_value=source())), store=Mock())
        app = Applications(workspace)
        app._session = Mock()
        job = {"id": "job-1", "recommendation_source": saved}
        self.assertTrue(app._description(job)[1]["recommendation_source"]["complete"])
        workspace.recommendations.read_job.return_value = saved
        with self.assertRaisesRegex(ValueError, "complete selected"):
            app._description(job)
        self.assertEqual(workspace.recommendations.read_job.call_count, 2)
        app._session.assert_not_called()

    def test_manual_platform_links_require_scoped_import_before_generic_browser(self):
        # Platform detail links and feeds must never enter the unscoped employer reader.
        app = Applications(SimpleNamespace())
        app._session = Mock()
        for url in ("https://www.linkedin.com/jobs/", "https://linkedin.com/jobs/view/12345/",
                    "https://app.joinhandshake.com/home", "https://app.joinhandshake.com/job-search/12345"):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "scoped source capture"):
                app._description({"id": "manual", "url": url})
        app._session.assert_not_called()
        job = {"id": "employer", "url": "https://careers.example.test/jobs/12345"}
        app._description(job)
        app._session.assert_called_once_with(job)
        app._session.return_value.call.assert_called_once_with("open", {"url": job["url"]})

    def test_employer_evidence_preserves_capture_hash_and_checks_new_process_quotes(self):
        captured = source()
        quote = "Apply to at most two roles across all affiliates."
        page = {"url": captured["employer_url"], "title": "Example Co — Analyst Intern",
                "text": quote + "\n" + "An internship working with data and building reports. " * 3}
        combined = employer_evidence(captured, page)
        self.assertEqual(combined["recommendation_source"]["description_sha256"], captured["description_sha256"])
        self.assertNotIn(quote, captured["description"])
        result = {**analysis(), "process_restrictions": [{"kind": "application_limit", "text": quote, "quote": quote}]}
        self.assertTrue(any("Manual verification" in issue for issue in process_issues(result, captured, combined["text"])))
        with self.assertRaisesRegex(ValueError, "complete employer"):
            employer_evidence(captured, {**page, "text_truncated": True})
        for text in ("", "Verify you are human" * 10, "This job is no longer available" * 10):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "manual review"):
                employer_evidence(captured, {**page, "text": text})

    def test_missing_employer_link_pauses_before_browser_documents_or_forms(self):
        captured = {**source(), "employer_url": ""}
        job = {"id": "job-1", "status": "new", "url": captured["source_url"], "recommendation_source": captured}
        workspace = SimpleNamespace(recommendations=SimpleNamespace(read_job=Mock(return_value=captured)),
                                    store=Mock(), profile=lambda: {"confirmed": True}, client=Mock())
        workspace.store.get.return_value = job
        app = Applications(workspace)
        app._screen, app._session, app._resume, app._walk = Mock(return_value=analysis()), Mock(), Mock(), Mock()
        with self.assertRaisesRegex(ValueError, "No verified employer"):
            app.prepare(job["id"])
        self.assertEqual(app._screen.call_args.args[2]["text"], captured["description"])
        app._session.assert_not_called()
        app._resume.assert_not_called()
        app._walk.assert_not_called()
        self.assertEqual(workspace.store.job.call_args.kwargs["status"], "needs_review")

    def test_process_failure_stays_needs_review_and_cannot_reach_audit_or_forms(self):
        captured = source()
        profile = {"confirmed": True, "revision": 1, "preferences": {},
                   "facts": [{"id": "skills.0", "text": "Python", "kind": "skill", "confirmed": True}]}
        workspace = SimpleNamespace(store=Mock(), client=Mock(), resumes=Mock(categories=lambda: ["General"], list=lambda: []))
        workspace.store.all.return_value = []
        app = Applications(workspace)
        result = {**analysis(), "process_complete": False, "details": captured["description"]}
        with patch("applydesk.intelligence.analyze_job", return_value=result), patch("applydesk.intelligence.audit_eligibility") as auditor:
            self.assertIsNone(app._screen({"id": "job-1"}, profile, scoped_page(captured, captured)))
        auditor.assert_not_called()
        self.assertEqual(workspace.store.job.call_args.kwargs["status"], "needs_review")

    def test_stricter_employer_qualification_uses_the_user_assumption(self):
        """Fresh employer requirements remain recorded without demanding unsupported personal evidence."""
        captured = source()
        job = {"id": "job-1", "status": "new", "recommendation_source": captured}
        profile = {"confirmed": True, "revision": 1, "preferences": {},
                   "facts": [{"id": "skills.0", "text": "Python", "kind": "skill", "confirmed": True}]}
        initial = {**analysis(), "details": captured["description"], "country": "US", "category": "Tech",
                   "preferred": [], "cover_letter_required": False}
        stricter = {**initial, "required": [*initial["required"], {"text": "SQL required", "evidence_ids": [], "met": False}]}
        worker, auditor = SimpleNamespace(ask=Mock(side_effect=[initial, stricter])), SimpleNamespace(ask=Mock(return_value=initial))
        workspace = SimpleNamespace(store=Mock(), recommendations=SimpleNamespace(read_job=Mock(return_value=captured)),
                                    profile=lambda: profile, client=lambda role: auditor if role == "auditor" else worker,
                                    resumes=Mock(categories=lambda: ["General", "Tech"], list=lambda: []))
        workspace.store.get.return_value, workspace.store.all.return_value = job, []
        browser = Mock()
        browser.call.return_value = {"url": captured["employer_url"], "title": "Example Co — Analyst Intern",
                                     "text": "Python required. SQL required. " + "An internship working with data and building reports. " * 3,
                                     "fields": [], "buttons": [], "links": []}
        app = Applications(workspace)
        app._session, app._resume, app._walk = Mock(return_value=browser), Mock(), Mock()
        with patch("applydesk.applications.public_job_url", side_effect=lambda value: value):
            app.prepare(job["id"])
        self.assertIn("SQL required", worker.ask.call_args.args[1]["page"]["text"])
        self.assertEqual(workspace.store.job.call_args.kwargs["analysis"]["required"], stricter["required"])
        self.assertEqual([call.args[0] for call in browser.call.call_args_list], ["open"])
        app._resume.assert_called_once()
        app._walk.assert_called_once()


if __name__ == "__main__":
    unittest.main()
