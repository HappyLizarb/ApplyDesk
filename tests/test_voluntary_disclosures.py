"""Voluntary identity disclosures are explicit, preserved, and excluded from job selection."""
import unittest

from applydesk.mapping import answer_field
from applydesk.greenhouse_answers import disclosure_issues
from applydesk.profile import empty_profile, model_profile, profile_with_defaults, validate_profile


class VoluntaryDisclosureTests(unittest.TestCase):
    def profile(self):
        """Synthetic applicant explicitly supplies three disclosures, leaving ethnicity unanswered."""
        draft = empty_profile()
        draft['identity'].update(legal_name='Test Person', email='test@example.invalid')
        draft['confirmed'] = True
        draft['preferences'].update(race='Asian', veteran='no', disability='no')
        return validate_profile(draft, empty_profile())

    def test_roundtrip_keeps_explicit_answers_and_no_ethnicity_inference(self):
        profile = self.profile()
        saved = validate_profile(profile, profile)
        self.assertEqual(saved['preferences']['race'], 'Asian')
        self.assertEqual(saved['preferences']['hispanic'], 'unknown')
        facts = {fact['id']: fact for fact in saved['facts']}
        self.assertEqual(facts['preferences.race']['kind'], 'disclosure')
        screened = model_profile(saved)
        for key in ('race', 'hispanic', 'veteran', 'disability', 'gender'):
            self.assertNotIn(key, screened['preferences'])
        self.assertFalse(any(fact['kind'] == 'disclosure' for fact in screened['facts']))

    def test_observed_greenhouse_labels_use_only_explicit_supported_choices(self):
        profile = self.profile()
        field = {'field_id': 'choice', 'widget': 'greenhouse_select', 'type': 'combobox'}
        for label, expected in [('Veteran Status', 'I am not a protected veteran'), ('Please identify your race', 'Asian')]:
            answer = answer_field({**field, 'label': label}, profile, 'US')
            self.assertEqual(answer['value'], expected)
        self.assertIsNone(answer_field({**field, 'label': 'Are you Hispanic/Latino?'}, profile, 'US'))
        self.assertIsNone(answer_field({**field, 'label': 'Gender'}, profile, 'US'))
        profile['confirmed'] = False
        self.assertIsNone(answer_field({**field, 'label': 'Veteran Status'}, profile, 'US'))

    def test_military_service_requires_its_own_answer_and_stays_out_of_screening(self):
        profile = self.profile()
        field = {'field_id': 'service', 'type': 'select', 'label': 'Veteran Status',
                 'options': [{'label': 'I am not a veteran', 'value': 'not-a-veteran'},
                             {'label': 'I identify as a veteran, just not a protected veteran', 'value': 'served'}]}
        self.assertIsNone(answer_field(field, profile, 'US'))
        profile['preferences']['veteran_service'] = 'no'
        saved = validate_profile(profile, profile)
        self.assertEqual(answer_field(field, saved, 'US'), {'field_id': 'service', 'value': 'not-a-veteran',
                                                            'fact_ids': ['preferences.veteran_service']})
        self.assertNotIn('veteran_service', model_profile(saved)['preferences'])
        del profile['preferences']['veteran_service']
        self.assertEqual(validate_profile(profile, saved)['preferences']['veteran_service'], 'no')
        profile['preferences']['veteran_service'] = 'yes'
        self.assertEqual(answer_field(field, validate_profile(profile, saved), 'US')['value'], 'served')

    def test_explicit_asian_answer_can_select_the_matching_multichoice_checkbox(self):
        profile = self.profile()
        field = {'field_id': 'race', 'type': 'checkbox', 'label': 'Asian',
                 'group': 'Please select one or more of the following categories that best describe you.'}
        self.assertEqual(answer_field(field, profile, 'US')['value'], True)
        self.assertIsNone(answer_field({**field, 'label': 'White'}, profile, 'US'))
        profile['confirmed'] = False
        self.assertIsNone(answer_field(field, profile, 'US'))

    def test_old_profiles_and_invalid_structured_race(self):
        old = self.profile()
        del old['preferences']['race'];del old['preferences']['hispanic']
        self.assertEqual(profile_with_defaults(old)['preferences']['race'], '')
        old['preferences']['race'] = {'inferred': 'Asian'}
        with self.assertRaisesRegex(ValueError, 'plain text'):
            validate_profile(old, old)

    def test_requested_race_waits_for_separate_ethnicity_answer(self):
        profile = self.profile()
        page = {'fields': [{'label': 'Are you Hispanic/Latino?'}]}
        self.assertTrue(disclosure_issues(profile, page))
        profile['preferences']['hispanic'] = 'no'
        self.assertEqual(disclosure_issues(profile, page), [])

    def test_gender_roundtrip_is_explicit_and_excluded_from_screening(self):
        profile = self.profile()
        self.assertEqual(profile['preferences']['gender'], '')
        profile['preferences']['gender'] = 'Male'
        saved = validate_profile(profile, profile)
        self.assertEqual(saved['preferences']['gender'], 'Male')
        self.assertIn({'id': 'preferences.gender', 'kind': 'disclosure', 'text': 'Male', 'confirmed': True}, saved['facts'])
        self.assertNotIn('gender', model_profile(saved)['preferences'])
        self.assertFalse(any(fact['id'] == 'preferences.gender' for fact in model_profile(saved)['facts']))
        saved['preferences']['gender'] = {'inferred': 'Male'}
        with self.assertRaisesRegex(ValueError, 'plain text'):
            validate_profile(saved, saved)

    def test_gender_only_fills_matching_choices_from_a_confirmed_profile(self):
        profile = self.profile()
        profile['preferences']['gender'] = 'Male'
        field = {'field_id': 'gender', 'label': 'Gender', 'type': 'select',
                 'options': [{'label': 'Male', 'value': 'm'}, {'label': 'Female', 'value': 'f'}]}
        answer = answer_field(field, profile, 'US')
        self.assertEqual((answer['value'], answer['fact_ids']), ('m', ['preferences.gender']))
        radio = {'field_id': 'male', 'type': 'radio', 'group': 'Gender', 'label': 'Male'}
        self.assertTrue(answer_field(radio, profile, 'US')['value'])
        self.assertIsNone(answer_field({**radio, 'label': 'Female'}, profile, 'US'))
        greenhouse = {'field_id': 'gender', 'widget': 'greenhouse_select', 'type': 'combobox', 'label': 'Gender'}
        self.assertEqual(answer_field(greenhouse, profile, 'US')['value'], 'Male')
        profile['confirmed'] = False
        self.assertIsNone(answer_field(field, profile, 'US'))
        self.assertIsNone(answer_field(greenhouse, profile, 'US'))
