"""Public listing URLs and availability are checked before use."""

import unittest

from applydesk.discovery import canonical_url, page_problem


URL = "https://careers.example.com/jobs/123/software-engineer"
TEXT = ("Acme is hiring a Software Engineer in San Francisco, US. Remote work is available in the US. "
        "You will build software with a collaborative engineering team. Required qualifications include "
        "three years of production software experience, Python and clear written communication.")
PAGE = {"url": URL, "source_url": URL, "title": "Software Engineer at Acme", "text": TEXT}


class DiscoveryValidationTests(unittest.TestCase):
    def test_canonical_aliases_keep_job_identifiers(self):
        greenhouse = "https://boards.greenhouse.io/acme/jobs/123?gh_jid=123&gh_src=mail&utm_campaign=x#app"
        self.assertEqual(canonical_url(greenhouse), "https://job-boards.greenhouse.io/acme/jobs/123")
        self.assertEqual(canonical_url("https://jobs.lever.co/acme/abc/apply?source=linkedin"), "https://jobs.lever.co/acme/abc")
        self.assertNotEqual(canonical_url(URL + "?job=1"), canonical_url(URL + "?job=2"))

    def test_closed_and_challenge_pages_are_not_listings(self):
        for phrase in ("This job is no longer available", "Verify you are human", "Job has expired"):
            self.assertTrue(page_problem({**PAGE, "text": phrase + TEXT}))
        self.assertTrue(page_problem({**PAGE, "text": "Short"}))
        self.assertEqual(page_problem(PAGE), "")
