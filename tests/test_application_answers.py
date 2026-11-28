"""Explicit user answers remain scoped and optional disclosures stay optional."""

import hashlib
import unittest
from datetime import date

from applydesk.agent_context import current_company_answer, employer_answers, form_profile, with_application_source
from applydesk.agent_contract import validate_answers
from applydesk.application_terms import disciplinary_question
from applydesk.native_evidence import validate_operation
from applydesk.native_review import _answer_issues
from applydesk.profile import empty_profile


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


class ProfileAnswerScopeTests(unittest.TestCase):
    question = "Have you ever been terminated or asked to resign from a job or academic institution?"
    identity = "preferences.disciplinary_action"

    def profile(self, answer="yes"):
        profile = empty_profile()
        profile.update(confirmed=True, facts=[{"id": self.identity, "text": answer, "kind": "disclosure", "confirmed": True}])
        profile["preferences"].update(relative_companies=["Other Employer", "Example, Inc."], disciplinary_action=answer)
        profile["experiences"] = [{"company": "Example, Inc."}, {"company": "Other Employer"}]
        return profile

    def test_company_answers_use_whole_names_and_current_posting(self):
        profile = self.profile()
        for company, result in (("Example", "yes."), ("Example Labs", "no.")):
            context = with_application_source(profile, {"company": company})
            answers = {fact["id"]: fact["text"] for fact in context["facts"]}
            for key in ("application.employment_history", "application.relative_affiliation"):
                self.assertTrue(answers[key].endswith(result))
                self.assertNotIn("Other Employer", answers[key])
            self.assertEqual(context["facts"], with_application_source(context, {"company": company})["facts"])
            profile = context
        url = "https://jobs.example.test/openings/14"
        description = "Example Corporation is hiring an Engineer. Recruitment handled by Other Employer."
        source = {"url": url, "company": "Example Corporation", "title": "Engineer", "description": description,
                  "description_sha256": hashlib.sha256(description.encode()).hexdigest(), "complete": True, "captured_at": "2026-11-22"}
        job = {"url": url, "company": "EXCO", "title": "Engineer", "public_source": source}
        self.assertEqual([value for _, value in employer_answers(profile, job)],
                         ["Relative affiliated with Example Corporation: yes.", "Listed employment at Example Corporation: yes."])
        profile["experiences"] = [{"company": "Other Employer"}]
        profile["preferences"]["relative_companies"] = ["Other Employer"]
        recruiter = dict(job, company="Other Employer", url="https://board.example/jobs/15", recommendation_source={"employer_url": url})
        self.assertEqual([value for _, value in employer_answers(profile, recruiter)],
                         ["Relative affiliated with Example Corporation: no.", "Listed employment at Example Corporation: no."])
        for unbound in (dict(job, url=url + "9"), dict(job, public_source=dict(source, description="changed")), dict(job, public_source="legacy")):
            self.assertFalse(employer_answers(profile, unbound))
        profile["experiences"] = []
        profile["preferences"]["relative_companies"] = []
        self.assertEqual(dict(employer_answers(profile, {"company": "Example"})),
                         {"application.relative_affiliation": "Relative affiliated with Example: no."})
        self.assertFalse(employer_answers({**profile, "confirmed": False}, {"company": "Example"}))

    def test_disclosure_reaches_only_its_own_native_or_draft_question(self):
        unrelated = ("Have you ever been dismissed from jury duty?", "Have you ever been terminated from a gym membership?",
                     "Have you ever supervised an employee who was asked to resign?",
                     "Have you ever been fired from a job for misconduct?")
        for draft in (True, False):
            for wording in (self.question, *unrelated):
                with self.subTest(draft=draft, wording=wording):
                    context = form_profile(self.profile(), wording, draft=draft)
                    self.assertNotIn("relative_companies", context["preferences"])
                    self.assertNotIn("disciplinary_action", context["preferences"])
                    self.assertEqual(any(f["id"] == self.identity for f in context["facts"]), wording == self.question)

    def test_compound_disclosure_preserves_scope_of_both_yes_and_no(self):
        cases = ((self.question, True, True),
                 ("Were you asked to resign or terminated from any academic institution or job?", True, True),
                 ("Terminated or asked to resign from any job/academic institution?", True, True),
                 ("In the last five years, have you ever been terminated or asked to resign from a job or school?", False, False),
                 ("Have you ever been terminated from a job?", False, True),
                 ("Have you ever been asked to resign from a school?", False, True),
                 ("Have you ever been terminated or asked to resign from any job?", False, True),
                 ("Have you ever been terminated or asked to resign from any job or school for cause?", False, True),
                 ("Have you ever been terminated or asked to resign from any job or school in the last 3 years?", False, True),
                 ("Have you ever been terminated or asked to resign from a job at Example?", False, False),
                 ("Have you ever received any disciplinary action?", False, False),
                 ("Have you ever been disciplined at a school?", False, False),
                 ("Have you ever been terminated or warned at any job or school?", False, False))
        for question, yes, no in cases:
            for answer, allowed in (("yes", yes), ("no", no)):
                with self.subTest(question=question, answer=answer):
                    self.assertEqual(disciplinary_question(question, answer), allowed)

    def test_draft_accepts_terms_and_rejects_unrelated_or_unconfirmed_declarations(self):
        cases = (("I agree to the application privacy policy", "Yes", [], True),
                 ("I agree to the terms and certify I am at least 18 years old", "Yes", [], False),
                 ("I accept the privacy policy and confirm I have never been convicted of a felony", "Yes", [], False),
                 ("I consent to optional marketing", "Yes", [], False),
                 (self.question, "Yes", [self.identity], True), (self.question, "No", [self.identity], False),
                 ("What are your hobbies?", "Yes", [self.identity], False))
        for question, value, ids, valid in cases:
            with self.subTest(question=question, value=value):
                answers = [{"question": question, "answer": value, "fact_ids": ids}]
                if valid:
                    self.assertEqual(validate_answers(answers, question, self.profile()), answers)
                else:
                    with self.assertRaises(ValueError):
                        validate_answers(answers, question, self.profile())

    def test_native_actions_and_review_bind_disclosure_to_correct_control(self):
        profile = self.profile()
        for label, value, valid in ((self.question, "Yes", True), (self.question, "No", False),
                                    ("Hobbies", "Yes", False)):
            with self.subTest(label=label, value=value):
                field = {"id": "a", "label": label, "value": value, "role": "AXTextField", "enabled": True}
                page = {"text": self.question, "elements": [field]}
                op = {"action": "type", "element_id": "a", "intent": "answer", "text": value, "fact_ids": [self.identity]}
                if valid:
                    self.assertEqual(validate_operation(profile, page, op), op)
                else:
                    with self.assertRaisesRegex(ValueError, "disciplinary"):
                        validate_operation(profile, page, op)
                review = {"answers": [{"element_id": "a", "quote": label, "value": value, "fact_ids": [self.identity]}]}
                self.assertEqual(bool(_answer_issues(profile, page, review, self.question + "\n" + label)), not valid)


    def test_generic_yes_is_bound_to_its_immediately_preceding_question(self):
        profile = self.profile()
        static = {"id": "q", "role": "AXStaticText", "label": self.question}
        yes = {"id": "yes", "role": "AXRadioButton", "label": "Yes", "enabled": True}
        page = {"elements": [static, yes]}
        action = {"action": "click", "intent": "answer", "element_id": "yes", "fact_ids": [self.identity],
                  "question_quote": self.question}
        self.assertEqual(validate_operation(profile, page, action), action)
        page["elements"].insert(1, {"id": "hobbies", "role": "AXStaticText", "label": "Do you have hobbies?"})
        with self.assertRaisesRegex(ValueError, "disciplinary"):
            validate_operation(profile, page, action)
