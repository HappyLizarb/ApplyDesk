"""New profile fields preserve applicant facts and remain unconfirmed on import."""

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from applydesk.agent_contract import profile_draft
from applydesk.intelligence import extract_profile
from applydesk.profile import empty_profile, facts_for, model_profile, profile_with_defaults, validate_profile


class ProfileFieldTests(unittest.TestCase):
    def source(self):
        """Create an applicant-confirmed profile with independently specified new facts."""
        profile = empty_profile()
        profile["identity"].update(legal_name="Test Applicant", email="test@example.invalid")
        profile["confirmed"] = True
        profile["languages"] = [{"name": "English", "proficiency": "Professional working proficiency"}, {"name": "French", "proficiency": ""}]
        profile["experiences"] = [{"company": "Example employer", "title": "Design intern", "job_type": "Internship",
                                   "start": "2025-06", "end": "2025-08", "location": "Remote", "details": "Designed an accessible form."}]
        profile["projects"] = [{"name": "Personal project", "start": "2024", "end": "Present", "details": "Built a course tracker.",
                                "url": "https://example.invalid/project"}]
        return profile

    def test_full_save_preserves_new_fields_and_dates_without_inferred_duration(self):
        source = self.source()
        result = validate_profile(source, {"revision": 4, "transcript_id": "transcript"})
        self.assertEqual(result["experiences"], source["experiences"])
        self.assertEqual(result["projects"], source["projects"])
        self.assertEqual(result["languages"], source["languages"])
        self.assertEqual(result["revision"], 5)
        self.assertEqual(result["transcript_id"], "transcript")
        self.assertNotIn("duration", result["experiences"][0])
        self.assertEqual(result["projects"][0]["start"], "2024")
        self.assertEqual(result["projects"][0]["end"], "Present")

    def test_old_saved_profile_gains_empty_fields_without_mutation_or_reconfirmation(self):
        profile = self.source()
        profile["revision"] = 7
        profile["experiences"] = [{"company": "Old employer", "title": "Old title", "start": "2019", "end": "2020", "details": "Old facts"}]
        profile["projects"] = [{"name": "Old project", "details": "Preserved details", "url": "https://example.invalid"}]
        del profile["languages"]
        before = copy.deepcopy(profile)
        result = profile_with_defaults(profile)
        self.assertEqual(profile, before)
        self.assertEqual(result["revision"], 7)
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["languages"], [])
        for field, value in before["experiences"][0].items():
            self.assertEqual(result["experiences"][0][field], value)
        self.assertEqual(result["experiences"][0]["job_type"], "")
        self.assertEqual(result["experiences"][0]["location"], "")
        self.assertEqual(result["projects"][0]["start"], "")
        self.assertEqual(result["projects"][0]["end"], "")

    def test_imported_fields_survive_but_cannot_confirm_themselves(self):
        source = self.source()
        result = profile_draft({"profile": source, "notes": []})["profile"]
        self.assertEqual(result["experiences"], source["experiences"])
        self.assertEqual(result["projects"], source["projects"])
        self.assertEqual(result["languages"], source["languages"])
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["facts"], [])

    def test_confirmed_facts_include_location_type_and_project_dates(self):
        profile = validate_profile(self.source(), empty_profile())
        facts = {fact["id"]: fact for fact in profile["facts"]}
        self.assertIn("job_type: Internship", facts["experiences.0"]["text"])
        self.assertIn("location: Remote", facts["experiences.0"]["text"])
        self.assertEqual(facts["experiences.0"]["kind"], "professional_experience")
        self.assertIn("start: 2024", facts["projects.0"]["text"])
        self.assertNotEqual(facts["projects.0"]["kind"], "professional_experience")
        self.assertEqual(model_profile(profile)["experiences"], profile["experiences"])
        self.assertEqual(model_profile(profile)["languages"], profile["languages"])
        self.assertEqual(facts["languages.0"]["kind"], "language")
        self.assertIn("Professional working proficiency", facts["languages.0"]["text"])
        self.assertEqual(facts["languages.1"]["text"], "name: French")

    def test_new_row_fields_reject_structured_values(self):
        for section, key in [("experiences", "job_type"), ("experiences", "location"), ("projects", "start"), ("projects", "end"), ("languages", "name"), ("languages", "proficiency")]:
            source = self.source()
            source[section][0][key] = {"untrusted": "object"}
            with self.assertRaisesRegex(ValueError, "plain text"):
                validate_profile(source, empty_profile())

    def test_extraction_keeps_language_rows_and_accepts_older_sources_without_them(self):
        source = self.source()
        client = Mock()
        client.ask.return_value = copy.deepcopy(source)
        result = extract_profile(client, "Applicant-provided language facts")
        self.assertEqual(result["languages"], source["languages"])
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["facts"], [])
        del source["languages"]
        client.ask.return_value = source
        self.assertEqual(extract_profile(client, "Older source")["languages"], [])

    def test_language_rows_are_bounded_and_proficiency_can_remain_unknown(self):
        source = self.source()
        source["languages"] = [{"name": "Spanish", "proficiency": ""}]
        self.assertEqual(validate_profile(source, empty_profile())["languages"][0]["proficiency"], "")
        for invalid in ["English", ["English"], [{"name": "English"}] * 101]:
            source["languages"] = invalid
            with self.assertRaisesRegex(ValueError, "Invalid languages rows"):
                validate_profile(source, empty_profile())


if __name__ == "__main__":
    unittest.main()
