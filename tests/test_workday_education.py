"""Expected education dates and canonical categories stay grounded in the saved profile."""

import unittest

from applydesk.workday_education import study_candidates, workday_education_answer


class WorkdayEducationTests(unittest.TestCase):
    def setUp(self):
        self.field = {"field_id": "education", "widget": "workday_education",
                      "end_date_kind": "actual_or_expected", "value": []}
        self.profile = {"confirmed": True, "education": [
            {"institution": "Example University", "degree": "Bachelor of Arts",
             "field": "Geography & Library Sciences (Honours)", "start": "2013-08", "end": "2017-05"},
            {"institution": "Example University", "degree": "Bachelor of Arts",
             "field": "Textile Engineering Science", "start": "2013-08", "end": "2017-05"}]}

    def test_both_current_degrees_keep_expected_end_year(self):
        answer = workday_education_answer(self.field, self.profile)
        self.assertEqual(answer["fact_ids"], ["education.0", "education.1"])
        self.assertEqual(len(answer["value"]), 2)
        for row in answer["value"]:
            self.assertEqual(row["degree"], "Bachelors")
            self.assertEqual(row["firstYearAttended"], "2013")
            self.assertEqual(row["lastYearAttended"], "2017")
            self.assertNotIn("graduated", row)

    def test_observed_category_requires_explicit_program_component(self):
        self.field["value"] = [{"fieldOfStudy": "Geography"}, {"fieldOfStudy": "Textile Engineering"}]
        answer = workday_education_answer(self.field, self.profile)
        self.assertEqual(answer["value"][0]["fieldOfStudy"], "Geography")
        self.assertEqual(answer["value"][1]["fieldOfStudy"], "Textile Engineering")
        self.field["value"][0]["fieldOfStudy"] = "Classical Studies"
        self.assertEqual(workday_education_answer(self.field, self.profile)["value"][0]["fieldOfStudy"], self.profile["education"][0]["field"])

    def test_missing_dates_or_unconfirmed_profile_do_not_invent_values(self):
        self.profile["education"][0]["end"] = ""
        self.assertIsNone(workday_education_answer(self.field, self.profile))
        self.profile["education"][0]["end"] = "2017-05"
        self.profile["confirmed"] = False
        self.assertIsNone(workday_education_answer(self.field, self.profile))
        self.profile["confirmed"] = True
        self.field["end_date_kind"] = "graduated"
        self.assertIsNone(workday_education_answer(self.field, self.profile))

    def test_gpa_preserves_scale_and_program_candidates_are_bounded(self):
        self.profile["education"][0].update(gpa="8.20", gpa_scale="10.00")
        value = workday_education_answer(self.field, self.profile)["value"][0]
        self.assertEqual(value["gradeAverage"], "8.20/10.00")
        self.assertEqual(study_candidates("Textile Engineering Science"),
                         ["Textile Engineering Science", "Textile Engineering"])
        self.assertIn("Library Science", study_candidates(self.profile["education"][0]["field"]))
        self.assertNotIn("Engineering", study_candidates("Textile Engineering Science"))
        self.assertEqual(study_candidates("Engineering Science"), ["Engineering Science"])
