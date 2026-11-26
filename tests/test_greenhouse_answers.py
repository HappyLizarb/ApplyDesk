"""Custom form answers retain explicit evidence and cannot generalize conditional authorization."""

import unittest

from applydesk.mapping import answer_field
from test_immigration_eligibility import profile


class GreenhouseAnswersTests(unittest.TestCase):
    def test_permanent_status_supports_future_sponsorship_but_not_conditional_status(self):
        field = {"field_id": "sponsor", "widget": "greenhouse_select", "type": "combobox",
                 "label": "Do you now, or will you in the future, require sponsorship for employment in the country which you are applying?*"}
        answer = answer_field(field, profile("permanent_resident"), "US")
        self.assertEqual(answer["value"], "No")
        self.assertEqual(answer["fact_ids"], ["authorizations.0"])
        for applicant in (profile(), profile("other_work_authorized"), profile("permanent_resident", "Restriction")):
            self.assertIsNone(answer_field(field, applicant, "US"))
        self.assertIsNone(answer_field(field, profile("permanent_resident"), "CA"))

    def test_phone_country_and_location_need_each_supporting_fact(self):
        applicant = profile("permanent_resident")
        applicant["identity"] = {"country": "US", "phone_country_code": "+1", "city": "Example City", "region": "Example State"}
        field = {"field_id": "country", "widget": "greenhouse_select", "type": "combobox", "label": "Country*", "group": "Phone"}
        self.assertEqual(answer_field(field, applicant, "US")["value"], "United States +1")
        field.update(label="Location (City)*", group="")
        answer = answer_field(field, applicant, "US")
        self.assertEqual(answer["value"], "Example City, Example State, United States")
        self.assertEqual(len(answer["fact_ids"]), 3)

    def test_hybrid_answer_needs_explicit_confirmed_preference(self):
        field = {"field_id": "hybrid", "widget": "greenhouse_select", "type": "combobox",
                 "label": ("This position is hybrid and requires 2 days a week in office including Tuesdays in our "
                           "Example City headquarters and the remaining 3 days in either Example City or our Sample City "
                           "office. Are you able to meet this requirement?")}
        applicant = profile("permanent_resident")
        self.assertIsNone(answer_field(field, applicant, "US"))
        applicant["facts"] = [{"id": "chat.location", "kind": "preference", "confirmed": True,
                               "text": "I am open to work hybrid or onsite and relocate anywhere."}]
        self.assertEqual(answer_field(field, applicant, "US")["fact_ids"], ["chat.location"])
