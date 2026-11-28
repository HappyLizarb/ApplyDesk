"""New profile fields preserve applicant facts and remain unconfirmed on import."""

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from applydesk.agent_contract import profile_draft
from applydesk.profile import PROFILE_SCHEMA_VERSION, empty_profile, model_profile, profile_with_defaults, validate_profile

from applydesk.workspace import Workspace


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
        profile["education"] = [{"institution": "Example University", "degree": "BS"}]
        profile["identity"].pop("postal_code")
        del profile["languages"]
        before = copy.deepcopy(profile)
        result = profile_with_defaults(profile)
        self.assertEqual(profile, before)
        self.assertEqual(result["revision"], 7)
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["languages"], [])
        self.assertEqual(result["identity"]["postal_code"], "")
        self.assertEqual(result["education"][0]["gpa"], "")
        self.assertEqual(validate_profile(profile, profile)["education"][0]["gpa"], "")
        for field, value in before["experiences"][0].items():
            self.assertEqual(result["experiences"][0][field], value)
        self.assertEqual(result["experiences"][0]["job_type"], "")
        self.assertEqual(result["experiences"][0]["location"], "")
        self.assertEqual(result["projects"][0]["start"], "")
        self.assertEqual(result["projects"][0]["end"], "")

    def test_imported_fields_survive_but_cannot_confirm_themselves(self):
        source = self.source()
        source["identity"]["postal_code"] = "00501"
        source["education"] = [{"institution": "Example University", "gpa": "3.85"}]
        result = profile_draft({"profile": source, "notes": []})["profile"]
        self.assertEqual(result["identity"]["postal_code"], "00501")
        self.assertEqual((result["education"][0]["gpa"], result["education"][0]["gpa_scale"]), ("3.85", ""))
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

    def test_gpa_and_scale_require_bounded_text(self):
        """Separate grading scales cannot smuggle structured data into model context."""
        for field in ("gpa", "gpa_scale"):
            for value in [{"grade": 4}, ["4"], True, None, "x" * 10001]:
                source = self.source()
                source["education"] = [{field: value}]
                with self.subTest(field=field, value=type(value).__name__), self.assertRaises(ValueError):
                    validate_profile(source, empty_profile())


class ProfileSaveTests(unittest.TestCase):
    def setUp(self):
        """Keep every record and credential interaction inside an isolated temporary workspace."""
        self.directory = tempfile.TemporaryDirectory()
        self.secrets = Mock()
        self.secrets.get.return_value = ""
        self.workspace = Workspace(self.directory.name, secrets=self.secrets)

    def tearDown(self):
        """Close only the temporary workspace created for this test."""
        self.workspace.close()
        self.directory.cleanup()

    def draft(self):
        """Use synthetic profile facts covering every field added by the profile redesign."""
        profile = empty_profile()
        profile["identity"].update(legal_name="Test Applicant", email="test@example.invalid", postal_code="SW1A 1AA")
        profile["education"] = [{"institution": "Example University", "degree": "BS", "field": "Engineering",
                                  "gpa": "3.85", "gpa_scale": "4.00", "start": "2021", "end": "2025", "coursework": "Circuit design"}]
        profile["experiences"] = [{"company": "Example employer", "title": "Designer", "job_type": "Contract",
                                   "start": "2024-04", "end": "2025-01", "location": "Remote", "details": "Built forms."}]
        profile["projects"] = [{"name": "Example project", "start": "2025-03", "end": "Present",
                                "details": "Built a tracker.", "url": "https://example.invalid/project"}]
        profile["languages"] = [{"name": "French", "proficiency": "Professional working proficiency"}]
        profile["authorizations"] = [{"country": "US", "status": "unknown", "sponsorship_required": "no",
                                       "immigration_status": "f1_stem_opt", "restrictions": "E-Verify employer; degree-related work."}]
        return profile

    def test_state_advertises_loaded_profile_schema(self):
        state = self.workspace.state()
        self.assertEqual(state["profile_schema"], 11)
        self.assertEqual(state["profile_schema"], PROFILE_SCHEMA_VERSION)
        self.assertIn("languages", state["profile"])
        self.secrets.get.assert_not_called()

    def test_saved_draft_survives_workspace_reopen(self):
        source = self.draft()
        source["preferences"].update(relative_companies=["Studio A", "Studio B"], disciplinary_action="no")
        saved = self.workspace.save_profile(source)
        self.assertFalse(saved["confirmed"])
        self.assertEqual(saved["facts"], [])
        self.workspace.close()
        self.workspace = Workspace(self.directory.name, secrets=self.secrets)
        restored = self.workspace.profile()
        self.assertEqual(restored, saved)
        self.assertEqual(restored["preferences"]["relative_companies"], ["Studio A", "Studio B"])
        self.assertEqual(restored["preferences"]["disciplinary_action"], "no")
        for section in ("education", "experiences", "projects", "languages", "authorizations"):
            self.assertEqual(restored[section], source[section])
        self.assertEqual(restored["revision"], 1)
        self.assertEqual(restored["identity"]["postal_code"], "SW1A 1AA")

    def test_confirmed_save_retains_new_facts_for_application_context(self):
        source = self.draft()
        source["confirmed"] = True
        source["preferences"].update(age_18_or_older="yes", relative_companies=[" Studio A ", "Studio A"], disciplinary_action="yes")
        source["education"][0]["start"] = "2023-08-20"
        saved = self.workspace.save_profile(source)
        facts = {fact["id"]: fact["text"] for fact in saved["facts"]}
        self.assertTrue(saved["confirmed"])
        self.assertEqual(facts["preferences.age_18_or_older"], "18 years or older: yes")
        self.assertEqual(saved["preferences"]["relative_companies"], ["Studio A"])
        self.assertEqual(facts["preferences.disciplinary_action"], "yes")
        screened = model_profile(saved)
        self.assertNotIn("relative_companies", screened["preferences"])
        self.assertNotIn("disciplinary_action", screened["preferences"])
        self.assertFalse(any(fact["id"] == "preferences.disciplinary_action" for fact in screened["facts"]))
        self.assertIn("start: 2023-08-20", facts["education.0"])
        self.assertEqual(facts["identity.postal_code"], "SW1A 1AA")
        self.assertIn("gpa: 3.85; gpa_scale: 4.00", facts["education.0"])
        self.assertIn("job_type: Contract", facts["experiences.0"])
        self.assertIn("location: Remote", facts["experiences.0"])
        self.assertIn("start: 2025-03", facts["projects.0"])
        self.assertIn("Professional working proficiency", facts["languages.0"])

    def test_legacy_free_text_does_not_supply_answers_after_update(self):
        """Past free text cannot become a new affiliation, disclosure, or citable answer."""
        source = self.draft()
        source.update(confirmed=True, revision=8)
        for key in ("disciplinary_action", "relative_companies"):
            del source["preferences"][key]
        source["preferences"]["application_answers"] = ["Yes for Example"]
        retired = {"id": "preferences.application_answers.0", "kind": "application_answer",
                   "text": "Yes for Example", "confirmed": True}
        source["facts"] = [retired]
        self.workspace.store.put("profile", "main", source)
        restored = self.workspace.profile()
        self.assertEqual(restored["preferences"]["disciplinary_action"], "unknown")
        self.assertEqual(restored["preferences"]["relative_companies"], [])
        for current in (restored, model_profile(source), self.workspace.save_profile(source)):
            self.assertNotIn("application_answers", current["preferences"])
            self.assertNotIn(retired, current["facts"])
        self.assertEqual(restored["revision"], 8)

    def test_affiliations_and_discipline_support_old_clients_and_explicit_clearing(self):
        """An older form can still edit a profile without deleting the newer answers."""
        source = self.draft()
        source["preferences"].update(relative_companies=["Studio A"], disciplinary_action="yes")
        self.workspace.save_profile(source)
        for key in ("disciplinary_action", "relative_companies"):
            del source["preferences"][key]
        saved = self.workspace.save_profile(source)
        self.assertEqual(saved["preferences"]["relative_companies"], ["Studio A"])
        self.assertEqual(saved["preferences"]["disciplinary_action"], "yes")
        saved["preferences"].update(relative_companies=[], disciplinary_action="unknown")
        cleared = self.workspace.save_profile(saved)
        self.assertEqual(cleared["preferences"]["relative_companies"], [])
        self.assertEqual(cleared["preferences"]["disciplinary_action"], "unknown")
        for key, value in (("relative_companies", "Studio A"), ("relative_companies", [42]),
                           ("disciplinary_action", "decline")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.workspace.save_profile({**cleared, "preferences": {**cleared["preferences"], key: value}})

    def test_legacy_gpa_read_does_not_write_or_reconfirm_the_profile(self):
        """Opening schema-five controls must not change persisted legacy profile bytes."""
        source = self.draft()
        source["education"] = [{"gpa": "3.85 / 4.00"}, {"gpa": "3.85"},
                               {"gpa": "3.85 / 4.00", "gpa_scale": ""},
                               {"gpa": "3.85 / 4.00", "gpa_scale": "Explicit scale"},
                               {"gpa": "3.85 / 4.00 weighted"}]
        source.update(revision=9, confirmed=True)
        self.workspace.store.put("profile", "main", source)
        restored = self.workspace.profile()
        grades = [(row["gpa"], row["gpa_scale"]) for row in restored["education"]]
        self.assertEqual(grades, [("3.85", "4.00"), ("3.85", ""), ("3.85 / 4.00", ""),
                                  ("3.85 / 4.00", "Explicit scale"), ("3.85 / 4.00 weighted", "")])
        self.assertEqual(restored["facts"], source["facts"])
        self.assertEqual((restored["revision"], restored["confirmed"]), (9, True))
        self.assertEqual(self.workspace.store.get("profile", "main"), source)


if __name__ == "__main__":
    unittest.main()
