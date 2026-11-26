"""Explicit user answers remain scoped and optional disclosures stay optional."""
import unittest
from applydesk.application_answers import with_application_source
from applydesk.mapping import answer_field, plan_fields


class ApplicationAnswerTests(unittest.TestCase):
    def profile(self, claim):
        return {'confirmed': True, 'identity': {}, 'authorizations': [], 'websites': [], 'preferences': {},
                'facts': [{'id': 'confirmed-chat', 'kind': 'application_answer', 'text': claim, 'confirmed': True}]}

    def test_future_answer_requires_exact_confirmation(self):
        field = {'field_id': 'sponsor', 'type': 'select', 'label': 'Will you need sponsorship to work in the U.S. now or anytime in the future?',
                 'options': [{'label': 'Yes', 'value': 'yes'}, {'label': 'No', 'value': 'no'}]}
        self.assertIsNone(answer_field(field, self.profile('I currently do not need sponsorship.'), 'US'))
        self.assertEqual(answer_field(field, self.profile('I require employer sponsorship now and in the future.'), 'US')['value'], 'yes')
        self.assertEqual(answer_field(field, self.profile('I do not require employer sponsorship now or in the future.'), 'US')['value'], 'no')

    def test_recording_confirmation_does_not_apply_to_other_employers(self):
        field = {'field_id': 'record', 'type': 'radio', 'group': 'Interview Recording Consent', 'label': 'Opt in to recording'}
        profile = self.profile('I accept interview recording at Example Tools.')
        profile['application_company'] = 'Example Tools.'
        self.assertTrue(answer_field(field, profile, 'US')['value'])
        profile['application_company'] = 'Other'
        self.assertIsNone(answer_field(field, profile, 'US'))

    def test_optional_unknown_radio_is_blank_but_required_answer_blocks(self):
        fields = [{'field_id': str(i), 'name': 'gender', 'group_id': 'gender', 'group': 'Gender', 'type': 'radio', 'label': label}
                  for i, label in enumerate(['Male', 'Female'])]
        self.assertEqual(plan_fields({'fields': fields}, self.profile(''), 'US'), ([], []))
        fields[0]['required'] = True
        self.assertTrue(plan_fields({'fields': fields}, self.profile(''), 'US')[1])

    def test_confirmed_question_answer_requires_matching_employer_and_question(self):
        """An employer-scoped saved answer stays private to the matching question and employer."""
        profile = self.profile('Employer: Example+Tools\nQuestion: Which office do you prefer?\nAnswer: North')
        profile = with_application_source(profile, {'company': 'Example+Tools'})
        field = {'field_id': 'office', 'type': 'text', 'label': 'Which office do you prefer?'}
        self.assertEqual(answer_field(field, profile, 'US'),
                         {'field_id': 'office', 'value': 'North', 'fact_ids': ['confirmed-chat']})
        self.assertIsNone(answer_field({**field, 'label': 'What hours do you prefer?'}, profile, 'US'))
        self.assertIsNone(answer_field(field, {**profile, 'application_company': 'Example&Tools'}, 'US'))
        first = profile['facts'][0]
        profile['facts'].append({**first, 'id': 'different', 'text': first['text'].replace('North', 'South')})
        self.assertIsNone(answer_field(field, profile, 'US'))
        first['confirmed'] = False
        self.assertEqual(answer_field(field, profile, 'US')['fact_ids'], ['different'])

    def test_current_employer_preference_overrides_multiple_present_jobs(self):
        profile = self.profile('Current Employer/Current Company: Example Research Institute only.')
        profile['facts'][0]['kind'] = 'professional_experience'
        profile['experiences'] = [{'company': 'Other current employer', 'end': 'Present'},
                                  {'company': 'Example Research Institute', 'end': 'Present'}]
        for label in ['Current company', 'Current employer']:
            field = {'field_id': 'employer', 'type': 'text', 'label': label}
            answer = answer_field(field, profile, 'US')
            self.assertEqual(answer['value'], 'Example Research Institute')
            self.assertEqual(answer['fact_ids'], ['confirmed-chat'])
        self.assertEqual(len(profile['experiences']), 2)
        self.assertEqual(profile['experiences'][0]['company'], 'Other current employer')

    def test_current_employer_canonical_preference_requires_unique_confirmation(self):
        profile = self.profile('Use as current employer: Explicit employer')
        field = {'field_id': 'employer', 'type': 'text', 'label': 'Current employer'}
        self.assertEqual(answer_field(field, profile, 'US')['value'], 'Explicit employer')
        profile['facts'][0]['confirmed'] = False
        self.assertIsNone(answer_field(field, profile, 'US'))
        profile['facts'][0]['confirmed'] = True
        profile['facts'].append({'id': 'conflict', 'kind': 'application_answer', 'confirmed': True,
                                 'text': 'Use as current employer: Conflicting employer'})
        self.assertIsNone(answer_field(field, profile, 'US'))
