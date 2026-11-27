"""Explicit user answers remain scoped and optional disclosures stay optional."""

import unittest
from datetime import date

from applydesk.agent_context import current_company_answer, with_application_source


class ApplicationAnswerTests(unittest.TestCase):
    def profile(self, claim):
        return {'confirmed': True, 'identity': {}, 'authorizations': [], 'websites': [], 'preferences': {},
                'facts': [{'id': 'confirmed-chat', 'kind': 'application_answer', 'text': claim, 'confirmed': True}]}

    def test_current_employer_preference_overrides_multiple_present_jobs(self):
        profile = self.profile('Current Employer/Current Company: Example Research Institute only.')
        profile['facts'][0]['kind'] = 'professional_experience'
        profile['experiences'] = [{'company': 'Other current employer', 'end': 'Present'},
                                  {'company': 'Example Research Institute', 'end': 'Present'}]
        self.assertEqual(current_company_answer(profile), ('Example Research Institute', ['confirmed-chat']))
        self.assertEqual(len(profile['experiences']), 2)
        self.assertEqual(profile['experiences'][0]['company'], 'Other current employer')

    def test_current_employer_canonical_preference_requires_unique_confirmation(self):
        profile = self.profile('Use as current employer: Explicit employer')
        self.assertEqual(current_company_answer(profile)[0], 'Explicit employer')
        profile['facts'][0]['confirmed'] = False
        self.assertIsNone(current_company_answer(profile))
        profile['facts'][0]['confirmed'] = True
        profile['facts'].append({'id': 'conflict', 'kind': 'application_answer', 'confirmed': True,
                                 'text': 'Use as current employer: Conflicting employer'})
        self.assertIsNone(current_company_answer(profile))


class ApplicationSourceTests(unittest.TestCase):
    def test_signing_date_is_current_and_never_saved_in_the_profile(self):
        profile = {"confirmed": True, "facts": []}
        job = {"url": "https://jobs.example.test/role"}
        context = with_application_source(profile, job)
        refreshed = with_application_source(context, job)
        dates = [fact for fact in refreshed["facts"] if fact["kind"] == "application_date"]
        self.assertEqual([fact["id"] for fact in dates], ["application.signing_date." + date.today().isoformat()])
        self.assertIn(date.today().strftime("%m/%d/%Y"), dates[0]["text"])
        self.assertEqual(profile["facts"], [])

    def test_application_uses_its_saved_discovery_source(self):
        """A source answer must match this job even if the previous job had a different source."""
        profile = {"confirmed": True, "facts": [], "identity": {}, "authorizations": [], "preferences": {}}
        context = profile
        for url, source in (("https://www.linkedin.com/jobs/view/123", "LinkedIn"),
                            ("https://app.joinhandshake.com/job-search/123", "Handshake")):
            context = with_application_source(context, {"url": url})
            facts = [fact for fact in context['facts'] if fact['kind'] == 'application_source']
            self.assertEqual([(fact['id'], fact['text']) for fact in facts], [('application.discovery_source', source)])
        self.assertEqual(profile["facts"], [])
        for url in ("https://linkedin.com.example.test/jobs/123", "https://joinhandshake.com.example.test/job-search/123",
                    "https://jobs.ashbyhq.com/sample-employer/job"):
            self.assertFalse(any(fact['kind'] == 'application_source'
                                 for fact in with_application_source(context, {'url': url})['facts']))
