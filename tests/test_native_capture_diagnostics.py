"""Capture failure evidence stays useful without retaining page text, labels or field values."""

import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from applydesk.native_page import HARD_FLAGS, capture_page, incomplete_capture_message
from applydesk.public_source import validate_public_page
from applydesk.web_research import WebResearch
from test_native_research import ViewportSession, viewport


class NativeCaptureDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        """All viewport data is fictional; these checks never invoke Chrome, network or a model."""
        patch("applydesk.native_page.time.sleep").start()
        patch("applydesk.web_research.public_job_url", side_effect=lambda url: url).start()
        self.addCleanup(patch.stopall)
        self.partial = {"id": "n42", "role": "AXStaticText", "web_content": True,
            "text_incomplete": True, "clipped": True, "label": "private-label", "value": "private-value",
            "bounds": {"x": 12, "y": 160, "width": 350, "height": 40, "unexpected": "private-bounds"}}

    def test_unreadable_geometry_and_bottom_reason_exclude_page_content(self):
        """Reaching the bottom retains the actual unresolved control without leaking its partial contents."""
        pages = [viewport("private-page", elements=[self.partial], text_truncated=True), viewport("Footer")]
        result = capture_page(ViewportSession(pages), pages[0])
        diagnostics = result["capture_diagnostics"]
        self.assertTrue(result["text_truncated"])
        self.assertEqual(diagnostics["stop_reason"], "bottom_reached")
        self.assertEqual(diagnostics["hard_flags"], [])
        self.assertEqual(diagnostics["unreadable_controls"], [{"id": "n42", "role": "AXStaticText",
            "bounds": {"x": 12, "y": 160, "width": 350, "height": 40}, "clipped": True, "viewport": 1}])
        self.assertNotIn("private-", json.dumps(diagnostics))

    def test_recovered_control_disappears_but_earlier_hard_flags_remain(self):
        """Diagnostics follow existing readability rules instead of silently relaxing completeness."""
        whole = {**self.partial, "text_incomplete": False, "clipped": False, "value": "Complete paragraph"}
        pages = [viewport("Top", elements=[self.partial], text_truncated=True),
                 viewport("Complete paragraph", elements=[whole]), viewport("Footer")]
        complete = capture_page(ViewportSession(pages), pages[0])
        self.assertFalse(complete["text_truncated"])
        self.assertEqual(complete["capture_diagnostics"]["unreadable_controls"], [])
        for flag in HARD_FLAGS:
            with self.subTest(flag=flag):
                first = {**pages[0], flag: True}
                result = capture_page(ViewportSession([first, *pages[1:]]), first)
                self.assertTrue(result["text_truncated"])
                self.assertEqual(result["capture_diagnostics"]["hard_flags"], [flag])
                self.assertEqual(result["capture_diagnostics"]["unreadable_controls"], [])

    def test_limit_and_unattributed_truncation_reasons_are_distinct(self):
        """A bounded scan, text cap and missing native evidence remain distinguishable after failure."""
        cases = [(viewport("Top"), 1, "viewport_limit", []),
                 (viewport("x" * 60001), 32, "text_limit", []),
                 (viewport("Incomplete", text_truncated=True), 32, "bottom_reached", ["text_truncated_without_control"])]
        for page, limit, reason, flags in cases:
            with self.subTest(reason=reason):
                result = capture_page(ViewportSession([page]), page, limit=limit)
                self.assertTrue(result["text_truncated"])
                self.assertEqual(result["capture_diagnostics"]["stop_reason"], reason)
                self.assertEqual(result["capture_diagnostics"]["hard_flags"], flags)
                self.assertGreater(result["capture_diagnostics"]["viewports"], 0)

    def test_public_research_and_preparation_preserve_failure_metadata(self):
        """The caller receives the same diagnostic reason instead of losing it behind a generic pause."""
        text = "Example Co Engineer. " + "Visible public role requirements. " * 8
        page = viewport(text, title="Example Co Engineer", elements=[self.partial], text_truncated=True)
        result = capture_page(ViewportSession([page]), page)
        web = WebResearch(SimpleNamespace(w=Mock()))
        owner = ("current-turn",)
        web._binding = Mock(return_value=owner)
        capture = web._capture(result, owner)
        self.assertEqual(capture["capture_diagnostics"], result["capture_diagnostics"])
        stored = web.captures[capture["capture_id"]]
        self.assertEqual(stored["capture_diagnostics"], result["capture_diagnostics"])
        with self.assertRaisesRegex(ValueError, "Some visible text could not be read") as saving:
            web.save_job({"capture_id": capture["capture_id"]})
        source = {"url": page["url"], "company": "Example Co", "title": "Engineer", "description": text,
            "description_sha256": hashlib.sha256(text.encode()).hexdigest(), "captured_at": "2026-11-17", "complete": True}
        with self.assertRaisesRegex(ValueError, "Read the complete job description") as preparing:
            validate_public_page(source, result)
        for error in (saving.exception, preparing.exception):
            self.assertIn("Some visible text could not be read", str(error))
            self.assertNotIn("AXStaticText", str(error))
            self.assertNotIn("n42", str(error))
            self.assertNotIn('"x":12', str(error))
            self.assertNotIn("private-", str(error))
            self.assertNotIn(text, str(error))

    def test_product_message_explains_change_or_limit_without_dumping_diagnostics(self):
        """User-visible history remains concise while detailed capture evidence stays structured."""
        cases = [("bottom_reached", ["render_unsettled"], "The page was still changing while being read."),
                 ("viewport_limit", [], "The page could not be read completely within this scan."),
                 ("text_limit", [], "The page could not be read completely within this scan.")]
        for stop, flags, reason in cases:
            with self.subTest(stop=stop):
                page = {"capture_diagnostics": {"stop_reason": stop, "hard_flags": flags,
                    "viewports": 7, "unreadable_controls": [self.partial]}}
                self.assertEqual(incomplete_capture_message(page, "Read this job again."), "Read this job again. " + reason)
