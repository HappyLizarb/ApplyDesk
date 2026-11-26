"""Required employer terms use the visible form and are independent of profile settings."""

from pathlib import Path
import tempfile
import unittest

from applydesk.native_evidence import agreement_facts, validate_operation
from applydesk.native_review import _answer_issues
from applydesk.storage import Store


class RequiredTermsTests(unittest.TestCase):
    def setUp(self):
        """Use visible checked controls from a required employer agreement."""
        self.job = {"id": "one"}
        self.terms = "I agree to the mandatory arbitration agreement."
        self.control = {"role": "AXCheckBox", "id": "terms", "enabled": True, "required": True,
                        "label": self.terms, "web_content": True, "value": 1}
        self.page = {"text": self.terms, "elements": [self.control]}
        self.action = {"action": "click", "element_id": "terms", "intent": "agreement",
                       "agreement_quote": self.terms}

    def test_default_applies_to_new_and_existing_profiles(self):
        """Terms work on current and prior steps regardless of any obsolete saved preference."""
        answer = {"element_id": "terms", "value": 1, "quote": self.terms, "agreement_quote": self.terms}
        review = {"answers": [answer], "summaries": [{"quote": self.terms, "agreement_quote": self.terms}]}
        for profile in ({}, {"preferences": {"accept_required_terms": False}}):
            self.assertFalse(agreement_facts(profile, self.job))
            validate_operation(profile, self.page, self.action, self.job)
            self.assertEqual(_answer_issues(profile, self.page, review, self.terms, self.job), [])

    def test_reopening_removes_only_the_obsolete_setting(self):
        """The actual upgrade must preserve every unrelated saved preference."""
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder))
            store.put("settings", "main", {"accept_required_terms": False, "cover_template_id": "original"})
            store.close()
            reopened = Store(Path(folder))
            try:
                self.assertEqual(reopened.get("settings", "main"), {"cover_template_id": "original"})
            finally:
                reopened.close()

    def test_visual_agreement_click_is_bound_to_one_observed_checkbox(self):
        """A screenshot point uses the current scale, scope, checkbox ID and live hit testing."""
        checkbox = dict(self.control, bounds={"x": 10, "y": 30, "width": 14, "height": 14})
        page = dict(self.page, elements=[checkbox], screenshot={"width": 400, "height": 200, "scale": 2})
        click = dict(self.action, point={"x": 24, "y": 68})
        del click["element_id"]
        self.assertEqual(validate_operation({}, page, click, self.job)["element_id"], checkbox["id"])
        for controls, action in (([checkbox], dict(click, element_id="another")),
                                 ([checkbox], dict(click, point={"x": 60, "y": 90})),
                                 ([checkbox, dict(checkbox, id="other")], click)):
            with self.assertRaisesRegex(ValueError, "observed agreement control"):
                validate_operation({}, dict(page, elements=controls), action, self.job)

    def test_scope_excludes_personal_answers_and_optional_choices(self):
        """A required choice about relatives or optional interview recording remains a separate decision."""
        profile = {}
        for label in ("Do you have relatives here?", "Under this policy I certify that I am a US citizen.",
                      "I don't agree to the terms", "I agree to optional interview recording under this policy."):
            page = {"text": label, "elements": [{**self.control, "label": label}]}
            with self.subTest(label=label), self.assertRaises(ValueError):
                validate_operation(profile, page, {**self.action, "agreement_quote": label}, self.job)
        with self.assertRaises(ValueError):
            validate_operation(profile, self.page, {**self.action, "intent": "answer"}, self.job)

    def test_asterisk_uses_adjacent_rendered_notice_without_requiring_a_copied_quote(self):
        """Workday can render the required declaration above a checkbox whose accessible name is just '*'."""
        profile = {}
        box = {"x": 10, "y": 100, "width": 16, "height": 16}
        control = {**self.control, "label": "*", "bounds": box}
        parts = [("AXStaticText", "I declare that I have read the"), ("AXLink", "Recruitment Notice"),
                 ("AXStaticText", "and agree to my application data being processed.")]
        items = [{"id": str(i), "role": role, "label": label, "bounds": {**box, "y": 42 + 15*i}}
                 for i, (role, label) in enumerate(parts)]
        page = {"elements": [*items, control]}
        action = {"intent": "agreement", "action": "click", "element_id": "terms"}
        validate_operation(profile, page, action, self.job)
        for changed in ([*items, control, {**control, "id": "other"}],
                        [*items, {**control, "bounds": {**box, "y": 500}}]):
            with self.assertRaisesRegex(ValueError, "next to its checkbox"):
                validate_operation(profile, {"elements": changed}, action, self.job)
