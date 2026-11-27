"""Authentication failures and concurrent requests keep credentials and signup reservations bound."""
import threading
from concurrent.futures import ThreadPoolExecutor
from test_employer_accounts import EmployerAccountFixture


class AccountLockingTests(EmployerAccountFixture):

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

    def test_previous_origin_still_blocks_an_alias_signup_after_origin_change(self):
        self.saved_account()
        self.accounts.credentials(self.first, self.origin, self.realm, "login")
        self.saved_account(account_origin="https://new.example.test", password="synthetic-new")
        alias = self.job("another-role", "Circuit Example Inc.")
        writes = list(self.secrets.writes)
        with self.assertRaises(ValueError):
            self.accounts.credentials(alias, self.origin, self.realm, "create_account")
        self.assertEqual(self.secrets.writes, writes)
