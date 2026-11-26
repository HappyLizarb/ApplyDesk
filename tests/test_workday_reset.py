"""The account recovery action sends at most one email and never sets a password."""

import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

from applydesk.account_scope import employer_key
from applydesk.workday_reset import request_reset_email


class WorkdayResetTests(unittest.TestCase):
    def setUp(self):
        self.url = "https://example.wd5.myworkdayjobs.com/Careers/job/Engineer_01/apply"
        self.job = {"id": "listing", "company": "Example", "public_source": {"complete": True, "url": self.url}}
        key = self.job["employer_account_id"] = employer_key(self.job)
        self.account = {"id": key, "owner_job_id": "listing", "realm": self.url, "origin": self.url.split("/Careers")[0],
                        "account": "test@example.org"}
        store = Mock()
        store.lease.return_value = nullcontext()
        store.get.side_effect = lambda kind, key: self.job if kind == "job" else self.account
        self.engine = SimpleNamespace(check_active=Mock(), w=SimpleNamespace(root="/tmp", store=store),
            active={"message": {"role": "user", "text": "Request one password-reset email for listing."}})
        self.session = self.enterContext(patch("applydesk.workday_reset.ApplicationSession")).return_value
        self.enterContext(patch("applydesk.workday_reset.time.sleep"))
        self.email = {"id": "email", "role": "AXTextField", "label": "Email Address", "enabled": True}
        self.button = {"id": "reset", "role": "AXButton", "label": "Reset Password", "enabled": True}
        self.form = {"snapshot_id": "page", "url": self.url, "text": "Forgot your password?", "elements": [self.email, self.button]}

    def test_user_authorizes_one_email_to_bound_account(self):
        typed = dict(self.form, elements=[dict(self.email, value=self.account["account"]), self.button])
        acknowledgement = "You will receive an email with instructions to reset your password if an account exists for this email address."
        self.session.call.side_effect = [self.form, typed, dict(self.form, text=acknowledgement)]
        result = request_reset_email(self.engine, "listing")
        self.assertEqual(result["status"], "acknowledged")
        self.assertEqual(request_reset_email(self.engine, "listing"), result)
        self.assertEqual(self.session.call.call_count, 3)
        self.assertEqual(self.session.call.call_args.args[1]["element_id"], "reset")

    def test_cross_tenant_and_password_forms_do_not_receive_input(self):
        foreign = dict(self.form, url=self.url.replace("example.", "other."))
        self.session.call.return_value = foreign
        with self.assertRaisesRegex(ValueError, "tenant|account"):
            request_reset_email(self.engine, "listing")
        self.session.call.return_value = dict(self.form, elements=[self.email, self.button, {"secure": True}])
        with self.assertRaisesRegex(ValueError, "identifiable"):
            request_reset_email(self.engine, "listing")
        self.assertFalse(any(call.args[0] == "native_action" for call in self.session.call.call_args_list))

    def test_existing_account_can_follow_signin_navigation_from_create_account(self):
        """The Sign In switch on registration only changes pages and cannot send a password."""
        def control(identity, role, label, **extra):
            return {"id": identity, "role": role, "label": label, "enabled": True, "web_content": True, **extra}
        signup = dict(self.form, elements=[control("pw", "AXTextField", "Password", secure=True),
            control("confirm", "AXTextField", "Verify Password", secure=True),
            control("create", "AXButton", "Create Account"), control("switch", "AXLink", "Sign In")])
        signin = dict(self.form, elements=[control("pw", "AXTextField", "Password", secure=True),
                                           control("forgot", "AXLink", "Forgot your password?")])
        typed = dict(self.form, elements=[dict(self.email, value=self.account["account"]), self.button])
        self.session.call.side_effect = [signup, signin, self.form, typed, dict(self.form, text="Check your inbox.")]
        self.assertEqual(request_reset_email(self.engine, "listing")["status"], "acknowledged")
        actions = [call.args[1]["element_id"] for call in self.session.call.call_args_list if call.args[0] == "native_action"]
        self.assertEqual(actions, ["switch", "forgot", "email", "reset"])

    def test_uncertain_dispatch_is_never_resent(self):
        typed = dict(self.form, elements=[dict(self.email, value=self.account["account"]), self.button])
        self.session.call.side_effect = [self.form, typed, TimeoutError()]
        with self.assertRaises(TimeoutError):
            request_reset_email(self.engine, "listing")
        self.assertEqual(request_reset_email(self.engine, "listing")["status"], "requested_unconfirmed")
        self.assertEqual(self.session.call.call_count, 3)
