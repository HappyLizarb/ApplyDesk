"""Public web research copies fictional browser captures without provider or employer calls."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from applydesk.chat_history import ChatHistory
from applydesk.orchestrator_tools import MasterTools, master_tools
from applydesk.storage import Store
from applydesk.web_research import WebResearch
from recommendation_helpers import saved_listing

TEXT = """Site navigation
Example Press
Production Editor
About the Team
We publish illustrated books for libraries and coordinate production with authors and designers.
Responsibilities
• Check proofs, track editorial corrections, and coordinate production milestones with printers.
Required Qualifications
Experience with editorial calendars and coordinating production schedules is required.
Preferred Qualifications
Familiarity with print proofing and accessible document formats is helpful.
Compensation
$100,000 - $130,000 per year.
Why Join Us
Work with experienced editors and help prepare illustrated books for readers.
Site footer"""


class WebResearchTests(unittest.TestCase):
    """Use real local persistence and a fake browser with observable cleanup and navigation."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="applydesk-web-test-")
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.addCleanup(self.store.close)
        self.workspace = SimpleNamespace(store=self.store, lock=threading.RLock())
        self.history = ChatHistory(self.workspace)
        message = self.history.add("user", "Find editing roles on company career sites.", "running")
        self.engine = SimpleNamespace(w=self.workspace, history=self.history, active={"message": message},
                                      check_active=Mock(), run_agent=Mock(side_effect=AssertionError("No provider call")),
                                      accounts=SimpleNamespace(generation=1, state=Mock(return_value={"active": "test"})))
        self.page = {"title": "Production Editor — Example Press", "text": TEXT, "links": []}
        self.after_open, self.redirect = None, None
        self.browser = Mock()
        self.browser.call.side_effect = self.open_page
        self.factory = Mock(return_value=self.browser)
        self.web = WebResearch(self.engine, session_factory=self.factory)
        self.url = "https://careers.example.test/opportunity?id=editing-1"
        self.validation = patch("applydesk.web_research.public_job_url", side_effect=lambda url: url)
        self.validate = self.validation.start()
        self.addCleanup(self.validation.stop)

    def open_page(self, method, data):
        """Return a rendered page while allowing cancellation or redirect changes during loading."""
        self.assertEqual(method, "open")
        result = {**copy.deepcopy(self.page), "url": self.redirect or data["url"]}
        if self.after_open:
            self.after_open()
        return result

    def job_data(self, capture, **changes):
        """Select the whole observed description while excluding navigation and footer text."""
        return {"capture_id": capture["capture_id"], "company": "Example Press", "title": "Production Editor",
                "first_line": 4, "last_line": 15, **changes}

    def test_search_encodes_query_for_each_engine_without_provider_access(self):
        query = '"production editor" new grad -intern & full-time'
        hosts = {"google": "www.google.com", "bing": "www.bing.com", "duckduckgo": "duckduckgo.com"}
        for engine, host in hosts.items():
            with self.subTest(engine=engine):
                result = self.web.search(query, engine)
                opened = urlsplit(self.browser.call.call_args.args[1]["url"])
                self.assertEqual(opened.hostname, host)
                self.assertEqual(parse_qs(opened.query), {"q": [query]})
                self.assertEqual(result["query"], query)
                self.assertEqual(result["search_engine"], engine)
        self.assertEqual(self.browser.close.call_count, 3)
        self.engine.run_agent.assert_not_called()

    def test_arbitrary_public_page_returns_numbered_text_and_observed_links(self):
        self.page["links"] = [{"text": "Details", "href": "https://another.example.test/opaque/42"},
                              {"text": "Unsafe scheme", "href": "javascript:alert(1)"}]
        self.page["fields"] = [{"type": "password", "value": "fictional-secret"}]
        result = self.web.read_page("https://unusual.example.test/not-a-job-pattern?opening=42")
        self.assertEqual(result["line_count"], 16)
        self.assertIn("3: Production Editor", result["text"])
        self.assertEqual(result["links"], [{"text": "Details", "url": "https://another.example.test/opaque/42"}])
        self.assertNotIn("fictional-secret", json.dumps(result))
        self.factory.assert_called_once_with(self.engine, research=True)
        self.browser.close.assert_called_once()

    def test_save_copies_complete_description_and_parses_tracker_fields(self):
        capture = self.web.read_page(self.url)
        result = self.web.save_job(self.job_data(capture))
        job = self.store.get("job", result["job_id"])
        description = "\n".join(TEXT.splitlines()[3:15])
        self.assertEqual(result["status"], "added")
        self.assertEqual(job["details"], description)
        self.assertEqual(job["public_source"]["description"], description)
        self.assertEqual(job["public_source"]["description_sha256"], hashlib.sha256(description.encode()).hexdigest())
        self.assertTrue(job["public_source"]["complete"])
        self.assertEqual(job["qualification_status"], "unverified")
        self.assertEqual(job["detail_sections"]["required_qualifications"], TEXT.splitlines()[8])
        self.assertEqual(job["detail_sections"]["responsibilities"], TEXT.splitlines()[6].removeprefix("• "))
        self.assertIn("$100,000 - $130,000 per year.", job["compensation"])

    def test_jobs_are_saved_in_the_order_selected_by_the_agent(self):
        first = self.web.read_page(self.url)
        second = self.web.read_page("https://careers.example.test/opportunity?id=editing-2")
        selected = [self.web.save_job(self.job_data(capture))["job_id"] for capture in (second, first)]
        self.assertEqual([job["id"] for job in self.store.all("job")], selected)
        self.assertEqual([job["url"] for job in self.store.all("job")], [second["url"], first["url"]])

    def test_manual_url_gains_its_verified_capture_on_the_existing_row(self):
        """A user-saved public link becomes eligible for screening after a complete employer read."""
        original = self.store.add_job(self.url)
        result = self.web.save_job(self.job_data(self.web.read_page(self.url)))
        self.assertEqual(result["job_id"], original["id"])
        self.assertTrue(self.store.get("job", original["id"])["public_source"]["complete"])
        self.assertEqual(len(self.store.all("job")), 1)

    def test_invented_identity_or_invalid_description_span_does_not_add_job(self):
        capture = self.web.read_page(self.url)
        invalid = [{"company": "Imaginary Employer"}, {"title": "Imaginary Role"}, {"first_line": 0},
                   {"first_line": True}, {"last_line": 99}, {"first_line": 15, "last_line": 4},
                   {"first_line": 1, "last_line": 3}]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.web.save_job(self.job_data(capture, **changes))
        self.assertEqual(self.store.all("job"), [])

    def test_truncated_closed_or_challenge_page_cannot_be_saved(self):
        for changes in ({"text_truncated": True}, {"title": "Verify you are human"},
                        {"title": "This position is no longer available"}, {"text": "Too short"}):
            original = copy.deepcopy(self.page)
            self.page.update(changes)
            capture = self.web.read_page(self.url)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.web.save_job(self.job_data(capture))
            self.page = original
        self.assertEqual(self.store.all("job"), [])

    def test_canonical_duplicate_and_removed_rows_stay_unchanged(self):
        capture = self.web.read_page(self.url + "&utm_source=search#details")
        first = self.web.save_job(self.job_data(capture))
        duplicate = self.web.read_page(self.url)
        self.assertEqual(self.web.save_job(self.job_data(duplicate)), {"status": "existing", "job_id": first["job_id"]})
        self.store.remove_jobs([first["job_id"]])
        before = self.store.get("job", first["job_id"])
        self.assertEqual(self.web.save_job(self.job_data(duplicate)), {"status": "excluded", "job_id": first["job_id"]})
        self.assertEqual(self.store.get("job", first["job_id"]), before)
        self.assertEqual(len(self.store.all("job")), 1)

    def test_native_source_employer_link_is_not_duplicated_or_restored(self):
        native = self.store.add_job("https://app.joinhandshake.com/job-search/123")
        self.store.job(native["id"], recommendation_source={"employer_url": self.url + "&utm_source=handshake"})
        capture = self.web.read_page(self.url)
        self.assertEqual(self.web.save_job(self.job_data(capture)), {"status": "existing", "job_id": native["id"]})
        self.store.remove_jobs([native["id"]])
        self.assertEqual(self.web.save_job(self.job_data(capture)), {"status": "excluded", "job_id": native["id"]})
        self.assertEqual(len(self.store.all("job")), 1)

    def test_public_capture_url_also_participates_in_duplicate_detection(self):
        existing = self.store.add_job("https://careers.example.test/application/42")
        self.store.job(existing["id"], public_source={"url": self.url})
        capture = self.web.read_page(self.url)
        self.assertEqual(self.web.save_job(self.job_data(capture)), {"status": "existing", "job_id": existing["id"]})

    def test_agent_links_verified_alternate_to_pending_import_only(self):
        """Keep the tracker ID across public sources, and reject a wrong tenant, requisition or draft."""
        text = TEXT + "\nJob requisition JR000001"
        saved = saved_listing(company="Example Press", title="Production Editor", employer_url="", text=text)
        job = self.store.add_job(saved["source_url"])
        self.store.job(job["id"], recommendation_source=saved, status="needs_review")
        self.page["text"] = text
        target = "https://ep.wd5.myworkdayjobs.com/Careers/job/Example-City/Production-Editor_JR000001"
        tools = MasterTools(self.engine, Mock())
        tools.web = self.web
        for wrong in (target.replace("ep.wd5", "other.wd5"), target.replace("JR000001", "JR000002"), target + "/apply"):
            capture = self.web.read_page(wrong)
            with self.subTest(url=wrong), self.assertRaises(ValueError):
                self.web.save_job(self.job_data(capture, job_id=job["id"]))
        capture = self.web.read_page(target)
        command = {"name": "link_saved_listing", "arguments": json.dumps(self.job_data(capture, job_id=job["id"]))}
        self.assertEqual(tools.call(command), {"status": "linked", "job_id": job["id"]})
        current = self.store.get("job", job["id"])
        self.assertEqual(current["public_source"]["url"], target)
        self.assertEqual(current["recommendation_source"], saved)
        self.assertEqual(len(self.store.all("job")), 1)
        duplicate = self.store.add_job(target)
        with self.assertRaisesRegex(ValueError, "another saved application"):
            tools.call(command)
        self.store.remove_jobs([duplicate["id"]])
        self.store.job(job["id"], submission_receipt={"confirmed": True})
        with self.assertRaisesRegex(ValueError, "submission attempt"):
            tools.call(command)

    def test_capture_from_another_turn_chat_or_connection_cannot_be_saved(self):
        changes = [lambda: self.engine.active.update(message=self.history.add("user", "Use another role.", "running")),
                   self.history.clear, lambda: setattr(self.engine.accounts, "generation", 2),
                   lambda: self.engine.accounts.state.return_value.update(active="different")]
        for change in changes:
            capture = self.web.read_page(self.url)
            change()
            with self.assertRaises(ValueError):
                self.web.save_job(self.job_data(capture))
        self.assertEqual(self.store.all("job"), [])

    def test_chat_change_while_loading_closes_browser_and_discards_capture(self):
        self.after_open = self.history.clear
        with self.assertRaises(InterruptedError):
            self.web.read_page(self.url)
        self.browser.close.assert_called_once()
        self.assertEqual(self.web.captures, {})

    def test_cancellation_after_loading_closes_browser_and_discards_capture(self):
        self.after_open = lambda: setattr(self.engine.check_active, "side_effect", InterruptedError("Paused"))
        with self.assertRaises(InterruptedError):
            self.web.read_page(self.url)
        self.browser.close.assert_called_once()
        self.assertEqual(self.web.captures, {})

    def test_browser_error_still_closes_the_session(self):
        self.browser.call.side_effect = RuntimeError("Synthetic page failure")
        with self.assertRaisesRegex(RuntimeError, "Synthetic page failure"):
            self.web.read_page(self.url)
        self.browser.close.assert_called_once()
        self.assertEqual(self.web.captures, {})

    def test_initial_or_redirected_url_rejection_does_not_create_capture(self):
        self.validate.side_effect = ValueError("Synthetic public URL rejection")
        with self.assertRaises(ValueError):
            self.web.read_page(self.url)
        self.factory.assert_not_called()
        self.validate.side_effect = [self.url, ValueError("Synthetic redirect rejection")]
        with self.assertRaises(ValueError):
            self.web.read_page(self.url)
        self.browser.close.assert_called_once()
        self.assertEqual(self.web.captures, {})

    def test_background_draft_cannot_open_a_research_browser(self):
        self.engine.active = {"task_id": "local-draft"}
        with self.assertRaises(ValueError):
            self.web.read_page(self.url)
        self.factory.assert_not_called()

    def test_tool_schema_and_dispatch_expose_research_with_existing_workflows(self):
        definitions = {tool["name"]: tool for tool in master_tools()[0]["tools"]}
        self.assertTrue({"web_search", "read_page", "save_job", "find_jobs", "run_applications"} <= definitions.keys())
        for name in ("web_search", "read_page", "save_job"):
            schema = definitions[name]["parameters"]
            self.assertEqual(set(schema["required"]), set(schema["properties"]))
            self.assertFalse(schema["additionalProperties"])
        tools = MasterTools(self.engine, Mock())
        tools.web = self.web
        capture = tools.call({"name": "read_page", "arguments": json.dumps({"url": self.url})})
        result = tools.call({"name": "save_job", "arguments": json.dumps(self.job_data(capture))})
        self.assertEqual(result["status"], "added")
        with self.assertRaises(ValueError):
            tools.call({"name": "read_page", "arguments": json.dumps({"url": self.url, "script": "unexpected"})})


if __name__ == "__main__":
    unittest.main()
