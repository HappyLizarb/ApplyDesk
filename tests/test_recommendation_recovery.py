"""Incomplete recommendations recover only before the user starts preparing an application."""

import unittest
from unittest.mock import Mock

from applydesk.recommendations import normalize_job
from recommendation_helpers import accept_captured
from tests import test_recommendations as fixtures


class RecommendationRecoveryTests(unittest.TestCase):
    # Reuse only the isolated synthetic workspace lifecycle, not the other test cases.
    setUp = fixtures.RecommendationTests.setUp
    tearDown = fixtures.RecommendationTests.tearDown
    finished = fixtures.RecommendationTests.finished

    def test_historic_false_zero_is_displayed_as_capture_error_without_rewriting_records(self):
        """Only contradictory blocked zero messages change in memory; saved reports remain untouched."""
        for issues, expected in ((["", "The description could not be read."], "The description could not be read."),
                                 ([], "The listings could not be read. Try the search again.")):
            saved = {"status": "blocked", "message": "Found 0 matching job(s).", "issues": issues}
            self.w.store.put("recommendation", "linkedin", saved)
            self.assertEqual(self.w.recommendations.state()["linkedin"]["message"], expected)
            self.assertEqual(self.w.store.get("recommendation", "linkedin"), saved)
        for status, message in (("blocked", "Source needs sign-in."), ("no_results", "Found 0 matching job(s).")):
            saved = {"status": status, "message": message, "issues": ["Earlier issue"]}
            self.w.store.put("recommendation", "linkedin", saved)
            self.assertEqual(self.w.recommendations.state()["linkedin"], saved)
        self.browser.assert_not_called()

    def test_refresh_repairs_untouched_incomplete_rows_without_profile_or_models(self):
        # Both legacy new placeholders and current needs-review rows can recover in place.
        for index, previous_status in enumerate(("new", "needs_review")):
            identity = str(700 + index)
            incomplete = fixtures.listing(identity, title="", company="", text="", complete=False)
            source = normalize_job("linkedin", incomplete)
            job = self.w.store.add_job(source["source_url"])
            original = self.w.store.job(job["id"], recommendation_source=source, status=previous_status)
            self.session.call.side_effect = lambda method, params=None: fixtures.response(jobs=[fixtures.listing(identity)])
            self.w.recommendations.begin({"platform": "linkedin"})
            report = accept_captured(self)
            updated = self.w.store.get("job", original["id"])
            self.assertEqual((report["added_count"], report["updated_count"]), (0, 1))
            self.assertEqual(updated["status"], "new")
            self.assertTrue(updated["recommendation_source"]["complete"])
            self.assertEqual(updated["qualification_status"], "unverified")
            self.assertNotEqual(updated["recommendation_source"]["description_sha256"], original["recommendation_source"]["description_sha256"])
            self.assertEqual(len(self.w.store.all("job")), index + 1)
        self.assertFalse(self.w.profile()["confirmed"])
        self.secrets.get.assert_not_called()
        self.w.client.assert_not_called()

    def test_handshake_intake_requires_verified_identity_and_complete_text(self):
        # Even a claimed complete capture cannot admit blank identity, short text or reader issues.
        failures = [{"title": ""}, {"company": ""}, {"text": "Card summary only"},
                    {"text": "x" * 50001}, {"issues": ["Description not fully read"]}, {"complete": False}]
        candidates = [fixtures.listing(str(1000 + index), platform="handshake", **change)
                      for index, change in enumerate(failures)]
        body = "Responsibilities: build tested services.\nRequired qualifications: Python and databases.\n" * 8
        complete = fixtures.listing("2000", platform="handshake", text=body, employer_url="")
        self.session.call.side_effect = lambda method, params=None: fixtures.response(platform="handshake", jobs=[*candidates, complete])
        self.w.recommendations.begin({"platform": "handshake", "limit": 7})
        report = accept_captured(self, "handshake")
        self.assertEqual((report["status"], report["added_count"]), ("blocked", 1))
        self.assertTrue(all(any(item["id"] in issue for issue in report["issues"]) for item in candidates))
        rows = self.w.store.all("job")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["company"], rows[0]["title"]), (complete["company"], complete["title"]))
        source = rows[0]["recommendation_source"]
        self.assertEqual(source["description"].split("\n\n", 1)[1], body.strip())
        self.assertEqual((source["job_id"], source["complete"], source["employer_url"]), ("2000", True, ""))
        self.assertEqual(rows[0]["status"], "new")
        self.secrets.get.assert_not_called()
        self.w.client.assert_not_called()

    def test_refresh_preserves_prepared_submitted_and_active_incomplete_rows(self):
        # A source refresh cannot replace facts once any application work or account exists.
        changes = [{"status": "preparing"}, {"status": "submitted"}, {"analysis": {"title": "Reviewed"}},
                   {"resume_id": "saved-doc"}, {"account": "saved@example.test"}, {"history": [{"message": "Reviewed"}]}, {}, {}]
        originals, incoming = [], []
        for index, update in enumerate(changes):
            item = fixtures.listing(str(800 + index))
            source = normalize_job("linkedin", {**item, "complete": False})
            job = self.w.store.add_job(source["source_url"])
            originals.append(self.w.store.job(job["id"], recommendation_source=source, **{"status": "needs_review", **update}))
            incoming.append(item)
        self.w.sessions[originals[-2]["id"]] = Mock()
        self.w.tasks["prepare:" + originals[-1]["id"]] = {"status": "running"}
        self.session.call.side_effect = lambda method, params=None: fixtures.response(jobs=incoming)
        self.w.recommendations.begin({"platform": "linkedin", "limit": 8})
        report = accept_captured(self)
        self.assertEqual((report["added_count"], report["updated_count"]), (0, 0))
        self.assertEqual(self.w.store.all("job"), originals)

    def test_refresh_does_not_replace_complete_captures_or_change_known_identity(self):
        # A matching numeric ID permits recovery, never a silent change to a known employer/title.
        originals, incoming = [], []
        for identity, complete in (("901", True), ("902", False)):
            source = normalize_job("linkedin", fixtures.listing(identity, complete=complete))
            job = self.w.store.add_job(source["source_url"])
            originals.append(self.w.store.job(job["id"], recommendation_source=source))
            incoming.append(fixtures.listing(identity, company="Different Company"))
        self.session.call.side_effect = lambda method, params=None: fixtures.response(jobs=incoming)
        self.w.recommendations.begin({"platform": "linkedin"})
        report = accept_captured(self)
        self.assertEqual((report["added_count"], report["updated_count"]), (0, 0))
        self.assertEqual(self.w.store.all("job"), originals)

    def test_restart_marks_legacy_incomplete_rows_for_review_without_reading_source(self):
        # Previously imported placeholders need a clear status even before Refresh is used.
        from applydesk.workspace import Workspace
        source = normalize_job("linkedin", fixtures.listing(text="", complete=False))
        job = self.w.store.add_job(source["source_url"])
        original = self.w.store.job(job["id"], recommendation_source=source)
        self.w.close()
        self.w = Workspace(self.w.root, secrets=self.secrets)
        migrated = self.w.store.get("job", job["id"])
        self.assertEqual(migrated["status"], "needs_review")
        self.assertEqual(migrated["recommendation_source"], original["recommendation_source"])
        self.browser.assert_not_called()
        self.secrets.get.assert_not_called()

    def test_still_incomplete_refresh_keeps_existing_placeholder_unchanged(self):
        # Failed recapture stays in the report and cannot replace previously saved facts.
        source = normalize_job("linkedin", fixtures.listing(title="", company="", text="", complete=False))
        job = self.w.store.add_job(source["source_url"])
        original = self.w.store.job(job["id"], recommendation_source=source, status="needs_review")
        partial = fixtures.listing(text="Short partial description", complete=False)
        self.session.call.side_effect = lambda method, params=None: fixtures.response(jobs=[partial])
        self.w.recommendations.begin({"platform": "linkedin"})
        report = accept_captured(self)
        self.assertEqual((report["status"], report["updated_count"]), ("blocked", 0))
        self.assertTrue(report["issues"])
        self.assertEqual(self.w.store.get("job", job["id"]), original)


if __name__ == "__main__":
    unittest.main()
