"""Removal tests use local synthetic jobs, without browser or provider calls."""

import tempfile
import unittest
from unittest.mock import Mock, patch

from applydesk.applications import Applications
from applydesk.discovery import Discovery
from applydesk.workspace import Workspace
from applydesk.recommendation_import import duplicate, import_selected


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
        with patch("applydesk.applications.BrowserSession") as browser:
            with self.assertRaises(ValueError):
                Applications(self.w).prepare(job["id"])
        browser.assert_not_called()

    def test_discovery_skips_removed_tracking_alias(self):
        # Public-search aliases remain excluded by the existing canonical URL dedup.
        job = self.jobs[0]
        self.w.remove_jobs({"ids": [job["id"]]})
        report = {"candidates": [{"url": job["url"] + "?utm_source=test"}], "job_ids": []}
        Discovery(self.w)._add(report)
        self.assertEqual(report["job_ids"], [])
        self.assertEqual(len(self.w.store.all("job")), 3)

    def test_recommendations_cannot_repair_or_reimport_removed_jobs(self):
        # Check incomplete placeholders, stable IDs, platform aliases and employer links.
        source = {"platform": "linkedin", "job_id": "123", "source_url": "https://www.linkedin.com/jobs/view/123/",
                  "employer_url": "https://careers.example.test/jobs/123", "company": "Example", "title": "Engineer", "complete": True}
        variants = [
            {"url": source["source_url"], "recommendation_source": {**source, "complete": False}},
            {"url": "https://www.linkedin.com/jobs/search-results/?currentJobId=123"},
            {"url": source["employer_url"]},
        ]
        for index, changes in enumerate(variants):
            with self.subTest(changes=changes):
                source = {**source, "job_id": str(123 + index),
                          "source_url": f"https://www.linkedin.com/jobs/view/{123 + index}/",
                          "employer_url": f"https://careers.example.test/jobs/{123 + index}"}
                changes = {**changes, "url": changes["url"].replace("123", source["job_id"])}
                job = self.w.store.add_job(changes["url"])
                self.w.store.job(job["id"], **changes)
                self.w.remove_jobs({"ids": [job["id"]]})
                prior, reusable = duplicate(self.w, source)
                self.assertEqual(prior["id"], job["id"])
                self.assertFalse(reusable)
                before = self.w.store.all("job")
                with self.w.lock, self.w.store.lock:
                    report = import_selected(self.w, [source], {"status": "complete", "job_ids": [], "excluded_count": 0})
                    self.w.store.put("recommendation", "linkedin", report)
                self.assertEqual(self.w.store.all("job"), before)
                self.assertEqual(self.w.recommendations.state()["linkedin"]["excluded_count"], 1)
                self.assertEqual(self.w.recommendations.state()["linkedin"]["job_ids"], [])
