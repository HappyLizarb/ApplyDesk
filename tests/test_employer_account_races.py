"""Concurrent edits cannot change an employer account during browser authentication."""

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
from tests.test_employer_accounts import MemorySecrets, authentication_page


class EmployerAccountRaceTests(unittest.TestCase):
    """Use real persistence and coordinated fake browser calls without external accounts."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="applydesk-account-race-")
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name))
        self.addCleanup(self.store.close)
        self.secrets = MemorySecrets()
        self.workspace = SimpleNamespace(store=self.store, secrets=self.secrets,
                                         profile=lambda: {"identity": {"email": "avery@example.test"}})
        self.accounts = EmployerAccounts(self.workspace)
        created = self.store.add_job("https://jobs.example.test/team/role")
        self.job = self.store.job(created["id"], company="Circuit Example")
        self.saved = {"id": self.job["id"], "account": "avery@example.test",
                      "account_origin": "https://jobs.example.test", "password": "synthetic-original"}
        self.accounts.save(self.saved)
        self.filling = threading.Event()
        self.release = threading.Event()
        self.edit_started = threading.Event()
        self.edit_finished = threading.Event()
        self.session = Mock()
        self.session.call.side_effect = self.browser_call

    def browser_call(self, method, parameters):
        """Hold the fill step open so a concurrent edit can attempt the shared lock."""
        if method == "fill":
            values = {answer["field_id"]: answer["value"] for answer in parameters["answers"]}
            self.assertEqual(values["password"], "synthetic-original")
            self.filling.set()
            self.assertTrue(self.release.wait(timeout=5))
        elif method == "authenticate":
            self.assertTrue(self.edit_finished.is_set())
            current = self.store.get("job", self.job["id"])
            self.assertEqual(current["account_origin"], self.saved["account_origin"])
            self.assertEqual(current["company"], "Circuit Example")
        return {"snapshot_id": method}

    def edit(self, operation):
        """Signal the attempted mutation separately from its eventual completion."""
        self.edit_started.set()
        try:
            return operation()
        finally:
            self.edit_finished.set()

    def exercise_race(self, operation):
        """Concurrent edits are rejected; retrying after authentication succeeds."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            login = pool.submit(authenticate, self.workspace, self.session, self.job, authentication_page())
            try:
                self.assertTrue(self.filling.wait(timeout=5))
                changed = pool.submit(self.edit, operation)
                self.assertTrue(self.edit_started.wait(timeout=5))
                self.assertTrue(self.edit_finished.wait(timeout=1))
            finally:
                self.release.set()
            self.assertEqual(login.result(timeout=5), {"snapshot_id": "authenticate", "intervention": "authentication_completed"})
            with self.assertRaisesRegex(ValueError, "authentication is in progress"):
                changed.result(timeout=5)
        operation()
        self.assertTrue(self.edit_finished.is_set())
        self.assertEqual([call.args[0] for call in self.session.call.call_args_list], ["fill", "authenticate"])

    def test_origin_edit_waits_for_authentication_and_old_page_cannot_read_new_secret(self):
        changed = {**self.saved, "account_origin": "https://new.example.test", "password": "synthetic-new"}
        self.exercise_race(lambda: self.accounts.save(changed))
        self.assertEqual(self.accounts.display(self.job)["account_origin"], changed["account_origin"])
        self.secrets.reads.clear()
        with self.assertRaises(ValueError):
            authenticate(self.workspace, Mock(), self.job, authentication_page())
        self.assertEqual(self.secrets.reads, [])

    def test_company_edit_waits_for_authentication_and_old_page_cannot_read_secret(self):
        self.exercise_race(lambda: self.store.job(self.job["id"], company="Another Example"))
        self.assertEqual(self.store.get("job", self.job["id"])["company"], "Another Example")
        self.secrets.reads.clear()
        with self.assertRaises(ValueError):
            authenticate(self.workspace, Mock(), self.job, authentication_page())
        self.assertEqual(self.secrets.reads, [])

    def test_previous_origin_still_blocks_an_alias_signup_after_origin_change(self):
        self.accounts.credentials(self.job, self.saved["account_origin"], "https://jobs.example.test/team/", "login")
        self.accounts.save({**self.saved, "account_origin": "https://new.example.test", "password": "synthetic-new"})
        created = self.store.add_job("https://jobs.example.test/team/another-role")
        alias = self.store.job(created["id"], company="Circuit Example Inc.")
        writes = list(self.secrets.writes)
        with self.assertRaises(ValueError):
            self.accounts.credentials(alias, self.saved["account_origin"], "https://jobs.example.test/team/", "create_account")
        self.assertEqual(self.secrets.writes, writes)


if __name__ == "__main__":
    unittest.main()
