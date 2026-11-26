import unittest
from datetime import date
from applydesk.application_answers import with_application_source
from applydesk.mapping import answer_field
from applydesk.intelligence import audit_fields

class ApplicationSourceTests(unittest.TestCase):
    def test_signing_date_is_current_and_never_saved_in_the_profile(self):
        profile = {"confirmed": True, "facts": []}
        job = {"url": "https://jobs.example.test/role"}
        context = with_application_source(profile, job)
        refreshed = with_application_source(context, job)
        dates = [fact for fact in refreshed["facts"] if fact["kind"] == "application_date"]
        self.assertEqual([fact["id"] for fact in dates], ["application.signing_date." + date.today().isoformat()])
        self.assertIn(date.today().strftime("%m/%d/%Y"), dates[0]["text"])
        self.assertEqual(profile["facts"], [])

    def test_application_uses_its_saved_discovery_source(self):
        """A source answer must match this job even if the previous job had a different source."""
        profile = {"confirmed": True, "facts": [], "identity": {}, "authorizations": [], "preferences": {}}
        field = {"field_id": "source", "label": "How did you hear about us?", "type": "select",
                 "options": [{"value": name, "label": name} for name in ("LinkedIn", "Handshake")]}
        context = profile
        for url, source in (("https://www.linkedin.com/jobs/view/123", "LinkedIn"),
                            ("https://app.joinhandshake.com/job-search/123", "Handshake")):
            context = with_application_source(context, {"url": url})
            answer = answer_field(field, context, "US")
            self.assertEqual(answer["value"], source)
            result = audit_fields(context, {"fields": [{"id": "source", "value": source}]},
                                  [{"id": "source", "value": source, "fact_ids": answer["fact_ids"]}])
            self.assertTrue(result["passed"])
        self.assertEqual(profile["facts"], [])
        for url in ("https://linkedin.com.example.test/jobs/123", "https://joinhandshake.com.example.test/job-search/123",
                    "https://jobs.ashbyhq.com/sample-employer/job"):
            self.assertIsNone(answer_field(field, with_application_source(context, {"url": url}), "US"))
