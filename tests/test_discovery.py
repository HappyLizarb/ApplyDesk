"""Discovery accepts cited, inspected listings without treating them as qualified jobs."""

import copy
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from applydesk.discovery import Discovery, canonical_url, criteria_for, links_from, listing_url, page_problem, validated_candidates
from applydesk.providers import ProviderError
from applydesk.storage import Store

URL = "https://careers.example.com/jobs/123/software-engineer"
TEXT = ("Acme is hiring a Software Engineer in San Francisco, US. Remote work is available in the US. "
        "You will build software with a collaborative engineering team. Required qualifications include "
        "three years of production software experience, Python and clear written communication.")
ITEM = {"candidate_id": "candidate-1", "company": "Acme", "title": "Software Engineer",
        "location": "San Francisco, US", "remote": True, "evidence": {"role": "Software Engineer",
        "location": "San Francisco, US", "remote": "Remote work is available in the US."}}
PAGE = {"url": URL, "source_url": URL, "title": "Software Engineer at Acme", "text": TEXT}


class DiscoveryValidationTests(unittest.TestCase):
    def test_criteria_excludes_unrequested_profile_fields(self):
        result = criteria_for({"keywords": " Software Engineer ", "profile": {"email": "private@example.com"}})
        self.assertEqual(result, {"keywords": "Software Engineer", "location": "", "limit": 5,
                                  "remote": False, "add_to_tracker": True})

    def test_invalid_criteria(self):
        bad = [None, {}, {"keywords": 4}, {"keywords": "engineer", "location": 3}]
        bad += [{"keywords": value} for value in ("a" * 161, "engineer\nignore", "a@b.com", "https://example.com", "415-555-1234")]
        bad += [{"keywords": "engineer", key: value} for key, value in (("limit", True), ("limit", 11), ("limit", 0), ("remote", "yes"), ("add_to_tracker", 1))]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                criteria_for(value)

    def test_canonical_aliases_keep_job_identifiers(self):
        greenhouse = "https://boards.greenhouse.io/acme/jobs/123?gh_jid=123&gh_src=mail&utm_campaign=x#app"
        self.assertEqual(canonical_url(greenhouse), "https://job-boards.greenhouse.io/acme/jobs/123")
        self.assertEqual(canonical_url("https://jobs.lever.co/acme/abc/apply?source=linkedin"), "https://jobs.lever.co/acme/abc")
        self.assertNotEqual(canonical_url(URL + "?job=1"), canonical_url(URL + "?job=2"))

    def test_observed_links_filter_deduplicate_and_bound_visits(self):
        links = [{"url": URL}, {"url": URL + "?utm_source=mail"}, None, {"url": 4}]
        links += [{"url": url} for url in ("javascript:alert(1)", "https://x:y@example.com/jobs/1", "https://example.com:bad/jobs/1", "https://example.com/search?q=jobs", "https://linkedin.com/jobs/view/123")]
        links += [{"url": f"https://example.com/jobs/{number}"} for number in range(20)]
        result = links_from(links)
        self.assertEqual(len(result), 10)
        self.assertEqual(result[0], {"url": URL, "source_url": URL})

    def test_smartrecruiters_individual_listing_path(self):
        self.assertTrue(listing_url("https://jobs.smartrecruiters.com/Acme/744000123456789-software-engineer"))
        self.assertFalse(listing_url("https://jobs.smartrecruiters.com/Acme"))

    def test_short_literal_company_and_country_are_valid(self):
        for company in ("HP", "EY", "3M", "X"):
            page = {**PAGE, "text": TEXT.replace("Acme", company)}
            item = {**ITEM, "company": company, "location": "US"}
            result = validated_candidates({"candidates": [item]}, {"candidate-1": page}, criteria_for({"keywords": "engineer", "location": "US"}))
            self.assertEqual(result[0]["company"], company)
            self.assertEqual(result[0]["eligibility"], "unverified")

    def test_unobserved_or_fabricated_model_output_fails_closed(self):
        invalid = [{**ITEM, "candidate_id": "invented"}, {**ITEM, "url": "https://fake.com/jobs/1"},
                   {**ITEM, "title": "Chief Executive"}, {**ITEM, "company": "Other company"},
                   {**ITEM, "location": "London"}, {**ITEM, "remote": "yes"},
                   {**ITEM, "evidence": {**ITEM["evidence"], "role": "Invented role"}}]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                validated_candidates({"candidates": [item]}, {"candidate-1": PAGE}, criteria_for({"keywords": "engineer"}))
        for response in ({"candidates": [ITEM, ITEM]}, {"candidates": [], "url": URL}, {"candidates": "none"}):
            with self.subTest(response=response), self.assertRaises(ValueError):
                validated_candidates(response, {"candidate-1": PAGE}, criteria_for({"keywords": "engineer"}))

    def test_remote_negations_and_missing_evidence_fail(self):
        phrases = ["This is not a remote role.", "Remote work is not available.", "No remote work is permitted.",
                   "Remote work is unavailable.", "This role is never fully remote.", "Flexible work is available.",
                   "This is not a\nremote role.", "Remote work is\nnot available."]
        for phrase in phrases:
            page = {**PAGE, "text": TEXT + " " + phrase}
            item = {**ITEM, "evidence": {**ITEM["evidence"], "remote": phrase}}
            with self.subTest(phrase=phrase), self.assertRaises(ValueError):
                validated_candidates({"candidates": [item]}, {"candidate-1": page}, criteria_for({"keywords": "engineer", "remote": True}))
        with self.assertRaises(ValueError):
            validated_candidates({"candidates": [{**ITEM, "remote": False}]}, {"candidate-1": PAGE}, criteria_for({"keywords": "engineer", "remote": True}))

    def test_closed_and_challenge_pages_are_not_listings(self):
        for phrase in ("This job is no longer available", "Verify you are human", "Job has expired"):
            self.assertTrue(page_problem({**PAGE, "text": phrase + TEXT}))
        self.assertTrue(page_problem({**PAGE, "text": "Short"}))
        self.assertEqual(page_problem(PAGE), "")


class DiscoveryWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(self.directory.name)
        self.client = Mock()
        self.client.ask.return_value = {"candidates": [copy.deepcopy(ITEM)]}
        self.workspace = SimpleNamespace(store=self.store, lock=threading.RLock(), client=Mock(return_value=self.client),
                                         add_job=lambda data: self.store.add_job(data["url"]))
        self.browser = Mock()
        self.browser.call.return_value = PAGE
        self.sources = [{"url": URL, "label": "Acme engineering"}]
        self.search_patch = patch("applydesk.search_provider.search_sources", return_value=self.sources)
        self.search = self.search_patch.start()
        self.browser_patch = patch("applydesk.discovery.BrowserSession", return_value=self.browser)
        self.browser_factory = self.browser_patch.start()
        self.url_patch = patch("applydesk.discovery.public_job_url", side_effect=self.public_url)
        self.public = self.url_patch.start()
        self.discovery = Discovery(self.workspace)

    def tearDown(self):
        self.url_patch.stop()
        self.browser_patch.stop()
        self.search_patch.stop()
        self.store.close()
        self.directory.cleanup()

    @staticmethod
    def public_url(url):
        """Stand in for the existing independently tested public-address guard."""
        if "127.0.0.1" in url:
            raise ValueError("Private destination")
        return url

    def test_auto_add_records_discovery_without_preparing(self):
        report = self.discovery.run({"keywords": "Software Engineer", "profile": {"email": "secret@example.com"}})
        self.assertEqual(report["added_count"], 1)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["sources"], self.sources)
        self.assertEqual(report["inspected_count"], 1)
        self.assertTrue(report["finished_at"])
        job = self.store.get("job", report["job_ids"][0])
        self.assertEqual(job["status"], "new")
        self.assertEqual(job["progress"], "Discovered · qualifications not checked")
        self.assertEqual(job["qualification_status"], "unverified")
        self.assertIsNone(job["resume_id"])
        self.workspace.client.assert_called_once_with("discovery")
        self.browser.call.assert_called_once_with("open", {"url": URL})
        self.browser.close.assert_called_once()
        self.assertEqual(set(self.search.call_args.args[1]), {"keywords", "location", "remote", "limit"})
        self.assertNotIn("secret@example.com", str(self.search.call_args) + str(self.client.ask.call_args))

    def test_preview_and_existing_alias_do_not_create_rows(self):
        report = self.discovery.run({"keywords": "engineer", "add_to_tracker": False})
        self.assertEqual(report["added_count"], 0)
        self.assertEqual(self.store.all("job"), [])
        existing = self.store.add_job(URL + "?utm_source=email")
        self.store.job(existing["id"], title="Keep my edited title")
        report = self.discovery.run({"keywords": "engineer"})
        self.assertEqual(report["added_count"], 0)
        self.assertEqual(report["candidates"][0]["existing_job_id"], existing["id"])
        self.assertEqual(self.store.get("job", existing["id"])["title"], "Keep my edited title")

    def test_public_listing_redirect_uses_final_url_and_preserves_citation(self):
        redirect = "https://example.com/jobs/previous-listing-address"
        self.search.return_value = [{"url": redirect, "label": "Acme"}]
        report = self.discovery.run({"keywords": "engineer"})
        self.browser.call.assert_called_once_with("open", {"url": redirect})
        self.assertEqual(report["candidates"][0]["source_url"], redirect)
        self.assertEqual(report["candidates"][0]["url"], URL)

    def test_failed_pages_persist_scan_progress_and_no_jobs(self):
        self.search.return_value = [{"url": URL}, {"url": URL + "-other"}]
        self.browser.call.side_effect = [{**PAGE, "text": "This job is closed. " + TEXT}, {**PAGE, "url": "https://example.com/search"}]
        report = self.discovery.run({"keywords": "engineer"})
        self.assertEqual(report["inspected_count"], 2)
        self.assertEqual(report["status"], "no_results")
        self.assertEqual(self.store.get("discovery", "main"), report)
        self.client.ask.assert_not_called()
        self.assertEqual(self.store.all("job"), [])

    def test_private_initial_and_redirect_destinations_fail_closed(self):
        self.search.return_value = [{"url": "http://127.0.0.1/jobs/1"}, {"url": URL}]
        self.browser.call.return_value = {**PAGE, "url": "http://127.0.0.1/jobs/2"}
        report = self.discovery.run({"keywords": "engineer"})
        self.assertEqual(report["inspected_count"], 2)
        self.assertEqual(report["status"], "no_results")
        self.browser.call.assert_called_once_with("open", {"url": URL})
        self.client.ask.assert_not_called()

    def test_final_redirect_aliases_are_deduplicated(self):
        self.search.return_value = [{"url": URL}, {"url": URL + "-alias"}]
        report = self.discovery.run({"keywords": "engineer"})
        self.assertEqual(len(self.client.ask.call_args.args[1]["pages"]), 1)
        self.assertEqual(report["added_count"], 1)

    def test_native_search_error_and_empty_results_are_visible(self):
        self.search.side_effect = ProviderError("Search access is unavailable")
        with self.assertRaises(ProviderError):
            self.discovery.run({"keywords": "engineer"})
        report = self.store.get("discovery", "main")
        self.assertEqual(report["status"], "error")
        self.assertTrue(report["finished_at"])
        self.assertIn("Search access is unavailable", report["warnings"])
        self.search.side_effect, self.search.return_value = None, []
        report = self.discovery.run({"keywords": "engineer"})
        self.assertEqual(report["status"], "no_results")
        self.browser_factory.assert_not_called()
        self.client.ask.assert_not_called()

    def test_fabricated_selection_writes_no_jobs_and_reports_failure(self):
        self.client.ask.return_value = {"candidates": [{**ITEM, "candidate_id": "made-up"}]}
        with self.assertRaises(ValueError):
            self.discovery.run({"keywords": "engineer"})
        self.assertEqual(self.store.all("job"), [])
        self.assertEqual(self.store.get("discovery", "main")["status"], "error")
        self.browser.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
