"""Exercise the Workday skill with visible controls, tenant variants and fictional profile facts."""

import tempfile
import unittest
from pathlib import Path

from applydesk.storage import Store
from applydesk.workday_skill import adapt_context, visible_stage


class WorkdaySkillTests(unittest.TestCase):
    def setUp(self):
        """Use an isolated store and no employer connections."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = Store(Path(folder.name))
        self.addCleanup(self.store.close)

    def context(self, heading, tenant="alpha"):
        """Retain enough prior evidence to detect accidental disclosure across steps."""
        return {"page": {"url": f"https://{tenant}.wd5.myworkdayjobs.com/en-US/External/job/123",
                         "elements": [{"role": "AXHeading", "label": heading}]},
                "profile": {"confirmed": True, "facts": [
                    {"id": "education.0", "kind": "education", "confirmed": True},
                    {"id": "preferences.race", "kind": "disclosure", "confirmed": True}]},
                "review_evidence": {"previous": "earlier fields"}}

    def test_current_step_changes_context_and_tenant_cache_is_only_observed_stages(self):
        experience = self.context("My Experience")
        guide = adapt_context(experience, self.store)
        self.assertIn("My Experience", experience["workday"]["observed_tenant_steps"])
        self.assertEqual([f["id"] for f in experience["profile"]["facts"]], ["education.0"])
        self.assertNotIn("review_evidence", experience)
        disclosure = self.context("Voluntary Disclosures")
        self.assertNotEqual(guide, adapt_context(disclosure, self.store))
        self.assertEqual([f["id"] for f in disclosure["profile"]["facts"]], ["preferences.race"])
        self.assertEqual(self.store.get("workday_steps", "alpha.wd5.myworkdayjobs.com/External"),
                         ["My Experience", "Voluntary Disclosures"])
        review = self.context("Review", "beta")
        adapt_context(review, self.store)
        self.assertEqual(review["workday"]["observed_tenant_steps"], ["Review"])
        self.assertIn("review_evidence", review)
        self.assertEqual(len(review["profile"]["facts"]), 2)

    def test_foreign_host_and_unmarked_progress_list_do_not_trigger_a_stage(self):
        context = self.context("Review")
        context["page"]["url"] = "https://alpha.myworkdayjobs.com.example.org/job"
        self.assertEqual(adapt_context(context, self.store), "")
        self.assertNotIn("workday", context)
        page = {"elements": [{"role": "AXStaticText", "value": v} for v in ["My Experience", "Review"]]}
        self.assertIsNone(visible_stage(page))
        page["elements"].append({"role": "AXStaticText", "label": "Current step: My Experience"})
        self.assertEqual(visible_stage(page), "My Experience")
        page["elements"][-1]["label"] = "current step 4 of 5 Voluntary Disclosures"
        self.assertEqual(visible_stage(page), "Voluntary Disclosures")

    def test_start_needs_only_documents_and_questions_use_confirmed_facts_once(self):
        """A variable employer question can draw on any confirmed fact without duplicate profile sections."""
        start = self.context("Start your application")
        start["documents"] = [{"kind": "resume", "id": "original"}]
        adapt_context(start, self.store)
        self.assertEqual(start["profile"]["facts"], [])
        self.assertEqual(start["documents"][0]["id"], "original")
        questions = self.context("Application Questions")
        questions["profile"]["identity"] = {"given_name": "Example"}
        questions["profile"]["facts"].append({"id": "draft", "kind": "skill", "confirmed": False})
        adapt_context(questions, self.store)
        self.assertEqual(len(questions["profile"]["facts"]), 2)
        self.assertNotIn("identity", questions["profile"])
        self.assertNotIn("review_evidence", questions)
