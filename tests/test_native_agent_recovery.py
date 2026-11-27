"""Recoverable native failures remain diagnosable without persisting private exception details."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from applydesk.native_browser import BrowserError
from applydesk.native_agent import NativeAgent
from applydesk.native_application_io import NativeInputRequired, _enter_credentials
from applydesk.native_review import review_corrections, review_surface
from applydesk.native_window_recovery import observe_application
from applydesk.storage import Store


class NativeAgentRecoveryTests(unittest.TestCase):
    def test_email_suggestion_dismissal_requires_the_original_secure_field(self):
        """Only the same verified form can receive the private password after Escape."""
        user = {"id": "email", "focused": True, "value": "", "enabled": True}
        password = {"id": "pw", "secure": True, "enabled": True}
        page = {"pid": 1, "window_id": 2, "active_tab_id": "job", "generation": 3,
                "snapshot_id": "start", "url": "https://ats.test/login", "elements": [user, password]}
        covered = {**page, "snapshot_id": "typed", "elements": [{**user, "value": "user@example.test"}]}
        for changed in ({}, {"active_tab_id": "other"}, {"elements": []}):
            session = Mock()
            session.call.side_effect = [covered, {**page, **changed}, page]
            if changed:
                with self.assertRaises(ValueError):
                    _enter_credentials(session, page, user, "user@example.test", [password], "test-secret")
            else:
                _enter_credentials(session, page, user, "user@example.test", [password], "test-secret")
            sent = [call.args[1] for call in session.call.call_args_list]
            self.assertEqual(sent[1]["key"], "escape")
            self.assertEqual(any(action.get("private") for action in sent), not changed)

    def test_lost_read_reattaches_only_the_verified_job_without_replaying_input(self):
        """A vanished handle can be rebound to the matching requisition for a fresh self-review."""
        source = "https://example.wd5.myworkdayjobs.com/Careers/job/City/Engineer_JR54321"
        job = {"live_url": source + "/apply", "public_source": {"url": source}}
        lost = BrowserError("The application window is unavailable. Open the application again.")
        session = Mock()
        for form, valid in (({"url": job["live_url"]}, True), ({"url": source.replace("54321", "54322") + "/apply"}, False), ({}, False)):
            session.reset_mock()
            session.call.side_effect = [lost, form]
            if valid:
                self.assertTrue(observe_application(session, job)["recovered_draft"])
            else:
                with self.assertRaises(BrowserError):
                    observe_application(session, job)
            self.assertEqual([call.args[0] for call in session.call.call_args_list], ["native_observe", "resume_visible"])
        session.call.side_effect = BrowserError("Enable Screen Recording")
        session.reset_mock()
        with self.assertRaisesRegex(BrowserError, "Screen Recording"):
            observe_application(session, job, screenshot=True)
        self.assertEqual(session.call.call_count, 1)

    def test_prefilled_username_survives_secure_password_entry(self):
        """A matching account can proceed directly to its secure password box."""
        username = {"id": "email", "value": "candidate@example.test", "enabled": True}
        password = {"id": "pw", "enabled": True, "secure": True}
        view = {"snapshot_id": "login", "url": "https://example.test/login", "elements": [username, password]}
        session = Mock()
        session.call.return_value = view
        _enter_credentials(session, view, username, username["value"], [password], "test-password")
        self.assertEqual(session.call.call_count, 1)
        self.assertEqual(session.call.call_args.args[1]["element_id"], "pw")
        self.assertTrue(session.call.call_args.args[1]["private"])
        session.call.side_effect = RuntimeError("test-password")
        with self.assertRaisesRegex(ValueError, "secure password field") as failure:
            _enter_credentials(session, view, username, username["value"], [password], "test-password")
        self.assertNotIn("test-password", str(failure.exception))

    def test_review_feedback_distinguishes_missing_personal_evidence_from_unseen_wording(self):
        """Already displayed values need a fact citation while a misquote needs the employer's wording."""
        review = {"answers": [{"quote": "Alex Test", "fact_ids": []}], "summaries": [{"quote": "Name: Alex Tests"}]}
        fixes = review_corrections(review, {"facts": []}, {"text": "Name: Alex Test"}, [])
        self.assertEqual(fixes[0]["displayed_quote"], "Alex Test")
        self.assertIn("evidence_issue", fixes[0])
        self.assertIn("similar_displayed_lines", fixes[1])

    def test_review_tolerates_browser_chrome_changes_but_preserves_employer_content(self):
        """A new native snapshot can represent the same form in the same tab."""
        browser = {"id": "toolbar", "role": "AXButton", "label": "Browser settings", "web_content": False}
        before = {"pid": 12, "window_id": 7, "active_tab_id": "job", "generation": 1,
                  "elements": [{"id": "submit", "role": "AXButton", "label": "Submit", "enabled": True}]}
        same = {**before, "snapshot_id": "new", "elements": [browser, *before["elements"]]}
        self.assertEqual(review_surface(before), review_surface(same))
        for changed in ({"active_tab_id": "other"}, {"native_modal": True}, {"text": "A new required declaration"},
                        {"elements": [{**before["elements"][0], "clipped": True}]}):
            self.assertNotEqual(review_surface(before), review_surface({**same, **changed}))

    def setUp(self):
        """Use a real temporary job history and a fake browser; no model or native input runs."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = Store(Path(folder.name))
        self.addCleanup(self.store.close)
        self.job = self.store.add_job("https://employer.example.test/job/one")
        workspace = SimpleNamespace(store=self.store, docs=Mock())
        self.client = Mock()
        self.client.ask.side_effect = [{"action": "upload", "purpose": "Attach the selected document"},
                                       {"action": "review", "purpose": "Check the completed application"}]
        session = Mock()
        session.call.return_value = {"snapshot_id": "view", "url": self.job["url"], "elements": []}
        self.agent = NativeAgent(SimpleNamespace(w=workspace, client=lambda _: self.client, checkpoint=Mock()),
                                 session, self.job, {"confirmed": True, "facts": []}, {})

    def recovered_history(self, error):
        """Exercise the production catch/reobserve loop and its persistent event, then finish normally."""
        with patch("applydesk.native_agent.upload", side_effect=error), patch.object(self.agent, "_review", return_value=self.job):
            self.assertEqual(self.agent.run(), self.job)
        self.assertEqual(self.client.ask.call_count, 2)
        self.assertEqual(self.agent.session.call.call_count, 2)
        self.assertTrue(any("error" in step for step in self.agent.history))
        return self.store.get("job", self.job["id"])["history"]

    def test_upload_failure_is_saved_and_recovery_continues(self):
        """The exact known local rejection survives in history between the attempted and next steps."""
        history = self.recovered_history(ValueError("Choose the observed native upload control."))
        self.assertEqual([item["message"] for item in history[-3:]], ["Attach the selected document",
            "Recovering from document upload: Choose the observed native upload control.", "Check the completed application"])

    def test_notice_preserves_saved_draft_address(self):
        """The resume address remains the form even while a separate notice is on screen."""
        self.agent._observe()
        self.agent.session.call.return_value = {"url": "https://example.test/privacy", "reading_notice": True,
                                               "snapshot_id": "notice", "elements": []}
        self.agent._observe()
        self.assertEqual(self.store.get("job", self.job["id"])["live_url"], self.job["url"])
        self.assertTrue(self.agent._context()["page"]["reading_notice"])

    def test_arbitrary_exception_details_never_enter_job_history(self):
        """A matching prefix cannot smuggle a password, path or field value through the diagnostic allowlist."""
        private = "password=secret-fixture /Users/private/resume.pdf field-value-fixture"
        error = ValueError("Choose the observed native upload control. " + private)
        history = self.recovered_history(error)
        self.assertIn("Recovering from document upload: The step did not pass validation.",
                      [item["message"] for item in history])
        self.assertNotIn(private, json.dumps(self.store.get("job", self.job["id"])))
        self.assertNotIn(private, json.dumps(self.agent.history))

    def test_repeated_text_reads_get_one_form_image_and_never_capture_a_picker(self):
        """Only a web form can trigger the visual fallback, and an unchanged view eventually stops."""
        for modal in (False, True):
            self.agent.session.call.reset_mock()
            self.client.ask.side_effect = [{"action": "observe"}] * 4
            self.agent.session.call.return_value["native_modal"] = modal
            with self.assertRaisesRegex(ValueError, "could not make progress"):
                self.agent.run()
            images = [call for call in self.agent.session.call.call_args_list if call.args[1]["screenshot"]]
            self.assertEqual(len(images), 0 if modal else 1)

    def test_native_error_code_preserves_reason_without_raw_details(self):
        """The native stale-view category is useful even if its raw exception contains private data."""
        error = BrowserError("private-path-fixture and value-fixture")
        error.code = "stale_observation"
        history = self.recovered_history(error)
        self.assertIn("Recovering from document upload: The visible page changed before the action.",
                      [item["message"] for item in history])
        self.assertNotIn("private-path-fixture", json.dumps(history))

    def test_unknown_native_failure_uses_only_a_fixed_category(self):
        """Unrecognized browser errors do not expose arbitrary worker text through persistence."""
        history = self.recovered_history(BrowserError("private-path-fixture"))
        self.assertIn("Recovering from document upload: The browser action could not be completed.",
                      [item["message"] for item in history])
        self.assertNotIn("private-path-fixture", json.dumps(history))

    def test_required_input_is_not_logged_as_a_retry(self):
        """An actual user-input blocker still exits immediately instead of entering recovery."""
        with patch("applydesk.native_agent.upload", side_effect=NativeInputRequired("Required fact missing.")):
            with self.assertRaisesRegex(NativeInputRequired, "Required fact missing"):
                self.agent.run()
        history = self.store.get("job", self.job["id"])["history"]
        self.assertFalse(any(item["message"].startswith("Recovering from") for item in history))
        self.assertEqual(self.client.ask.call_count, 1)

    def test_partial_batch_identifies_unexecuted_followup(self):
        """Checking an agreement and advancing are separate clicks on potentially different views."""
        self.agent.page = self.agent.session.call.return_value
        first = {"action": "click", "element_id": "one"}
        self.agent._actions([first, {**first, "element_id": "two"}])
        self.assertEqual(self.agent.session.call.call_count, 1)
        self.assertEqual(self.agent.history[-1]["last_element"], "one")
        self.assertEqual(self.agent.history[-1]["completed_operations"], 1)
        self.assertEqual(self.agent.history[-1]["requested_operations"], 2)

    def test_agreement_click_without_readback_reports_dispatch_once(self):
        """A blank accessibility value is neither a rejection nor proof of acceptance."""
        control = {"id": "notice", "role": "AXCheckBox", "label": "I agree to the employer terms",
                   "enabled": True, "value": "", "selected": False}
        page = {"snapshot_id": "view", "url": self.job["url"], "elements": [control]}
        self.agent.page = self.agent.session.call.return_value = page
        self.agent._actions([{"action": "click", "element_id": "notice", "intent": "agreement"}])
        self.assertEqual(self.agent.session.call.call_count, 1)
        self.assertEqual(self.agent.history[-1]["checked_state"], "not exposed")
        self.assertEqual(self.agent.history[-1]["choice_clicked"], "notice")
        self.assertEqual(control["value"], "")

    def test_new_image_on_last_observation_is_available_for_next_decision(self):
        """An unchanged page can still return new visual evidence on the last normal retry."""
        page = dict(self.agent.session.call.return_value)
        image = {"data": "fixture"}
        self.agent.session.call.side_effect = lambda method, params: {**page, "screenshot": image if params.get("screenshot") else None}
        actions = ["act", "act", "act", "screenshot", "review"]
        self.client.ask.side_effect = [{"action": action} for action in actions]
        with patch.object(self.agent, "_review", return_value=self.job):
            self.assertEqual(self.agent.run(), self.job)
        self.assertEqual(self.client.ask.call_args.kwargs["images"], [image])
