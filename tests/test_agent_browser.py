"""The single agent handles unfamiliar labels without weakening evidence or replaying actions."""

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from applydesk.agent_browser import AgentBrowser, ApplicationInputRequired


class PageSession:
    """A changing employer page records actual requested operations independently of the model."""

    def __init__(self, elements):
        self.page = {"snapshot_id": "before", "url": "https://new-employer.test/apply",
                     "title": "Example opening", "text": "Example opening", "elements": elements}
        self.writes = []
        self.replace_form = False

    def call(self, method, params=None):
        if method == "agent_action":
            self.writes.append(copy.deepcopy(params))
            field = next(item for item in self.page["elements"] if item["id"] == params["element_id"])
            field["value"] = params["value"]
            self.page["snapshot_id"] = "after" + str(len(self.writes))
            if self.replace_form:
                self.page["elements"] = [{"id": "next-step", "label": "New question", "type": "text"}]
        return copy.deepcopy(self.page)


class AgentBrowserTests(unittest.TestCase):
    """Use unknown wording and explicit model choices rather than hard-coded field aliases."""

    def setUp(self):
        self.profile = {"confirmed": True, "revision": 1, "facts": [
            {"id": "identity.name", "text": "My name is Example Person.", "confirmed": True}]}
        self.job = {"id": "job", "company": "Example", "title": "Opening"}
        self.session = PageSession([{"id": "unfamiliar", "label": "How should colleagues address you?",
                                    "type": "text", "required": True, "value": ""}])
        self.client = Mock()
        self.workspace = SimpleNamespace(store=Mock(), docs=Mock())
        self.workspace.store.job.side_effect = lambda identity, **values: {"id": identity, **values}
        self.app = SimpleNamespace(w=self.workspace, client=lambda role: self.client, checkpoint=Mock())
        accounts = patch("applydesk.agent_browser.EmployerAccounts")
        accounts.start().return_value.display.return_value = {}
        self.addCleanup(accounts.stop)

    def driver(self):
        return AgentBrowser(self.app, self.session, self.job, self.profile, {})

    def answer(self, identity="unfamiliar", facts=None):
        return {"action": "fill", "element_id": identity, "value": "Example Person", "intent": "explore",
                "fact_ids": ["identity.name"] if facts is None else facts}

    def test_unfamiliar_label_uses_agent_decision_and_fresh_review(self):
        self.client.ask.side_effect = [{"action": "act", "operations": [self.answer()]},
                                       {"action": "review", "review": {"final_element_id": "final"}}]
        with patch("applydesk.agent_browser_review.validate_review", return_value={"passed": True}) as review:
            result = self.driver().run()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(self.session.writes[0]["element_id"], "unfamiliar")
        self.assertEqual(review.call_args.args[3]["snapshot_id"], "after1")
        self.assertEqual(self.client.ask.call_args_list[1].args[1]["page"]["elements"][0]["value"], "Example Person")

    def test_unknown_fact_never_writes_and_returns_feedback_to_same_agent(self):
        self.client.ask.side_effect = [{"action": "act", "operations": [self.answer(facts=["invented"])]},
                                       {"action": "needs_input", "reason": "A required answer is missing."}]
        with self.assertRaisesRegex(ApplicationInputRequired, "required answer"):
            self.driver().run()
        self.assertEqual(self.session.writes, [])
        feedback = self.client.ask.call_args_list[1].args[1]["recent_actions"]
        self.assertIn("confirmed profile facts", feedback[-1]["error"])

    def test_required_input_is_not_retried_as_a_browser_failure(self):
        self.client.ask.return_value = {"action": "needs_input", "reason": "The required start day is unknown."}
        with self.assertRaisesRegex(ApplicationInputRequired, "start day"):
            self.driver().run()
        self.assertEqual(self.client.ask.call_count, 1)
        self.assertEqual(self.session.writes, [])

    def test_dynamic_next_form_stops_remaining_batch(self):
        self.session.page["elements"].append({"id": "old-second", "label": "Old question", "type": "text"})
        self.session.replace_form = True
        self.client.ask.side_effect = [{"action": "act", "operations": [self.answer(), self.answer("old-second")]},
                                       {"action": "needs_input", "reason": "Inspect the new question."}]
        with self.assertRaises(ApplicationInputRequired):
            self.driver().run()
        self.assertEqual([item["element_id"] for item in self.session.writes], ["unfamiliar"])

    def test_model_cannot_supply_password_text(self):
        self.session.page["elements"][0]["type"] = "password"
        self.client.ask.side_effect = [{"action": "act", "operations": [self.answer()]},
                                       {"action": "needs_input", "reason": "Use the saved account."}]
        with self.assertRaises(ApplicationInputRequired):
            self.driver().run()
        self.assertEqual(self.session.writes, [])

    def test_three_unchanged_observations_stop_a_token_loop(self):
        self.client.ask.return_value = {"action": "observe", "purpose": "Check again"}
        with self.assertRaisesRegex(ValueError, "could not make progress"):
            self.driver().run()
        self.assertEqual(self.client.ask.call_count, 3)


if __name__ == "__main__":
    unittest.main()
