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

    def test_gpa_round_trip_preserves_the_supplied_value_and_scale(self):
        """GPA remains text rather than acquiring an assumed grading system or numeric range."""
        for gpa in ["3.85 / 4.00", "8.7 / 10", "92%", "4.50 weighted", "Not reported", ""]:
            source = self.source()
            source["education"] = [{"institution": "Example University", "gpa": gpa}]
            saved = validate_profile(source, empty_profile())
            self.assertEqual(saved["education"][0]["gpa"], gpa)
            self.assertEqual(model_profile(saved)["education"][0]["gpa"], gpa)
            fact = next(row for row in saved["facts"] if row["id"] == "education.0")
            self.assertEqual("gpa:" in fact["text"], bool(gpa))
            if gpa:
                self.assertIn("gpa: " + gpa, fact["text"])

    def test_legacy_education_gets_empty_gpa_without_mutating_saved_data(self):
        """Adding the optional field cannot fabricate a grade or reconfirm an older profile."""
        source = self.source()
        source["revision"] = 7
        source["education"] = [{"institution": "Example University", "degree": "BS"}]
        before = copy.deepcopy(source)
        restored = profile_with_defaults(source)
        self.assertEqual(source, before)
        self.assertEqual(restored["education"][0]["gpa"], "")
        self.assertEqual(restored["revision"], 7)
        self.assertTrue(restored["confirmed"])
        self.assertEqual(validate_profile(source, source)["education"][0]["gpa"], "")

    def test_imported_gpa_remains_unconfirmed_and_never_gains_a_scale(self):
        """Both extraction entry points retain the explicit source text pending user review."""
        source = self.source()
        source["education"] = [{"institution": "Example University", "gpa": "3.85"}]
        client = Mock(ask=Mock(return_value=copy.deepcopy(source)))
        extracted = extract_profile(client, "Example University; GPA: 3.85")
        draft = profile_draft({"profile": extracted, "notes": []})["profile"]
        self.assertEqual(extracted["education"][0]["gpa"], "3.85")
        self.assertEqual(draft["education"][0]["gpa"], "3.85")
        self.assertFalse(draft["confirmed"])
        self.assertEqual(draft["facts"], [])
        source["education"][0].pop("gpa")
        self.assertEqual(profile_draft({"profile": source, "notes": []})["profile"]["education"][0]["gpa"], "")

    def test_gpa_rejects_structured_or_boolean_values(self):
        """The optional text field cannot accept model objects or boolean claims."""
        for value in [{"value": 3.8}, ["3.8"], True, None]:
            source = self.source()
            source["education"] = [{"gpa": value}]
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "plain text"):
                validate_profile(source, empty_profile())

    def test_separate_gpa_scale_survives_save_and_source_import(self):
        """Explicit values remain distinct text without a default scale or range restriction."""
        source = self.source()
        source["education"] = [{"institution": "Example University", "gpa": "8.70", "gpa_scale": "10.00"}]
        saved = validate_profile(source, empty_profile())
        imported = profile_draft({"profile": source, "notes": []})["profile"]
        for profile in (saved, imported, model_profile(saved)):
            self.assertEqual(profile["education"][0]["gpa"], "8.70")
            self.assertEqual(profile["education"][0]["gpa_scale"], "10.00")
        self.assertFalse(imported["confirmed"])
        self.assertEqual(imported["facts"], [])

    def test_legacy_numeric_gpa_ratio_splits_on_read_only_when_scale_is_absent(self):
        """A slash is source evidence for both numbers, while explicit empty scales stay empty."""
        source = self.source()
        source["revision"] = 9
        source["education"] = [{"gpa": "3.85 / 4.00"}, {"gpa": "3.85"},
                               {"gpa": "3.85 / 4.00", "gpa_scale": ""},
                               {"gpa": "3.85 / 4.00", "gpa_scale": "Explicit scale"},
                               {"gpa": "3.85 / 4.00 weighted"}]
        before = copy.deepcopy(source)
        restored = profile_with_defaults(source)
        actual = [(row["gpa"], row["gpa_scale"]) for row in restored["education"]]
        self.assertEqual(actual, [("3.85", "4.00"), ("3.85", ""), ("3.85 / 4.00", ""),
                                  ("3.85 / 4.00", "Explicit scale"), ("3.85 / 4.00 weighted", "")])
        self.assertEqual(source, before)
        self.assertEqual(restored["revision"], 9)
        self.assertTrue(restored["confirmed"])
        self.assertEqual(restored["facts"], source["facts"])

    def test_gpa_scale_uses_the_same_bounded_text_validation(self):
        """Separate grading scales cannot smuggle structured data into model context."""
        for value in [{"scale": 4}, ["4"], True, None, "x" * 10001]:
            source = self.source()
            source["education"] = [{"gpa": "3.85", "gpa_scale": value}]
            with self.subTest(value=type(value).__name__), self.assertRaises(ValueError):
                validate_profile(source, empty_profile())


if __name__ == "__main__":
    unittest.main()
