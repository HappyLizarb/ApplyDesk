"""Postal codes remain optional applicant-supplied strings, never inferred location data."""

import copy
import unittest
from unittest.mock import Mock

from applydesk.agent_contract import profile_draft
from applydesk.intelligence import extract_profile
from applydesk.mapping import answer_field
from applydesk.profile import empty_profile, profile_with_defaults, validate_profile


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
                answer = answer_field({"field_id": "postal", "label": "ZIP / Postal code"}, saved, "US")
                self.assertEqual(answer["value"], code)
                self.assertEqual(answer["fact_ids"], ["identity.postal_code"])

    def test_missing_postal_code_remains_empty_without_a_fact_or_answer(self):
        self.profile["identity"].update(city="Boston", region="Massachusetts", country="US")
        self.profile["identity"].pop("postal_code")
        saved = validate_profile(self.profile, empty_profile())
        self.assertEqual(saved["identity"]["postal_code"], "")
        self.assertFalse(any(item["id"] == "identity.postal_code" for item in saved["facts"]))
        self.assertIsNone(answer_field({"field_id": "postal", "label": "Postal code"}, saved, "US"))

    def test_numeric_or_structured_codes_are_rejected_instead_of_coerced(self):
        for value in (501, 501.0, True, None, [], {"code": "00501"}):
            with self.subTest(value=value):
                self.profile["identity"]["postal_code"] = value
                with self.assertRaisesRegex(ValueError, "Postal code must be text"):
                    validate_profile(self.profile, empty_profile())

    def test_legacy_read_does_not_mutate_or_reconfirm_saved_profile(self):
        self.profile["identity"].pop("postal_code")
        self.profile["revision"] = 9
        original = copy.deepcopy(self.profile)
        restored = profile_with_defaults(self.profile)
        self.assertEqual(self.profile, original)
        self.assertEqual(restored["identity"]["postal_code"], "")
        self.assertEqual(restored["revision"], 9)

    def test_extraction_retains_explicit_text_but_needs_user_confirmation(self):
        self.profile["identity"]["postal_code"] = "00501"
        client = Mock()
        client.ask.return_value = copy.deepcopy(self.profile)
        extracted = extract_profile(client, "Applicant address: example address, postal code 00501")
        draft = profile_draft({"profile": extracted, "notes": []})["profile"]
        self.assertEqual(draft["identity"]["postal_code"], "00501")
        self.assertFalse(draft["confirmed"])
        self.assertEqual(draft["facts"], [])
        self.assertIn("postal_code", client.ask.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
