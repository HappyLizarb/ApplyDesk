"""Literal eligibility requirements cannot inherit the ordinary qualification assumption."""

import unittest

from applydesk.immigration_eligibility import authorization_context, check_immigration
from applydesk.intelligence import validate_eligibility
from applydesk.mapping import answer_field
from test_mapping import field


def profile(status="f1_opt", restrictions="", authorized="authorized_no_sponsor"):
    """Self-reported test rows distinguish current permission from immigration status."""
    return {"confirmed": True, "facts": [], "identity": {}, "preferences": {}, "websites": [],
            "authorizations": [{"country": "US", "status": authorized, "sponsorship_required": "no",
                                "immigration_status": status, "restrictions": restrictions}]}


class ImmigrationEligibilityTests(unittest.TestCase):
    def test_citizen_only_conflict_needs_an_exact_employer_requirement(self):
        quote = "Applicants must be U.S. citizens."
        result = check_immigration(profile(), quote, "US")
        self.assertFalse(result["eligible"])
        self.assertFalse(result["needs_input"])
        self.assertIn(quote, result["conflicts"][0])
        self.assertTrue(check_immigration(profile("us_citizen"), quote, "US")["eligible"])

    def test_foreign_or_unknown_country_does_not_inherit_us_status(self):
        for country in ("CA", "", "US, CA"):
            with self.subTest(country=country):
                result = check_immigration(profile("us_citizen"), "US citizenship required.", country)
                self.assertTrue(result["needs_input"])
                self.assertFalse(result["eligible"])
                self.assertFalse(result["conflicts"])

    def test_unknown_and_other_statuses_do_not_establish_non_citizenship(self):
        for status in ("", "other_unsure", "other_work_authorized", "us_national"):
            with self.subTest(status=status):
                result = check_immigration(profile(status), "US citizenship required.", "US")
                self.assertTrue(result["needs_input"])
                self.assertFalse(result["conflicts"])

    def test_export_control_us_person_and_clearance_are_not_citizen_only(self):
        for quote in ("Must be a U.S. person under ITAR.", "This role is subject to export controls.",
                      "A security clearance is required.", "Must be a US citizen or permanent resident."):
            with self.subTest(quote=quote):
                result = check_immigration(profile("permanent_resident"), quote, "US")
                self.assertTrue(result["needs_input"])
                self.assertFalse(result["conflicts"])

    def test_conditional_permission_and_entered_restrictions_need_job_specific_input(self):
        quote = "Applicants must be authorized to work in the United States."
        for applicant in (profile(), profile("f1_stem_opt"), profile("f1_cpt"),
                          profile("other_work_authorized", "Work only for my approved employer")):
            result = check_immigration(applicant, quote, "US")
            self.assertTrue(result["needs_input"])
            self.assertIn(quote, result["reasons"][0])

    def test_ordinary_roles_are_not_excluded_by_status_or_restrictions(self):
        applicant = profile("f1_opt", "Work must relate to my degree")
        for text in ("Design and test electronics. Python required.", "US citizenship preferred.",
                     "We consider all applicants regardless of citizenship status.",
                     "We do not require US citizenship.", "We hire not only US citizens."):
            self.assertTrue(check_immigration(applicant, text, "US")["eligible"])

    def test_current_permission_is_independent_of_status_and_future_sponsorship(self):
        current = "Applicants must be authorized to work in the United States."
        self.assertTrue(check_immigration(profile("permanent_resident"), current, "US")["eligible"])
        self.assertTrue(check_immigration(profile("us_citizen", authorized=""), current, "US")["needs_input"])
        for future in ("Applicants must not require sponsorship now or in the future.",
                       "Sponsorship for US employment authorization is not available now or in the future for this position.",
                       "Will you now or in the future require sponsorship for employment visa status (e.g. H-1B VISA STATUS)?"):
            with self.subTest(future=future):
                self.assertTrue(check_immigration(profile("permanent_resident"), future, "US")["eligible"])
                for applicant in (profile(), profile("permanent_resident", "Employer restriction"), profile("other_work_authorized")):
                    self.assertTrue(check_immigration(applicant, future, "US")["needs_input"])

    def test_assumed_or_omitted_model_requirements_cannot_bypass_citizenship(self):
        quote = "This role requires US citizenship."
        for required in ([], [{"text": quote, "met": True, "evidence_ids": []}]):
            analysis = {"requirements_complete": True, "source_text": quote, "country": "US", "required": required}
            self.assertFalse(validate_eligibility(analysis, profile())["eligible"])
        unsupported = {"requirements_complete": True, "required": [{"text": quote, "met": True, "evidence_ids": []}]}
        self.assertFalse(validate_eligibility(unsupported, profile())["eligible"])

    def test_source_backed_bare_eligibility_requirement_requests_input(self):
        quote = "US citizenship"
        analysis = {"requirements_complete": True, "source_text": "Required:\n" + quote,
                    "country": "US", "required": [{"text": quote, "met": True, "evidence_ids": []}]}
        result = validate_eligibility(analysis, profile())
        self.assertFalse(result["eligible"])
        self.assertTrue(result["needs_input"])
        self.assertIn(quote, result["reasons"][0])
        for exception in ("US citizenship preferred", "We consider applicants regardless of citizenship"):
            analysis.update(source_text=exception, required=[{"text": exception, "met": True, "evidence_ids": []}])
            self.assertTrue(validate_eligibility(analysis, profile())["eligible"])

    def test_fragment_cannot_turn_an_exempt_statement_into_a_requirement(self):
        fragment = {"text": "US citizenship", "met": True, "evidence_ids": []}
        analysis = {"requirements_complete": True, "country": "US", "required": [fragment]}
        for statement in ("We consider applicants regardless of US citizenship.", "US citizenship preferred."):
            analysis["source_text"] = statement
            self.assertTrue(validate_eligibility(analysis, profile())["eligible"])
        analysis["source_text"] += "\nThis role requires US citizenship."
        self.assertFalse(validate_eligibility(analysis, profile())["eligible"])

    def test_unconfirmed_status_does_not_enter_search_context(self):
        applicant = profile()
        applicant["confirmed"] = False
        self.assertEqual(authorization_context(applicant), [])
        self.assertTrue(check_immigration(applicant, "US citizenship required.", "US")["needs_input"])

    def test_future_and_conditional_form_answers_are_not_inferred(self):
        for label in ("Will you require sponsorship?", "Do you now or in the future require sponsorship?",
                      "Are you authorized to work in the United States?"):
            self.assertIsNone(answer_field(field(label=label), profile(), "US"))
        self.assertIsNotNone(answer_field(field(label="Do you require sponsorship?"), profile(), "US"))


if __name__ == "__main__":
    unittest.main()
