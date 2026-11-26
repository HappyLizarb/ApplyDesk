"""Workday listing navigation and credential dispatch are distinct actions."""
import unittest
from unittest.mock import Mock

from applydesk.account_scope import bound_signin_host, employer_key, is_signin_switch
from applydesk.native_evidence import validate_operation
from applydesk.native_page import return_to_listing


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
