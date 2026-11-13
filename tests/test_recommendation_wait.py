"""Search tools return actual completed jobs and cooperate with pause without reacquiring scans."""

from types import SimpleNamespace
import threading
import time
from unittest.mock import Mock, patch

from chat_helpers import ChatFixture
from applydesk.recommendation_wait import find_jobs, wait_for_jobs
from applydesk.recommendations import normalize_job
from test_recommendations import listing


class RecommendationWaitTests(ChatFixture):
    def search_source(self, report):
        """Keep source state synthetic while exercising actual query-aware tool routing."""
        self.w.orchestrator.active = {"message": {"id": "request", "role": "user", "text": "Find hardware jobs."}}
        source = SimpleNamespace(preferences=lambda: {"handshake": {"enabled": True}}, state=lambda: {"handshake": report},
                                 active={}, begin=Mock(return_value={"run_id": "next"}), close=lambda: None, cancel_all=Mock())
        self.w.recommendations = source
        return source

    def test_keywords_reach_browser_search_without_becoming_a_recommendation_scan(self):
        """Changing keywords creates a new search within the existing user request."""
        source = self.search_source({"run_id": "old", "status": "complete", "request_id": "request", "query": "software"})
        with patch("applydesk.recommendation_wait.wait_for_jobs", return_value={"pending": False}):
            find_jobs(self.w.orchestrator, "handshake", " hardware   new grad ")
        source.begin.assert_called_once()
        self.assertEqual(source.begin.call_args.args[0], {"platform": "handshake", "limit": 10, "query": "hardware new grad"})

    def test_identical_completed_query_returns_existing_result_without_another_browser(self):
        """Repeated tool calls do not revisit completed cards or reopen the source."""
        report = {"run_id": "old", "status": "complete", "request_id": "request", "query": "hardware", "screened": True}
        source = self.search_source(report)
        for _ in range(3):
            result = find_jobs(self.w.orchestrator, "handshake", " hardware ")
            self.assertEqual(result["run_id"], "old")
        source.begin.assert_not_called()

    def test_another_query_cannot_overlap_the_current_source_session(self):
        """A new keyword request waits for or cancels the active browser instead of spawning another."""
        source = self.search_source({"run_id": "old", "status": "scanning", "request_id": "request", "query": "hardware"})
        source.active = {"handshake": {}}
        with self.assertRaisesRegex(ValueError, "Another search"):
            find_jobs(self.w.orchestrator, "handshake", "silicon")
        source.begin.assert_not_called()

    def test_invalid_keywords_never_open_a_source(self):
        """Queries remain a bounded plain text input to the platform search field."""
        source = self.search_source({})
        for query in (None, [], "x" * 301):
            with self.subTest(query=query), self.assertRaises(ValueError):
                find_jobs(self.w.orchestrator, "handshake", query)
        source.begin.assert_not_called()

    def test_completed_run_returns_listing_ids_titles_and_company(self):
        """The master can continue directly from the result instead of asking for copied listings."""
        report = {"platform": "handshake", "run_id": "scan", "status": "complete", "job_ids": [self.job["id"]], "screened": True}
        self.w.recommendations = SimpleNamespace(preferences=lambda: {"handshake": {"enabled": True}},
                                               state=lambda: {"handshake": report}, active={}, close=lambda: None, cancel_all=Mock())
        result = wait_for_jobs(self.w.orchestrator, "handshake", "scan", timeout=0)
        self.assertFalse(result["pending"])
        self.assertEqual(result["jobs"][0]["company"], "Example")
        self.assertEqual(result["jobs"][0]["id"], self.job["id"])

    def test_existing_scan_is_joined_and_sign_in_is_a_real_visible_blocker(self):
        """Polling an active scan never opens another browser or restarts a failed listing."""
        self.w.orchestrator.active = {"message": {"id": "request", "role": "user", "text": "Find hardware jobs."}}
        report = {"platform": "handshake", "run_id": "scan", "status": "waiting_signin", "added_job_ids": [], "request_id": "request"}
        source = SimpleNamespace(preferences=lambda: {"handshake": {"enabled": True}}, state=lambda: {"handshake": report},
                                 active={"handshake": {}}, begin=Mock(), close=lambda: None, cancel_all=Mock())
        self.w.recommendations = source
        result = find_jobs(self.w.orchestrator, "handshake")
        self.assertTrue(result["pending"])
        self.assertEqual(result["status"], "waiting_signin")
        source.begin.assert_not_called()

    def test_pause_interrupts_wait_before_a_result_can_be_returned(self):
        """Cancellation remains responsive while an external website is loading."""
        self.w.orchestrator.cancelled.set()
        with self.assertRaises(InterruptedError):
            wait_for_jobs(self.w.orchestrator, "handshake", "scan", timeout=0)

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
