"""Private generic authentication uses only fictional forms, accounts and in-memory secrets."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from applydesk.agent_browser_auth import AuthenticationInputRequired, _controls, _purpose, authenticate
from applydesk.browser_bridge import BrowserError
from applydesk.employer_accounts import EmployerAccounts
from applydesk.storage import Store
from test_employer_accounts import MemorySecrets


def account_page(mode="sign_in", action="https://careers.example.test/team/login"):
    """Model-chosen IDs identify real form membership without a recognized submit label."""
    form = {"form_id": "account", "form_action": action, "form_method": "get",
            "authentication_form": True, "visible": True, "disabled": False}
    fields = [{**form, "id": "user", "tag": "input", "type": "email", "label": "Account", "value": ""},
              {**form, "id": "secret", "tag": "input", "type": "password", "label": "Access code", "value": None}]
    if mode == "create_account":
        fields.append({**fields[1], "id": "confirm", "label": "Repeat access code"})
    fields.append({**form, "id": "send", "tag": "div", "role": "button", "type": "", "label": "Submit",
                   "form_context": "Create account" if mode == "create_account" else "Sign in"})
    return {"snapshot_id": "observed-auth", "url": "https://careers.example.test/team/job/engineer",
            "text": "Circuit Example applicant account", "elements": fields}


def auth_decision(mode="sign_in"):
    """The model chooses controls and purpose, never username or password values."""
    return {"action": "authenticate", "mode": mode, "username_id": "user", "submit_id": "send",
            "password_ids": ["secret", "confirm"] if mode == "create_account" else ["secret"]}


class AgentAuthenticationTests(unittest.TestCase):
    """Exercise the backend entry point and its actual durable account registry."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="applydesk-agent-auth-")
        self.store = Store(Path(self.temporary.name))
        self.secrets = MemorySecrets()
        self.workspace = SimpleNamespace(store=self.store, secrets=self.secrets,
            profile=lambda: {"identity": {"email": "avery@example.test"}})
        job = self.store.add_job("https://careers.example.test/team/job/engineer")
        self.job = self.store.job(job["id"], company="Circuit Example", title="Engineer")
        self.accounts = EmployerAccounts(self.workspace)
        self.session = Mock()
        self.session.call.return_value = {"snapshot_id": "after", "elements": [], "text": "Signed in"}

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def save_account(self, realm="https://careers.example.test/team/"):
        """Save one explicit employer account and pin its established authentication realm."""
        self.accounts.save({"id": self.job["id"], "account": "avery@example.test",
                            "account_origin": "https://careers.example.test", "password": "private-test-password"})
        record = self.store.all("employer_account")[0]
        self.store.put("employer_account", record["id"], {**record, "realm": realm})
        self.secrets.reads.clear()

    def test_saved_account_accepts_generic_submit_and_spa_action(self):
        self.save_account()
        page = account_page(action="https://careers.example.test/team/job/engineer?source=board")
        result = authenticate(self.workspace, self.session, self.job, page, auth_decision())
        method, private = self.session.call.call_args.args
        self.assertEqual(method, "agent_authenticate")
        self.assertEqual(private["password"], "private-test-password")
        self.assertEqual(private["submit_id"], "send")
        self.assertEqual(private["intent"], "sign_in")
        self.assertEqual(result["text"], "Signed in")
        self.assertNotIn("private-test-password", json.dumps(self.store.all("employer_account")))

    def test_signup_reserves_once_before_browser_dispatch(self):
        mode = "create_account"
        authenticate(self.workspace, self.session, self.job, account_page(mode), auth_decision(mode))
        record = self.store.all("employer_account")[0]
        self.assertEqual(record["status"], "creation_attempted")
        self.assertEqual(record["account"], "avery@example.test")
        self.assertEqual(len(self.secrets.writes), 1)
        with self.assertRaises(AuthenticationInputRequired):
            authenticate(self.workspace, self.session, self.job, account_page(mode), auth_decision(mode))
        self.assertEqual(self.session.call.call_count, 1)

    def test_signup_timeout_never_repeats_uncertain_creation(self):
        self.session.call.side_effect = TimeoutError("private-test-error")
        with self.assertRaises(BrowserError):
            authenticate(self.workspace, self.session, self.job, account_page("create_account"), auth_decision("create_account"))
        with self.assertRaises(AuthenticationInputRequired):
            authenticate(self.workspace, self.session, self.job, account_page("create_account"), auth_decision("create_account"))
        self.assertEqual(self.session.call.call_count, 1)

    def test_errors_and_page_echoes_do_not_return_passwords(self):
        self.save_account()
        self.session.call.return_value = {"text": "echo private-test-password", "elements": [{"value": "private-test-password"}]}
        result = authenticate(self.workspace, self.session, self.job, account_page(), auth_decision())
        self.assertNotIn("private-test-password", json.dumps(result))
        self.session.call.side_effect = BrowserError("private-test-password was rejected")
        with self.assertRaises(BrowserError) as error:
            authenticate(self.workspace, self.session, self.job, account_page(), auth_decision())
        self.assertNotIn("private-test-password", str(error.exception))

    def test_origin_realm_and_identity_changes_never_read_secrets(self):
        self.save_account()
        variations = []
        for url in ("http://careers.example.test/team/login", "https://other.example.test/team/login",
                    "https://careers.example.test/teammate/login", "https://careers.example.test/team/%2e%2e/other/login"):
            page = account_page(action=url)
            page["url"] = url
            variations.append(page)
        page = account_page(); page["text"] = "Another Example account"; variations.append(page)
        for page in variations:
            with self.subTest(url=page["url"]), self.assertRaises(AuthenticationInputRequired):
                authenticate(self.workspace, self.session, self.job, page, auth_decision())
        self.assertEqual(self.secrets.reads, [])
        self.session.call.assert_not_called()

    def test_account_terms_are_included_but_password_reset_needs_input(self):
        self.save_account()
        for text in ("By creating this account, you agree to the terms.", "I accept the candidate agreement."):
            page = account_page(); page["elements"][-1]["form_context"] = text
            with self.subTest(text=text):
                _purpose(page, "sign_in", *_controls(page, auth_decision()))
        page = account_page(); page["elements"][-1]["label"] = "Reset password"
        with self.assertRaises(AuthenticationInputRequired):
            authenticate(self.workspace, self.session, self.job, page, auth_decision())
        self.assertEqual(self.secrets.reads, [])

    def test_same_form_membership_and_complete_passwords_are_required(self):
        self.save_account()
        for field, key, value in ((0, "form_id", "other"), (1, "type", "text"), (1, "readonly", True),
                                  (2, "authentication_form", False), (2, "form_action", "https://other.example.test/login")):
            page = account_page(); page["elements"][field][key] = value
            with self.subTest(field=field, key=key), self.assertRaises(ValueError):
                authenticate(self.workspace, self.session, self.job, page, auth_decision())
        page = account_page(); page["elements"].append({**page["elements"][1], "id": "extra-secret"})
        with self.assertRaises(ValueError):
            authenticate(self.workspace, self.session, self.job, page, auth_decision())
        self.assertEqual(self.secrets.reads, [])

    def test_unrecognized_creation_is_not_a_password_reset(self):
        page = account_page("create_account"); page["elements"][-1]["form_context"] = "Choose a new password"
        with self.assertRaises(ValueError):
            authenticate(self.workspace, self.session, self.job, page, auth_decision("create_account"))
        self.assertEqual(self.secrets.writes, [])

    def test_model_supplied_password_and_malformed_ids_are_rejected(self):
        for changes in ({"password": "invented"}, {"password_ids": ["secret", "secret"]},
                        {"submit_id": []}, {"username_id": "missing"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                authenticate(self.workspace, self.session, self.job, account_page(), {**auth_decision(), **changes})
        self.session.call.assert_not_called()

    def test_missing_saved_account_does_not_silently_create_one(self):
        with self.assertRaises(AuthenticationInputRequired):
            authenticate(self.workspace, self.session, self.job, account_page(), auth_decision())
        self.assertEqual(self.store.all("employer_account"), [])
        self.assertEqual(self.secrets.writes, [])

    def test_other_required_fields_must_be_completed_first(self):
        page = account_page("create_account")
        page["elements"].append({**page["elements"][0], "id": "name", "type": "text", "required": True})
        with self.assertRaises(ValueError):
            authenticate(self.workspace, self.session, self.job, page, auth_decision("create_account"))
        self.assertEqual(self.secrets.writes, [])

    def test_saved_tenant_query_must_match_current_page_and_form(self):
        self.save_account("https://careers.example.test/career?company=circuit")
        page = account_page(action="https://careers.example.test/career?company=circuit&view=login")
        page["url"] = page["elements"][0]["form_action"]
        authenticate(self.workspace, self.session, self.job, page, auth_decision())
        page["url"] = "https://careers.example.test/career?company=other"
        self.secrets.reads.clear()
        with self.assertRaises(AuthenticationInputRequired):
            authenticate(self.workspace, self.session, self.job, page, auth_decision())
        self.assertEqual(self.secrets.reads, [])


if __name__ == "__main__":
    unittest.main()
