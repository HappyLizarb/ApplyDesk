"""Authentication handoffs must preserve source identity without weakening final review."""
import unittest
from unittest.mock import Mock
from applydesk.recommendation_checks import validate_employer_page
from applydesk.successfactors_auth import prepare_successfactors_account, successfactors_auth_context


class SuccessFactorsTests(unittest.TestCase):
    def test_only_verified_account_screen_can_omit_job_title(self):
        source = {"company": "Supermicro", "title": "Fixture Systems Engineer",
                  "employer_url": "https://jobs.supermicro.com/job/role/1000000001/"}
        meta = {"tenant": "supermicro", "requisition": "10001", "form_id": "account", "kind": "login"}
        entry = {"provider": "successfactors", "source_url": source["employer_url"],
                 "company": source["company"], "title": source["title"], "tenant": "supermicro", "requisition": "10001"}
        page = {"url": "https://career4.successfactors.com/careers", "title": "Sign In", "text": "Sign In",
                "authentication_entry": entry, "successfactors_auth": meta,
                "fields": [{"type": "password", "form_id": "account"}]}
        action = "https://career4.successfactors.com/career"
        button = {"form_action": action, "auth_form": True, "form_method": "POST", "form_id": "account", "action": "login"}
        meta["action"] = action
        page["fields"][0].update(name="password", supported=True)
        for requisition in ("10001", "10002"):
            entry["requisition"] = meta["requisition"] = requisition
            self.assertIs(validate_employer_page(source, page), page)
            self.assertEqual(successfactors_auth_context(page, button)[1], page["fields"])
        for changes in ({"requisition": "10001"}, {"tenant": "other"}):
            changed = {**page, "successfactors_auth": {**meta, **changes}}
            with self.assertRaises(ValueError):
                validate_employer_page(source, changed)
            with self.assertRaises(ValueError):
                successfactors_auth_context(changed, button)
        for change in ({"authentication_entry": {}}, {"fields": []},
                       {"authentication_entry": {**entry, "title": "Other role"}},
                       {"authentication_entry": {**entry, "source_url": source["employer_url"].replace("1000000001", "1000000002")}},
                       {"url": "https://other.example/careers"}):
            with self.assertRaises(ValueError):
                validate_employer_page(source, {**page, **change})

    def test_unrelated_forms_do_not_enter_account_preparation(self):
        session = Mock()
        self.assertIsNone(prepare_successfactors_account(session, {}, {}))
        session.call.assert_not_called()

    def test_confirmed_us_residence_selects_displayed_united_states(self):
        session = Mock()
        session.call.return_value = {"snapshot_id": "selected"}
        profile = {"confirmed": True, "identity": {"country": "US"}}
        page = {"snapshot_id": "initial", "authentication_entry": {"provider": "successfactors"},
                "successfactors_auth": {"kind": "create_account"}, "buttons": [],
                "fields": [{"field_id": "country", "name": "fbclc_country", "label": "Country",
                            "type": "select", "value": "", "options": [{"label": "United States", "value": "US"}]}]}
        prepared = prepare_successfactors_account(session, page, profile)
        self.assertEqual(prepared["intervention"], "authentication_country")
        self.assertEqual(session.call.call_args.args[1]["answers"],
                         [{"field_id": "country", "value": "US", "fact_ids": ["identity.country"]}])

    def test_reviewed_privacy_dialog_advances_without_creating_an_account(self):
        session = Mock()
        session.call.return_value = {"snapshot_id": "accepted"}
        page = {"snapshot_id": "policy", "authentication_entry": {"provider": "successfactors"},
                "successfactors_auth": {"kind": "create_account"},
                "buttons": [{"field_id": "accept", "reviewed_privacy": True}], "fields": []}
        result = prepare_successfactors_account(session, page, {})
        self.assertEqual(result["intervention"], "authentication_privacy_accepted")
        session.call.assert_called_once_with("next", {"snapshot_id": "policy", "field_id": "accept"})

    def test_unchanged_country_is_not_refilled_after_privacy_acceptance(self):
        from applydesk.application_steps import _signup_answer
        field = {"field_id": "country", "label": "Country", "type": "select", "value": "US",
                 "options": [{"label": "United States", "value": "US"}]}
        profile = {"confirmed": True, "identity": {"country": "US"}}
        self.assertIsNone(_signup_answer(field, "create_account", "synthetic@example.test", profile))
