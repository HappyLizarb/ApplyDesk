"""Workspace and HTTP boundaries exercised without real accounts or API keys."""

import base64
import copy
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlparse

from applydesk.document_tasks import DocumentTasks
from applydesk.profile import empty_profile, model_profile
from applydesk.server import AppServer
from applydesk.workspace import Workspace


class FakeSecrets:
    def __init__(self):
        # Tests never invoke the operating system Keychain.
        self.values = {}

    def get(self, name):
        # Match the production secret store's absent-key behavior.
        return self.values.get(name)

    def set(self, name, value):
        # Values are synthetic and remain in memory for each test.
        self.values[name] = value


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        # Each test gets an isolated database and deterministic fake secret storage.
        self.temporary = tempfile.TemporaryDirectory(prefix="applydesk-workspace-test-")
        self.secrets = FakeSecrets()
        self.workspace = Workspace(Path(self.temporary.name), secrets=self.secrets)

    def tearDown(self):
        # Close SQLite before deleting its private temporary directory.
        self.workspace.close()
        self.temporary.cleanup()

    def confirmed_profile(self):
        # The caller still uses the normal validation and confirmation path.
        profile = empty_profile()
        profile["identity"].update(legal_name="Alex Test", email="alex@example.test", country="US")
        profile.update(confirmed=True, skills=["Python"])
        return self.workspace.save_profile(profile)

    def test_duplicate_url_and_detached_records(self):
        # A repeated user link should reuse its row, and reads cannot mutate storage.
        with patch("applydesk.workspace.public_job_url", side_effect=lambda url: url):
            first = self.workspace.add_job({"url": "https://jobs.example.test/role"})
            second = self.workspace.add_job({"url": "https://jobs.example.test/role"})
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.workspace.store.all("job")), 1)
        first["status"] = "submitted"
        self.assertEqual(self.workspace.store.get("job", first["id"])["status"], "new")

    def test_only_confirmed_facts_are_canonical(self):
        # Client-supplied fact IDs cannot create evidence or bypass user confirmation.
        draft = empty_profile()
        draft["facts"] = [{"id": "invented", "text": "Ten professional years", "confirmed": True}]
        self.assertEqual(self.workspace.save_profile(draft)["facts"], [])
        confirmed = self.confirmed_profile()
        self.assertIn("skills.0", {fact["id"] for fact in confirmed["facts"]})
        self.assertNotIn("invented", {fact["id"] for fact in confirmed["facts"]})
        confirmed["preferences"].update(disability="yes", veteran="decline")
        confirmed = self.workspace.save_profile(confirmed)
        screened = model_profile(confirmed)
        self.assertNotIn("disability", screened["preferences"])
        self.assertFalse(any(fact["kind"] == "disclosure" for fact in screened["facts"]))

    def test_profile_changes_invalidate_ready_but_not_submitted(self):
        # Old eligibility and field audits cannot survive a changed source profile.
        profile = self.confirmed_profile()
        ready = self.workspace.store.add_job("https://jobs.example.test/ready")
        submitted = self.workspace.store.add_job("https://jobs.example.test/submitted")
        self.workspace.store.job(ready["id"], status="ready")
        self.workspace.store.job(submitted["id"], status="submitted")
        profile["identity"]["phone"] = "+1 415 555 0101"
        saved = self.workspace.save_profile(profile)
        self.assertEqual(saved["revision"], profile["revision"] + 1)
        self.assertEqual(self.workspace.store.get("job", ready["id"])["status"], "needs_review")
        self.assertEqual(self.workspace.store.get("job", submitted["id"])["status"], "submitted")

    def test_transcript_change_invalidates_attachment_and_revision(self):
        # Replacing an attachment requires a fresh audit without repeating renderer tests.
        profile = self.confirmed_profile()
        job = self.workspace.store.add_job("https://jobs.example.test/role")
        self.workspace.store.job(job["id"], status="ready")
        upload = {"filename": "transcript.pdf", "kind": "transcript", "data": base64.b64encode(b"synthetic").decode()}
        with patch.object(self.workspace.docs, "import_file", return_value={"id": "transcript-v2"}):
            self.workspace.import_file(upload)
        saved = self.workspace.profile()
        self.assertEqual(saved["transcript_id"], "transcript-v2")
        self.assertEqual(saved["revision"], profile["revision"] + 1)
        self.assertEqual(self.workspace.store.get("job", job["id"])["status"], "needs_review")

    def test_job_document_edit_copies_master_and_invalidates_only_job(self):
        # A per-job edit leaves the reusable resume and other applications untouched.
        job = self.workspace.store.add_job("https://jobs.example.test/role")
        other = self.workspace.store.add_job("https://jobs.example.test/other")
        for record in (job, other):
            self.workspace.store.job(record["id"], resume_id="master", status="ready")
        self.workspace.store.put("library", "engineering", {"document_id": "master"})
        master = {"id": "master", "job_id": None, "category": "Engineering"}
        edited = {"id": "edited", "job_id": job["id"], "document_type": "resume"}
        self.workspace.docs = Mock()
        self.workspace.docs.get.return_value = master
        self.workspace.docs.copy_for_job.return_value = {**master, "id": "job-copy", "job_id": job["id"]}
        self.workspace.docs.edit.return_value = edited
        tasks = DocumentTasks(self.workspace)
        with patch.object(tasks, "review", return_value={"passed": True}):
            tasks.save({"document_id": "master", "job_id": job["id"], "edits": {"block": "Changed"}})
        self.workspace.docs.copy_for_job.assert_called_once_with("master", job["id"])
        self.workspace.docs.edit.assert_called_once_with("job-copy", {"block": "Changed"})
        self.assertEqual(self.workspace.store.get("library", "engineering")["document_id"], "master")
        self.assertEqual(self.workspace.store.get("job", job["id"])["status"], "documents_changed")
        self.assertEqual(self.workspace.store.get("job", other["id"])["resume_id"], "master")

    def test_saved_version_survives_unavailable_review_provider(self):
        # A missing key leaves the saved edit available but explicitly unapproved.
        self.workspace.docs = Mock()
        self.workspace.docs.get.return_value = {"id": "previous", "category": "Engineering"}
        self.workspace.docs.edit.return_value = {"id": "saved-version", "category": "Engineering"}
        tasks = DocumentTasks(self.workspace)
        with patch.object(tasks, "review", side_effect=ValueError("Add a provider API key in Settings.")):
            result = tasks.save({"document_id": "previous", "edits": {"block": "Edited fact"}})
        self.assertEqual(result["document_id"], "saved-version")
        self.assertFalse(result["review"]["passed"])
        self.assertTrue(result["review"]["pending"])
        self.assertEqual(self.workspace.store.get("library", "engineering")["document_id"], "saved-version")
        self.assertTrue(self.workspace.store.get("document_review", "saved-version")["pending"])
        self.workspace.docs.edit.assert_called_once_with("previous", {"block": "Edited fact"})

    def test_cached_review_is_rebound_to_current_document_version(self):
        # Byte-identical copies may reuse review work but must name the current version.
        self.workspace.docs = Mock()
        original = {"id": "master", "template_id": "template", "sha256": "pdf-hash",
                    "docx_sha256": "docx-hash", "image_paths": ["preview.png"]}
        self.workspace.docs.get.return_value = original
        self.workspace.docs.get_template.return_value = {"source_sha256": "source-hash", "image_paths": ["template.png"]}
        tasks = DocumentTasks(self.workspace)
        with patch.object(self.workspace, "client", return_value=object()), patch.object(tasks, "_images", return_value=[]):
            with patch("applydesk.intelligence.review_format", return_value={"passed": True, "issues": []}) as formatting:
                with patch("applydesk.intelligence.audit_document", return_value={"passed": True, "issues": []}) as factual:
                    self.assertEqual(tasks.review("master")["document_id"], "master")
                    self.workspace.docs.get.return_value = {**original, "id": "job-copy"}
                    result = tasks.review("job-copy")
                    formatting.assert_called_once()
                    factual.assert_called_once()
        self.assertEqual(result["document_id"], "job-copy")
        self.assertTrue(result["passed"])
        self.assertEqual(self.workspace.store.get("document_review", "job-copy")["document_id"], "job-copy")


class HttpBoundaryTests(unittest.TestCase):
    def setUp(self):
        # A real ephemeral loopback server validates HTTP behavior without provider calls.
        self.temporary = tempfile.TemporaryDirectory(prefix="applydesk-http-test-")
        self.secrets = FakeSecrets()
        self.workspace = Workspace(Path(self.temporary.name), secrets=self.secrets)
        self.server = AppServer(self.workspace)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        # Wait for the listener to close before releasing its database.
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.workspace.close()
        self.temporary.cleanup()

    def request(self, method, route, data=None, authenticated=True, headers=None, raw=None):
        # Request defaults model the local browser, with explicit overrides for attacks.
        request_headers = {"Cookie": "applydesk=" + self.server.token} if authenticated else {}
        body = raw if raw is not None else json.dumps(data) if data is not None else None
        if method == "POST":
            request_headers.update({"Origin": self.server.origin, "X-ApplyDesk": "1", "Content-Type": "application/json"})
        request_headers.update(headers or {})
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, route, body=body, headers=request_headers)
            response = connection.getresponse()
            content = response.read()
            return response.status, dict(response.getheaders()), content
        finally:
            connection.close()

    def test_launcher_token_and_private_state_gate(self):
        # A session begins only through the capability-bearing launcher URL.
        self.assertEqual(self.request("GET", "/api/state", authenticated=False)[0], 403)
        route = urlparse(self.server.launch_url)
        status, headers, _ = self.request("GET", route.path + "?" + route.query, authenticated=False)
        self.assertEqual(status, 303)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.assertEqual(self.request("GET", "/api/state")[0], 200)
        self.assertEqual(self.request("GET", "/start?token=wrong", authenticated=False)[0], 403)

    def test_resume_generation_route_is_removed_and_tags_require_authentication(self):
        # Hiding generation controls must also remove the provider-calling route.
        self.assertEqual(self.request("POST", "/api/document/generate", {})[0], 400)
        data = {"document_id": "missing", "category": "General"}
        self.assertEqual(self.request("POST", "/api/resume/tag", data, authenticated=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/resume/tag", data)[0], 400)

    def test_removal_route_validates_session_and_selection(self):
        # The same local authorization boundary protects bulk removal.
        job = self.workspace.store.add_job("https://example.test/jobs/1")
        data = {"ids": [job["id"]]}
        self.assertEqual(self.request("POST", "/api/jobs/remove", data, authenticated=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/jobs/remove", {"ids": []})[0], 400)
        status, _, body = self.request("POST", "/api/jobs/remove", data)
        self.assertEqual((status, json.loads(body)), (200, {"removed": 1}))
        self.assertEqual(self.workspace.state()["jobs"], [])

    def test_wrong_host_is_rejected_including_bootstrap(self):
        # The launch route must enforce the same Host boundary as authenticated routes.
        bad_host = {"Host": "attacker.example"}
        self.assertEqual(self.request("GET", "/api/state", headers=bad_host)[0], 403)
        route = urlparse(self.server.launch_url)
        self.assertEqual(self.request("GET", route.path + "?" + route.query, headers=bad_host)[0], 403)

    def test_wrong_origin_and_asset_traversal_do_not_write(self):
        # Cookie possession alone does not authorize a cross-origin mutation.
        original = self.workspace.profile()
        status, _, _ = self.request("POST", "/api/profile", {"confirmed": True}, headers={"Origin": "https://attacker.example"})
        self.assertEqual(status, 403)
        self.assertEqual(self.workspace.profile(), original)
        self.assertEqual(self.request("GET", "/asset/%2e%2e/workspace.sqlite")[0], 400)
        self.assertEqual(self.request("GET", "/%2e%2e/applydesk/workspace.py")[0], 404)

    def test_malformed_profile_and_json_leave_state_intact(self):
        # Invalid object shapes are user errors, not partial profile replacements.
        original = self.workspace.profile()
        for body in ("{", "[]", '{"identity":null}', '{"skills":[{}]}'):
            with self.subTest(body=body):
                self.assertEqual(self.request("POST", "/api/profile", raw=body)[0], 400)
                self.assertEqual(self.workspace.profile(), original)

    def test_invalid_settings_do_not_partially_replace_keys(self):
        # Validate a settings request before any credential mutation occurs.
        self.secrets.set("api:openai", "synthetic-original")
        original = copy.deepcopy(self.workspace.settings())
        data = {**original, "api_keys": {"openai": "synthetic-new", "unknown": "bad"}}
        self.assertEqual(self.request("POST", "/api/settings", data)[0], 400)
        self.assertEqual(self.secrets.get("api:openai"), "synthetic-original")
        self.assertEqual(self.workspace.settings(), original)

    def test_account_password_is_shared_by_employer_and_bound_to_signin_origin(self):
        # Different roles reuse one saved employer account without exposing its password.
        job = self.workspace.store.add_job("https://jobs.example.test/role")
        self.workspace.store.job(job["id"], company="Example Company")
        data = {"id": job["id"], "account": "alex@example.test", "account_origin": "https://careers.example.test", "password": "synthetic-password"}
        self.assertEqual(self.request("POST", "/api/job/account", data)[0], 200)
        account = self.workspace.store.all("employer_account")[0]
        self.assertEqual(self.secrets.get(account["secret_key"]), "synthetic-password")
        second = self.workspace.store.add_job("https://jobs.example.test/another-role")
        self.workspace.store.job(second["id"], company="EXAMPLE COMPANY")
        shown = next(row for row in self.workspace.state()["jobs"] if row["id"] == second["id"])
        self.assertEqual((shown["account"], shown["employer_account_id"]), (data["account"], account["id"]))
        conflict = {**data, "id": second["id"], "account": "different@example.test"}
        self.assertEqual(self.request("POST", "/api/job/account", conflict)[0], 400)
        self.assertEqual(len(self.workspace.store.all("employer_account")), 1)
        changed = {**data, "account_origin": "https://different.example.test", "password": ""}
        self.assertEqual(self.request("POST", "/api/job/account", changed)[0], 400)
        self.assertEqual(self.workspace.store.get("job", job["id"])["account_origin"], data["account_origin"])
        self.assertEqual(self.request("POST", "/api/job/account", {**data, "id": "unknown"})[0], 400)
        self.assertEqual(self.request("POST", "/api/job/account", {**data, "account_origin": "https://careers.example.test/login"})[0], 400)
        self.assertNotIn("synthetic-password", self.request("GET", "/api/state")[2].decode())

    def test_chat_reset_requires_the_private_ui_session(self):
        """Cross-site or unauthenticated requests cannot erase the conversation."""
        self.workspace.orchestrator.history.add('user', 'Synthetic conversation')
        self.assertEqual(self.request('POST', '/api/orchestrator/reset', {}, authenticated=False)[0], 403)
        self.assertEqual(self.request('POST', '/api/orchestrator/reset', {}, headers={'Origin': 'https://example.test'})[0], 403)
        self.assertEqual(len(self.workspace.orchestrator.history.read()), 1)
        self.assertEqual(self.request('POST', '/api/orchestrator/reset', {})[0], 200)
        self.assertEqual(self.workspace.orchestrator.history.read(), [])

    def test_quit_is_available_during_background_work(self):
        """The Quit action cancels inference and shuts down even while another task is running."""
        self.workspace.tasks['synthetic'] = {'status': 'running'}
        status, _, body = self.request('POST', '/api/shutdown', {})
        self.assertEqual((status, json.loads(body)), (200, {'closed': True}))
        self.assertTrue(self.workspace.orchestrator.stop.is_set())
        self.assertTrue(self.workspace.orchestrator.cancelled.is_set())


if __name__ == "__main__":
    unittest.main()
