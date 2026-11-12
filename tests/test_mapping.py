"""Regression checks for ambiguous fields, upload types, and credential boundaries."""

import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from applydesk.application_steps import attachment_plan, audit_snapshot, authenticate, choose_attachment, cover_text_plan
from applydesk.mapping import answer_field, document_kind, plan_fields


def profile():
    """Use only fictional confirmed values in mapping and authentication tests."""
    return {"identity": {"legal_name": "Avery Example", "given_name": "Avery", "family_name": "Example",
                         "email": "avery@example.test"}, "websites": [], "preferences": {},
            "authorizations": [{"country": "US", "status": "authorized_no_sponsor"}],
            "facts": [{"id": "identity.legal_name", "text": "Avery Example", "confirmed": True, "kind": "identity"}]}


def field(identity="f", label="First name", **changes):
    """Describe the native metadata consumed by the planner without a browser."""
    result = {"field_id": identity, "label": label, "type": "text", "required": True,
              "disabled": False, "supported": True, "value": "", "form_id": "form",
              "form_action": "https://jobs.example.test/login", "form_method": "post"}
    return {**result, **changes}


def auth_page(action="login"):
    """An isolated native POST form is the only positive authentication fixture."""
    endpoint = "https://jobs.example.test/" + ("register" if action == "create_account" else "login")
    fields = [field("email", "Email", type="email", form_action=endpoint),
              field("password", "Password", type="password", value=None, form_action=endpoint)]
    button = {"field_id": "login", "action": action, "disabled": False, "auth_form": True,
              "type": "submit", "form_id": "form", "form_action": endpoint, "form_method": "POST"}
    return {"url": endpoint, "snapshot_id": "first", "fields": fields, "buttons": [button]}


class MappingTests(unittest.TestCase):
    """Visible wording and native grouping must identify a single confirmed answer."""

    def test_field_label_takes_priority_over_fieldset(self):
        answer = answer_field(field(group="Name"), profile(), "US")
        self.assertEqual(answer["value"], "Avery")
        self.assertEqual(answer["fact_ids"], ["identity.given_name"])

    def test_option_values_do_not_define_visible_meaning(self):
        question = field(label="Do you require sponsorship?", type="select",
                         options=[{"label": "Yes", "value": "no"}])
        self.assertIsNone(answer_field(question, profile(), "US"))
        question["options"].append({"label": "No", "value": "yes"})
        self.assertEqual(answer_field(question, profile(), "US")["value"], "yes")

    def test_duplicate_visible_answers_or_opaque_values_pause(self):
        for options in ([{"label": "No", "value": "a"}, {"label": "No", "value": "b"}],
                        [{"label": "No", "value": "a"}, {"label": "Yes", "value": "a"}]):
            with self.subTest(options=options):
                question = field(label="Do you require sponsorship?", type="select", options=options)
                self.assertIsNone(answer_field(question, profile(), "US"))

    def test_unknown_or_negated_authorization_is_not_sponsorship(self):
        for label in ("Are you not authorized to work in the United States?", "Will your authorization expire?",
                      "Can you work without sponsorship?", "Are you authorized to work in Canada?"):
            with self.subTest(label=label):
                self.assertIsNone(answer_field(field(label=label), profile(), "US"))

    def test_radios_need_one_native_group_and_one_answer(self):
        radios = [field(value, label, type="radio", name="sponsor", group="Do you require sponsorship?",
                        group_id="question", checked=False) for value, label in (("y", "Yes"), ("n", "No"))]
        answers, issues = plan_fields({"fields": radios}, profile(), "US")
        self.assertEqual([answer["field_id"] for answer in answers], ["n"])
        self.assertEqual(issues, [])
        for mutation in ({"name": ""}, {"group_id": None}, {"group": "An unrelated question"}, {"label": "No"}):
            changed = copy.deepcopy(radios)
            changed[0].update(mutation)
            with self.subTest(mutation=mutation):
                self.assertTrue(plan_fields({"fields": changed}, profile(), "US")[1])

    def test_duplicate_radio_question_in_another_form_is_not_exempt(self):
        fields = [field("y", "Yes", type="radio", name="sponsor", group="Do you require sponsorship?", group_id="one"),
                  field("n", "No", type="radio", name="sponsor", group="Do you require sponsorship?", group_id="one"),
                  field("other", "Maybe", type="radio", name="sponsor", group="Do you require sponsorship?",
                        group_id="two", form_id="other-form")]
        answers, issues = plan_fields({"fields": fields}, profile(), "US")
        self.assertEqual(len(answers), 1)
        self.assertTrue(issues)

    def test_optional_unchecked_box_is_not_a_populated_answer(self):
        checkbox = field(label="Marketing updates", type="checkbox", required=False, checked=False, value="on")
        self.assertEqual(plan_fields({"fields": [checkbox]}, profile(), "US"), ([], []))
        checkbox["checked"] = True
        self.assertTrue(plan_fields({"fields": [checkbox]}, profile(), "US")[1])

    def test_readonly_is_audited_and_never_silently_trusted(self):
        name = field(readonly=True, value="Wrong name")
        self.assertTrue(plan_fields({"fields": [name]}, profile(), "US")[1])
        name["value"] = "Avery"
        answers, issues = plan_fields({"fields": [name]}, profile(), "US")
        self.assertEqual(answers[0]["value"], "Avery")
        self.assertEqual(issues, [])


class AttachmentTests(unittest.TestCase):
    """File identity and accepted formats are checked before any upload action."""

    def setUp(self):
        """Small temporary byte fixtures exercise selection without document tooling."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name in ("letter.pdf", "letter.docx"):
            (self.root / name).write_bytes(name.encode())
        self.w = SimpleNamespace(docs=Mock(), profile=profile)
        self.w.docs.asset.side_effect = lambda name: self.root / name
        self.document = {"id": "doc", "kind": "document", "pdf_path": "letter.pdf", "docx_path": "letter.docx"}

    def test_mixed_attachment_label_is_unresolved(self):
        for label in ("Resume and cover letter", "Transcript or resume", "Do not upload a cover letter"):
            with self.subTest(label=label):
                upload = field(label=label, type="file")
                self.assertIsNone(document_kind(upload))
                self.assertTrue(attachment_plan(self.w, Mock(), {}, {"fields": [upload]})[1])

    def test_optional_existing_cover_or_transcript_cannot_be_ignored(self):
        for label in ("Cover letter", "Transcript"):
            upload = field(label=label, type="file", required=False, files=[{"name": "old.pdf"}])
            documents = Mock()
            self.assertTrue(attachment_plan(self.w, documents, {}, {"fields": [upload]})[1])
            documents.cover.assert_not_called()
            upload["files"] = []
            self.assertEqual(attachment_plan(self.w, documents, {}, {"fields": [upload]}), ([], []))

    def test_exact_mime_or_extension_selects_the_supported_file(self):
        for accept, suffix in (("application/pdf", ".pdf"), (".DOCX", ".docx"),
                               ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
                               (".docx, .pdf", ".pdf")):
            with self.subTest(accept=accept):
                path, digest = choose_attachment(self.w, self.document, {"accept": accept})
                self.assertEqual(path.suffix, suffix)
                self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
        for accept in ("application/msword", "image/*", ".doc", "application/x-pdf-malformed"):
            with self.subTest(accept=accept), self.assertRaises(ValueError):
                choose_attachment(self.w, self.document, {"accept": accept})

    def test_required_cover_text_uses_reviewed_version_and_excludes_disclosures(self):
        facts = profile()
        facts["facts"].append({"id": "preferences.disability", "confirmed": True, "kind": "disclosure"})
        self.w.profile = lambda: facts
        self.w.docs.get.return_value = {"id": "letter", "blocks": [{"text": "Dear team"}, {"text": "Avery Example"}]}
        documents = Mock()
        answers, handled = cover_text_plan(self.w, documents, {"cover_id": "letter"},
                                          {"fields": [field("cover", "Cover letter", type="textarea")]})
        self.assertEqual(handled, {"cover"})
        self.assertEqual(answers[0]["value"], "Dear team\n\nAvery Example")
        self.assertEqual(answers[0]["document_id"], "letter")
        self.assertEqual(answers[0]["fact_ids"], ["identity.legal_name"])
        documents.require_review.assert_called_once_with("letter")


class AuthenticationTests(unittest.TestCase):
    """No credential is filled until origin and the native auth form are proven."""

    def setUp(self):
        """Secrets and browser operations are mocked; no live credentials are accessed."""
        self.w = SimpleNamespace(secrets=Mock(), store=Mock(), profile=profile)
        self.w.secrets.get.return_value = "fictional-secret"
        self.session = Mock()
        self.session.call.side_effect = [{"snapshot_id": "filled"}, {"snapshot_id": "authenticated"}]
        self.job = {"id": "job", "account": "avery@example.test", "account_origin": "https://jobs.example.test"}

    def test_bound_login_fills_then_uses_exact_button(self):
        authenticate(self.w, self.session, self.job, auth_page())
        self.assertEqual(self.session.call.call_args_list[0].args[0], "fill")
        self.assertEqual(self.session.call.call_args_list[1].args,
                         ("authenticate", {"snapshot_id": "filled", "field_id": "login", "intent": "sign_in"}))

    def test_unbound_or_changed_origin_never_fills_saved_credentials(self):
        for origin in (None, "https://other.example.test", "http://jobs.example.test"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                authenticate(self.w, self.session, {**self.job, "account_origin": origin}, auth_page())
        self.session.call.assert_not_called()

    def test_saved_password_without_account_origin_cannot_be_reused_for_signup(self):
        with self.assertRaises(ValueError):
            authenticate(self.w, self.session, {"id": "job"}, auth_page("create_account"))
        self.session.call.assert_not_called()
        self.w.secrets.set.assert_not_called()

    def test_unsafe_native_forms_fail_before_reading_secrets(self):
        cases = []
        for change in ({"type": "button"}, {"auth_form": False}, {"form_id": None}, {"form_method": "GET"},
                       {"form_action": "https://other.example.test/login"}, {"form_action": "https://jobs.example.test/jobs/submit"},
                       {"context_text": "By clicking Create account you agree to our terms."}):
            snapshot = auth_page("create_account")
            snapshot["buttons"][0].update(change)
            cases.append(snapshot)
        insecure = auth_page()
        insecure["url"] = "http://jobs.example.test/login"
        cases.append(insecure)
        for extra in (field("file", "Resume", type="file"), field("answer", "Why work here?"),
                      field("consent", "Terms", type="checkbox", checked=False)):
            snapshot = auth_page()
            snapshot["fields"].append(extra)
            cases.append(snapshot)
        for snapshot in cases:
            with self.subTest(snapshot=snapshot), self.assertRaises(ValueError):
                authenticate(self.w, self.session, {"id": "job"}, snapshot)
        self.w.secrets.get.assert_not_called()
        self.session.call.assert_not_called()

    def test_new_signup_binds_origin_before_browser_fill(self):
        self.w.secrets.get.return_value = None
        job = {"id": "job"}
        def browser_call(method, parameters):
            """The account binding must already be durable at the first browser write."""
            self.assertEqual(self.w.store.job.call_args.kwargs["account_origin"], "https://jobs.example.test")
            return {"snapshot_id": method}
        self.session.call.side_effect = browser_call
        authenticate(self.w, self.session, job, auth_page("create_account"))
        self.assertEqual(job["account_origin"], "https://jobs.example.test")
        self.assertEqual(self.w.store.job.call_args.kwargs["account_origin"], job["account_origin"])
        self.assertEqual(self.w.store.job.call_args.kwargs["live_url"], "https://jobs.example.test/register")
        self.w.secrets.set.assert_called_once()

    def test_audit_stamp_requires_nonempty_verified_fields(self):
        snapshot = {"snapshot_id": "live", "fields": [field(label="Full name", value="Avery Example")], "guard": {}}
        self.session.call.side_effect = None
        self.session.call.return_value = snapshot
        self.w.client = Mock()
        with patch("applydesk.intelligence.audit_fields", return_value={"passed": True, "issues": []}):
            actual = audit_snapshot(self.w, self.session, profile(), "US", "", [])
        self.assertEqual(actual["audit"], {"passed": True, "snapshot_id": "live", "field_ids": ["f"]})
        snapshot["fields"] = []
        self.assertNotIn("audit", audit_snapshot(self.w, self.session, profile(), "US", "", []))


if __name__ == "__main__":
    unittest.main()
