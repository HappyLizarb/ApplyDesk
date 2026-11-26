"""Role-specific seniority remains a preference and survives legacy profile reads."""

import copy
import unittest

from applydesk.application_policy import search_scope
from applydesk.profile import empty_profile, profile_with_defaults, validate_profile


class RolePreferenceTests(unittest.TestCase):
    def profile(self):
        """Keep distinct desired levels for two unrelated fictional role families."""
        profile = empty_profile()
        profile["identity"].update(legal_name="Example Applicant", email="example@example.test")
        profile["confirmed"] = True
        profile["industries"] = [{"name": "Publishing", "years": "2", "levels": ["Entry level", "Associate"]},
                                 {"name": "Museum operations", "years": "8", "levels": ["Senior", "Principal"]}]
        return profile

    def test_role_levels_round_trip_without_becoming_qualification_facts(self):
        """The stored association reaches search without claiming sought seniority as experience."""
        source = self.profile()
        saved = validate_profile(source, empty_profile())
        self.assertEqual(saved["industries"], source["industries"])
        self.assertEqual(saved["preferences"]["levels"], ["Entry level", "Associate", "Senior", "Principal"])
        self.assertEqual(profile_with_defaults(saved), saved)
        self.assertEqual(search_scope(saved, ["Anywhere"])["role_preferences"], source["industries"])
        industry_facts = [row["text"] for row in saved["facts"] if row["kind"] == "industries"]
        self.assertEqual(industry_facts, ["name: Publishing; years: 2", "name: Museum operations; years: 8"])

    def test_legacy_global_levels_read_into_each_existing_role_without_mutation(self):
        """Reading old records preserves their revision, confirmation, and stored bytes."""
        source = self.profile()
        source["revision"] = 7
        source["preferences"]["levels"] = ["Internship", "Custom level"]
        for row in source["industries"]:
            row.pop("levels")
        before = copy.deepcopy(source)
        restored = profile_with_defaults(source)
        self.assertEqual(source, before)
        self.assertEqual(restored["revision"], 7)
        self.assertTrue(restored["confirmed"])
        self.assertEqual([row["levels"] for row in restored["industries"]], [source["preferences"]["levels"]] * 2)
        saved = validate_profile(source, source)
        self.assertEqual(saved["industries"], restored["industries"])

    def test_legacy_levels_without_a_role_stay_editable(self):
        """An empty role name retains an older user's levels without inventing an industry."""
        source = empty_profile()
        source["preferences"]["levels"] = ["Director"]
        self.assertEqual(profile_with_defaults(source)["industries"], [{"name": "", "years": "", "levels": ["Director"]}])
        source["preferences"]["levels"] = []
        self.assertEqual(profile_with_defaults(source)["industries"], [])

    def test_cleared_row_levels_do_not_inherit_stale_aggregate_levels(self):
        """An explicit empty choice is different from an old record with no levels field."""
        source = self.profile()
        source["preferences"]["levels"] = ["Director"]
        source["industries"][0]["levels"] = []
        restored = profile_with_defaults(source)
        saved = validate_profile(restored, source)
        self.assertEqual(saved["industries"][0]["levels"], [])
        self.assertEqual(saved["preferences"]["levels"], ["Senior", "Principal"])
        saved["industries"] = []
        saved["preferences"]["levels"] = []
        self.assertEqual(profile_with_defaults(validate_profile(saved, source))["industries"], [])

    def test_each_row_requires_a_bounded_list_of_plain_level_names(self):
        """Malformed choices cannot be coerced into silently discarded role preferences."""
        for invalid in ["Senior", None, [{"level": "Senior"}], ["Senior"] * 201]:
            source = self.profile()
            source["industries"][0]["levels"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_profile(source, empty_profile())
        source = self.profile()
        source["industries"][0]["levels"] = ["Associate", "Associate", ""]
        self.assertEqual(validate_profile(source, empty_profile())["industries"][0]["levels"], ["Associate"])


if __name__ == "__main__":
    unittest.main()
