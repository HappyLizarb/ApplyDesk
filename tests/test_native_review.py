"""Visible-only submission preserves profile/file versions and the durable no-replay ledger."""

import copy
import hashlib
import unittest
from unittest.mock import Mock

from native_pipeline_fixture import NativeReviewFixture
from applydesk.native_evidence import observation, review_context, validate_operation
from applydesk.native_review import cite_missing_answers, retained_resume, validate_review


class NativeReviewTests(NativeReviewFixture, unittest.TestCase):
    """An unseen employer has a native review page with no DOM or server-byte claims."""

    def test_public_job_search_is_navigation_only_in_a_visible_search_field(self):
        job = {"title": "Graduate Engineer", "company": "Example"}
        field = {"id": "q", "role": "AXTextField", "label": "Search jobs by keyword", "enabled": True, "web_content": True}
        page = {"elements": [field]}
        op = {"action": "type", "intent": "navigation", "element_id": "q", "text": job["title"]}
        validate_operation(self.profile, page, op, job)
        for label, text in (("Search jobs", "private text"), ("Current employer", "Example")):
            with self.subTest(label=label), self.assertRaises(ValueError):
                validate_operation(self.profile, {"elements": [dict(field, label=label)]}, dict(op, text=text), job)

    def test_native_review_does_not_claim_server_file_bytes(self):
        self.assertTrue(self.validate()["passed"])
        self.assertFalse(self.ledger[0]["browser_bytes_verified"])

    def test_latest_autofilled_employer_must_match_the_confirmed_preference(self):
        """An optional autofilled value still goes to the employer when Submit is clicked."""
        self.profile["facts"].append({"id": "current", "kind": "preference", "confirmed": True,
                                      "text": "Use as current employer: Example Research"})
        field = {"id": "company", "role": "AXTextField", "label": "Current company", "value": "Old Lab"}
        self.evidence.append({"controls": [field]})
        with self.assertRaisesRegex(ValueError, "confirmed current employer"):
            self.validate()
        self.evidence.append({"controls": [dict(field, value="Example Research")]})
        self.assertTrue(self.validate()["passed"])

    def test_empty_placeholder_is_not_a_personal_answer(self):
        """Skip optional empty selectors, yet catch a real selection or a required placeholder."""
        select = {"id": "option", "role": "AXPopUpButton", "label": "Select a category",
                  "enabled": True, "value": "Select ..."}
        self.page["elements"].append(select)
        self.assertTrue(self.validate()["passed"])
        select["required"] = True
        with self.assertRaisesRegex(ValueError, "required selection"):
            self.validate()
        select.update(required=False, value="Prefer not to answer")
        with self.assertRaisesRegex(ValueError, "populated"):
            self.validate()

    def test_required_consent_next_to_terms_heading_cannot_be_left_unchecked(self):
        self.page["elements"].extend([
            {"id": "tos", "role": "AXHeading", "label": "Terms and Conditions",
             "bounds": {"x": 15, "y": 30, "height": 16}},
            {"id": "consent", "role": "AXCheckBox", "label": "I consent", "enabled": True, "value": 0,
             "required": True, "bounds": {"x": 15, "y": 55, "height": 16}},
        ])
        with self.assertRaisesRegex(ValueError, "required employer agreement"):
            self.validate()

    def test_saved_draft_requires_matching_requisition_visible_filename_and_original_bytes(self):
        root = "https://example.wd4.myworkdayjobs.com/Careers/job"
        job = {**self.job, "url": root + "/Location/Graduate_JR145", "attachments": self.ledger}
        page = {**self.page, "url": root + "/Graduate_JR145/apply", "text": "Résumé\nresume.pdf"}
        self.assertEqual(retained_resume(self.workspace, job, page), self.ledger[0])
        link = {"id": "download", "role": "AXButton", "label": "Resume Download document resume.pdf Last Modified: today"}
        labeled = {**page, "text": "My Documents", "elements": [link]}
        self.assertEqual(retained_resume(self.workspace, job, labeled), self.ledger[0])
        for bad in ("Cover letter Download document resume.pdf", "Resume Download document resume.pdf.bak",
                    "Resume Download document Resume.pdf"):
            self.assertIsNone(retained_resume(self.workspace, job, {**labeled, "elements": [{**link, "label": bad}]}))
        self.assertIsNone(retained_resume(self.workspace, job, {**page, "text": "Résumé\nother-resume.pdf"}))
        self.assertIsNone(retained_resume(self.workspace, job, {**page, "url": root + "/Graduate_JR146/apply"}))
        self.assertIsNone(retained_resume(self.workspace, {**job, "resume_id": "new"}, page))
        self.source.write_bytes(b"new content")
        self.assertIsNone(retained_resume(self.workspace, job, page))

    def test_observed_listing_brand_requires_verified_draft_identity(self):
        """The actual employer name may differ from the board label on the verified requisition."""
        text = "Example Corporation Hardware Graduate Job Req ID: 54321"
        source = {"url": self.job["url"], "company": "Example Corporation", "title": self.job["title"],
                  "description": text, "description_sha256": hashlib.sha256(text.encode()).hexdigest(),
                  "complete": True, "captured_at": "2026-11-22T00:00:00Z"}
        self.job.update(company="EXCO", public_source=source, attachments=self.ledger,
                        account_origin="https://employer.example", live_url="https://employer.example/portal?t=abc")
        self.review["company_quote"] = "Example Corporation"
        self.page.update(url=self.job["live_url"], document_url=self.job["live_url"],
                         title="Apply for Hardware Graduate (54321)", text=self.page["text"].replace("Example Co", "Example Corporation"))
        self.assertTrue(self.validate()["passed"])
        self.page["title"] = "Apply for Hardware Graduate (54322)"
        with self.assertRaisesRegex(ValueError, "observed company"):
            self.validate()

    def test_changed_profile_file_or_document_selection_blocks_final_click(self):
        self.profile["revision"] = 4
        with self.assertRaisesRegex(ValueError, "profile changed"):
            self.validate()
        self.profile["revision"] = 3
        self.source.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "attachment changed"):
            self.validate()

    def test_citation_repair_preserves_form_values_and_requires_confirmed_evidence(self):
        """A missing citation can be repaired without making any browser edits or changing checked answers."""
        self.review["summaries"][0]["fact_ids"] = []
        discipline = "preferences.disciplinary_action"
        self.profile["facts"].append({"id": discipline, "text": "no", "kind": "disclosure", "confirmed": True})
        client = Mock(**{"ask.return_value": {"citations": [["name"]]}})
        fixed = cite_missing_answers(client, self.profile, self.review, self.page, self.evidence)
        self.assertNotIn(discipline, client.ask.call_args.args[1]["facts"])
        self.assertEqual(fixed["summaries"][0], {"quote": "Name: Alex Test", "fact_ids": ["name"]})
        self.assertEqual(self.review["summaries"][0]["fact_ids"], [])
        self.assertTrue(validate_review(self.workspace, self.job, self.profile, self.page, self.ledger, self.evidence, fixed)["passed"])
        for invalid in ([], [[]], [[discipline]], [["absent"]], [["name"], ["name"]]):
            client.ask.return_value = {"citations": invalid}
            self.assertIsNone(cite_missing_answers(client, self.profile, self.review, self.page, self.evidence))
        self.page["text"] = "Have you ever been terminated or asked to resign from any job or academic institution?"
        self.review["summaries"].append({"quote": self.page["text"]})
        for citations, expected in (([[discipline], ["name"]], False), ([["name"], [discipline]], True)):
            client.ask.return_value = {"citations": citations}
            self.assertEqual(bool(cite_missing_answers(client, self.profile, self.review, self.page, self.evidence)), expected)
            self.assertIn(discipline, client.ask.call_args.args[1]["facts"])

    def test_fabricated_quotes_and_unconfirmed_facts_are_rejected(self):
        self.review["summaries"][0]["quote"] = "Name: Someone Else"
        with self.assertRaisesRegex(ValueError, "absent"):
            self.validate()
        self.review["summaries"][0] = {"quote": "Name: Alex Test", "fact_ids": ["invented"]}
        with self.assertRaisesRegex(ValueError, "unknown"):
            self.validate()

    def test_account_button_and_incomplete_check_are_rejected(self):
        self.page["elements"][0]["label"] = "Sign in"
        with self.assertRaisesRegex(ValueError, "account"):
            self.validate()

    def test_plain_submit_on_password_form_cannot_reserve_application_attempt(self):
        self.page["elements"].append({"id": "password", "role": "AXTextField", "enabled": True, "secure": True})
        with self.assertRaisesRegex(ValueError, "account form"):
            self.validate()

    def test_live_value_overrides_an_earlier_correct_summary(self):
        self.page["elements"].append({"id": "name-field", "role": "AXTextField", "enabled": True, "value": "Someone Else"})
        self.review["answers"] = [{"element_id": "name-field", "value": "Alex Test", "quote": "Alex Test", "fact_ids": ["name"]}]
        with self.assertRaisesRegex(ValueError, "current answer differs"):
            self.validate()
        self.page["elements"][0]["label"] = "Send application"
        self.review["checked_all_steps"] = False
        with self.assertRaisesRegex(ValueError, "all application steps"):
            self.validate()

    def test_native_answer_and_coordinate_require_evidence(self):
        with self.assertRaisesRegex(ValueError, "confirmed profile"):
            validate_operation(self.profile, self.page, {"action": "type", "text": "Made up"})
        with self.assertRaisesRegex(ValueError, "screenshot"):
            validate_operation(self.profile, self.page, {"action": "click", "point": {"x": 1, "y": 2}})

    def test_basic_privacy_is_distinct_from_binding_waiver(self):
        page = {**self.page, "text": "I agree to processing my application under the privacy notice."}
        operation = {"action": "click", "intent": "privacy", "consent": {"kind": "application_privacy", "quote": page["text"]}}
        validate_operation(self.profile, page, operation)
        page["text"] = "I agree to a class-action waiver."
        operation["consent"]["quote"] = page["text"]
        with self.assertRaisesRegex(ValueError, "binding terms"):
            validate_operation(self.profile, page, operation)

    def test_additional_permission_applies_only_to_named_job_and_approved_topics(self):
        """A specific grant for an optional permission stays scoped to its employer and subject."""
        control, answer = self.privacy_answer("I agree to optional interview recording.")
        self.profile["facts"].append({"id": "approval", "kind": "application_agreement", "confirmed": True,
            "text": "Application job: Optional interview recording.",
            "sources": [{"role": "user", "quote": "I accept these terms."}]})
        action = {"intent": "agreement", "action": "click", "element_id": control["id"],
                  "agreement_quote": control["label"], "fact_ids": ["approval"]}
        validate_operation(self.profile, self.page, action, self.job)
        answer.pop("consent")
        answer["fact_ids"] = ["approval"]
        answer["agreement_quote"] = control["label"]
        self.assertTrue(self.validate()["passed"])
        with self.assertRaisesRegex(ValueError, "this posting"):
            validate_operation(self.profile, self.page, action, {"id": "different"})
        control["label"] = action["agreement_quote"] = "I agree to optional marketing."
        with self.assertRaisesRegex(ValueError, "does not cover"):
            validate_operation(self.profile, self.page, action, self.job)

    def privacy_answer(self, label="I agree"):
        """Represent a short checkbox label with its separate currently visible processing notice."""
        notice = "The recruitment privacy notice describes processing your application data."
        self.page["text"] += "\n" + notice
        control = {"id": "privacy", "role": "AXCheckBox", "label": label, "value": 1,
                   "enabled": True, "web_content": True}
        answer = {"element_id": "privacy", "value": 1, "quote": label,
                  "consent": {"kind": "application_privacy", "quote": notice}}
        self.page["elements"].append(control)
        self.review["answers"] = [answer]
        return control, answer

    def test_permitted_privacy_choice_survives_final_review_without_profile_fact(self):
        """The same visible consent accepted during filling is satisfiable during final self-review."""
        control, answer = self.privacy_answer()
        validate_operation(self.profile, self.page, {"action": "click", "element_id": control["id"],
            "intent": "privacy", "consent": answer["consent"]})
        self.assertTrue(self.validate()["passed"])
        control["role"] = "AXRadioButton"
        self.assertTrue(self.validate()["passed"])
        self.review["answers"] = []
        with self.assertRaisesRegex(ValueError, "currently populated"):
            self.validate()

    def test_privacy_review_requires_current_checked_control_and_exact_value(self):
        """Disabled, unchecked, mixed, changed-value and personal text controls cannot use this branch."""
        control, answer = self.privacy_answer()
        for changed in ({"enabled": False}, {"value": 0}, {"value": 2}, {"role": "AXTextField"}):
            original = copy.deepcopy(control)
            control.update(changed)
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "currently enabled, checked"):
                self.validate()
            control.clear()
            control.update(original)
        answer["value"] = 0
        with self.assertRaisesRegex(ValueError, "current answer differs"):
            self.validate()

    def test_privacy_review_requires_current_notice_and_matching_label(self):
        """An old notice or a quote from a different checkbox cannot authorize the current choice."""
        control, answer = self.privacy_answer()
        self.evidence.append(observation(self.page))
        self.page["text"] = "Example Co Hardware Graduate Name: Alex Test Resume: resume.pdf"
        with self.assertRaisesRegex(ValueError, "visible notice"):
            self.validate()
        self.page["text"] += "\n" + answer["consent"]["quote"]
        control["label"] = "Another choice"
        with self.assertRaisesRegex(ValueError, "currently observed control label"):
            self.validate()

    def test_privacy_review_keeps_binding_terms_and_other_classifications_blocked(self):
        """Neither the visible notice nor a waiver checkbox can hide behind ordinary privacy classification."""
        control, answer = self.privacy_answer()
        answer["consent"]["quote"] = "I agree to a class-action waiver."
        self.page["text"] += "\n" + answer["consent"]["quote"]
        with self.assertRaisesRegex(ValueError, "binding terms"):
            self.validate()
        answer["consent"]["quote"] = "Example Co"
        control["label"] = answer["quote"] = "I agree to a class-action waiver."
        with self.assertRaisesRegex(ValueError, "binding terms"):
            self.validate()
        answer["consent"]["kind"] = "cookie_reject"
        with self.assertRaisesRegex(ValueError, "ordinary application privacy"):
            self.validate()

    def test_personal_answers_and_prior_summaries_still_require_profile_facts(self):
        """Consent metadata is not a general exemption from confirmed personal evidence."""
        _, answer = self.privacy_answer()
        consent = answer.pop("consent")
        with self.assertRaisesRegex(ValueError, "No confirmed supporting evidence"):
            self.validate()
        answer["consent"] = consent
        self.review["summaries"] = [{"quote": "Name: Alex Test", "consent": consent}]
        with self.assertRaisesRegex(ValueError, "No confirmed supporting evidence"):
            self.validate()

    def test_numeric_year_is_retained_and_checked_as_an_exact_native_value(self):
        """Numeric controls participate in evidence and review without converting their values to strings."""
        self.profile["facts"].append({"id": "end-year", "text": "2027", "confirmed": True})
        year = {"id": "year", "role": "AXIncrementor", "label": "Expected year", "value": 2027.0,
                "enabled": True, "web_content": True, "bounds_source": "native_container"}
        self.page["elements"].append(year)
        answer = {"element_id": "year", "value": 2027.0, "quote": "2027", "fact_ids": ["end-year"]}
        self.review["answers"] = [answer]
        observed = observation(self.page)
        self.assertEqual(observed["controls"][0]["value"], 2027.0)
        self.assertEqual(review_context([observed])["latest_controls"][0]["role"], "AXIncrementor")
        self.assertTrue(self.validate()["passed"])
        answer["value"] = "2027"
        with self.assertRaisesRegex(ValueError, "current answer differs"):
            self.validate()
        answer["value"] = 2027.0
        year["value"] = 2023.0
        with self.assertRaisesRegex(ValueError, "current answer differs"):
            self.validate()

    def test_numeric_zero_is_an_answer_and_numeric_typing_still_needs_confirmed_facts(self):
        """Zero can be a real numeric answer; it must not disappear as an unchecked boolean."""
        self.page["elements"].append({"id": "years", "role": "AXIncrementor", "label": "Years", "value": 0,
                                      "enabled": True, "web_content": True})
        with self.assertRaisesRegex(ValueError, "currently populated"):
            self.validate()
        with self.assertRaisesRegex(ValueError, "confirmed profile"):
            validate_operation(self.profile, self.page, {"action": "type", "element_id": "years", "text": "0"})
        self.profile["facts"].append({"id": "years", "text": "0 years", "confirmed": True})
        self.review["answers"] = [{"element_id": "years", "value": 0, "quote": "0", "fact_ids": ["years"]}]
        validate_operation(self.profile, self.page, {"action": "type", "element_id": "years", "text": "0", "fact_ids": ["years"]})
        self.assertTrue(self.validate()["passed"])
        self.review["answers"][0]["value"] = False
        with self.assertRaisesRegex(ValueError, "current answer differs"):
            self.validate()
