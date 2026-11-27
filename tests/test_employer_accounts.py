"""Employer login reuse and signup reservations use only fictional local records."""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from applydesk.account_scope import bound_signin_host, employer_key, is_signin_switch, native_company_quote
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


class EmployerAccountFixture(unittest.TestCase):
    """Provide isolated employer records, a keychain and login forms for account tests."""

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


class EmployerAccountTests(EmployerAccountFixture):
    """Multiple roles must share an account without widening its credential scope."""

    def test_existing_account_name_survives_verified_legal_name_on_same_posting(self):
        url = self.origin + "/job/1"
        source = {"company": "Circuit Example", "employer_url": url}
        identity = employer_key(source)
        self.store.put("employer_account", identity, {"company": source["company"]})
        job = {"employer_account_id": identity, "recommendation_source": source}
        page = {"public_source": {"url": url, "complete": True}, "text": "Circuit Example, also known as Circuit Corporation"}
        self.assertEqual(self.accounts.listing_company(job, page, "Circuit Corporation"), "Circuit Example")
        for change in ({"text": "Circuit Corporation"}, {"public_source": {"url": url + "2", "complete": True}}):
            self.assertEqual(self.accounts.listing_company(job, {**page, **change}, "Circuit Corporation"), "Circuit Corporation")
        self.assertEqual(self.secrets.reads, [])

    def test_signin_legal_name_requires_the_existing_binding_and_exact_verified_listing(self):
        url = self.origin + "/job/2"
        company, legal = "Example Brand", "Example Incorporated"
        saved = {"id": employer_key({"company": company}), "realm": self.realm}
        job = {"company": company, "employer_account_id": saved["id"], "analysis": {"company": legal},
               "recommendation_source": {"company": company, "employer_url": url},
               "public_source": {"company": legal, "url": url, "complete": True}}
        self.assertTrue(native_company_quote(job, saved, legal))
        self.assertFalse(native_company_quote(job, {}, legal))
        self.assertFalse(native_company_quote({**job, "analysis": {}}, saved, legal))
        self.assertFalse(native_company_quote(job, saved, "Unrelated business"))

    def test_existing_account_switch_is_distinct_from_signin_submit(self):
        """Only the Sign In switch on a complete, matching two-password signup form is navigation."""
        url = "https://example.wd5.myworkdayjobs.com/Careers/job/Engineer_123/apply"
        fields = [{"id": str(i), "enabled": True, "web_content": True, "role": role, "label": label,
                   "secure": role == "AXTextField"} for i, (role, label) in enumerate((
                      ("AXTextField", "Password"), ("AXTextField", "Verify New Password"),
                      ("AXButton", "Create Account"), ("AXButton", "Sign In")))]
        page, account = {"url": url, "elements": fields}, {"origin": "https://example.wd5.myworkdayjobs.com", "realm": url}
        click = {"action": "click", "element_id": "3"}
        self.assertTrue(is_signin_switch(page, click, account))
        for changed in ({"elements": fields[1:]}, {"elements_truncated": True},
                        {"url": url.replace("example.", "other.")}):
            self.assertFalse(is_signin_switch({**page, **changed}, click, account))
        self.assertFalse(is_signin_switch(page, {**click, "element_id": "2"}, account))

    def test_returning_workday_signin_can_use_its_verified_address(self):
        """The visible HTTPS address identifies a logo-only page only for its already bound job and realm."""
        url = "https://abc.wd5.myworkdayjobs.com/Careers/job/City/Engineer_123"
        job = {"id": "job", "company": "ABC", "public_source": {"company": "ABC", "complete": True, "url": url}}
        job["employer_account_id"] = employer_key(job)
        form = url + "/apply/autofillWithResume"
        saved = {"id": employer_key(job), "owner_job_id": "job", "origin": "https://abc.wd5.myworkdayjobs.com", "realm": form}
        self.assertEqual(bound_signin_host(job, saved, form), "abc.wd5.myworkdayjobs.com")
        for wrong in (form.replace("123", "456"), form.replace("/Careers/", "/Other/"), form.replace("abc.", "xyz.")):
            self.assertIsNone(bound_signin_host(job, saved, wrong))
        self.assertIsNone(bound_signin_host(job, {**saved, "owner_job_id": "other"}, form))
        self.assertIsNone(bound_signin_host(job, {}, form))

    def test_signup_accepts_employer_initials_only_on_original_workday_site(self):
        """A short brand can prove identity without extending the scope of saved credentials."""
        url = "https://abc.wd5.myworkdayjobs.com/Careers/job/Engineer_123"
        job = {"company": "Alpha Beta Company", "public_source": {
            "company": "Alpha Beta Company", "complete": True, "url": url}}
        signup = "https://abc.wd5.myworkdayjobs.com/en-US/Careers/createAccount"
        self.assertTrue(native_company_quote(job, {}, "ABC Recruitment Notice", page_url=signup))
        for wrong in (signup.replace("abc.wd5", "other.wd5"), signup.replace("Careers", "Other"),
                      signup.replace(".com/", ".com:1234/"), signup.replace("https:", "http:")):
            self.assertFalse(native_company_quote(job, {}, "ABC Recruitment Notice", page_url=wrong))
        self.assertFalse(native_company_quote(job, {}, "ABCX Recruitment Notice", page_url=signup))
        job["public_source"]["complete"] = False
        self.assertFalse(native_company_quote(job, {}, "ABC Recruitment Notice", page_url=signup))

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


if __name__ == "__main__":
    unittest.main()
