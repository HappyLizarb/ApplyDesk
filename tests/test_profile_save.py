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
        profile["identity"].update(legal_name="Test Applicant", email="test@example.invalid")
        profile["experiences"] = [{"company": "Example employer", "title": "Designer", "job_type": "Contract",
                                   "start": "2024-04", "end": "2025-01", "location": "Remote", "details": "Built forms."}]
        profile["projects"] = [{"name": "Example project", "start": "2025-03", "end": "Present",
                                "details": "Built a tracker.", "url": "https://example.invalid/project"}]
        profile["languages"] = [{"name": "French", "proficiency": "Professional working proficiency"}]
        return profile

    def test_state_advertises_loaded_profile_schema(self):
        state = self.workspace.state()
        self.assertEqual(state["profile_schema"], 2)
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
        for section in ("experiences", "projects", "languages"):
            self.assertEqual(restored[section], source[section])
        self.assertEqual(restored["revision"], 1)

    def test_confirmed_save_retains_new_facts_for_application_context(self):
        source = self.draft()
        source["confirmed"] = True
        saved = self.workspace.save_profile(source)
        facts = {fact["id"]: fact["text"] for fact in saved["facts"]}
        self.assertTrue(saved["confirmed"])
        self.assertIn("job_type: Contract", facts["experiences.0"])
        self.assertIn("location: Remote", facts["experiences.0"])
        self.assertIn("start: 2025-03", facts["projects.0"])
        self.assertIn("Professional working proficiency", facts["languages.0"])

    def test_previous_loaded_whitelist_reproduces_silent_field_loss(self):
        """Emulate the old in-memory schema independently of files already updated on disk."""
        source = self.draft()
        legacy_fields = copy.deepcopy(ROW_FIELDS)
        legacy_fields.pop("languages")
        legacy_fields["experiences"] = ["company", "title", "start", "end", "details"]
        legacy_fields["projects"] = ["name", "details", "url"]
        with patch.dict(ROW_FIELDS, legacy_fields, clear=True):
            old_response = validate_profile(source, {"revision": 5})
        self.assertEqual(old_response["revision"], 6)
        self.assertNotIn("languages", old_response)
        self.assertNotIn("job_type", old_response["experiences"][0])
        self.assertNotIn("location", old_response["experiences"][0])
        self.assertNotIn("start", old_response["projects"][0])
        self.assertNotIn("end", old_response["projects"][0])
        current_response = validate_profile(source, {"revision": 5})
        for section in ("experiences", "projects", "languages"):
            self.assertEqual(current_response[section], source[section])


if __name__ == "__main__":
    unittest.main()
