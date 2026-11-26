"""Profile saves require a compatible running server and preserve new facts on disk."""

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from applydesk.profile import PROFILE_SCHEMA_VERSION, ROW_FIELDS, empty_profile, validate_profile
from applydesk.workspace import Workspace


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
        self.assertEqual(state["profile_schema"], 10)
        self.assertEqual(state["profile_schema"], PROFILE_SCHEMA_VERSION)
        self.assertIn("languages", state["profile"])
        self.secrets.get.assert_not_called()

    def test_saved_draft_survives_workspace_reopen(self):
        source = self.draft()
        saved = self.workspace.save_profile(source)
        self.assertFalse(saved["confirmed"])
        self.assertEqual(saved["facts"], [])
        self.workspace.close()
        self.workspace = Workspace(self.directory.name, secrets=self.secrets)
        restored = self.workspace.profile()
        self.assertEqual(restored, saved)
        for section in ("education", "experiences", "projects", "languages", "authorizations"):
            self.assertEqual(restored[section], source[section])
        self.assertEqual(restored["revision"], 1)
        self.assertEqual(restored["identity"]["postal_code"], "SW1A 1AA")

    def test_confirmed_save_retains_new_facts_for_application_context(self):
        source = self.draft()
        source["confirmed"] = True
        source["preferences"].update(age_18_or_older="yes", application_answers=["Use the highest posted base salary."])
        source["education"][0]["start"] = "2023-08-20"
        saved = self.workspace.save_profile(source)
        facts = {fact["id"]: fact["text"] for fact in saved["facts"]}
        self.assertTrue(saved["confirmed"])
        self.assertEqual(facts["preferences.age_18_or_older"], "18 years or older: yes")
        self.assertEqual(facts["preferences.application_answers.0"], "Use the highest posted base salary.")
        self.assertIn("start: 2023-08-20", facts["education.0"])
        self.assertEqual(facts["identity.postal_code"], "SW1A 1AA")
        self.assertIn("gpa: 3.85; gpa_scale: 4.00", facts["education.0"])
        self.assertIn("job_type: Contract", facts["experiences.0"])
        self.assertIn("location: Remote", facts["experiences.0"])
        self.assertIn("start: 2025-03", facts["projects.0"])
        self.assertIn("Professional working proficiency", facts["languages.0"])

    def test_previous_loaded_whitelist_reproduces_silent_field_loss(self):
        """Emulate the old in-memory schema independently of files already updated on disk."""
        source = self.draft()
        legacy_fields = copy.deepcopy(ROW_FIELDS)
        legacy_fields.pop("languages")
        legacy_fields["education"] = ["institution", "degree", "field", "start", "end", "coursework"]
        legacy_fields["experiences"] = ["company", "title", "start", "end", "details"]
        legacy_fields["projects"] = ["name", "details", "url"]
        with patch.dict(ROW_FIELDS, legacy_fields, clear=True):
            old_response = validate_profile(source, {"revision": 5})
        self.assertEqual(old_response["revision"], 6)
        self.assertNotIn("languages", old_response)
        self.assertNotIn("gpa", old_response["education"][0])
        self.assertNotIn("gpa_scale", old_response["education"][0])
        self.assertNotIn("job_type", old_response["experiences"][0])
        self.assertNotIn("location", old_response["experiences"][0])
        self.assertNotIn("start", old_response["projects"][0])
        self.assertNotIn("end", old_response["projects"][0])
        current_response = validate_profile(source, {"revision": 5})
        for section in ("education", "experiences", "projects", "languages", "authorizations"):
            self.assertEqual(current_response[section], source[section])

    def test_legacy_gpa_read_does_not_write_or_reconfirm_the_profile(self):
        """Opening schema-five controls must not change persisted legacy profile bytes."""
        source = self.draft()
        source["education"][0].update(gpa="3.85 / 4.00")
        source["education"][0].pop("gpa_scale")
        source.update(revision=9, confirmed=True)
        self.workspace.store.put("profile", "main", source)
        restored = self.workspace.profile()
        self.assertEqual(restored["education"][0]["gpa"], "3.85")
        self.assertEqual(restored["education"][0]["gpa_scale"], "4.00")
        self.assertEqual((restored["revision"], restored["confirmed"]), (9, True))
        self.assertEqual(self.workspace.store.get("profile", "main"), source)


if __name__ == "__main__":
    unittest.main()
