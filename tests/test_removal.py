"""Removal tests use local synthetic jobs, without browser or provider calls."""

import tempfile
import unittest
from unittest.mock import Mock, patch

from applydesk.applications import Applications
from applydesk.workspace import Workspace


class RemovalTests(unittest.TestCase):
    def setUp(self):
        # Isolate records and documents from the user's workspace.
        self.temp = tempfile.TemporaryDirectory()
        self.w = Workspace(self.temp.name, secrets=Mock())
        self.jobs = [self.w.store.add_job(f"https://example.test/jobs/{index}") for index in range(3)]

    def tearDown(self):
        # Release SQLite before removing the temporary directory.
        self.w.close()
        self.temp.cleanup()

    def test_bulk_removal_retains_records_documents_and_other_rows(self):
        # Only selected rows disappear; source and document references remain intact.
        job = self.w.store.job(self.jobs[0]["id"], resume_id="resume", account="example",
                               details="Required: testing.", history=[{"message": "Saved"}])
        document = self.w.docs.root / "keep.txt"
        document.write_text("Keep this document")
        session = self.w.sessions[job["id"]] = Mock()
        ids = [job["id"], self.jobs[1]["id"]]
        self.assertEqual(self.w.remove_jobs({"ids": ids + ids}), {"removed": 2})
        self.assertEqual([row["id"] for row in self.w.state()["jobs"]], [self.jobs[2]["id"]])
        retained = self.w.store.get("job", job["id"])
        self.assertTrue(retained.pop("removed_at"))
        self.assertEqual(retained, job)
        self.assertEqual(document.read_text(), "Keep this document")
        session.close.assert_called_once()
        self.assertNotIn(job["id"], self.w.sessions)
        self.assertEqual(self.w.remove_jobs({"ids": ids}), {"removed": 2})

    def test_invalid_selection_never_partially_removes(self):
        # Validate the entire request before touching any record.
        for ids in (None, [], "id", [False], [""], [self.jobs[0]["id"], "missing"]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.w.remove_jobs({"ids": ids})
            self.assertEqual(len(self.w.state()["jobs"]), 3)

    def test_running_task_blocks_removal(self):
        # A preparation cannot race with removal.
        self.w.tasks["prepare"] = {"status": "running"}
        with self.assertRaises(ValueError):
            self.w.remove_jobs({"ids": [self.jobs[0]["id"]]})
        self.assertEqual(len(self.w.state()["jobs"]), 3)

    def test_removal_survives_restart_and_explicit_add_restores(self):
        # Automatic exclusion persists; an intentional Add link can recover the row.
        job = self.jobs[0]
        self.w.remove_jobs({"ids": [job["id"]]})
        self.w.close()
        self.w = Workspace(self.temp.name, secrets=Mock())
        self.assertEqual(len(self.w.state()["jobs"]), 2)
        with patch("applydesk.workspace.public_job_url", side_effect=lambda url: url):
            restored = self.w.add_job({"url": job["url"]})
        self.assertEqual(restored["id"], job["id"])
        self.assertFalse(restored["removed_at"])
        self.assertEqual(len(self.w.state()["jobs"]), 3)

    def test_removed_jobs_cannot_be_prepared(self):
        # Stale controls must not launch a browser for a hidden application.
        job = self.jobs[0]
        self.w.remove_jobs({"ids": [job["id"]]})
        with patch("applydesk.applications.ApplicationSession") as browser:
            with self.assertRaises(ValueError):
                Applications(self.w).prepare(job["id"])
        browser.assert_not_called()
