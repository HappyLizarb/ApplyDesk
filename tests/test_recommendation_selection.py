"""Recommendation matching precedes tracker writes and uses the real current request."""

import json
import threading
import time
from unittest.mock import Mock, patch

from chat_helpers import ChatFixture
from test_orchestrator import response
from test_recommendations import listing
from applydesk.recommendation_selection import request_context, selected_records
from applydesk.recommendation_wait import find_jobs, wait_for_jobs
from applydesk.recommendations import normalize_job


class RecommendationSelectionTests(ChatFixture):
    def setUp(self):
        """Mixed fictional listings expose publishing/software confusion without live browsing."""
        super().setUp()
        self.engine = self.w.orchestrator
        self.engine.active = {"message": {"id": "publishing-request", "role": "user", "text": "Find new-grad publishing jobs in Boston."}}
        self.w.store.put("recommendation_preference", "handshake", {"enabled": True})
        self.publishing = listing("101", "handshake", title="Publishing Editor", location="Boston",
                                text="Design and verify digital publishing circuits in Boston. New graduates welcome. " * 4)
        self.software = listing("102", "handshake", title="Software Engineer", company="Publishing Company", location="Boston",
                                text="Build frontend web applications and maintain cloud services. We license publishing software. " * 4)
        self.session = Mock()
        self.session.call.side_effect = lambda method, params=None: {"source": "handshake", "status": "ready", "jobs": [self.publishing, self.software]}
        self.browser = patch("applydesk.recommendations.BrowserSession", return_value=self.session).start()
        self.calls = []
        self.agent = patch.object(self.engine, "run_agent", side_effect=self.model).start()

    def tearDown(self):
        """Stop synthetic browsers before closing their temporary workspace."""
        super().tearDown()
        patch.stopall()

    def model(self, role, prompt, inputs, context):
        """Provider fixtures choose actual duties, never a company's incidental sector name."""
        payload = json.loads(inputs[0]["content"])
        self.calls.append(payload)
        self.assertEqual(role, "application")
        self.assertEqual(self.w.store.all("job"), [self.w.store.get("job", self.job["id"])])
        return "review", response(json.dumps({"matches": [{"job_id": "101", "quote": "Design and verify digital publishing circuits in Boston."}]}))

    def staged(self, request=None):
        """Wait for capture ownership to release without performing any relevance selection."""
        started = self.w.recommendations.begin({"platform": "handshake"}, request=request)
        deadline = time.monotonic() + 2
        while self.w.recommendations.active and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertFalse(self.w.recommendations.active)
        return started["run_id"]

    def test_search_adds_only_relevant_duties_after_the_model_sees_the_actual_request(self):
        """Publishing-company software roles do not enter the tracker during a publishing search."""
        self.engine.history.add("user", "Previously I wanted software roles.")
        result = find_jobs(self.engine, "handshake")
        self.assertEqual((result["matched_count"], result["rejected_count"], result["added_count"]), (1, 1, 1))
        self.assertEqual([job["title"] for job in result["jobs"]], ["Publishing Editor"])
        self.assertEqual(self.calls[0]["request"], self.engine.active["message"]["text"])
        self.assertIn("Previously I wanted software roles.", self.calls[0]["prior_requests"])
        self.assertEqual(len(self.calls[0]["jobs"]), 2)
        self.assertNotIn("email", self.calls[0])
        repeated = wait_for_jobs(self.engine, "handshake", result["run_id"])
        self.assertEqual(repeated["job_ids"], result["job_ids"])
        self.assertEqual(self.agent.call_count, 1)
        self.assertEqual(self.browser.call_count, 1)

    def test_connect_only_never_scans_or_adds_jobs(self):
        """The platform button establishes a session without filling the application list."""
        self.w.recommendations.begin({"platform": "handshake", "connect_only": True})
        deadline = time.monotonic() + 2
        while self.w.recommendations.active and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(self.w.recommendations.state()["handshake"]["connected"])
        self.session.call.assert_called_once_with("source_login")
        self.assertEqual(len(self.w.store.all("job")), 1)
        self.agent.assert_not_called()

    def test_unscoped_scan_captures_but_never_adds_unfiltered_rows(self):
        """A direct scan cannot bypass the chat request even when every capture is complete."""
        self.staged()
        self.assertEqual(len(self.w.store.all("job")), 1)
        stage = self.w.store.get("recommendation_candidates", "handshake")
        self.assertEqual(len(stage["records"]), 2)
        self.agent.assert_not_called()

    def test_no_matches_adds_nothing_and_does_not_retry_the_same_scan(self):
        """Zero relevance matches is a complete result rather than an invitation to pad the list."""
        self.agent.side_effect = None
        self.agent.return_value = "review", response('{"matches":[]}')
        result = find_jobs(self.engine, "handshake")
        self.assertEqual((result["status"], result["rejected_count"], result["jobs"]), ("no_results", 2, []))
        self.assertEqual(len(self.w.store.all("job")), 1)
        find_jobs(self.engine, "handshake")
        self.assertEqual(self.agent.call_count, 1)
        self.assertEqual(self.browser.call_count, 1)

    def test_blocked_capture_is_not_reported_as_zero_matching_jobs(self):
        """Unreadable listings are a source error, not evidence that the requested roles do not exist."""
        self.session.call.side_effect = lambda method, params=None: {
            "source": "handshake", "status": "blocked", "jobs": [],
            "message": "The job description could not be read.", "issues": ["The job description could not be read."]}
        result = find_jobs(self.engine, "handshake")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["message"], "The job description could not be read.")
        self.assertNotIn("matching job", result["message"])
        self.agent.assert_not_called()

    def test_invalid_selection_and_source_quotes_add_nothing(self):
        """The relevance model cannot inject a source or replace the supporting job facts."""
        records = [normalize_job("handshake", self.publishing)]
        for matches in ([{"job_id": "999", "quote": "Publishing Editor"}],
                        [{"job_id": "101", "quote": "Invented circuit design role"}],
                        [{"job_id": "101", "quote": "Publishing Editor"}] * 2):
            with self.subTest(matches=matches), self.assertRaises(ValueError):
                selected_records({"matches": matches}, records)
        self.agent.side_effect = None
        self.agent.return_value = "review", response('{"matches":[{"job_id":"999","quote":"Publishing Editor"}]}')
        with self.assertRaises(ValueError):
            find_jobs(self.engine, "handshake")
        result = find_jobs(self.engine, "handshake")
        self.assertTrue(result["selection_error"])
        self.assertEqual(result["jobs"], [])
        self.assertEqual(self.agent.call_count, 1)
        self.assertEqual(len(self.w.store.all("job")), 1)

    def test_pause_or_replaced_message_discards_late_model_matches(self):
        """A newer user turn can never inherit a previous request's import side effects."""
        original = self.model
        def changed(role, prompt, inputs, context):
            result = original(role, prompt, inputs, context)
            self.engine.active = {"message": {"id": "different", "role": "user", "text": "Do something else."}}
            return result
        self.agent.side_effect = changed
        with self.assertRaises(InterruptedError):
            find_jobs(self.engine, "handshake")
        self.assertEqual(len(self.w.store.all("job")), 1)
        self.engine.cancelled.set()
        with self.assertRaises(InterruptedError):
            wait_for_jobs(self.engine, "handshake", "unused")

    def test_account_change_or_replaced_scan_prevents_selection(self):
        """Saved candidate IDs cannot be replayed under a different account or generation."""
        run_id = self.staged(request_context(self.engine))
        with patch.object(self.engine.accounts, "state", return_value={"active": "other-account"}):
            with self.assertRaises(ValueError):
                wait_for_jobs(self.engine, "handshake", run_id)
        self.w.store.put("recommendation", "handshake", {"run_id": "new", "status": "complete"})
        with self.assertRaisesRegex(ValueError, "replaced"):
            wait_for_jobs(self.engine, "handshake", run_id)
        self.agent.assert_not_called()
        self.assertEqual(len(self.w.store.all("job")), 1)

    def test_provider_switch_and_same_source_reconnection_invalidate_staged_matches(self):
        """A provider label alone cannot identify the credential generation that began a search."""
        self.w.store.put("agent_connection", "main", {"source": "openai_api",
                         "api_keys_saved": {"openai": True, "anthropic": True}})
        context = request_context(self.engine)
        self.assertEqual((context["account_id"], context["connection_source"]), ("openai_api", "openai_api"))
        run_id = self.staged(context)
        for source in ("anthropic_api", "openai_api"):
            self.engine.accounts.select_source(source)
            self.engine.generation = self.engine.accounts.generation
            with self.subTest(source=source), self.assertRaises(ValueError):
                wait_for_jobs(self.engine, "handshake", run_id)
        self.agent.assert_not_called()
        self.assertEqual(len(self.w.store.all("job")), 1)

    def test_pending_signin_waits_then_filters_exactly_once(self):
        """Retrieving completed results follows the same selection gate as the initial call."""
        context = request_context(self.engine)
        self.w.store.put("recommendation", "handshake", {"run_id": "signin", "status": "waiting_signin", "request_id": context["message_id"]})
        pending = wait_for_jobs(self.engine, "handshake", "signin", timeout=0)
        self.assertTrue(pending["pending"])
        self.agent.assert_not_called()
        records = [normalize_job("handshake", item) for item in (self.publishing, self.software)]
        self.w.store.put("recommendation_candidates", "handshake", {"run_id": "signin", "request": context, "records": records})
        self.w.store.put("recommendation", "handshake", {"run_id": "signin", "status": "complete", "request_id": context["message_id"], "candidate_count": 2})
        result = wait_for_jobs(self.engine, "handshake", "signin", timeout=0)
        self.assertEqual(result["added_count"], 1)
        self.assertEqual(self.agent.call_count, 1)

    def test_removed_listing_stays_removed_after_passing_relevance(self):
        """Relevance cannot restore a previously excluded source identity."""
        removed = self.w.store.add_job(self.publishing["url"])
        self.w.remove_jobs({"ids": [removed["id"]]})
        self.agent.side_effect = None
        self.agent.return_value = "review", response('{"matches":[{"job_id":"101","quote":"Publishing Editor"}]}')
        result = find_jobs(self.engine, "handshake")
        self.assertEqual((result["added_count"], result["excluded_count"], result["jobs"]), (0, 1, []))
        self.assertTrue(self.w.store.get("job", removed["id"])["removed_at"])
