"""Public Google discovery and historical-job routing never open platform connections."""

import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from chat_helpers import ChatFixture
from recommendation_helpers import public_source, saved_listing
from applydesk.public_source import validate_public_analysis, validate_public_page, validate_public_source
from applydesk.application_public_source import recently_screened_draft, native_form_entry
from applydesk.storage import now
from applydesk.applications import Applications
from applydesk.orchestrator_tools import MasterTools, master_tools
from applydesk.discovery import canonical_url, google_xray_query, require_public_listing


class PublicSearchToolTests(unittest.TestCase):
    """The single agent gets public search captures, then reads and saves selected listings itself."""

    def setUp(self):
        self.workspace = SimpleNamespace(recommendations=Mock())
        self.engine = SimpleNamespace(w=self.workspace, check_active=Mock())
        self.tools = MasterTools(self.engine, Mock())
        self.tools.web = Mock()
        self.tools.web.search.return_value = {"capture_id": "search", "links": []}

    def test_find_jobs_is_query_only_google_xray_without_platform_service(self):
        result = self.tools.call({"name": "find_jobs", "arguments": '{"query":"publishing new graduate Boston"}'})
        query, engine = self.tools.web.search.call_args.args
        self.assertEqual((result["capture_id"], engine), ("search", "google"))
        self.assertIn("publishing new graduate Boston", query)
        self.assertIn("site:myworkdayjobs.com", query)
        self.assertIn("-site:linkedin.com", query)
        self.workspace.recommendations.begin.assert_not_called()
        schema = {tool["name"]: tool for tool in master_tools()[0]["tools"]}
        self.assertEqual(schema["find_jobs"]["parameters"]["required"], ["query"])
        self.assertNotIn("job_search_status", schema)

    def test_explicit_employer_site_is_preserved_without_default_site_restriction(self):
        query = google_xray_query('site:careers.example.test "museum curator"')
        self.assertIn('site:careers.example.test "museum curator"', query)
        self.assertNotIn("site:myworkdayjobs.com", query)
        self.assertIn("-site:joinhandshake.com", query)

    def test_old_platform_arguments_and_non_google_search_are_rejected(self):
        cases = [("find_jobs", {"platform": "linkedin", "query": "publishing"}),
                 ("job_search_status", {"platform": "linkedin", "run_id": "old"}),
                 ("web_search", {"query": "publishing", "engine": "bing"})]
        for name, data in cases:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.tools.call({"name": name, "arguments": json.dumps(data)})
        self.tools.web.search.assert_not_called()
        self.workspace.recommendations.begin.assert_not_called()

    def test_platform_hosts_and_explicit_platform_xray_queries_are_rejected(self):
        for host in ("linkedin.com", "www.linkedin.com", "app.joinhandshake.com"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                require_public_listing("https://" + host + "/jobs/123")
            with self.subTest(query=host), self.assertRaises(ValueError):
                google_xray_query("site:" + host + " publishing")
        self.assertEqual(require_public_listing("https://linkedin.example.test/jobs/123"), "https://linkedin.example.test/jobs/123")


class HistoricalPublicRoutingTests(ChatFixture):
    """Retain saved jobs and attempt identities while reading fresh public employer evidence."""

    def setUp(self):
        super().setUp()
        self.source = saved_listing()
        job = self.w.store.add_job(self.source["source_url"])
        self.job = self.w.store.job(job["id"], company=self.source["company"], title=self.source["title"],
            recommendation_source=copy.deepcopy(self.source), native_application=True)
        self.page = {"url": self.source["employer_url"], "text": self.source["description"], "title": "Example Labs Software Engineer"}
        self.browser = Mock()
        self.browser.call.return_value = self.page
        self.factory = Mock(return_value=self.browser)
        self.browser.manager.native = True
        self.client = Mock()
        self.client.ask.return_value = {"same_posting": True, "company": self.source["company"], "title": self.source["title"]}
        self.app = Applications(self.w, client=lambda role: self.client, session_factory=self.factory)

    def describe(self):
        """Refresh the stored job using its observed employer destination and a synthetic native tab."""
        with patch("applydesk.application_public_source.public_job_url", side_effect=lambda url: url):
            return self.app._description(self.w.store.get("job", self.job["id"]))

    def test_old_job_reopens_observed_employer_without_platform_flags_or_history_changes(self):
        _, page = self.describe()
        self.browser.call.assert_called_once_with("open", {"url": self.source["employer_url"], "listing_only": True})
        self.factory.assert_called_once_with(job_id=self.job["id"])
        self.assertEqual(self.client.ask.call_args.args[1]["source"], self.source)
        current = self.w.store.get("job", self.job["id"])
        self.assertEqual(current["recommendation_source"], self.source)
        self.assertEqual(current["url"], self.source["source_url"])
        self.assertFalse(current["native_application"])
        self.assertEqual(page["public_source"], current["public_source"])
        self.assertNotIn("recommendation_source", page)

    def test_missing_employer_destination_requests_discovery_without_opening_browser(self):
        self.w.store.job(self.job["id"], recommendation_source={**self.source, "employer_url": ""},
                         live_url="https://unverified.example.test/guessed-role")
        with self.assertRaisesRegex(ValueError, "Find and save.*public employer"):
            self.describe()
        self.factory.assert_not_called()
        self.browser.call.assert_not_called()

    def test_changed_employer_identity_or_incomplete_capture_does_not_replace_source(self):
        for changes in ({"title": "Another Employer", "text": "Another Employer Software Engineer " * 10}, {"text_truncated": True}):
            self.browser.call.return_value = {**self.page, **changes}
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.describe()
            self.assertNotIn("public_source", self.w.store.get("job", self.job["id"]))
            self.assertEqual(self.w.store.get("job", self.job["id"])["recommendation_source"], self.source)

    def test_new_mandatory_experience_stops_a_historical_job_before_form_input(self):
        """Reread employer requirements before applying with an older saved recommendation."""
        requirement = "Three years of professional SQL experience are required."
        self.page["text"] += "\n" + requirement
        review = {"company": self.source["company"], "title": self.source["title"],
                  "country": "US", "category": "General", "details": self.page["text"],
                  "required": [{"text": requirement, "met": False, "evidence_ids": []}], "preferred": [],
                  "requirements_complete": True, "cover_letter_required": False, "process_complete": True,
                  "identity_consistent": True, "employment_type_consistent": True,
                  "process_restrictions": [], "consistency_issues": []}
        self.client.ask.side_effect = [self.client.ask.return_value, review]
        with patch("applydesk.application_public_source.public_job_url", side_effect=lambda url: url):
            result = self.app.prepare(self.job["id"])
        self.assertIn(requirement, self.client.ask.call_args.args[1]["page"]["text"])
        self.assertIn(requirement, " ".join(result["exceptions"]))
        self.assertIn(result["status"], {"needs_review", "skipped"})
        self.assertEqual(self.w.store.all("submission_attempt"), [])
        self.browser.call.assert_called_once_with("open", {"url": self.source["employer_url"], "listing_only": True})

    def test_disabled_platform_preferences_do_not_hide_historical_saved_jobs(self):
        self.w.store.put("recommendation_preference", "linkedin", {"enabled": False})
        candidates = self.w.orchestrator.applications._candidates()
        self.assertIn(self.job["id"], [job["id"] for job in candidates])
        self.w.store.job(self.job["id"], status="submitted")
        self.assertNotIn(self.job["id"], [job["id"] for job in self.w.orchestrator.applications._candidates()])


class PublicSourceTests(unittest.TestCase):
    def test_workday_can_resume_account_creation_before_the_first_upload(self):
        """A job-specific sign-in URL can replace the missing heading only on its own requisition."""
        url = "https://company.wd5.myworkdayjobs.com/en-US/Careers/job/City/Engineer_123456-2"
        source = dict(public_source(), url=url, captured_at=now())
        app = Mock()
        browser = app._session.return_value
        browser.manager.native = True
        for posting, matches in ((url, True), (url.replace("123456-2", "123456-1"), False)):
            form = {"url": posting + "/apply/autofillWithResume", "text": "Create Account", "title": "Sign In"}
            browser.call.return_value = form
            for previous in (form["url"], "https://employer.test/recruitment-notice"):
                job = {"public_source": source, "live_url": previous}
                self.assertEqual(bool(recently_screened_draft(app, job)), matches)

    def test_resumed_authentication_survives_the_second_identity_check(self):
        """The normal form-entry check must accept the same sign-in page as draft resumption."""
        url = "https://company.wd5.myworkdayjobs.com/Careers/job/City/Engineer_123456"
        source = dict(public_source(), url=url, captured_at=now())
        form = {"native": True, "url": url + "/apply/autofillWithResume", "title": "Sign In", "text": "Create Account",
                "elements": [{"id": "password", "secure": True, "enabled": True, "web_content": True}]}
        session = Mock()
        session.call.return_value = form
        for evidence in ({"checked_form_url": form["url"]}, {"native": True, "has_open_draft": True}):
            resumed = native_form_entry(session, evidence)
            self.assertEqual(validate_public_page(source, resumed, listing=False)["url"], form["url"])
            for change in ({"url": form["url"].replace("123456", "987654")}, {"elements": []}, {"resumed_form": False}):
                with self.assertRaisesRegex(ValueError, "does not identify"):
                    validate_public_page(source, {**resumed, **change}, listing=False)

    def test_resume_reuses_only_recent_complete_requirements_and_checks_the_live_form_again(self):
        """Resuming a populated form neither reloads it nor mixes applicant answers into listing evidence."""
        source = dict(public_source(), captured_at=now())
        form = {"url": "https://careers.example.test/application/101", "title": "Analyst at Example Co",
                "text": "Private draft answer", "native": True}
        app, browser = Mock(), Mock()
        app._session.return_value = browser
        browser.manager.native = True
        browser.call.return_value = form
        job = {"public_source": source, "live_url": form["url"], "attachments": [{"kind": "resume"}]}
        session, listing = recently_screened_draft(app, job)
        self.assertNotIn(form["text"], str(listing))
        self.assertEqual(native_form_entry(session, listing), dict(form, resumed_form=True))
        browser.call.return_value = dict(form, url="https://careers.example.test/application/other")
        with self.assertRaisesRegex(ValueError, "changed"):
            native_form_entry(session, listing)
        browser.reset_mock()
        job["public_source"] = dict(source, captured_at="2020-01-01T00:00:00+00:00")
        self.assertIsNone(recently_screened_draft(app, job))
        browser.call.assert_not_called()

    def test_capture_hash_and_complete_fields_are_required(self):
        for change in ({"complete": False}, {"company": ""}, {"description_sha256": "wrong"},
                       {"url": "file:///tmp/example"}, {"url": "https://name:secret@example.test"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_public_source({**public_source(), **change})

    def test_canonical_tracking_aliases_preserve_the_same_listing(self):
        source = public_source()
        page = {"url": source["url"] + "&utm_source=example", "title": "Example Co — Analyst", "text": source["description"]}
        self.assertEqual(validate_public_page(source, page)["public_source"], source)

    def test_unknown_or_truncated_public_requirements_stop(self):
        source = public_source()
        page = {"url": source["url"], "title": "Example Co — Analyst", "text": source["description"]}
        for change in ({"truncated": True}, {"text_truncated": True}, {"text": "Example Co Analyst"},
                       {"text": source["description"] + " This position is no longer available."}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_public_page(source, {**page, **change})

    def test_application_destination_can_change_but_identity_must_remain(self):
        source = public_source()
        form = {"url": "https://application.example.test/123", "title": "Apply", "text": "Example Co Analyst application"}
        self.assertEqual(validate_public_page(source, form, listing=False)["url"], form["url"])
        with self.assertRaises(ValueError):
            validate_public_page(source, {**form, "text": "Another Company Analyst"}, listing=False)

    def test_analyzer_cannot_replace_the_saved_employer_or_role(self):
        validate_public_analysis(public_source(), {"company": " example   co ", "title": "ANALYST"})
        for change in ({"company": "Another Company"}, {"title": "Senior Analyst"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_public_analysis(public_source(), {"company": "Example Co", "title": "Analyst", **change})


URL = "https://careers.example.com/jobs/123/software-engineer"


class DiscoveryValidationTests(unittest.TestCase):
    def test_canonical_aliases_keep_job_identifiers(self):
        greenhouse = "https://boards.greenhouse.io/acme/jobs/123?gh_jid=123&gh_src=mail&utm_campaign=x#app"
        self.assertEqual(canonical_url(greenhouse), "https://job-boards.greenhouse.io/acme/jobs/123")
        self.assertEqual(canonical_url("https://jobs.lever.co/acme/abc/apply?source=linkedin"), "https://jobs.lever.co/acme/abc")
        self.assertNotEqual(canonical_url(URL + "?job=1"), canonical_url(URL + "?job=2"))
