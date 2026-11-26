"""The application bridge must support bounded choice reads after authentication."""
import tempfile
import unittest
from unittest.mock import Mock
from applydesk.browser_bridge import BrowserSession, BrowserError
from applydesk.application_steps import authenticate


class WorkdayControlTests(unittest.TestCase):
    def test_dropdown_preparation_precedes_field_mapping(self):
        session = Mock()
        session.call.return_value = {"snapshot_id": "read", "intervention": "dropdown_choices_read"}
        page = {"snapshot_id": "new", "fields": [{"field_id": "type", "choices_unread": True}]}
        result = authenticate(Mock(), session, {}, page)
        session.call.assert_called_once_with("read_choices", {"snapshot_id": "new", "field_id": "type"})
        self.assertEqual(result["intervention"], "dropdown_choices_read")

    def test_bridge_allows_choice_reads_but_no_arbitrary_script(self):
        with tempfile.TemporaryDirectory() as root:
            browser = BrowserSession(root)
            browser.process = Mock()
            def reply(payload, generation):
                browser.messages.put({"id": payload["id"], "result": {"snapshot_id": "read"}})
            browser._send = Mock(side_effect=reply)
            self.assertEqual(browser.call("read_choices", {"field_id": "type"}), {"snapshot_id": "read"})
            with self.assertRaises(BrowserError):
                browser.call("evaluate", {})
            browser.options["research"] = True
            with self.assertRaises(BrowserError):
                browser.call("read_choices", {})
