"""Grounded education mappings preserve separate degrees and current-study status."""

from datetime import date
import unittest

from applydesk.ashby_education import education_answer


class EducationMappingTests(unittest.TestCase):
    def setUp(self):
        self.field = {"field_id": "education", "widget": "ashby_education",
                      "education_fields": ["degree", "major", "startDate", "endDate", "isCurrent"]}
        self.profile = {"confirmed": True, "education": [
            {"institution": "Example University", "degree": "Bachelor of Arts", "field": "Linguistics",
             "start": "2013-08", "end": "2017-05", "gpa": "8", "gpa_scale": "10"},
            {"institution": "Example University", "degree": "Bachelor of Arts", "field": "Geography",
             "start": "2013-08", "end": "2017-05"}]}

    def test_current_degrees_keep_separate_fields_and_omit_end_date(self):
        answer = education_answer(self.field, self.profile, date(2016, 11, 16))
        self.assertEqual(answer["fact_ids"], ["education.0", "education.1"])
        self.assertEqual(len(answer["value"]), 2)
        self.assertNotEqual(answer["value"][0]["major"], answer["value"][1]["major"])
        for row in answer["value"]:
            self.assertTrue(row["isCurrent"])
            self.assertEqual(row["startDate"], "2013-08")
            self.assertNotIn("endDate", row)
            self.assertNotIn("gpa", row)

    def test_completed_and_unknown_dates_are_not_invented(self):
        self.profile["education"][0].update(start="2010", end="2014-05")
        self.profile["education"][1].update(start="", end="")
        values = education_answer(self.field, self.profile, date(2016, 11, 16))["value"]
        self.assertFalse(values[0]["isCurrent"])
        self.assertEqual(values[0]["endDate"], "2014-05")
        self.assertNotIn("startDate", values[0])
        self.assertNotIn("isCurrent", values[1])
        self.assertNotIn("endDate", values[1])

    def test_unconfirmed_profile_or_missing_school_has_no_answer(self):
        self.profile["confirmed"] = False
        self.assertIsNone(education_answer(self.field, self.profile))
        self.profile["confirmed"] = True
        self.profile["education"][1]["institution"] = ""
        self.assertIsNone(education_answer(self.field, self.profile))

    def test_excluded_controls_are_not_filled(self):
        self.field["education_fields"] = []
        value = education_answer(self.field, self.profile)["value"][0]
        self.assertEqual(value, {"schoolName": "Example University"})
