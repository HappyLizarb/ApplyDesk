"""Recommendation workers use synthetic browser responses, never accounts or providers."""

import copy
import hashlib
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import ANY, Mock, patch

from applydesk.recommendations import normalize_job, source_identity
from applydesk.workspace import Workspace
from recommendation_helpers import accept_captured, listing, response


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        # Startup and recommendation operations must not touch Keychain or a model.
        self.temp = tempfile.TemporaryDirectory(prefix="applydesk-recommendations-test-")
        self.secrets = Mock()
        self.secrets.get.side_effect = AssertionError("No secret reads during recommendation imports")
        self.w = Workspace(Path(self.temp.name), secrets=self.secrets)
        self.w.client = Mock(side_effect=AssertionError("No model calls during recommendation imports"))
        self.session = Mock()
        self.session.call.side_effect = lambda method, params=None: response(jobs=[listing()])
        self.browser = patch("applydesk.recommendations.BrowserSession", return_value=self.session).start()

    def tearDown(self):
        # Cancellation is exercised before SQLite and the local temporary directory close.
        self.w.close()
        patch.stopall()
        self.temp.cleanup()

    def finished(self, platform="linkedin"):
        # Wait for worker ownership to release, with a short explicit test deadline.
        deadline = time.monotonic() + 3
        while platform in self.w.recommendations.active and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertNotIn(platform, self.w.recommendations.active)
        return self.w.recommendations.state()[platform]

    def test_invalid_requests_never_start_browser(self):
        # Boolean limits and source spoofing cannot reserve a browser profile.
        cases = [None, [], {}, {"platform": "other"}, {"platform": "linkedin", "limit": True},
                 {"platform": "linkedin", "limit": 0}, {"platform": "linkedin", "limit": 11}]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.w.recommendations.begin(data)
        self.browser.assert_not_called()
        self.secrets.get.assert_not_called()

    def test_query_is_kept_in_report_and_forwarded_to_visible_browser_search(self):
        """The worker scans the requested keyword results, not the default home recommendations."""
        self.w.recommendations.begin({"platform": "linkedin", "query": "hardware new grad", "limit": 2})
        report = self.finished()
        self.assertEqual(report["query"], "hardware new grad")
        self.session.call.assert_any_call("source_scan", {"limit": 2, "query": "hardware new grad"})

    def test_import_is_unverified_and_canonical_without_model_calls(self):
        # A read listing never implies applicant eligibility or document approval.
        self.session.call.side_effect = lambda method, params=None: response(jobs=[listing(url="https://www.linkedin.com/jobs/search-results/?currentJobId=123&keywords=private-filter")])
        started = self.w.recommendations.begin({"platform": "linkedin", "limit": 1})
        report = accept_captured(self)
        job = self.w.store.all("job")[0]
        self.assertEqual(report["run_id"], started["run_id"])
        self.assertEqual((report["status"], report["added_count"]), ("complete", 1))
        self.assertTrue(report["connected"])
        self.assertEqual(job["url"], "https://www.linkedin.com/jobs/view/123/")
        self.assertNotIn("private-filter", str(self.w.state()))
        self.assertEqual((job["status"], job["qualification_status"], job["resume_id"]), ("new", "unverified", None))
        source = job["recommendation_source"]
        self.assertEqual(source["description_sha256"], hashlib.sha256(source["description"].encode()).hexdigest())
        self.browser.assert_called_once_with(self.w.root / "sources/linkedin", source="linkedin")
        self.w.client.assert_not_called()
        self.secrets.get.assert_not_called()

    def test_failed_signin_is_not_shown_as_connected(self):
        # Enabling a source alone is not evidence of a readable signed-in feed.
        self.session.call.side_effect = lambda method, params=None: response("blocked")
        self.w.recommendations.begin({"platform": "linkedin"})
        self.assertFalse(self.finished()["connected"])
        self.session.call.assert_any_call("source_login")
        self.assertNotIn("source_scan", [call.args[0] for call in self.session.call.call_args_list])

    def test_waiting_does_not_reserve_workspace_task_and_cancel_is_generation_bound(self):
        # Signing in may wait while the independent workspace task slot remains available.
        entered = threading.Event()
        self.w.recommendations._publish = Mock(wraps=self.w.recommendations._publish)
        def wait_for_login(method, params=None):
            entered.set()
            return response("waiting_signin", message="Complete browser verification.")
        self.session.call.side_effect = wait_for_login
        started = self.w.recommendations.begin({"platform": "linkedin"})
        self.assertTrue(entered.wait(1))
        self.assertEqual(self.w.tasks, {})
        self.assertFalse(self.w.recommendations.state()["linkedin"]["connected"])
        with self.assertRaises(ValueError):
            self.w.recommendations.cancel({"platform": "linkedin", "run_id": "stale"})
        self.session.cancel.assert_not_called()
        completed = threading.Event()
        self.w.start("independent", completed.set)
        self.assertTrue(completed.wait(1))
        self.w.recommendations.cancel(started)
        self.assertEqual(self.finished()["status"], "cancelled")
        self.session.cancel.assert_called_once()
        self.w.recommendations._publish.assert_any_call("linkedin", ANY, status="waiting_signin", message="Complete browser verification.")
        self.assertEqual(self.w.store.all("job"), [])

    def test_cancel_during_scan_prevents_late_rows(self):
        # Even a successful response arriving after cancellation must be discarded.
        entered, release = threading.Event(), threading.Event()
        def scan(method, params=None):
            if method == "source_scan":
                entered.set()
                release.wait(2)
            return response(jobs=[listing()])
        self.session.call.side_effect = scan
        self.session.cancel.side_effect = release.set
        started = self.w.recommendations.begin({"platform": "linkedin"})
        self.assertTrue(entered.wait(1))
        self.w.recommendations.cancel(started)
        self.assertEqual(self.finished()["status"], "cancelled")
        self.assertEqual(self.w.store.all("job"), [])
        self.session.close.assert_called_once()

    def test_duplicates_preserve_existing_rows_and_do_not_merge_similar_titles(self):
        # Source IDs and employer URL aliases dedupe; title similarity does not.
        existing = self.w.store.add_job("https://www.linkedin.com/jobs/view/engineer-123/?trackingId=x")
        original = self.w.store.job(existing["id"], status="submitted", title="User title")
        employer = self.w.store.add_job("https://careers.example.test/jobs/456?utm_source=old")
        protected = [self.w.store.add_job(f"https://www.linkedin.com/jobs/view/{index}/") for index in (120, 121, 122, 124)]
        protected = [self.w.store.job(row["id"], **changes) for row, changes in zip(protected,
                     ({"status": "preparing"}, {"resume_id": "existing-doc"}, {"account": "saved@example.test"}, {}))]
        self.w.tasks["prepare:" + protected[-1]["id"]] = {"status": "running"}
        jobs = [listing(), listing("456", platform="handshake"), listing("789")]
        self.session.call.side_effect = lambda method, params=None: response(jobs=[jobs[0], jobs[2], *[listing(str(index)) for index in (120, 121, 122, 124)]])
        self.w.recommendations.begin({"platform": "linkedin", "limit": 6})
        self.assertEqual(accept_captured(self)["added_count"], 1)
        self.session.call.side_effect = lambda method, params=None: response(platform="handshake", jobs=[jobs[1]])
        self.w.recommendations.begin({"platform": "handshake"})
        self.assertEqual(accept_captured(self, "handshake")["added_count"], 0)
        self.assertEqual(len(self.w.store.all("job")), 7)
        self.assertTrue(all(self.w.store.get("job", row["id"]) == row for row in protected))
        self.assertEqual(self.w.store.get("job", existing["id"]), original)
        self.assertEqual(self.w.store.get("job", employer["id"]), employer)

    def test_unprepared_manual_platform_row_gains_scoped_provenance(self):
        # Upgrade a matching manual row instead of leaving a generic platform-read path.
        existing = self.w.store.add_job("https://www.linkedin.com/jobs/search-results/?currentJobId=123&keywords=private")
        self.w.recommendations.begin({"platform": "linkedin"})
        report = accept_captured(self)
        self.assertEqual((report["added_count"], report["updated_count"]), (0, 1))
        rows = self.w.store.all("job")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], existing["id"])
        self.assertEqual(rows[0]["url"], "https://www.linkedin.com/jobs/view/123/")
        self.assertEqual(rows[0]["recommendation_source"]["job_id"], "123")
        self.assertEqual(rows[0]["qualification_status"], "unverified")
        self.assertEqual(rows[0]["status"], "new")
        self.assertIsNone(rows[0]["resume_id"])

    def test_invalid_record_cannot_drop_valid_jobs_or_invent_source_identity(self):
        # Import complete jobs while malformed and incomplete candidates remain report issues.
        bad = listing("456", url="https://www.linkedin.com.attacker.test/jobs/view/456/")
        self.session.call.side_effect = lambda method, params=None: response(jobs=[listing(), bad, listing("789", title=42)])
        self.w.recommendations.begin({"platform": "linkedin"})
        report = accept_captured(self)
        self.assertEqual((report["status"], report["added_count"]), ("blocked", 1))
        self.assertTrue(report["issues"])
        rows = self.w.store.all("job")
        self.assertEqual({row["recommendation_source"]["job_id"] for row in rows}, {"123"})
        self.assertTrue(any("789" in issue for issue in report["issues"]))
        for url in ("http://www.linkedin.com/jobs/view/123/", "https://www.linkedin.com/jobs/view/456/", "https://app.joinhandshake.com/stu/jobs/123"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                source_identity("linkedin", url, "123")

    def test_incomplete_candidates_remain_report_issues_without_new_tracker_rows(self):
        # A valid source link is insufficient: company, title and complete text must be captured.
        for index, status in enumerate(("ready", "blocked")):
            candidate = listing(str(123 + index), title="", company="", text="", employer_url="", complete=False)
            self.session.call.side_effect = lambda method, params=None: response(status, jobs=[candidate]) if method == "source_scan" else response()
            self.w.recommendations.begin({"platform": "linkedin"})
            report = accept_captured(self)
            self.assertEqual((report["status"], report["added_count"], report["updated_count"]), ("blocked", 0, 0))
            self.assertTrue(any(candidate["id"] in issue for issue in report["issues"]))
            self.assertEqual(self.w.store.all("job"), [])

    def test_complete_description_without_employer_link_remains_unprepared(self):
        # Missing application navigation is separate from a complete source description.
        self.session.call.side_effect = lambda method, params=None: response(jobs=[listing(employer_url="")], warnings=["Nonessential network request blocked"])
        self.w.recommendations.begin({"platform": "linkedin"})
        self.assertEqual(accept_captured(self)["status"], "complete")
        job = self.w.store.all("job")[0]
        self.assertTrue(job["recommendation_source"]["complete"])
        self.assertEqual(job["recommendation_source"]["issues"], [])
        self.assertIn("Employer link not available", job["exceptions"][0])
        self.assertEqual(job["status"], "new")

    def test_restart_marks_inflight_report_interrupted_without_browser(self):
        # An old operation is never mistaken for a live watcher after restart.
        self.w.store.put("recommendation", "linkedin", {"status": "waiting_signin", "run_id": "old"})
        self.w.close()
        self.w = Workspace(Path(self.temp.name), secrets=self.secrets)
        self.assertEqual(self.w.state()["recommendations"]["linkedin"]["status"], "interrupted")
        self.assertEqual(self.w.recommendations.active, {})
        self.browser.assert_not_called()

    def test_read_job_recovers_unknown_metadata_and_accepts_changed_description(self):
        # Incomplete provenance can be refreshed, but successful source reads must be complete.
        prior = normalize_job("linkedin", listing(title="", company="", text="", complete=False))
        original = copy.deepcopy(prior)
        self.session.call.return_value = response(job=listing(text="Updated required Python and testing experience. " * 6))
        self.session.call.side_effect = None
        fresh = self.w.recommendations.read_job(prior)
        self.assertTrue(fresh["complete"])
        self.assertNotEqual(fresh["description_sha256"], prior["description_sha256"])
        self.assertEqual(prior, original)
        self.session.call.assert_called_once_with("source_job", {"url": prior["source_url"], "id": "123"})
        self.browser.assert_called_once_with(self.w.root / "sources/linkedin", source="linkedin")
        self.session.close.assert_called_once()

    def test_read_job_rejects_changed_known_identity_or_incomplete_fresh_read(self):
        # Same numeric ID cannot silently change known company/title or bypass completeness.
        prior = normalize_job("linkedin", listing())
        for changes in ({"company": "Different Company"}, {"title": "Different Role"}, {"complete": False}, {"id": "456", "url": "https://www.linkedin.com/jobs/view/456/"}):
            self.session.call.side_effect = None
            self.session.call.return_value = response(job=listing(**changes))
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.w.recommendations.read_job(prior)
            self.assertEqual(self.w.recommendations.active, {})
        self.assertEqual(self.session.close.call_count, 4)

    def test_tampered_capture_fails_before_browser_or_provider(self):
        # A digest alone cannot bind source metadata; the canonical prefix must still match.
        prior = normalize_job("linkedin", listing())
        for changes in ({"description_sha256": "bad"}, {"captured_at": "2099-01-01T00:00:00+00:00"},
                        {"captured_at": "2026-01-01"}, {"company": "Changed Company"}, {"job_id": "456"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.w.recommendations.read_job({**prior, **changes})
        self.browser.assert_not_called()
        self.w.client.assert_not_called()

    def test_platforms_run_independently_and_same_profile_cannot_be_reused(self):
        # Each source gets its own worker, but a fresh read cannot race its source profile.
        linkedin, handshake = Mock(), Mock()
        linkedin.call.return_value = response("waiting_signin")
        handshake.call.return_value = response(platform="handshake", jobs=[listing(platform="handshake")])
        self.browser.side_effect = lambda path, source: {"linkedin": linkedin, "handshake": handshake}[source]
        active = self.w.recommendations.begin({"platform": "linkedin"})
        with self.assertRaises(ValueError):
            self.w.recommendations.begin({"platform": "linkedin"})
        with self.assertRaises(ValueError):
            self.w.recommendations.read_job(normalize_job("linkedin", listing()))
        self.w.recommendations.begin({"platform": "handshake"})
        self.assertEqual(accept_captured(self, "handshake")["added_count"], 1)
        self.assertIn("linkedin", self.w.recommendations.active)
        self.w.recommendations.cancel(active)
        self.assertEqual(self.finished()["status"], "cancelled")
        self.assertEqual(self.browser.call_count, 2)

    def test_invalid_employer_links_stay_incomplete_without_navigation(self):
        # Broken or private destinations are retained as issues, never navigation targets.
        for url in ({"url": "malformed"}, "http://127.0.0.1/jobs/123", "https://app.joinhandshake.com/job-search/123"):
            with self.subTest(url=url):
                source = normalize_job("linkedin", listing(employer_url=url))
                self.assertFalse(source["complete"])
                self.assertEqual(source["employer_url"], "")
                self.assertTrue(source["issues"])
        self.browser.assert_not_called()

    def test_close_interrupts_watchers_before_database_close(self):
        # A late scan after shutdown cannot write into the closed database.
        entered, release = threading.Event(), threading.Event()
        def wait(method, params=None):
            entered.set()
            release.wait(2)
            return response(jobs=[listing()])
        self.session.call.side_effect = wait
        self.session.cancel.side_effect = release.set
        self.w.recommendations.begin({"platform": "linkedin"})
        self.assertTrue(entered.wait(1))
        self.w.recommendations.close()
        self.assertEqual(self.finished()["status"], "interrupted")
        self.assertEqual(self.w.store.all("job"), [])
        self.session.close.assert_called_once()
        with self.assertRaises(ValueError):
            self.w.recommendations.begin({"platform": "handshake"})


if __name__ == "__main__":
    unittest.main()
