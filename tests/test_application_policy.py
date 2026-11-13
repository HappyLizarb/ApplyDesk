"""Broad role matching retains observed evidence and explicit user constraints."""

import copy
import unittest
from unittest.mock import Mock

from applydesk.application_automation import ApplicationAutomation
from applydesk.application_policy import search_scope, validate_scope


class ApplicationPolicyTests(unittest.TestCase):
    """Model decisions are fixtures; these checks exercise the actual policy boundaries."""

    def setUp(self):
        """Use a physical-product role whose duties need no literal hardware category name."""
        self.scope = {"industries": ["Hardware"], "levels": ["Entry"], "locations": ["Boston"]}
        self.source = "Own the mixed-signal sensor product roadmap in Boston. Python required."
        self.decision = {"matches": True, "industry": "Hardware", "level": "", "location": "Boston",
                         "quotes": {"industry": "mixed-signal sensor product roadmap", "level": "", "location": "Boston"},
                         "issues": []}

    def test_blank_saved_defaults_do_not_block_confirmed_profile(self):
        """The active conversation can supply a role even without saved category or level tags."""
        profile = {"confirmed": True, "industries": [], "preferences": {"levels": []}}
        self.assertEqual(search_scope(profile, ["Anywhere"]),
                         {"industries": [], "levels": [], "locations": ["Anywhere"]})
        with self.assertRaisesRegex(ValueError, "Confirm your profile"):
            search_scope({**profile, "confirmed": False}, ["Anywhere"])

    def test_related_duties_and_unspecified_level_are_valid(self):
        """Observed product duties support a broad category without a keyword or level label."""
        self.assertEqual(validate_scope(self.decision, self.scope, self.source), self.decision)
        changed = {**self.decision, "industry": "Product Management"}
        self.assertEqual(validate_scope(changed, self.scope, self.source), changed)

    def test_explicit_locations_and_real_quotes_still_required(self):
        """Classification flexibility cannot invent location evidence or override rejection."""
        wrong_quote = {**self.decision["quotes"], "industry": "Invented unrelated duties"}
        missing_location = {**self.decision["quotes"], "location": ""}
        for changes in ({"location": "Paris"}, {"industry": ""}, {"quotes": wrong_quote}, {"quotes": missing_location},
                        {"matches": False, "issues": ["The user explicitly excluded this role."]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_scope({**self.decision, **changes}, self.scope, self.source)

    def test_unrestricted_location_needs_no_location_quote(self):
        """No location preference is inferred from a home address or category."""
        result = {**self.decision, "location": "Anywhere",
                  "quotes": {**self.decision["quotes"], "location": ""}}
        self.assertEqual(validate_scope(result, {**self.scope, "locations": ["Anywhere"]}, self.source), result)

    def test_scope_classifications_preserve_the_selected_resume_category(self):
        """Overlapping role labels neither veto a job nor replace its uploaded-resume category."""
        automation = ApplicationAutomation.__new__(ApplicationAutomation)
        automation.check_active = Mock()
        instruction = "Include product management for physical products, in Boston."
        automation.run = {"scope": self.scope, "instruction": instruction}
        primary = Mock(ask=Mock(return_value={**copy.deepcopy(self.decision), "industry": "Product Management"}))
        auditor = Mock(ask=Mock(return_value=copy.deepcopy(self.decision)))
        clients = {"application": primary, "auditor": auditor}
        analysis = {"company": "Example", "title": "Product Manager", "category": "Hardware",
                    "required": [{"text": "Python required."}]}
        automation._scope(clients.__getitem__, analysis, {"text": self.source})
        self.assertEqual(analysis["category"], "Hardware")
        self.assertEqual(primary.ask.call_args.args[1]["request"], instruction)
        self.assertEqual(auditor.ask.call_args.args[1]["preferences"], self.scope)


if __name__ == "__main__":
    unittest.main()
