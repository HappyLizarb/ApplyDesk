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

from applydesk.documents import DocumentTasks
from applydesk.profile import empty_profile, model_profile
from applydesk.server import AppServer
from applydesk.workspace import Workspace
from test_employer_accounts import MemorySecrets


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        # Each test gets an isolated database and deterministic fake secret storage.
        self.temporary = tempfile.TemporaryDirectory(prefix="applydesk-workspace-test-")
        self.secrets = MemorySecrets()
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
        jobs = {}
        for status in ("new", "ready", "submitted", "submitting", "submission_unknown"):
            job = self.workspace.store.add_job(f"https://jobs.example.test/{status}")
            self.workspace.store.job(job["id"], status=status)
            jobs[status] = job["id"]
        profile["identity"]["phone"] = "+1 415 555 0101"
        saved = self.workspace.save_profile(profile)
        self.assertEqual(saved["revision"], profile["revision"] + 1)
        for status, job_id in jobs.items():
            expected = "needs_review" if status == "ready" else status
            with self.subTest(status=status):
                self.assertEqual(self.workspace.store.get("job", job_id)["status"], expected)

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

    def test_template_upload_rejection_precedes_decoding_or_file_changes(self):
        """Disabled uploads cannot reach the renderer or mutate saved settings."""
        prior = self.workspace.settings()
        with patch.object(self.workspace.docs, "import_file") as importer:
            for kind in ("resume_template", "cover_template"):
                with self.assertRaisesRegex(ValueError, "Template uploads"):
                    self.workspace.import_file({"kind": kind, "data": "invalid base64"})
        importer.assert_not_called()
        self.assertEqual(self.workspace.settings(), prior)

    def test_local_integrity_checks_name_the_current_document_version(self):
        """Byte-identical copies keep their own result and never reuse model audit claims."""
        self.workspace.docs = Mock()
        original = {"id": "master", "image_paths": ["preview.png"]}
        self.workspace.docs.get.return_value = original
        self.workspace.docs.asset.return_value.read_bytes.return_value = b"preview bytes"
        tasks = DocumentTasks(self.workspace)
        with patch.object(self.workspace, "client", side_effect=AssertionError("Unexpected model call")):
            self.assertEqual(tasks.review("master")["document_id"], "master")
            self.workspace.docs.get.return_value = {**original, "id": "job-copy"}
            result = tasks.review("job-copy")
        self.assertEqual(result["document_id"], "job-copy")
        self.assertTrue(result["passed"])
        self.assertEqual(self.workspace.docs.verify.call_count, 2)
        self.assertEqual(self.workspace.store.get("document_review", "job-copy")["method"], "file_integrity")


class HttpBoundaryTests(unittest.TestCase):
    def setUp(self):
        # A real ephemeral loopback server validates HTTP behavior without provider calls.
        self.temporary = tempfile.TemporaryDirectory(prefix="applydesk-http-test-")
        self.secrets = MemorySecrets()
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

    def test_instructions_require_the_chat_session(self):
        """A retired bridge credential cannot read tasks or send instructions."""
        self.assertFalse((self.workspace.root / "agent.connection.json").exists())
        headers = {"Authorization": "Bearer retired-draft-token"}
        for path in ("/agent/v1/enqueue", "/api/orchestrator/send"):
            self.assertEqual(self.request("POST", path, {"message": "Test"}, False, headers)[0], 403)
        retired = ("/api/chat/enqueue", "/api/orchestrator/retry", "/agent/v1/draft", "/api/models/refresh",
                   "/api/jobs/discover", "/api/recommendations/begin", "/api/recommendations/cancel",
                   "/api/recommendations/preference")
        for path in retired:
            self.assertEqual(self.request("POST", path, {})[0], 400)
        self.assertEqual(self.request("GET", "/agent/v1/tasks")[0], 404)
        self.assertEqual(self.workspace.tasks, {})

    def test_timer_only_resumes_existing_chat_work(self):
        """The authenticated timer cannot carry a separate instruction."""
        path = "/api/orchestrator/auto-start"
        data = {"enabled": True, "interval": 5, "unit": "hours"}
        self.assertEqual(self.request("POST", path, data, authenticated=False)[0], 403)
        self.assertEqual(self.request("POST", path, data, headers={"Origin": "https://elsewhere.test"})[0], 403)
        self.assertEqual(self.request("POST", path, {**data, "message": "Apply now"})[0], 400)
        status, _, body = self.request("POST", path, data)
        self.assertEqual(status, 200)
        saved = json.loads(body)
        self.assertEqual({key: saved[key] for key in data}, data)
        self.assertIsNotNone(saved["next_run_at"])
        self.assertFalse(saved["pending"])
        self.assertIsNone(self.workspace.orchestrator.active)

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

    def test_resume_actions_require_authenticated_explicit_file_selection(self):
        # Hiding generation controls must also remove the provider-calling route.
        self.assertEqual(self.request("POST", "/api/document/generate", {})[0], 400)
        data = {"document_id": "missing", "category": "General"}
        self.assertEqual(self.request("POST", "/api/resume/tag", data, authenticated=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/resume/tag", data)[0], 400)

        with patch("applydesk.native_browser.choose_resume") as chooser:
            self.assertEqual(self.request("POST", "/api/choose-resume", {}, authenticated=False)[0], 403)
            self.assertEqual(self.request("POST", "/api/choose-resume", {"path": "/example/file.pdf"})[0], 400)
            chooser.assert_not_called()

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
        bad_headers = ({"Cookie": ""}, {"Cookie": "applydesk=incorrect"}, {"Origin": "https://attacker.example"},
                       {"Origin": self.server.origin + "/"}, {"Host": f"localhost:{self.server.server_port}"},
                       {"Host": "attacker.example"}, {"X-ApplyDesk": ""})
        for headers in bad_headers:
            with self.subTest(headers=headers):
                self.assertEqual(self.request("POST", "/api/profile", original, headers=headers)[0], 403)
        self.assertEqual(self.workspace.profile(), original)
        self.assertEqual(self.request("GET", "/asset/%2e%2e/workspace.sqlite")[0], 400)
        self.assertEqual(self.request("GET", "/%2e%2e/applydesk/workspace.py")[0], 404)

    def test_malformed_profile_and_json_leave_state_intact(self):
        # Invalid object shapes are user errors, not partial profile replacements.
        original = self.workspace.profile()
        for body in ("{", "[]", "null", '{"identity":null}', '{"skills":[{}]}'):
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

    def test_background_work_blocks_profile_edits_and_allows_quit(self):
        """An active task protects workspace data while the user can still quit."""
        self.workspace.tasks['synthetic'] = {'status': 'running'}
        original = self.workspace.profile()
        status, _, body = self.request('POST', '/api/profile', {**original, 'skills': ['Python']})
        self.assertEqual((status, json.loads(body)['error']), (400, 'A task is running. Wait before changing the workspace.'))
        self.assertEqual(self.workspace.profile(), original)
        status, _, body = self.request('POST', '/api/shutdown', {})
        self.assertEqual((status, json.loads(body)), (200, {'closed': True}))
        self.assertTrue(self.workspace.orchestrator.stop.is_set())
        self.assertTrue(self.workspace.orchestrator.cancelled.is_set())


if __name__ == "__main__":
    unittest.main()
