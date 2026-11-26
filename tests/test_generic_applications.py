"""Production application runs let the single agent drive observed controls and verify receipts."""

import json
from unittest.mock import patch

from agent_browser_helpers import GenericBrowserFixture
from chat_helpers import ChatFixture
import test_application_automation as automation
from test_orchestrator import response
import test_public_applications as public
from applydesk.agent_browser_prompt import PAGE_PROMPT
from applydesk.application_client import STEP_BOUNDARY
from applydesk.application_submission import attempt_key
from applydesk.orchestrator_prompts import SELF_CHECK


class GenericApplicationTests(ChatFixture):
    """Use the production orchestrator, real generic driver, real review and durable ledger."""

    public_job = public.PublicApplicationTests.public_job

    def setUp(self):
        super().setUp()
        self.engine = self.w.orchestrator
        self.w.store.put("chatgpt", "accounts", {"active": "test", "accounts": [{"id": "test", "label": "Test", "client_id": "oaiapp_test", "subject": "subject", "connected": True}]})
        self.w.store.put("chatgpt_models", "test", [{"id": "test-model", "name": "Test", "thinking": []}])
        profile = self.w.profile()
        profile["industries"] = [{"name": "Hardware", "years": "0"}]
        profile["preferences"]["levels"] = ["Entry"]
        self.w.save_profile(profile)
        self.w.store.clear("job")
        self.jobs = [self.public_job(str(index)) for index in range(5)]
        self.roles, self.browsers, self.requests = [], [], []
        self.fail_analysis = self.fail_receipt = self.stale_first = self.bad_fact = self.bad_review = False
        message = self.engine.history.add("user", "Apply to five new hardware jobs in Boston.")
        self.engine.active = {"message": message}
        self.intent = {"action": "start", "run_id": "", "authorization_message_id": message["id"], "limit": 5,
                       "locations": ["Boston"], "instruction": message["text"], "resume_id": "", "issues": []}

    def browser(self, _engine, _root, **options):
        """No driver selection is patched: the production caller must opt into generic browsing."""
        self.assertIs(options.get("agent_driven"), True)
        browser = GenericBrowserFixture(self.w.store.get("job", options["job_id"]), confirmed=not self.fail_receipt)
        self.browsers.append(browser)
        return browser

    def model(self, payload, cancelled, emit):
        """The same model connection chooses unknown field labels from observed context and facts."""
        prompt = payload["instructions"].removeprefix(SELF_CHECK).removeprefix(STEP_BOUNDARY)
        if prompt != PAGE_PROMPT:
            return automation.AutomationTests.model(self, payload, cancelled, emit)
        self.roles.append(self.engine.runner.current["role"])
        self.requests.append(payload)
        content = payload["input"][0]["content"]
        context = json.loads(content if isinstance(content, str) else content[0]["text"])
        return response(json.dumps(self.decision(context)))

    def decision(self, context):
        """Choose a fill, unchanged upload or complete evidence-bound review from the current page."""
        page = context["page"]
        fields = {item["id"]: item for item in page["elements"]}
        if not fields["identity"]["value"]:
            identity = "obsolete" if self.stale_first and not any("error" in item for item in context["recent_actions"]) else "identity"
            return {"action": "act", "operations": [{"action": "fill", "element_id": identity, "value": "Alex Test",
                    "fact_ids": [] if self.bad_fact else ["identity.legal_name"]}]}
        if not fields["attachment"]["files"]:
            return {"action": "upload", "element_id": "attachment", "document_id": "resume", "kind": "resume"}
        return {"action": "review", "review": {"final_element_id": "finish", "company_quote": "Example Co",
                "title_quote": "Analyst", "answers": [{"element_id": "identity",
                    "value": "Wrong name" if self.bad_review else "Alex Test", "fact_ids": ["identity.legal_name"]}],
                "summaries": [], "resume_quote": "Uploaded: original.pdf", "required_complete": True}}

    def run_generic(self):
        """Only remote model transport and browser I/O are replaced with isolated fixtures."""
        with patch("applydesk.application_automation.ApplicationSession", side_effect=self.browser):
            with patch.object(self.engine.runner, "_request", side_effect=self.model):
                return self.engine.applications.start([job["id"] for job in self.jobs])

    def test_one_short_prompt_runs_five_new_jobs_through_generic_review_and_receipts(self):
        run = self.run_generic()
        self.assertEqual((run["status"], run["submitted_count"], run["attempted_count"]), ("complete", 5, 5))
        self.assertEqual(set(self.roles), {"master"})
        self.assertEqual(len(self.browsers), 5)
        for browser in self.browsers:
            self.assertEqual(browser.methods.count("agent_action"), 2)
            self.assertEqual(browser.methods.count("agent_submit"), 1)
        for job in self.jobs:
            current = self.w.store.get("job", job["id"])
            self.assertEqual(current["status"], "submitted")
            self.assertTrue(current["final_audit"]["passed"])
            self.assertEqual(current["attachments"][0]["sha256"], self.resume["source_sha256"])
        self.assertEqual(self.run_generic()["attempted_count"], 5)
        self.assertEqual(len(self.browsers), 5)

    def test_agent_recovers_from_stale_control_without_a_provider_adapter(self):
        self.jobs = self.jobs[:1]
        self.intent["limit"] = 1
        self.stale_first = True
        run = self.run_generic()
        self.assertEqual(run["submitted_count"], 1)
        self.assertEqual(self.browsers[0].methods.count("agent_action"), 3)

    def test_missing_facts_never_reach_submission(self):
        self.jobs = self.jobs[:1]
        self.intent["limit"] = 1
        self.bad_fact = True
        run = self.run_generic()
        self.assertEqual(run["attempted_count"], 0)
        self.assertNotIn("agent_submit", self.browsers[0].methods)
        self.assertFalse(self.w.store.all("submission_attempt"))

    def test_wrong_live_readback_never_reaches_submission(self):
        self.jobs = self.jobs[:1]
        self.intent["limit"] = 1
        self.bad_review = True
        run = self.run_generic()
        self.assertEqual(run["attempted_count"], 0)
        self.assertNotIn("agent_submit", self.browsers[0].methods)
        self.assertFalse(self.w.store.all("submission_attempt"))

    def test_absent_receipt_remains_uncertain_and_cannot_be_retried(self):
        self.jobs = self.jobs[:1]
        self.intent["limit"] = 1
        self.fail_receipt = True
        run = self.run_generic()
        self.assertEqual((run["attempted_count"], run["uncertain_count"], run["submitted_count"]), (1, 1, 0))
        self.assertEqual(self.w.store.get("submission_attempt", attempt_key(self.jobs[0]))["status"], "submission_unknown")
        self.assertEqual(self.run_generic()["attempted_count"], 1)
        self.assertEqual(self.browsers[0].methods.count("agent_submit"), 1)
