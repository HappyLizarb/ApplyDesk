"""Authentication failures and concurrent requests keep credentials and signup reservations bound."""
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
from test_employer_accounts import EmployerAccountFixture, authentication_page
from applydesk.application_steps import authenticate


class AccountLockingTests(EmployerAccountFixture):
    def test_browser_call_does_not_hold_store_lock(self):
        """A browser worker's checkpoint can acquire the store lock before filling."""
        self.saved_account()
        checks = []
        def browser_call(method, params):
            def checkpoint():
                acquired = self.store.lock.acquire(timeout=.3)
                checks.append(acquired)
                if acquired:
                    self.store.lock.release()
            worker = threading.Thread(target=checkpoint)
            worker.start()
            worker.join(timeout=1)
            self.assertTrue(checks[-1], 'Authentication held the store lock while waiting for the browser')
            return {'snapshot_id': 'filled'}
        session = Mock()
        session.call.side_effect = browser_call
        authenticate(self.workspace, session, self.first, authentication_page())
        self.assertEqual(checks, [True, True])

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
        session.call.side_effect = [{**authentication_page("create_account"), "snapshot_id": "filled"}, TimeoutError("Synthetic browser timeout")]
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
