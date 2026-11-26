"""Synthetic search and application flows retain unclear jobs while blocking unsafe assumptions."""

import hashlib
import unittest

import test_public_applications as public_applications


def confirmed_authorization(workspace, status="f1_opt", restrictions=""):
    """Use the same saved-profile boundary as the editor, without touching a personal workspace."""
    profile = workspace.profile()
    profile["authorizations"] = [{"country": "US", "status": "authorized_no_sponsor", "sponsorship_required": "no",
                                   "immigration_status": status, "restrictions": restrictions}]
    workspace.save_profile(profile)
    return workspace.profile()["authorizations"]


class ImmigrationApplicationTests(unittest.TestCase):
    def setUp(self):
        self.case = public_applications.PublicApplicationTests()
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def add_requirement(self, quote):
        """Preserve source provenance while the model fixture omits the eligibility requirement."""
        for job in self.case.jobs:
            source = {**job["public_source"], "description": job["public_source"]["description"] + " " + quote}
            source["description_sha256"] = hashlib.sha256(source["description"].encode()).hexdigest()
            self.case.w.store.job(job["id"], public_source=source, details=source["description"])

    def test_citizenship_conflict_stops_before_personal_fields(self):
        confirmed_authorization(self.case.w)
        self.add_requirement("This role requires US citizenship.")
        run = self.case.run_public()
        self.assertEqual(run["attempted_count"], 0)
        self.assertTrue(all(result["status"] == "skipped" for result in run["results"]))
        self.assertTrue(all("fill" not in browser.methods for browser in self.case.browsers))

    def test_conditional_authorization_becomes_needs_input_not_excluded(self):
        confirmed_authorization(self.case.w, "f1_stem_opt", "Employment must relate to my degree")
        self.add_requirement("Applicants must be authorized to work in the United States.")
        run = self.case.run_public()
        self.assertEqual(run["attempted_count"], 0)
        self.assertTrue(all(result["status"] == "needs_review" for result in run["results"]))
        self.assertTrue(all("fill" not in browser.methods for browser in self.case.browsers))

    def test_confirmed_citizen_can_pass_an_explicit_citizen_requirement(self):
        confirmed_authorization(self.case.w, "us_citizen")
        self.add_requirement("Applicants must be US citizens.")
        run = self.case.run_public()
        self.assertEqual(run["submitted_count"], 1)


if __name__ == "__main__":
    unittest.main()
