"""Fresh source reads preserve verified routing without bypassing current listing identity."""

import copy
import unittest
from unittest.mock import Mock, patch

from chat_helpers import ChatFixture
from recommendation_helpers import listing, response
from applydesk.recommendation_checks import retain_employer_link, validate_native_page
from applydesk.recommendation_source import normalize_job


class RecommendationRefreshTests(ChatFixture):
    """Only synthetic source-worker responses are used; no websites or personal records are read."""

    def setUp(self):
        super().setUp()
        self.prior = normalize_job("linkedin", listing(employer_job_id="req-123"))
        self.fresh_item = listing(employer_url="", text="Updated mandatory responsibilities. " * 8)
        self.session = Mock()
        self.session.call.side_effect = lambda method, params=None: response(job=copy.deepcopy(self.fresh_item))

    def read(self, prior=None):
        """Exercise the public refresh operation with an isolated controlled reader."""
        with patch("applydesk.recommendations.BrowserSession", return_value=self.session):
            return self.w.recommendations.read_job(prior or self.prior)

    def test_missing_fresh_link_preserves_exact_observed_destination_and_old_provenance(self):
        refreshed = self.read()
        self.assertEqual(refreshed["employer_url"], self.prior["employer_url"])
        self.assertEqual(refreshed["employer_job_id"], "req-123")
        self.assertIn("Updated mandatory responsibilities", refreshed["description"])
        self.assertNotEqual(refreshed["description_sha256"], self.prior["description_sha256"])
        proof = refreshed["employer_url_provenance"]
        self.assertEqual(proof["description_sha256"], self.prior["description_sha256"])
        self.assertEqual(proof["captured_at"], self.prior["captured_at"])
        again = self.read(refreshed)
        self.assertEqual(again["employer_url_provenance"], proof)
        self.session.close.assert_called()

    def test_new_observed_link_wins_without_old_requisition_or_provenance(self):
        prior = self.read()
        self.fresh_item["employer_url"] = "https://new-careers.example.test/role/other"
        fresh = self.read(prior)
        self.assertEqual(fresh["employer_url"], self.fresh_item["employer_url"])
        self.assertNotIn("employer_job_id", fresh)
        self.assertNotIn("employer_url_provenance", fresh)

    def test_changed_identity_and_tampered_prior_capture_do_not_restore_links(self):
        for changes in ({"id": "456", "url": "https://www.linkedin.com/jobs/view/456/"},
                        {"company": "Another Employer"}, {"title": "Different Role"}):
            self.fresh_item = listing(employer_url="", **changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.read()
        with self.assertRaises(ValueError):
            self.read({**self.prior, "description": "Changed after capture"})

    def test_different_platform_or_unknown_prior_identity_cannot_transfer_a_link(self):
        fresh = normalize_job("handshake", listing(platform="handshake", employer_url=""))
        with self.assertRaises(ValueError):
            retain_employer_link(self.prior, fresh)
        prior = normalize_job("linkedin", listing(company="", title="", complete=False))
        refreshed = retain_employer_link(prior, normalize_job("linkedin", self.fresh_item))
        self.assertEqual(refreshed["employer_url"], "")

    def test_invalid_link_or_conflicting_retained_provenance_cannot_transfer(self):
        prior = self.read()
        for changes in ({"employer_url": "https://localhost/private"},
                        {"employer_url_provenance": {**prior["employer_url_provenance"], "job_id": "456"}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.read({**prior, **changes})

    def test_no_prior_link_stays_native_instead_of_inventing_a_destination(self):
        fresh = self.read({**self.prior, "employer_url": ""})
        self.assertEqual(fresh["employer_url"], "")
        self.assertNotIn("employer_url_provenance", fresh)


class NativeExplorationTests(unittest.TestCase):
    """Page admission is separate from the later action-specific browser checks."""

    def setUp(self):
        self.source = normalize_job("linkedin", listing(employer_url=""))
        self.page = {"url": self.source["source_url"], "text": self.source["description"],
                     "warnings": ["Embedded frames require manual inspection.", "Shadow DOM controls require manual inspection."]}

    def test_agent_explores_verified_native_page_with_incidental_embedded_content(self):
        self.assertIs(validate_native_page(self.source, self.page, agent_driven=True), self.page)
        self.assertEqual(len(self.page["warnings"]), 2)
        with self.assertRaisesRegex(ValueError, "manual inspection"):
            validate_native_page(self.source, self.page)

    def test_actual_manual_warning_is_retained_even_alongside_incidental_content(self):
        self.page["warnings"].append("CAPTCHA requires manual completion.")
        with self.assertRaisesRegex(ValueError, "CAPTCHA"):
            validate_native_page(self.source, self.page, agent_driven=True)

    def test_wrong_platform_job_or_visible_identity_remains_blocked(self):
        for changes in ({"url": "https://www.linkedin.com/jobs/view/999/"},
                        {"url": "https://other.example.test/jobs/123/"}, {"text": "Another Employer Different Role"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_native_page(self.source, {**self.page, **changes}, agent_driven=True)
