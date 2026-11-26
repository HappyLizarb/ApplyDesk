"""Watching an application restores its existing window without changing application authority."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from chat_helpers import ChatFixture
from applydesk.application_session import ApplicationSession
from applydesk.application_window import show_application, status
from applydesk.browser_bridge import BrowserError, BrowserSession
from applydesk.orchestrator_http import orchestrator_action


class ApplicationWindowTests(ChatFixture):
    def setUp(self):
        """Use a synthetic running browser; no Chrome or employer site is contacted."""
        super().setUp()
        self.browser = SimpleNamespace(ended=False, process=Mock(poll=lambda: None), close=Mock(), interrupt=Mock())
        self.session = SimpleNamespace(browser=self.browser, call=Mock(return_value={"visible": True}), close=Mock())
        self.w.sessions[self.job["id"]] = self.session
        self.w.orchestrator.applications.run = {"status": "running", "current_job": self.job["id"]}
        manager = self.w.application_browser
        manager.owner = {"pid": 101, "window_id": 202}
        manager.browser, manager.workspace_id = self.browser, "fixture-browser"
        manager.jobs, manager.last_job_id = [self.job["id"]], self.job["id"]

    def test_chrome_approval_requires_explicit_idle_opt_in(self):
        """Only this user's boolean consent enables approval; active work cannot change it."""
        self.assertIsNot(self.w.settings().get("chrome_auto_approval"), True)
        self.w.orchestrator.active = None
        result = orchestrator_action(self.w, "chrome-approval", {"enabled": True})
        self.assertEqual(result, {"enabled": True})
        self.assertIs(self.w.settings()["chrome_auto_approval"], True)
        for data in [{}, {"enabled": "yes"}, {"enabled": True, "extra": True}]:
            with self.assertRaises(ValueError):
                orchestrator_action(self.w, "chrome-approval", data)
        self.w.orchestrator.active = {"message": {"id": "running"}}
        with self.assertRaisesRegex(ValueError, "Stop the agent"):
            orchestrator_action(self.w, "chrome-approval", {"enabled": False})
        self.w.orchestrator.active = None
        orchestrator_action(self.w, "chrome-approval", {"enabled": False})
        self.assertIs(self.w.settings()["chrome_auto_approval"], False)

    def test_chrome_approval_cannot_extend_to_source_or_research_browsers(self):
        """The permission stays scoped to an existing visible application workspace."""
        for options in [{}, {"source": "linkedin"}, {"research": True}]:
            with self.assertRaises(BrowserError):
                BrowserSession(self.w.root, auto_approve_chrome=True, **options)
        browser = BrowserSession(self.w.root, application_workspace=True, existing_chrome=True,
                                 auto_approve_chrome=True)
        self.assertIs(browser.options["autoApproveChrome"], True)

    def test_status_keeps_the_last_live_application_after_a_run_finishes(self):
        """Retained completed or blocked tabs remain inspectable without resuming work."""
        expected = {"available": True, "job_id": self.job["id"], "workspace_id": "fixture-browser"}
        self.assertEqual(status(self.w), expected)
        for change in [{"status": "complete"}, {"current_job": "other"}]:
            original = self.w.orchestrator.applications.run.copy()
            self.w.orchestrator.applications.run.update(change)
            self.assertEqual(status(self.w), expected)
            self.w.orchestrator.applications.run = original
        owner = self.w.application_browser.owner
        self.w.application_browser.owner = None
        self.assertFalse(status(self.w)["available"])
        self.w.application_browser.owner = owner
        self.browser.ended = True
        self.assertFalse(status(self.w)["available"])

    def test_show_queues_only_a_presentation_command_on_the_existing_session(self):
        """The UI gets an immediate task ID without opening another browser or changing budgets."""
        before = self.w.orchestrator.applications.run.copy()
        with patch.object(self.w, "start", return_value={"task": "show-application"}) as start:
            result = orchestrator_action(self.w, "show-application", {})
        self.assertEqual(result, {"task": "show-application"})
        start.assert_called_once_with("show-application", self.session.call, "show", {})
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

    def test_window_bounds_are_validated_before_queuing_presentation(self):
        """The layout control accepts geometry only, including monitors left of the primary display."""
        bounds = {"left": -1200, "top": 30, "width": 1000, "height": 800}
        with patch.object(self.w, "start", return_value={"task": "show-application"}) as start:
            orchestrator_action(self.w, "show-application", {"bounds": bounds})
            start.assert_called_once_with("show-application", self.session.call, "show", {"bounds": bounds})
        invalid = [None, {}, {**bounds, "width": True}, {**bounds, "height": 0}, {**bounds, "left": 999999},
                   {**bounds, "width": 500.1}, {**bounds, "url": "https://example.test"}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                orchestrator_action(self.w, "show-application", {"bounds": value})

    def test_pause_prevents_the_queued_show_command_from_resuming_work(self):
        """The existing cancellation boundary applies even to presentation-only browser commands."""
        engine = SimpleNamespace(check_active=Mock(side_effect=InterruptedError("Paused")))
        browser = Mock()
        with patch("applydesk.application_session.BrowserSession", return_value=browser):
            session = ApplicationSession(engine, self.w.root)
        with self.assertRaises(InterruptedError):
            session.call("show")
        browser.call.assert_not_called()
