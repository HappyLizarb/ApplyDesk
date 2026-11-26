"""Chrome startup timing is simulated without browser input, network, or employer pages."""

import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from applydesk.browser_bridge import BrowserError
from applydesk.native_application_browser import NativeApplicationBrowser
from native_window_fixture import NativeWindowFixture
from applydesk.native_application_io import _select_file
from applydesk.native_navigation import clear_upload_picker, open_job_tab, restore_job_tab, resume_visible_draft


class DelayedChrome(NativeWindowFixture):
    """Expose delayed tab painting without opening a real browser."""

    def __init__(self, redactions=None):
        super().__init__(redactions)
        self.pending, self.missing_tabs = [], False

    def open_public_url(self, url, generation):
        before = self.page()
        super().open_public_url(url, generation)
        self.pending = [before, before]

    def call(self, method, params=None, expected_generation=None):
        result = super().call(method, params, expected_generation)
        if method == "observe" and self.pending:
            result = self.pending.pop(0)
        if self.missing_tabs:
            result = {**result, "tabs": [], "active_tab_id": None}
        return copy.deepcopy(result)


class NativeNavigationTests(unittest.TestCase):
    def setUp(self):
        """Every native helper and public hostname is replaced before startup can run."""
        self.factory = patch("applydesk.native_application_browser.NativeBrowser", side_effect=DelayedChrome).start()
        patch("applydesk.native_application_browser.public_job_url", side_effect=lambda url: url).start()
        patch("applydesk.native_navigation.time.sleep").start()
        patch.object(NativeApplicationBrowser, "_settle", lambda manager, generation: manager._wire("observe", {}, generation)).start()
        self.addCleanup(patch.stopall)
        store = SimpleNamespace(get=lambda kind, identity: {"id": identity})
        self.manager = NativeApplicationBrowser(SimpleNamespace(store=store))

    def test_delayed_link_opens_once_and_never_types_into_the_address_bar(self):
        """Use the new rendered document even when the previous tab is visible for two reads."""
        self.manager.call("one", "open", {"url": "https://employer.test/one"})
        calls = self.manager.browser.calls
        self.assertEqual([params["url"] for method, params in calls if method == "open_public_url"], ["https://employer.test/one"])
        self.assertFalse(any(method == "action" for method, _ in calls))
        self.assertEqual(self.manager.browser.active, self.manager.tabs["one"])

    def test_wrong_document_is_never_adopted_or_reopened(self):
        """A different rendered job cannot become the requested application, even in a new tab."""
        self.manager._ensure_browser()
        browser = self.manager.browser
        original = browser.open_public_url
        with patch.object(browser, "open_public_url", side_effect=lambda url, gen: original("https://employer.test/different", gen)) as opened:
            for _ in range(2):
                with self.assertRaisesRegex(BrowserError, "did not show the requested posting"):
                    self.manager.call("one", "open", {"url": "https://employer.test/one"})
        opened.assert_called_once()
        self.assertNotIn("one", self.manager.tabs)

    def test_browser_arrow_names_reach_the_native_driver(self):
        """Keys used to choose a focused radio or menu option retain their intended direction."""
        page = self.manager.call("one", "open", {"url": "https://employer.test/one"})
        for direction in ("Up", "Down", "Left", "Right"):
            page = self.manager.call("one", "native_action", {"action": "key", "key": "Arrow" + direction,
                                     "snapshot_id": page["snapshot_id"]})
            self.assertEqual(self.manager.browser.calls[-1][1]["key"], direction.lower())

    def test_protected_tab_title_uses_fresh_chrome_tab_evidence(self):
        """A repaint before dispatch can be retried while a web radio never receives tab authority."""
        self.manager.call("one", "open", {"url": "https://employer.test/apply"})
        self.manager.browser.active = "tab-1"
        browser, original = self.manager.browser, self.manager.browser.call
        tab, repainted = self.manager.tabs["one"], False

        def guarded(method, params, expected_generation):
            nonlocal repainted
            if method == "action" and params.get("element_id") == tab:
                if not params.get("final_permit"):
                    browser.revision += 1
                    raise BrowserError("This control requires the protected account or reviewed submission operation.")
                self.assertFalse(is_web)
                self.assertEqual(params["snapshot_id"], str(browser.revision))
                if not repainted:
                    repainted = True
                    browser.revision += 1
                    error = BrowserError("The visible page changed.")
                    error.code = "stale_observation"
                    raise error
            result = original(method, params, expected_generation)
            result["controls"].append({"id": tab, "label": "Create Account", "role": "AXRadioButton", "web_content": is_web})
            return result

        with patch.object(browser, "call", side_effect=guarded):
            is_web = True
            with self.assertRaisesRegex(BrowserError, "Chrome tab could not be verified"):
                self.manager.call("one", "native_observe")
            is_web = False
            self.assertEqual(self.manager.call("one", "native_observe")["active_tab_id"], tab)
            self.assertTrue(repainted)

    def test_exact_foreground_listing_can_be_reused_without_opening_a_tab(self):
        """A restarted session can bind the visible posting unless another job owns its tab."""
        target = "https://employer.test/jobs/123"
        page = {"document_url": target, "active_tab_id": "existing", "tabs": [{"id": "existing"}]}
        for owners, expected in (({}, "existing"), ({"other": "existing"}, "new")):
            manager = Mock(tabs=owners.copy(), drafts={}, notice_forms={})
            with patch("applydesk.native_navigation.open_public_tab", return_value={"active_tab_id": "new"}) as open_tab:
                open_job_tab(manager, "job", page, 1, target, False)
            self.assertEqual(manager.tabs["job"], expected)
            self.assertEqual(open_tab.call_count, int(bool(owners)))

    def test_failed_tab_readiness_keeps_owned_window_available_for_show(self):
        """A startup failure cannot hide the retained window from the user or invent tab ownership."""
        self.manager._ensure_browser()
        self.manager.browser.missing_tabs = True
        with self.assertRaisesRegex(BrowserError, "identifiable"):
            self.manager.call("one", "open", {"url": "https://employer.test/one"})
        self.assertEqual(self.manager.last_job_id, "one")
        self.assertTrue(self.manager.live())
        self.assertEqual(self.manager.tabs, {})
        self.assertFalse(any(params.get("action") == "type" for _, params in self.manager.browser.calls))

    def test_only_unsent_open_can_retry_in_the_same_picker_and_tab(self):
        """A stale pre-click observation can recover; a changed tab or uncertain click cannot replay."""
        from test_native_application_io import upload_pages
        _, picker, _, _, selected, done = upload_pages()
        owner = {"pid": 7, "window_id": 8, "active_tab_id": "job", "generation": 1}
        picker.update(owner)
        selected.update(owner)
        for code, tab, succeeds in [("stale_observation", "job", True), ("stale_observation", "other", False),
                                    (None, "job", False)]:
            session = Mock()
            error = BrowserError("Rejected")
            error.code = code
            session.call.side_effect = [error, dict(picker, active_tab_id=tab), done]
            with patch("applydesk.native_application_io._unchanged"), self.subTest(code=code, tab=tab), patch("applydesk.native_application_io._enter_path", return_value=selected) as enter:
                if succeeds:
                    self.assertEqual(_select_file(session, picker, "picker", "/fixture/original.pdf", "digest"), done)
                else:
                    with self.assertRaises(BrowserError):
                        _select_file(session, picker, "picker", "/fixture/original.pdf", "digest")
                self.assertEqual(enter.call_count, 2 if succeeds else 1)

    def test_closes_only_chromes_optional_password_suggestion(self):
        """Webpage controls, other prompts and an unidentified tab cannot inherit the Close action."""
        rect = {"x": 0, "y": 0, "width": 300, "height": 250}
        dialog = {"id": "dialog", "native": True, "label": "Save password?", "bounds": rect}
        buttons = [{"id": label, "label": label, "parent_dialog_id": "dialog", "web_content": False,
                    "role": "AXButton", "enabled": True, "bounds": rect} for label in ("Save", "Close", "No, thanks")]
        page = {"active_tab_id": "tab", "native_modal": True, "dialogs": [dialog], "controls": buttons}
        done = {"active_tab_id": "tab", "dialogs": [], "native_modal": False}
        manager = Mock(upload_picker=None)
        manager._action.return_value = done
        self.assertEqual(clear_upload_picker(manager, page, 1), done)
        manager._action.assert_called_once_with({"action": "click", "element_id": "Close"}, page, 1)
        for changes in ({"active_tab_id": None}, {"dialogs": [dict(dialog, label="Allow access?")]},
                        {"controls": [dict(item, web_content=True) for item in buttons]}):
            with self.subTest(changes=changes), self.assertRaises(BrowserError):
                clear_upload_picker(manager, {**page, **changes}, 1)
        self.assertEqual(manager._action.call_count, 1)

    def test_screening_preserves_a_draft_only_at_its_original_destination(self):
        """Refresh the public listing, then resume the same draft unless the user navigated its tab away."""
        form, listing = "https://employer.test/one/apply", "https://employer.test/one"
        tabs = [{"id": "form"}, {"id": "listing"}]
        for destination in (form, "https://employer.test/other/apply"):
            with self.subTest(destination=destination):
                manager = Mock(tabs={"one": "form"}, drafts={}, notice_forms={}, created_tabs={})
                manager.w.store.get.return_value = {"live_url": form, "attachments": [{"kind": "resume"}]}
                draft = {"active_tab_id": "form", "document_url": form, "tabs": tabs}
                fresh = {"active_tab_id": "listing", "document_url": listing, "tabs": tabs}
                manager._find_tab.return_value = draft
                manager._select.side_effect = [draft, dict(draft, document_url=destination), fresh]
                with patch("applydesk.native_navigation.open_public_tab", return_value=fresh):
                    open_job_tab(manager, "one", draft, 1, listing, True)
                self.assertEqual(manager.tabs["one"], "listing")
                result = restore_job_tab(manager, "one", fresh, 1)
                self.assertEqual(result["document_url"], form if destination == form else listing)
                self.assertEqual(result["active_tab_id"], manager.tabs["one"])
                self.assertFalse(manager.drafts)

    def test_restart_recognizes_only_the_selected_exact_saved_application(self):
        """Two postings on the same employer board cannot inherit one another's drafts."""
        listing = "https://jobs.lever.co/company/requisition"
        form = listing + "/apply"
        page = {"active_tab_id": "selected", "document_url": form, "tabs": [{"id": "selected"}]}
        for target, matches in ((listing, True), (listing + "-different", False)):
            manager = Mock(tabs={}, drafts={}, notice_forms={})
            manager.w.store.get.return_value = {"live_url": form, "attachments": [{"kind": "resume"}]}
            with patch("applydesk.native_navigation.open_public_tab", return_value={"active_tab_id": "listing"}):
                open_job_tab(manager, "job", page, 1, target, True)
            self.assertEqual(bool(manager.drafts), matches)

    def test_visible_workday_form_recovers_a_legacy_notice_address(self):
        """The active tab must match the original tenant and requisition and be free of other job owners."""
        listing = "https://company.wd5.myworkdayjobs.com/Careers/job/City/Engineer_12345"
        form = listing + "/apply/autofillWithResume"
        for url, owners, accepted in ((form, {}, True), (form, {"other": "selected"}, False),
                                      (form.replace("12345", "123456"), {}, False)):
            manager = Mock(owner=None, tabs=owners.copy(), notice_forms={}, drafts={}, jobs=[], created_tabs={})
            manager.w.store.get.return_value = {"live_url": "https://example.test/notice",
                                                "public_source": {"url": listing}}
            manager._wire.return_value = {"active_tab_id": "selected", "document_url": url}
            self.assertEqual(bool(resume_visible_draft(manager, "job", 1)), accepted)

    def test_restored_form_matches_visible_requisition_on_the_saved_account(self):
        """A portal form on a different host from the listing needs matching role, account and requisition."""
        import hashlib
        text = "Example Employer Sample Engineer Job Req ID: 54321"
        source = {"url": "https://jobs.example.test/engineer", "company": "Example Employer", "title": "Sample Engineer",
                  "description": text, "description_sha256": hashlib.sha256(text.encode()).hexdigest(), "complete": True,
                  "captured_at": "2026-11-22T00:00:00+00:00"}
        origin = "https://hiring.example.test"
        draft_title = "Career Opportunities: Apply for Sample Engineer (54321)"
        for saved, query, visible, accepted in (
            (origin, "career_job_req_id=54321", text, True),
            ("https://another.example.test", "career_job_req_id=54321", text, False),
            (origin, "career_job_req_id=54322", text, False),
            (origin, "career_job_req_id=54321", text.replace("Sample Engineer", "Other role"), False),
            (origin, "page=54321", text, False),
            (origin, "session=opaque", draft_title, True),
            (origin, "session=opaque", draft_title.replace("54321", "54322"), False),
            (origin, "career_job_req_id=54322", draft_title, False),
            (origin, "career_job_req_id=", draft_title, False),
        ):
            url = origin + "/application?" + query
            manager = Mock(owner=None, tabs={}, notice_forms={}, drafts={}, jobs=[], created_tabs={})
            manager.w.store.get.return_value = {"live_url": url, "account_origin": saved, "public_source": source,
                                                "attachments": [{"kind": "resume"}]}
            manager._wire.return_value = {"active_tab_id": "selected", "document_url": url,
                                          "title": visible, "text": "Example Employer"}
            self.assertEqual(bool(resume_visible_draft(manager, "job", 1)), accepted)

    def test_scrolled_draft_uses_only_its_active_tab_title(self):
        """An exact document title can restore the form while the employer header is offscreen."""
        import hashlib
        from applydesk.public_source import matches_saved_requisition_form, saved_form_tab
        text = "Example Employer Sample Engineer Job Req ID: 54321"
        source = {"url": "https://jobs.example.test/job", "company": "Example Employer", "title": "Sample Engineer",
                  "description": text, "description_sha256": hashlib.sha256(text.encode()).hexdigest(),
                  "complete": True, "captured_at": "2026-11-22T00:00:00+00:00"}
        job = {"public_source": source, "account_origin": "https://ats.example.test", "attachments": [{}],
               "live_url": "https://ats.example.test/portal?session=opaque"}
        draft = {"id": "draft", "label": "Apply for Sample Engineer (54321)"}
        page = {"document_url": job["live_url"], "active_tab_id": "other", "text": "Signature date",
                "tabs": [{"id": "other", "label": "Another job"}, draft]}
        self.assertFalse(matches_saved_requisition_form(job, page))
        self.assertEqual(saved_form_tab(job, page, []), "draft")
        self.assertIsNone(saved_form_tab(job, {**page, "tabs": [draft, {**draft, "id": "two"}]}, []))
        self.assertIsNone(saved_form_tab(job, page, ["draft"]))
        self.assertTrue(matches_saved_requisition_form(job, {**page, "active_tab_id": "draft"}))

    def test_interrupted_review_uses_the_saved_listing_only_for_its_live_populated_draft(self):
        """An aged capture needs a verified form which is still accepting applications."""
        from hashlib import sha256
        from applydesk.application_public_source import recently_screened_draft
        text = "Example Employer Sample Engineer Job Req ID: 54321"
        source = {"url": "https://jobs.example.test/job", "company": "Example Employer", "title": "Sample Engineer",
                  "description": text, "description_sha256": sha256(text.encode()).hexdigest(),
                  "complete": True, "captured_at": "2020-01-01T00:00:00+00:00"}
        origin = "https://ats.example.test"
        job = {"public_source": source, "account_origin": origin, "attachments": [{"kind": "resume"}],
               "live_url": origin + "/portal?session=saved"}
        form = {"document_url": job["live_url"], "url": job["live_url"], "title": "Apply for Sample Engineer (54321)",
                "text": "Candidate application documents and review. " * 4}
        app = Mock()
        app._session.return_value.manager.native = True
        for change, eligible in (({}, True), ({"native_modal": True}, False), ({"title": "Other job"}, False),
                                 ({"text": form["text"] + "Your session has expired."}, False),
                                 ({"text": form["text"] + "This job is closed."}, False)):
            with self.subTest(change=change):
                app._session.return_value.call.return_value = {**form, **change}
                self.assertEqual(bool(recently_screened_draft(app, job)), eligible)
