"""Structured job fields persist without changing source captures or application state."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from applydesk.storage import Store


class JobDetailsStorageTests(unittest.TestCase):
    def test_combined_capture_keeps_employer_only_requirements_and_pay(self):
        """Verified raw evidence from both pages is richer than the platform capture alone."""
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                job = store.add_job("https://example.test/job")
                source = {"description": "• Responsibilities\n· Build tools."}
                combined = source["description"] + "\nEmployer job page:\nRequired Qualifications\n1. SQL.\n▪ Salary: $150,000 annually."
                saved = store.job(job["id"], recommendation_source=source, details=combined, details_origin="captured")
                self.assertEqual(saved["detail_sections"]["responsibilities"], "Build tools.")
                self.assertEqual(saved["detail_sections"]["required_qualifications"], "SQL.")
                self.assertEqual(saved["compensation"], "Salary: $150,000 annually.")
                self.assertEqual(saved["recommendation_source"], source)
                self.assertEqual(store.job(job["id"], progress="Checking")["details"], combined)
            finally:
                store.close()

    def test_legacy_model_summary_is_not_promoted_to_captured_facts(self):
        """Old prepared manual jobs lack raw text and must remain visibly distinguished."""
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                job = store.add_job("https://example.test/job")
                summary = "Required Qualifications\nPython.\nSalary: $100,000 annually."
                legacy = {**job, "details": summary, "analysis": {"details": summary}}
                legacy.pop("details_origin")
                saved = store.put("job", job["id"], legacy)
                self.assertEqual(saved["details"], summary)
                self.assertEqual(saved["details_origin"], "summary")
                self.assertEqual(saved["compensation"], "")
                self.assertFalse(any(saved["detail_sections"].values()))
                fresh = store.job(job["id"], details=summary, details_origin="captured")
                self.assertIn("$100,000", fresh["compensation"])
            finally:
                store.close()

    def test_existing_jobs_gain_fields_without_losing_data(self):
        """Opening a workspace cleans old derived list markers while retaining raw captures."""
        with tempfile.TemporaryDirectory() as directory:
            raw = "• Responsibilities\n· Build tools.\n\n\uf0b7 Salary: $40 per hour."
            source = {"description": raw, "captured_at": "unchanged", "description_sha256": "unchanged"}
            original = {"id": "old", "status": "submitted", "details": raw, "recommendation_source": source,
                        "updated_at": "unchanged", "history": [{"message": "User submitted"}], "resume_id": "saved-resume"}
            with sqlite3.connect(Path(directory) / "workspace.sqlite") as db:
                db.execute("CREATE TABLE records (kind TEXT, id TEXT, data TEXT, PRIMARY KEY(kind,id))")
                previous = {**original, "detail_sections": {"responsibilities": "· Build tools."},
                            "compensation": "\uf0b7 Salary: $40 per hour."}
                db.execute("INSERT INTO records VALUES ('job','old',?)", (json.dumps(previous),))
            store = Store(directory)
            try:
                saved = store.get("job", "old")
                self.assertEqual({key: saved[key] for key in original}, original)
                self.assertEqual(saved["detail_sections"]["responsibilities"], "Build tools.")
                self.assertEqual(saved["compensation"], "Salary: $40 per hour.")
            finally:
                store.close()

    def test_updates_reparse_and_source_capture_beats_summary(self):
        """A changed description cannot retain stale pay or prefer a generated summary."""
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                job = store.add_job("https://example.test/job")
                self.assertEqual(job["compensation"], "")
                stored = store.job(job["id"], details="Responsibilities\nBuild.\nSalary: $90,000 annually.")
                self.assertIn("$90,000", stored["compensation"])
                updated = store.job(job["id"], details="Responsibilities\nReview code.")
                self.assertEqual(updated["compensation"], "")
                source = {"description": "Required Qualifications\n2) Python.\n· Salary: $120,000 annually."}
                saved = store.job(job["id"], recommendation_source=source, details="Generated summary")
                self.assertEqual(saved["recommendation_source"], source)
                self.assertEqual(saved["details"], "Generated summary")
                self.assertEqual(saved["detail_sections"]["required_qualifications"], "Python.")
                self.assertEqual(saved["compensation"], "Salary: $120,000 annually.")
            finally:
                store.close()
