"""Watching an application restores its existing window without changing application authority."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from chat_helpers import ChatFixture
from applydesk.application_session import ApplicationSession
from applydesk.application_window import show_application, status
from applydesk.orchestrator_http import orchestrator_action


class ApplicationWindowTests(ChatFixture):
    def setUp(self):
        """Use a synthetic running browser; no Chrome or employer site is contacted."""
        super().setUp()
        self.browser = SimpleNamespace(ended=False, process=Mock(poll=lambda: None), options={"headless": False})
        self.session = SimpleNamespace(browser=self.browser, call=Mock(return_value={"visible": True}), close=Mock())
        self.w.sessions[self.job["id"]] = self.session
        self.w.orchestrator.applications.run = {"status": "running", "current_job": self.job["id"]}

    def test_status_reports_only_the_live_current_application(self):
        """Drafts, old sessions, closed windows, and background research do not appear as watchable."""
        self.assertEqual(status(self.w), {"available": True, "job_id": self.job["id"]})
        for change in [{"status": "complete"}, {"current_job": "other"}]:
            original = self.w.orchestrator.applications.run.copy()
            self.w.orchestrator.applications.run.update(change)
            self.assertEqual(status(self.w), {"available": False, "job_id": None})
            self.w.orchestrator.applications.run = original
        self.browser.options["headless"] = True
        self.assertFalse(status(self.w)["available"])
        self.browser.options["headless"] = False
        self.browser.ended = True
        self.assertFalse(status(self.w)["available"])

    def test_show_queues_only_a_presentation_command_on_the_existing_session(self):
        """The UI gets an immediate task ID without opening another browser or changing budgets."""
        before = self.w.orchestrator.applications.run.copy()
        with patch.object(self.w, "start", return_value={"task": "show-application"}) as start:
            result = orchestrator_action(self.w, "show-application", {})
        self.assertEqual(result, {"task": "show-application"})
        start.assert_called_once_with("show-application", self.session.call, "show")
        self.assertEqual(self.w.orchestrator.applications.run, before)
        self.assertIs(self.w.sessions[self.job["id"]], self.session)
        self.session.call.assert_not_called()

    def test_duplicate_show_reuses_pending_task_and_rejects_extra_arguments(self):
        """Repeated clicks cannot queue new browser work or inject a URL, job, or manual handoff."""
        self.w.tasks["show-application"] = {"status": "running"}
        with patch.object(self.w, "start") as start:
            self.assertEqual(show_application(self.w), {"task": "show-application"})
            start.assert_not_called()
        for data in [{"url": "https://example.test"}, {"manual": True}, {"job_id": "other"}]:
            with self.assertRaises(ValueError):
                orchestrator_action(self.w, "show-application", data)

    def test_stopped_or_exited_application_does_not_open_a_replacement(self):
        """Unavailable windows return a clear error and keep the browser registry intact."""
        self.browser.process.poll = lambda: 1
        with patch.object(self.w, "start") as start:
            with self.assertRaisesRegex(ValueError, "no application window"):
                show_application(self.w)
            start.assert_not_called()
        self.assertIs(self.w.sessions[self.job["id"]], self.session)

    def test_pause_prevents_the_queued_show_command_from_resuming_work(self):
        """The existing cancellation boundary applies even to presentation-only browser commands."""
        engine = SimpleNamespace(check_active=Mock(side_effect=InterruptedError("Paused")))
        browser = Mock()
        with patch("applydesk.application_session.BrowserSession", return_value=browser):
            session = ApplicationSession(engine, self.w.root)
        with self.assertRaises(InterruptedError):
            session.call("show")
        browser.call.assert_not_called()
