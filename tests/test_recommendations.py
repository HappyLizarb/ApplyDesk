"""Historical platform records remain readable when new applications use the native browser."""

from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from applydesk.recommendation_source import employer_target, process_issues, validate_source
from applydesk.server import AppServer
from applydesk.workspace import Workspace
from recommendation_helpers import saved_listing


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        """Use an isolated workspace and fail if historical records cause a secret lookup."""
        self.temp = tempfile.TemporaryDirectory()
        self.secrets = Mock()
        self.secrets.get.side_effect = AssertionError("No secret reads during historical report handling")
        self.w = Workspace(Path(self.temp.name), secrets=self.secrets)

    def tearDown(self):
        """Close SQLite before discarding the temporary workspace."""
        self.w.close()
        self.temp.cleanup()

    def test_restart_marks_inflight_report_interrupted_without_browser(self):
        # An old operation is never mistaken for a live watcher after restart.
        self.w.store.put("recommendation", "linkedin", {"status": "waiting_signin", "run_id": "old"})
        self.w.close()
        self.w = Workspace(Path(self.temp.name), secrets=self.secrets)
        self.assertEqual(self.w.state()["recommendations"]["linkedin"]["status"], "interrupted")

    def test_historic_false_zero_is_displayed_as_capture_error_without_rewriting_records(self):
        """Only contradictory blocked zero messages change in memory; saved reports remain untouched."""
        for issues, expected in ((["", "The description could not be read."], "The description could not be read."),
                                 ([], "The listings could not be read. Try the search again.")):
            saved = {"status": "blocked", "message": "Found 0 matching job(s).", "issues": issues}
            self.w.store.put("recommendation", "linkedin", saved)
            self.assertEqual(self.w.recommendations.state()["linkedin"]["message"], expected)
            self.assertEqual(self.w.store.get("recommendation", "linkedin"), saved)
        for status, message in (("blocked", "Source needs sign-in."), ("no_results", "Found 0 matching job(s).")):
            saved = {"status": status, "message": message, "issues": ["Earlier issue"]}
            self.w.store.put("recommendation", "linkedin", saved)
            self.assertEqual(self.w.recommendations.state()["linkedin"], saved)

    def test_restart_marks_legacy_incomplete_rows_for_review_without_reading_source(self):
        # Previously imported placeholders need a clear status even before Refresh is used.
        source = saved_listing(text="", complete=False)
        job = self.w.store.add_job(source["source_url"])
        original = self.w.store.job(job["id"], recommendation_source=source)
        self.w.close()
        self.w = Workspace(self.w.root, secrets=self.secrets)
        migrated = self.w.store.get("job", job["id"])
        self.assertEqual(migrated["status"], "needs_review")
        self.assertEqual(migrated["recommendation_source"], original["recommendation_source"])
        self.secrets.get.assert_not_called()

    def test_defaults_ignore_historic_reports_and_nonboolean_records(self):
        # A prior successful import cannot silently opt either platform into refreshes.
        self.w.store.put("recommendation", "linkedin", {"status": "complete", "added_count": 5})
        self.w.store.put("recommendation_preference", "handshake", {"enabled": 1})
        expected = {"handshake": {"enabled": False}, "linkedin": {"enabled": False}}
        self.assertEqual(self.w.state()["recommendation_preferences"], expected)
        self.secrets.get.assert_not_called()


class PreferenceRouteTests(unittest.TestCase):
    def setUp(self):
        # The only real network traffic in these tests is the app's loopback HTTP API.
        self.temp = tempfile.TemporaryDirectory(prefix="applydesk-preference-route-")
        self.w = Workspace(Path(self.temp.name), secrets=Mock())
        self.server = AppServer(self.w)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        # Stop request handlers before closing their SQLite workspace.
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.w.close()
        self.temp.cleanup()

    def post(self, data, headers=None):
        # Call the actual route so the busy exception cannot skip origin/session checks.
        request_headers = {"Cookie": "applydesk=" + self.server.token, "Origin": self.server.origin,
                           "X-ApplyDesk": "1", "Content-Type": "application/json", **(headers or {})}
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request("POST", "/api/recommendations/preference", json.dumps(data), request_headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_retired_route_cannot_change_saved_preferences(self):
        # Historical preferences survive, but old UI requests cannot enable platform sessions.
        data = {"platform": "handshake", "enabled": True}
        self.assertEqual(self.post(data)[0], 400)
        self.assertEqual(self.w.state()["recommendation_preferences"]["handshake"], {"enabled": False})
        self.assertEqual(self.w.tasks, {})

    def test_retired_route_stays_disabled_during_work_and_requires_authorization(self):
        # Removed controls cannot mutate a workspace through an old or unauthorized request.
        self.w.tasks["test"] = {"id": "test", "status": "running"}
        data = {"platform": "handshake", "enabled": False}
        self.assertEqual(self.post(data)[0], 400)
        self.assertEqual(self.post({**data, "enabled": True})[0], 400)
        self.assertEqual(self.post({**data, "enabled": 0})[0], 400)
        for headers in ({"Origin": "https://attacker.example"}, {"Cookie": ""}, {"X-ApplyDesk": ""}):
            with self.subTest(headers=headers):
                self.assertEqual(self.post(data, headers)[0], 403)


def analysis():
    """Only this complete, supported baseline may reach an independent audit."""
    return {"company": "Example Labs", "title": "Software Engineer", "requirements_complete": True,
            "required": [{"text": "Python required", "evidence_ids": ["skills.0"], "met": True}],
            "process_complete": True, "identity_consistent": True, "employment_type_consistent": True,
            "consistency_issues": [], "process_restrictions": [], "passed": True, "issues": []}


class RecommendationChecksTests(unittest.TestCase):
    """Check integrity and eligibility of stored captures before preparing an application."""

    def test_incomplete_capture_and_modified_bytes_fail(self):
        for changes in ({"complete": False}, {"description": "Replacement job"}, {"issues": ["Show more is collapsed"]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_source({**saved_listing(), **changes})

    def test_bad_platform_source_cannot_claim_a_valid_capture(self):
        for url in ("https://evil.test/jobs/12345", "https://linkedin.com.evil.test/jobs/12345", "http://linkedin.com/jobs/12345"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_source({**saved_listing(), "source_url": url})

    def test_unknown_process_and_contradictory_type_cannot_pass(self):
        self.assertEqual(process_issues(analysis(), saved_listing()), [])
        for key in ("process_complete", "identity_consistent", "employment_type_consistent", "process_restrictions", "consistency_issues"):
            result = analysis()
            result.pop(key)
            with self.subTest(key=key):
                self.assertTrue(process_issues(result, saved_listing()))
        result = {**analysis(), "employment_type_consistent": False,
                  "consistency_issues": ["The description is a permanent role but the card says internship."]}
        self.assertTrue(process_issues(result, saved_listing()))

    def test_verified_listing_can_reach_form_before_account_setup(self):
        """A pending sign-in does not disqualify a role; explicit posting restrictions still apply."""
        review = {**analysis(), "process_complete": False}
        self.assertEqual(process_issues(review, saved_listing(), check_on_form=True), [])
        self.assertTrue(process_issues({**review, "identity_consistent": False}, saved_listing(), check_on_form=True))
        broken = {**review, "process_restrictions": [{"kind": "unknown"}]}
        self.assertTrue(process_issues(broken, saved_listing(), check_on_form=True))

    def test_application_cap_and_availability_always_require_manual_evidence(self):
        rules = [("application_limit", "Apply to at most two roles across our affiliates."),
                 ("availability_statement", "State your availability dates on your resume."),
                 ("other", "Complete the prerequisite assessment before applying.")]
        for kind, quote in rules:
            result = analysis()
            result["process_restrictions"] = [{"kind": kind, "text": quote, "quote": quote, "met": True, "count": 0}]
            captured = saved_listing(text="Fixture role. " + quote)
            with self.subTest(kind=kind):
                self.assertTrue(any("Manual verification" in issue for issue in process_issues(result, captured)))

    def test_observed_application_limit_blocks_without_a_second_agent(self):
        """Process restrictions remain enforced locally after the single extraction."""
        quote = "Apply to at most two roles across all affiliates."
        captured = saved_listing(text="Fixture role. " + quote)
        result = analysis()
        result["process_restrictions"] = [{"kind": "application_limit", "text": quote, "quote": quote}]
        self.assertTrue(any("Manual verification" in issue for issue in process_issues(result, captured)))

    def test_missing_or_platform_employer_link_pauses(self):
        for target in (None, "", "https://www.linkedin.com/jobs/view/12345", "http://employer.example.test/jobs/12345"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                employer_target({**saved_listing(), "employer_url": target})
