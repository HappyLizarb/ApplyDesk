"""Only the already-open account for the selected job can replace its screened public listing."""

from hashlib import sha256
import unittest
from unittest.mock import Mock, patch

from applydesk.account_scope import employer_key
from applydesk.native_tab_workspace import resume_account_tab
from applydesk.public_source import reopened_account_draft


class AccountTabTests(unittest.TestCase):
    def test_restored_draft_survives_the_account_session_change_only_on_the_same_requisition(self):
        """The recruiter portal URL alone cannot transfer an upload to a different applicant or role."""
        text = "Example Manufacturing Engineer Job Req ID: 54321"
        source = {"url": "https://example.test/jobs/42", "description": text, "company": "Example",
                  "title": "Manufacturing Engineer", "description_sha256": sha256(text.encode()).hexdigest(),
                  "captured_at": "2026-11-22T00:00:00Z", "complete": True}
        job = {"id": "42", "employer_account_id": "account", "account_origin": "https://portal.test",
               "resume_id": "resume", "attachments": [{"kind": "resume"}], "public_source": source, "live_url": source["url"]}
        stored = {**job, "live_url": "https://portal.test/candidate?session=second"}
        page = {"document_url": stored["live_url"], "title": "Apply for Manufacturing Engineer (54321)"}
        self.assertEqual(reopened_account_draft(job, stored, page), stored)
        for change in ({"title": "Apply for Manufacturing Engineer (54322)"}, {"native_modal": True},
                       {"document_url": job["live_url"]}):
            self.assertIsNone(reopened_account_draft(job, stored, {**page, **change}))
        self.assertIsNone(reopened_account_draft(job, {**stored, "employer_account_id": "other"}, page))

    def test_scrolled_portal_form_binds_explicit_query_to_the_current_tab_requisition(self):
        """The account form keeps its identity after scrolling down into the application."""
        text = "Example Manufacturing Engineer Job Req ID: 54321"
        source = {"url": "https://example.test/job", "description": text, "company": "Example",
                  "title": "Manufacturing Engineer", "description_sha256": sha256(text.encode()).hexdigest(),
                  "captured_at": "2026-11-22T00:00:00Z", "complete": True}
        job = {"id": "job", "employer_account_id": "account", "account_origin": "https://portal.test",
               "resume_id": "upload", "attachments": [{"kind": "resume"}], "public_source": source}
        tab = {"id": "active", "label": "Applied to Manufacturing Engineer (54321)"}
        for query, accepted in (("54321", True), ("54320", False), ("54321&req_id=54321", False), ("", False)):
            saved = {**job, "live_url": "https://portal.test/app?career_job_req_id=" + query}
            page = {"document_url": saved["live_url"], "active_tab_id": "active", "tabs": [tab],
                    "title": "Chrome", "text": "Preferred First Name"}
            with self.subTest(query=query):
                self.assertEqual(bool(reopened_account_draft(job, saved, page)), accepted)

    def test_reused_signin_requires_the_matching_tenant_and_visible_login_form(self):
        """Similar tab titles never establish account authority on a shared recruiting site."""
        listing, origin = "https://example.test/jobs/42", "https://accounts.example.test"
        realm = origin + "/careers?tenant=example"
        job = {"id": "job", "company": "Example", "public_source": {"url": listing}, "attachments": [{}], "account_origin": origin}
        job["employer_account_id"] = employer_key(job)
        saved = {"id": employer_key(job), "owner_job_id": "job", "origin": origin, "realm": realm, "has_password": True}
        tabs = [{"id": "post", "label": "Engineer"}, {"id": "account", "label": "Careers: Sign In"}]
        post = {"active_tab_id": "post", "document_url": listing, "tabs": tabs}
        controls = [{"role": "AXButton", "label": "Sign In", "enabled": True, "web_content": True},
                    {"role": "AXTextField", "enabled": True, "web_content": True}]
        for url, inputs, accept in ((realm, controls, True), (realm + "-other", controls, False), (realm, [], False)):
            manager = Mock(tabs={"job": "post"})
            manager.w.store.get.side_effect = [job, saved]
            manager._wire.return_value = post
            signin = {"active_tab_id": "account", "document_url": url, "controls": inputs}
            with patch("applydesk.native_tab_workspace.select_tab", side_effect=[signin, post]), self.subTest(url=url):
                self.assertEqual(bool(resume_account_tab(manager, "job", 1)), accept)
            self.assertEqual(manager.tabs["job"], "account" if accept else "post")
