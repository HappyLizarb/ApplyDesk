"""Profile field round trips and conservative application answers use fictional records."""

import unittest

from applydesk.profile import empty_profile, profile_with_defaults, validate_profile


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
        facts = {row['id']: row['text'] for row in saved['facts']}
        self.assertEqual(facts['identity.street_address'], '123 Example Street')
        self.assertEqual(facts['identity.phone_country_code'], '+44')
        self.assertEqual(facts['identity.phone'], '20 7946 0000')

    def test_github_website_round_trips_and_answers_its_matching_field(self):
        """GitHub retains its own type instead of replacing a personal portfolio link."""
        self.raw['websites'] = [{'type': 'GitHub', 'url': 'https://github.com/example'},
                                {'type': 'Portfolio', 'url': 'https://example.test'}]
        saved = validate_profile(self.raw, empty_profile())
        self.assertEqual(saved['websites'], self.raw['websites'])
        self.assertTrue(any(fact['id'] == 'websites.0' for fact in saved['facts']))

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
            self.assertEqual(saved['authorizations'][0]['sponsorship_required'], sponsor)

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


class PhoneTypeTests(unittest.TestCase):
    def test_unknown_remains_unknown_and_confirmed_choice_survives_save(self):
        self.assertEqual(profile_with_defaults({"identity": {}})["identity"]["phone_type"], "")
        profile = empty_profile()
        profile["identity"].update(legal_name="Example Candidate", email="example@example.test", phone_type="mobile")
        profile["confirmed"] = True
        saved = validate_profile(profile, {})
        self.assertEqual(saved["identity"]["phone_type"], "mobile")
        self.assertIn("identity.phone_type", {fact["id"] for fact in saved["facts"]})
        profile["identity"]["phone_type"] = "guess"
        with self.assertRaises(ValueError):
            validate_profile(profile, {})


class PostalCodeTests(unittest.TestCase):
    """Fictional domestic and international codes exercise the public profile boundary."""

    def setUp(self):
        self.profile = empty_profile()
        self.profile["identity"].update(legal_name="Test Applicant", email="test@example.invalid")
        self.profile["confirmed"] = True

    def test_codes_preserve_leading_zeros_letters_spaces_and_hyphens(self):
        for code in ("00501", "02108-1234", "SW1A 1AA", "K1A 0B1", "00100"):
            with self.subTest(code=code):
                self.profile["identity"]["postal_code"] = code
                saved = validate_profile(self.profile, empty_profile())
                self.assertEqual(saved["identity"]["postal_code"], code)
                fact = next(item for item in saved["facts"] if item["id"] == "identity.postal_code")
                self.assertEqual(fact, {"id": "identity.postal_code", "text": code, "kind": "identity", "confirmed": True})

    def test_missing_postal_code_remains_empty_without_a_fact_or_answer(self):
        self.profile["identity"].update(city="Boston", region="Massachusetts", country="US")
        self.profile["identity"].pop("postal_code")
        saved = validate_profile(self.profile, empty_profile())
        self.assertEqual(saved["identity"]["postal_code"], "")
        self.assertFalse(any(item["id"] == "identity.postal_code" for item in saved["facts"]))

    def test_numeric_or_structured_codes_are_rejected_instead_of_coerced(self):
        for value in (501, 501.0, True, None, [], {"code": "00501"}):
            with self.subTest(value=value):
                self.profile["identity"]["postal_code"] = value
                with self.assertRaisesRegex(ValueError, "Postal code must be text"):
                    validate_profile(self.profile, empty_profile())
