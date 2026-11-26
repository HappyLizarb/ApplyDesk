"""Recommendation workers use synthetic browser responses, never accounts or providers."""

import copy
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from applydesk.recommendation_source import normalize_job
from applydesk.workspace import Workspace
from recommendation_helpers import listing, response


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
        self.browser.assert_called_once_with(self.w.root / "sources/linkedin", source="linkedin", headless=False)
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

    def test_invalid_employer_links_stay_incomplete_without_navigation(self):
        # Broken or private destinations are retained as issues, never navigation targets.
        for url in ({"url": "malformed"}, "http://127.0.0.1/jobs/123", "https://app.joinhandshake.com/job-search/123"):
            with self.subTest(url=url):
                source = normalize_job("linkedin", listing(employer_url=url))
                self.assertFalse(source["complete"])
                self.assertEqual(source["employer_url"], "")
                self.assertTrue(source["issues"])
        self.browser.assert_not_called()

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
        self.browser.assert_not_called()

    def test_restart_marks_legacy_incomplete_rows_for_review_without_reading_source(self):
        # Previously imported placeholders need a clear status even before Refresh is used.
        source = normalize_job("linkedin", listing(text="", complete=False))
        job = self.w.store.add_job(source["source_url"])
        original = self.w.store.job(job["id"], recommendation_source=source)
        self.w.close()
        self.w = Workspace(self.w.root, secrets=self.secrets)
        migrated = self.w.store.get("job", job["id"])
        self.assertEqual(migrated["status"], "needs_review")
        self.assertEqual(migrated["recommendation_source"], original["recommendation_source"])
        self.browser.assert_not_called()
        self.secrets.get.assert_not_called()

    def test_quit_interrupts_a_listing_read_before_waiting_for_the_chat_worker(self):
        """A pending browser read must not hold Quit until the external site times out."""
        entered, cancelled = threading.Event(), threading.Event()
        session = Mock()
        session.cancel.side_effect = cancelled.set
        def pending_read(method, params):
            entered.set()
            cancelled.wait(2)
            return {"source": "linkedin", "status": "blocked"}
        session.call.side_effect = pending_read
        def read_listing():
            with self.assertRaises(ValueError):
                self.w.recommendations.read_job(normalize_job("linkedin", listing()))
        with patch("applydesk.recommendations.BrowserSession", return_value=session):
            worker = threading.Thread(target=read_listing)
            self.w.orchestrator.thread = worker
            worker.start()
            self.assertTrue(entered.wait(1))
            started = time.monotonic()
            self.w.orchestrator.close()
        self.assertTrue(cancelled.is_set())
        self.assertFalse(worker.is_alive())
        self.assertLess(time.monotonic() - started, 1)


if __name__ == "__main__":
    unittest.main()
