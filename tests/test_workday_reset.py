"""The account recovery action sends at most one email and never sets a password."""

import unittest
from applydesk.account_scope import bound_signin_host, employer_key, is_signin_switch
from applydesk.native_evidence import validate_operation
from applydesk.native_page import return_to_listing
from applydesk.recommendation_source import same_workday_job
from applydesk.workday_reset import request_reset_email
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch


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


class WorkdayListingSignInTests(unittest.TestCase):
    def setUp(self):
        self.origin = "https://example.wd5.myworkdayjobs.com"
        self.url = self.origin + "/en-US/Engineering/job/Engineer_JR00001"
        self.job = {"id": "other-opening", "company": "Example", "public_source": {
            "complete": True, "url": self.url, "company": "Example"}}
        identity = self.job["employer_account_id"] = employer_key(self.job)
        self.saved = {"id": identity, "owner_job_id": "earlier-opening", "reuse_confirmed": True,
                      "origin": self.origin, "realm": self.origin + "/en-US/Engineering/"}
        self.signin = {"id": "sign", "label": "Sign In", "role": "AXButton", "enabled": True, "web_content": True}
        self.page = {"url": self.url, "elements": [self.signin, dict(self.signin, id="apply", label="Apply")]}
        self.click = {"action": "click", "element_id": "sign", "intent": "navigation"}

    def test_verified_listing_allows_only_the_signin_navigation(self):
        self.assertTrue(is_signin_switch(self.page, self.click, self.saved, self.job))
        self.assertFalse(is_signin_switch(self.page, dict(self.click, element_id="apply"), self.saved, self.job))
        for field in ({"role": "AXTextField"}, {"secure": True, "clipped": True}, {"parent_dialog_id": "login"}):
            page = dict(self.page, elements=self.page["elements"] + [dict(field, web_content=True)])
            self.assertFalse(is_signin_switch(page, self.click, self.saved, self.job))
        for url in (self.url + "/apply", self.url.replace("00001", "00002"), self.url.replace("example.", "foreign.")):
            self.assertFalse(is_signin_switch(dict(self.page, url=url), self.click, self.saved, self.job))

    def test_account_reuse_must_be_confirmed_for_another_requisition(self):
        self.assertEqual(bound_signin_host(self.job, self.saved, self.url + "/apply"), "example.wd5.myworkdayjobs.com")
        saved = dict(self.saved, reuse_confirmed=False)
        self.assertIsNone(bound_signin_host(self.job, saved, self.url + "/apply"))
        self.assertFalse(is_signin_switch(self.page, self.click, saved, self.job))

    def test_registration_header_opens_signin_when_signup_submit_is_offscreen(self):
        """Only the unique same-site navigation above a verified two-password registration form is safe."""
        header = dict(self.signin, bounds={"y": 10, "height": 20})
        heading = dict(header, id="title", role="AXHeading", label="Create Account", bounds={"y": 100})
        password = dict(header, id="p", role="AXTextField", secure=True, label="Password")
        confirm = dict(password, id="confirm", label="Verify Password")
        page = dict(self.page, url=self.url + "/apply", elements=[header, heading, password, confirm])
        self.assertTrue(is_signin_switch(page, self.click, self.saved, self.job))
        for changed in ([header, heading, password], [header, password, confirm],
                        [header, heading, password, confirm, dict(header, id="second")],
                        [header, dict(heading, bounds={"y": 29}), password, confirm],
                        [dict(header, clipped=True), heading, password, confirm],
                        [dict(header, role="AXLink", url="https://elsewhere.example/login"), heading, password, confirm]):
            with self.subTest(elements=changed):
                self.assertFalse(is_signin_switch(dict(page, elements=changed), self.click, self.saved, self.job))
        for changed in ({"elements_truncated": True}, {"native_modal": True}, {"url": page["url"].replace("Engineering", "Other")}):
            self.assertFalse(is_signin_switch(dict(page, **changed), self.click, self.saved, self.job))
        # Recovery may follow a redirect within this career site, while a normal sign-in stays inside its saved realm.
        exact_realm = dict(self.saved, realm=page["url"])
        redirected = dict(page, url=self.origin + "/en-US/Engineering/createAccount")
        self.assertFalse(is_signin_switch(redirected, self.click, exact_realm, self.job))
        self.assertTrue(is_signin_switch(redirected, self.click, exact_realm, recovery=True))
        foreign = dict(redirected, url=redirected["url"].replace("Engineering", "Other"))
        self.assertFalse(is_signin_switch(foreign, self.click, exact_realm, recovery=True))

    def test_only_the_verified_requisition_can_be_used_for_job_search(self):
        self.job["public_source"]["description"] = "Job requisition id\nJR00001"
        field = dict(self.signin, role="AXTextField", label="Search for Jobs")
        page = {"elements": [field]}
        query = dict(self.click, action="type", text="JR00001")
        validate_operation({}, page, query, self.job)
        for different in (dict(query, text="JR00003"), dict(query, element_id="missing")):
            with self.assertRaises(ValueError):
                validate_operation({}, page, different, self.job)
        self.job["public_source"]["complete"] = False
        with self.assertRaises(ValueError):
            validate_operation({}, page, query, self.job)

    def test_candidate_home_can_reopen_only_its_verified_posting(self):
        session = Mock()
        home = self.origin + "/en-US/Engineering/userHome"
        return_to_listing(session, self.job, {"url": home})
        session.call.assert_called_once_with("open", {"url": self.url})
        for link in (home.replace("Engineering", "Other"), self.url + "/apply", home.replace("example.", "foreign.")):
            with self.assertRaises(ValueError):
                return_to_listing(session, self.job, {"url": link})
        self.assertEqual(session.call.call_count, 1)

    def test_workday_application_steps_keep_exact_employer_site_and_requisition(self):
        original = 'https://example.wd5.myworkdayjobs.com/Careers/job/Example-City/Engineer_JR123'
        self.assertTrue(same_workday_job(original, original + '/apply'))
        self.assertTrue(same_workday_job(original, original.replace('/Careers/', '/en-US/Careers/') + '/apply/applyManually'))
        for changed in [original.replace('JR123', 'JR124'), original.replace('example.', 'other.'), original.replace('/Careers/', '/Other/'), original + '/unknown']:
            self.assertFalse(same_workday_job(original, changed))
