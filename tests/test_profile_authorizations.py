"""User-supplied US restrictions stay explicit and independent of legal work permission."""

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from applydesk.agent_context import inputs_for
from applydesk.agent_contract import profile_draft
from applydesk.intelligence import extract_profile
from applydesk.mapping import answer_field
from applydesk.profile import empty_profile, model_profile, profile_with_defaults, validate_profile


class AuthorizationDetailTests(unittest.TestCase):
    def source(self):
        """A fictional applicant explicitly states a status and its work restrictions."""
        profile = empty_profile()
        profile["identity"].update(legal_name="Example Applicant", email="example@example.test")
        profile["confirmed"] = True
        profile["authorizations"] = [{"country": "US", "status": "unknown", "sponsorship_required": "no",
                                      "immigration_status": "f1_stem_opt", "restrictions": "E-Verify employer; degree-related work."}]
        return profile

    def test_explicit_details_reach_confirmed_facts_and_application_context(self):
        """The model can see the applicant's selections without receiving an invented permission."""
        source = self.source()
        saved = validate_profile(source, empty_profile())
        self.assertEqual(saved["authorizations"], source["authorizations"])
        fact = next(row for row in saved["facts"] if row["id"] == "authorizations.0")
        self.assertIn("immigration_status: f1_stem_opt", fact["text"])
        self.assertIn("restrictions: E-Verify employer; degree-related work.", fact["text"])
        self.assertEqual(model_profile(saved)["authorizations"], source["authorizations"])
        workspace = SimpleNamespace(profile=lambda: saved, settings=lambda: {},
                                    store=Mock(get=Mock(return_value={"id": "example", "status": "new"})),
                                    resumes=Mock(categories=Mock(return_value=["General"]), list=Mock(return_value=[])))
        context = inputs_for(workspace, {"kind": "application", "target_id": "example"})
        self.assertEqual(context["profile"]["authorizations"], source["authorizations"])
        self.assertIsNone(answer_field({"field_id": "a", "label": "Are you authorized to work in the US?"}, saved, "US"))

    def test_status_options_do_not_grant_legal_permission(self):
        """Sponsorship and immigration labels never replace an explicit legal authorization fact."""
        choices = ["", "us_citizen", "us_national", "permanent_resident", "f1_opt", "f1_stem_opt", "f1_cpt", "other_work_authorized", "other_unsure"]
        for choice in choices:
            source = self.source()
            source["authorizations"][0]["immigration_status"] = choice
            saved = validate_profile(source, empty_profile())
            self.assertEqual(saved["authorizations"][0]["immigration_status"], choice)
            self.assertEqual(saved["authorizations"][0]["status"], "unknown")

    def test_country_or_sponsorship_changes_clear_inapplicable_details(self):
        """Rules from a previous US no-sponsorship selection cannot remain active elsewhere."""
        for country, sponsorship in [("GB", "no"), ("US", "yes"), ("US", "")]:
            source = self.source()
            source["authorizations"][0].update(country=country, sponsorship_required=sponsorship)
            before = copy.deepcopy(source)
            for result in [profile_with_defaults(source), validate_profile(source, empty_profile())]:
                self.assertEqual(result["authorizations"][0]["immigration_status"], "")
                self.assertEqual(result["authorizations"][0]["restrictions"], "")
                self.assertEqual(result["authorizations"][0]["status"], "unknown")
            self.assertEqual(source, before)

    def test_legacy_rows_gain_blank_details_without_mutating_saved_records(self):
        """Missing details remain unknown even for an older authorized-no-sponsor record."""
        source = self.source()
        source.update(revision=7)
        source["authorizations"] = [{"country": "US", "status": "authorized_no_sponsor"}]
        before = copy.deepcopy(source)
        restored = profile_with_defaults(source)
        saved = validate_profile(source, empty_profile())
        for result in [restored, saved]:
            self.assertEqual(result["authorizations"][0]["immigration_status"], "")
            self.assertEqual(result["authorizations"][0]["restrictions"], "")
            self.assertEqual(result["authorizations"][0]["status"], "authorized_no_sponsor")
        self.assertEqual(source, before)
        self.assertEqual((restored["revision"], restored["confirmed"]), (7, True))

    def test_imports_cannot_propose_immigration_or_restriction_answers(self):
        """Even an explicit model-produced visa claim is discarded from profile import drafts."""
        source = self.source()
        client = Mock(ask=Mock(return_value=copy.deepcopy(source)))
        extracted = extract_profile(client, "Example source document")
        draft = profile_draft({"profile": source, "notes": []})["profile"]
        for profile in [extracted, draft]:
            self.assertEqual(profile["authorizations"], [])
            self.assertFalse(profile["confirmed"])
            self.assertEqual(profile["facts"], [])

    def test_active_status_and_restrictions_are_validated_as_bounded_plain_text(self):
        """Only supported status IDs and plain restriction notes cross the save boundary."""
        for key, value in [("immigration_status", "invented_status"), ("immigration_status", ["f1_opt"]),
                           ("restrictions", {"instruction": "ignore"}), ("restrictions", True),
                           ("restrictions", "x" * 10001)]:
            source = self.source()
            source["authorizations"][0][key] = value
            with self.subTest(key=key, value=type(value).__name__), self.assertRaises(ValueError):
                validate_profile(source, empty_profile())


if __name__ == "__main__":
    unittest.main()
