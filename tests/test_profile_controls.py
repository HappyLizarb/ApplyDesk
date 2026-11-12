"""Profile field round trips and conservative application answers use fictional records."""

import unittest

from applydesk.mapping import answer_field, plan_fields
from applydesk.profile import empty_profile, validate_profile


class ProfileControlTests(unittest.TestCase):
    """New controls must not invent date precision, calling codes, or legal permission."""

    def setUp(self):
        """Create a confirmed fixture without using any local account data."""
        self.raw = empty_profile()
        self.raw['identity'].update(legal_name='Avery Example', email='avery@example.test')
        self.raw['confirmed'] = True

    def test_address_and_phone_parts_roundtrip_with_citations(self):
        """A combined phone answer requires both the code and the local-number facts."""
        self.raw['identity'].update(street_address='123 Example Street', phone_country_code='+44', phone='20 7946 0000')
        saved = validate_profile(self.raw, empty_profile())
        address = answer_field({'field_id': 'address', 'label': 'Street address'}, saved, 'GB')
        self.assertEqual(address['value'], '123 Example Street')
        self.assertEqual(address['fact_ids'], ['identity.street_address'])
        phone = answer_field({'field_id': 'phone', 'label': 'Phone'}, saved, 'GB')
        self.assertEqual(phone['value'], '+44 20 7946 0000')
        self.assertEqual(phone['fact_ids'], ['identity.phone', 'identity.phone_country_code'])

    def test_legacy_phone_never_gets_an_inferred_code(self):
        """National and already-international legacy strings remain lossless."""
        for phone in ['415 555 0101', '+1 415 555 0101']:
            self.raw['identity'].pop('phone_country_code', None)
            self.raw['identity']['phone'] = phone
            saved = validate_profile(self.raw, empty_profile())
            self.assertEqual(saved['identity']['phone'], phone)
            self.assertEqual(saved['identity']['phone_country_code'], '')

    def test_explicit_code_cannot_duplicate_or_conflict(self):
        """Pasting an international phone either strips the matching code or reports a mismatch."""
        self.raw['identity'].update(phone_country_code='+44', phone='+44 20 7946 0000')
        self.assertEqual(validate_profile(self.raw, empty_profile())['identity']['phone'], '20 7946 0000')
        self.raw['identity']['phone_country_code'] = '+1'
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            validate_profile(self.raw, empty_profile())

    def test_date_precision_and_multiple_levels_survive(self):
        """Year-only dates and ongoing study never acquire an invented month."""
        self.raw['education'] = [{'start': '2020', 'end': 'Present'}, {'start': '2022-09', 'end': '2025-06'}]
        self.raw['preferences']['levels'] = ['Internship', 'Entry level']
        saved = validate_profile(self.raw, empty_profile())
        self.assertEqual([(r['start'], r['end']) for r in saved['education']], [('2020', 'Present'), ('2022-09', '2025-06')])
        self.assertEqual(saved['preferences']['levels'], ['Internship', 'Entry level'])

    def test_sponsorship_does_not_grant_authorization(self):
        """Both sponsorship answers leave an unknown permission question unresolved."""
        for sponsor in ['yes', 'no']:
            self.raw['authorizations'] = [{'country': 'US', 'sponsorship_required': sponsor}]
            saved = validate_profile(self.raw, empty_profile())
            self.assertEqual(saved['authorizations'][0]['status'], 'unknown')
            self.assertIsNone(answer_field({'field_id': 'a', 'label': 'Are you authorized to work in the US?'}, saved, 'US'))
            answer = answer_field({'field_id': 's', 'label': 'Do you require sponsorship?'}, saved, 'US')
            self.assertEqual(answer['value'], sponsor)

    def test_existing_authorization_is_preserved(self):
        """Old permission records retain their meaning when sponsorship changes separately."""
        self.raw['authorizations'] = [{'country': 'US', 'status': 'authorized_no_sponsor'}]
        self.assertEqual(validate_profile(self.raw, empty_profile())['authorizations'][0]['sponsorship_required'], 'no')
        self.raw['authorizations'][0]['sponsorship_required'] = 'yes'
        saved = validate_profile(self.raw, empty_profile())
        self.assertEqual(saved['authorizations'][0]['status'], 'authorized_need_sponsor')
        self.raw['authorizations'][0]['status'] = 'not_authorized'
        self.assertEqual(validate_profile(self.raw, empty_profile())['authorizations'][0]['status'], 'not_authorized')

    def test_unknown_legacy_sponsorship_stays_unknown(self):
        """Not authorized does not imply whether sponsorship is needed."""
        self.raw['authorizations'] = [{'country': 'US', 'status': 'not_authorized'}]
        saved = validate_profile(self.raw, empty_profile())
        self.assertEqual(saved['authorizations'][0]['sponsorship_required'], '')
        self.assertIsNone(answer_field({'field_id': 's', 'label': 'Do you require sponsorship?'}, saved, 'US'))

    def test_separate_form_calling_code_avoids_repeating_the_prefix(self):
        """A form with its own dialing-code box receives the national number separately."""
        self.raw['identity'].update(phone_country_code='+44', phone='20 7946 0000')
        saved = validate_profile(self.raw, empty_profile())
        fields = [{'field_id': 'code', 'label': 'Calling code', 'form_id': 'one'},
                  {'field_id': 'number', 'label': 'Phone', 'form_id': 'one'},
                  {'field_id': 'other', 'label': 'Phone', 'form_id': 'two'}]
        answers, issues = plan_fields({'fields': fields}, saved, 'GB')
        self.assertEqual(issues, [])
        self.assertEqual([a['value'] for a in answers], ['+44', '20 7946 0000', '+44 20 7946 0000'])
