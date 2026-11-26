"""Contact types must stay explicit and profile edits must preserve submission outcomes."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from applydesk.profile import empty_profile, profile_with_defaults, validate_profile
from applydesk.mapping import answer_field
from applydesk.workspace import Workspace


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

    def test_workday_home_cellular_uses_only_confirmed_mobile_choice(self):
        field = {"field_id": "type", "label": "Phone Device Type*", "type": "select", "options": [
            {"value": "home", "label": "Home"}, {"value": "cell", "label": "Home Cellular"}]}
        profile = empty_profile()
        self.assertIsNone(answer_field(field, profile, "US"))
        profile["identity"]["phone_type"] = "mobile"
        self.assertEqual(answer_field(field, profile, "US"),
                         {"field_id": "type", "value": "cell", "fact_ids": ["identity.phone_type"]})

    def test_profile_edit_does_not_reset_submitted_or_uncertain_attempts(self):
        store = Mock()
        statuses = ["new", "submitted", "submitting", "submission_unknown", "ready"]
        store.all.return_value = [{"id": status, "status": status} for status in statuses]
        Workspace.invalidate_profile_jobs(SimpleNamespace(store=store), "changed")
        store.job.assert_called_once_with("ready", status="needs_review", exceptions=["changed"])

    def test_prior_employment_is_not_inferred_from_missing_work_history(self):
        from applydesk.application_answers import confirmed_application_answer
        question = "have you previously worked for example devices as an employee or contractor"
        self.assertIsNone(confirmed_application_answer({}, {"experiences": []}, question, ""))
        profile = {"facts": [{"id": "confirmed", "kind": "application_answer", "confirmed": True,
                             "text": "I have worked for Example Devices as an employee or contractor."}]}
        self.assertEqual(confirmed_application_answer({}, profile, question, ""), ("yes", ["confirmed"]))
        profile["facts"][0]["text"] = "I have not worked for Example Devices as an employee or contractor."
        self.assertEqual(confirmed_application_answer({}, profile, question, ""), ("no", ["confirmed"]))
        self.assertIsNone(confirmed_application_answer({}, profile, question.replace("devices", "labs"), ""))
