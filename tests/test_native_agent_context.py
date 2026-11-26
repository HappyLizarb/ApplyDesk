"""Fresh native decisions receive credential metadata, never historical job blockers as account context."""

import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from applydesk.account_scope import employer_key, is_registration_link
from applydesk.native_agent import NativeAgent
from applydesk.native_application_io import NativeInputRequired, _account_scope
from applydesk.storage import Store


class NativeAgentContextTests(unittest.TestCase):
    def setUp(self):
        """Use fictional local records and forbid both browser activity and secret reads."""
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = Store(Path(self.folder.name))
        self.addCleanup(self.store.close)
        job = self.store.add_job("https://employer.example.test/job/one")
        self.job = self.store.job(job["id"], company="Example Employer", title="Engineer",
            account="legacy@example.test", account_origin="https://legacy.example.test", has_password=False,
            history=[{"message": "historical-privacy-block"}], exceptions=["historical-exception"],
            details="historical-description", final_audit={"reason": "historical-audit"},
            native_review={"reason": "historical-review"}, native_evidence=[{"text": "historical-evidence"}])
        self.account_id = employer_key(self.job)
        self.store.put("employer_account", self.account_id, {"id": self.account_id, "account": "current@example.test",
            "origin": "https://employer.example.test", "has_password": True, "secret_key": "private-secret-id"})
        self.store.put("application_run", "run", {"instruction": "Existing authorization", "limit": 5})
        self.store.put("submission_attempt", "attempt", {"job_id": "another-job", "status": "submitted"})
        self.workspace = SimpleNamespace(store=self.store, docs=Mock(), secrets=Mock())
        self.agent = NativeAgent(SimpleNamespace(w=self.workspace, client=Mock()), Mock(), self.job,
            {"confirmed": True, "facts": []}, {})
        self.agent.page = {"text": "Current ordinary application privacy notice", "elements": []}

    def test_context_omits_historical_job_material_and_preserves_credential_status(self):
        """Account lookup must not smuggle previous blockers back into the current visible-page decision."""
        before = {kind: copy.deepcopy(self.store.all(kind)) for kind in
            ("job", "employer_account", "application_run", "submission_attempt")}
        context = self.agent._context()
        self.assertEqual(context["account"], {"account": "current@example.test",
            "account_origin": "https://employer.example.test", "has_password": True,
            "employer_account_id": self.account_id})
        self.assertNotIn("historical-", json.dumps(context))
        self.assertNotIn("private-secret-id", json.dumps(context))
        self.assertEqual(context["page"]["text"], "Current ordinary application privacy notice")
        self.assertEqual(before, {kind: self.store.all(kind) for kind in before})
        self.workspace.secrets.get.assert_not_called()
        self.agent.session.call.assert_not_called()

    def test_legacy_account_row_is_filtered_too(self):
        """The no-shared-account fallback still preserves explicit false credential status without old history."""
        self.store.delete("employer_account", self.account_id)
        context = self.agent._context()
        self.assertEqual(context["account"], {"account": "legacy@example.test",
            "account_origin": "https://legacy.example.test", "has_password": False})
        self.assertNotIn("historical-", json.dumps(context))
        self.assertEqual(self.store.get("job", self.job["id"])["history"], [{"message": "historical-privacy-block"}])

    def test_registration_hint_only_for_owner_with_an_open_reservation(self):
        """Distinguish a reserved password from an existing account on the native registration page."""
        account = self.store.get("employer_account", self.account_id)
        for status, owner, pending in (("reserved", self.job["id"], True), ("reserved", "other", False),
                                       ("creation_attempted", self.job["id"], False)):
            with self.subTest(status=status, owner=owner):
                self.store.put("employer_account", self.account_id, {**account, "status": status, "owner_job_id": owner})
                self.assertEqual(self.agent._context()["account"].get("registration_pending", False), pending)
        self.workspace.secrets.get.assert_not_called()

    def test_registration_navigation_is_separate_from_signup_submission(self):
        """Only an observed same-tenant signup link beside the login controls gets navigation authority."""
        url = "https://careers.example.test/careers?company=acme"
        link = {"id": "signup", "role": "AXLink", "enabled": True, "web_content": True,
                "label": "Create an account", "url": url + "&login_ns=register"}
        fields = [{"secure": True, "enabled": True}, {"role": "AXButton", "label": "Sign In", "enabled": True}]
        page = {"url": url, "elements": [link, *fields]}
        operation = {"action": "click", "element_id": "signup"}
        self.assertTrue(is_registration_link(page, operation))
        for change in ({"role": "AXButton"}, {"url": url + "&login_ns=submit"},
                       {"url": url.replace("acme", "other") + "&login_ns=register"},
                       {"url": "https://other.example.test/register?company=acme"}, {"enabled": False}):
            self.assertFalse(is_registration_link({**page, "elements": [{**link, **change}, *fields]}, operation))
        self.assertFalse(is_registration_link({**page, "elements": [link]}, operation))

    def test_signup_on_a_separate_path_requires_the_exact_observed_route(self):
        """A sign-in grant cannot authorize another employer, an unobserved form or login on a signup page."""
        origin = "https://employer.example.test"
        realm = origin + "/careers?company=acme"
        signup = origin + "/register?company=acme&job=12"
        self.store.put("employer_account", self.account_id, {"id": self.account_id, "realm": realm, "origin": origin})
        page = {"url": signup, "text": self.job["company"]}
        mode = {"mode": "create_account"}
        self.assertEqual(_account_scope(self.workspace, self.job, page, mode, (realm, signup)), (origin, realm))
        for form, action, route in ((signup, mode, None), (signup, {"mode": "sign_in"}, (realm, signup)),
                                    (signup, mode, (realm.replace("acme", "other"), signup)),
                                    (signup + "3", mode, (realm, signup)),
                                    ("https://elsewhere.test/register", mode, (realm, signup))):
            with self.assertRaises(NativeInputRequired):
                _account_scope(self.workspace, self.job, {**page, "url": form}, action, route)
