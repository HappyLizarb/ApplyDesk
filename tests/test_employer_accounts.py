"""Employer login reuse and signup reservations use only fictional local records."""

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from applydesk.application_steps import authenticate
from applydesk.employer_accounts import EmployerAccounts
from applydesk.storage import Store


class MemorySecrets:
    """Keep test credentials in memory and expose reads for boundary assertions."""

    def __init__(self):
        self.values = {}
        self.reads = []
        self.writes = []
        self.fail_write = False

    def get(self, key):
        self.reads.append(key)
        return self.values.get(key)

    def set(self, key, value):
        if self.fail_write:
            raise RuntimeError("Synthetic Keychain failure")
        self.writes.append(key)
        self.values[key] = value


def authentication_page(action="login", realm="https://jobs.example.test/team/"):
    """Represent one employer's separate native authentication endpoint."""
    endpoint = realm + ("register" if action == "create_account" else "login")
    metadata = {"form_id": "auth", "form_action": endpoint, "form_method": "POST"}
    fields = [{**metadata, "field_id": "email", "label": "Email", "type": "email"},
              {**metadata, "field_id": "password", "label": "Password", "type": "password"}]
    button = {**metadata, "field_id": "submit-auth", "type": "submit", "auth_form": True, "action": action}
    return {"snapshot_id": "auth-page", "url": endpoint, "text": "Circuit Example applicant account",
            "fields": fields, "buttons": [button]}


class EmployerAccountTests(unittest.TestCase):
    """Multiple roles must share an account without widening its credential scope."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="applydesk-employer-test-")
        self.store = Store(Path(self.temporary.name))
        self.secrets = MemorySecrets()
        self.workspace = SimpleNamespace(store=self.store, secrets=self.secrets, lock=threading.RLock(),
                                         profile=lambda: {"identity": {"email": "avery@example.test"}})
        self.accounts = EmployerAccounts(self.workspace)
        self.origin = "https://jobs.example.test"
        self.realm = self.origin + "/team/"
        self.first = self.job("first")
        self.second = self.job("second", "  CIRCUIT   EXAMPLE  ")

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def job(self, role, company="Circuit Example"):
        """Create distinct job records with a known employer and no credentials."""
        record = self.store.add_job("https://jobs.example.test/team/" + role)
        return self.store.job(record["id"], company=company, title=role)

    def saved_account(self, job=None, **changes):
        """Use the same public account-save request that the application row uses."""
        data = {"id": (job or self.first)["id"], "account": "avery@example.test",
                "account_origin": self.origin, "password": "synthetic-password"}
        return self.accounts.save({**data, **changes})

    def test_two_roles_reuse_the_same_account_and_secret(self):
        first = self.accounts.credentials(self.first, self.origin, self.realm, "create_account")
        with self.assertRaises(ValueError):
            self.accounts.credentials(self.second, self.origin, self.realm, "login")
        self.saved_account(password="")
        second = self.accounts.credentials(self.second, self.origin, self.realm, "login")
        self.assertEqual(first, second)
        self.assertEqual(first[0], "avery@example.test")
        self.assertEqual(len(self.secrets.writes), 1)
        self.assertEqual(len(self.store.all("employer_account")), 1)

    def test_repeated_signup_is_blocked_for_current_and_other_roles(self):
        self.accounts.credentials(self.first, self.origin, self.realm, "create_account")
        for job in (self.first, self.second):
            with self.subTest(job=job["id"]), self.assertRaises(ValueError):
                self.accounts.credentials(job, self.origin, self.realm, "create_account")
        self.assertEqual(len(self.secrets.writes), 1)

    def test_reservation_survives_a_restart(self):
        self.accounts.credentials(self.first, self.origin, self.realm, "create_account")
        self.store.close()
        self.store = Store(Path(self.temporary.name))
        self.workspace.store = self.store
        resumed = EmployerAccounts(self.workspace)
        with self.assertRaises(ValueError):
            resumed.credentials(self.second, self.origin, self.realm, "create_account")
        self.assertEqual(len(self.secrets.writes), 1)

    def test_keychain_failure_does_not_allow_another_signup_attempt(self):
        self.secrets.fail_write = True
        with self.assertRaises(RuntimeError):
            self.accounts.credentials(self.first, self.origin, self.realm, "create_account")
        self.secrets.fail_write = False
        with self.assertRaises(ValueError):
            self.accounts.credentials(self.second, self.origin, self.realm, "create_account")
        self.assertEqual(len(self.store.all("employer_account")), 1)
        self.assertEqual(self.secrets.writes, [])

    def test_concurrent_signup_reserves_only_one_attempt(self):
        barrier = threading.Barrier(2)
        def attempt(job):
            """Both threads enter the public operation at the same time."""
            barrier.wait(timeout=5)
            try:
                self.accounts.credentials(job, self.origin, self.realm, "create_account")
                return "reserved"
            except ValueError:
                return "blocked"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, (self.first, self.second)))
        self.assertCountEqual(results, ["reserved", "blocked"])
        self.assertEqual(len(self.secrets.writes), 1)

    def test_different_tenant_and_origin_cannot_read_saved_password(self):
        self.accounts.credentials(self.first, self.origin, self.realm, "create_account")
        for origin, realm in ((self.origin, self.origin + "/other/"),
                              ("https://other.example.test", "https://other.example.test/team/")):
            self.secrets.reads.clear()
            with self.subTest(realm=realm), self.assertRaises(ValueError):
                self.accounts.credentials(self.second, origin, realm, "login")
            self.assertEqual(self.secrets.reads, [])

    def test_shared_host_does_not_prove_a_second_employer_account_is_safe(self):
        self.accounts.credentials(self.first, self.origin, self.realm, "create_account")
        other = self.job("other-role", "Another Example")
        with self.assertRaises(ValueError):
            self.accounts.credentials(other, self.origin, self.origin + "/another/", "create_account")
        self.assertEqual(len(self.store.all("employer_account")), 1)

    def test_explicitly_saved_distinct_employer_accounts_are_not_merged(self):
        self.saved_account()
        other = self.job("other-role", "Another Example")
        self.saved_account(other, account="another@example.test", password="synthetic-other")
        first = self.accounts.credentials(self.first, self.origin, self.realm, "login")
        second = self.accounts.credentials(other, self.origin, self.origin + "/another/", "login")
        self.assertEqual(first, ("avery@example.test", "synthetic-password"))
        self.assertEqual(second, ("another@example.test", "synthetic-other"))

    def test_alternate_company_name_on_claimed_realm_cannot_create_again(self):
        self.accounts.credentials(self.first, self.origin, self.realm, "create_account")
        alias = self.job("alias-role", "Circuit Example Inc.")
        with self.assertRaises(ValueError):
            self.accounts.credentials(alias, self.origin, self.realm, "create_account")
        self.assertEqual(len(self.secrets.writes), 1)

    def test_removed_application_does_not_remove_employer_guard(self):
        original = self.accounts.credentials(self.first, self.origin, self.realm, "create_account")
        self.store.remove_jobs([self.first["id"]])
        with self.assertRaises(ValueError):
            self.accounts.credentials(self.second, self.origin, self.realm, "create_account")
        self.saved_account(self.second, password="")
        self.assertEqual(self.accounts.credentials(self.second, self.origin, self.realm, "login"), original)

    def test_saved_account_is_shared_without_exposing_password(self):
        self.saved_account()
        shown = self.accounts.display(self.second)
        self.assertEqual(shown["account"], "avery@example.test")
        self.assertEqual(shown["account_origin"], self.origin)
        self.assertTrue(shown["has_password"])
        self.assertNotIn("synthetic-password", json.dumps(shown))
        self.assertNotIn("synthetic-password", json.dumps(self.store.all("employer_account")))
        self.assertEqual(self.accounts.credentials(self.second, self.origin, self.realm, "login"),
                         ("avery@example.test", "synthetic-password"))

    def test_saved_account_cannot_signup_or_change_to_second_identifier(self):
        self.saved_account()
        with self.assertRaises(ValueError):
            self.accounts.credentials(self.second, self.origin, self.realm, "create_account")
        with self.assertRaises(ValueError):
            self.saved_account(self.second, account="different@example.test")
        self.assertEqual(self.accounts.display(self.first)["account"], "avery@example.test")

    def test_password_update_is_used_by_every_role(self):
        self.saved_account()
        self.saved_account(self.second, password="synthetic-replacement")
        for job in (self.first, self.second):
            self.assertEqual(self.accounts.credentials(job, self.origin, self.realm, "login"),
                             ("avery@example.test", "synthetic-replacement"))
        self.assertEqual(len(self.store.all("employer_account")), 1)

    def test_origin_change_requires_a_password(self):
        self.saved_account()
        with self.assertRaises(ValueError):
            self.saved_account(account_origin="https://new.example.test", password="")
        self.assertEqual(self.accounts.display(self.first)["account_origin"], self.origin)

    def test_consistent_legacy_credentials_are_reused_across_roles(self):
        self.store.job(self.first["id"], account="avery@example.test", account_origin=self.origin, has_password=True)
        self.secrets.set("account:" + self.first["id"], "synthetic-legacy")
        self.accounts.credentials(self.first, self.origin, self.realm, "login")
        self.saved_account(password="")
        actual = self.accounts.credentials(self.second, self.origin, self.realm, "login")
        self.assertEqual(actual, ("avery@example.test", "synthetic-legacy"))
        with self.assertRaises(ValueError):
            self.accounts.credentials(self.second, self.origin, self.realm, "create_account")

    def test_conflicting_legacy_accounts_require_manual_resolution(self):
        for job, account in ((self.first, "first@example.test"), (self.second, "second@example.test")):
            self.store.job(job["id"], account=account, account_origin=self.origin, has_password=True)
            self.secrets.set("account:" + job["id"], "synthetic-legacy")
        with self.assertRaises(ValueError):
            self.accounts.credentials(self.second, self.origin, self.realm, "login")
        self.assertEqual(self.store.all("employer_account"), [])

    def test_unknown_employer_cannot_reserve_signup(self):
        unknown = self.job("unknown-role", "")
        with self.assertRaises(ValueError):
            self.accounts.credentials(unknown, self.origin, self.realm, "create_account")
        self.assertEqual(self.secrets.writes, [])

    def test_conflicting_legacy_passwords_are_not_silently_merged(self):
        for job, password in ((self.first, "synthetic-one"), (self.second, "synthetic-two")):
            self.store.job(job["id"], account="avery@example.test", account_origin=self.origin, has_password=True)
            self.secrets.set("account:" + job["id"], password)
        with self.assertRaises(ValueError):
            self.accounts.credentials(self.first, self.origin, self.realm, "login")
        self.assertEqual(self.store.all("employer_account"), [])

    def test_legacy_account_without_origin_cannot_authorize_signup(self):
        self.store.job(self.first["id"], account="avery@example.test", has_password=True)
        self.secrets.set("account:" + self.first["id"], "synthetic-unbound")
        with self.assertRaises(ValueError):
            self.accounts.credentials(self.second, self.origin, self.realm, "create_account")
        self.assertEqual(self.store.all("employer_account"), [])

    def test_missing_visible_employer_stops_authentication_before_fill(self):
        page = authentication_page("create_account")
        page["text"] = "Applicant account"
        session = Mock()
        with self.assertRaises(ValueError):
            authenticate(self.workspace, session, self.first, page)
        session.call.assert_not_called()
        self.assertEqual(self.secrets.writes, [])

    def test_authentication_timeout_never_repeats_signup_click(self):
        session = Mock()
        session.call.side_effect = [{"snapshot_id": "filled"}, TimeoutError("Synthetic browser timeout")]
        with self.assertRaises(TimeoutError):
            authenticate(self.workspace, session, self.first, authentication_page("create_account"))
        self.assertEqual(session.call.call_count, 2)
        session.reset_mock()
        with self.assertRaises(ValueError):
            authenticate(self.workspace, session, self.second, authentication_page("create_account"))
        session.call.assert_not_called()

    def test_failed_origin_change_cannot_read_the_previous_origin_secret(self):
        self.saved_account()
        original_keys = set(self.secrets.values)
        self.secrets.fail_write = True
        with self.assertRaises(RuntimeError):
            self.saved_account(account_origin="https://new.example.test", password="synthetic-new")
        self.secrets.reads.clear()
        with self.assertRaises(ValueError):
            self.accounts.credentials(self.second, "https://new.example.test", "https://new.example.test/team/", "login")
        self.assertTrue(original_keys.isdisjoint(self.secrets.reads))

    def test_ambiguous_authentication_context_never_reads_secrets(self):
        for ambiguity in ("query", "hidden"):
            page = authentication_page("create_account")
            if ambiguity == "query":
                page["buttons"][0]["form_action"] += "?tenant=other"
                for field in page["fields"]:
                    field["form_action"] = page["buttons"][0]["form_action"]
            else:
                page["fields"].append({**page["fields"][0], "field_id": "tenant", "type": "hidden", "label": "Tenant"})
            session = Mock()
            with self.subTest(ambiguity=ambiguity), self.assertRaises(ValueError):
                authenticate(self.workspace, session, self.first, page)
            session.call.assert_not_called()
        self.assertEqual(self.secrets.reads, [])


if __name__ == "__main__":
    unittest.main()
