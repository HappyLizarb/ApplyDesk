"""Scanned upload pages reach the single agent without changing the uploaded resume."""

import base64
import copy
import json
from unittest.mock import patch

from chat_helpers import ChatFixture
from applydesk.orchestrator_prompts import DRAFTER, SELF_CHECK


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jZ1sAAAAASUVORK5CYII=")


class DraftSourceImageTests(ChatFixture):
    def setUp(self):
        """Use a real workspace and request builder with synthetic provider responses."""
        super().setUp()
        self.engine = self.w.orchestrator
        self.calls = []
        self.w.store.put("chatgpt", "accounts", {"active": "test", "host_id": "host", "accounts": [
            {"id": "test", "label": "Test", "connected": True, "client_id": "oaiapp_test", "subject": "subject"}]})
        self.w.store.put("chatgpt_models", "test", [{"id": "test-model", "name": "Test", "thinking": []}])
        self.w.docs.asset(self.resume["image_paths"][0]).write_bytes(PNG)

    def scanned_resume(self, **changes):
        """Remove extraction text while keeping the original upload and rendered page intact."""
        record = self.w.docs.get(self.resume["id"])
        record.update(text="", blocks=[], **changes)
        self.w.docs._save(record)
        return record

    def respond(self, payload, cancelled, emit):
        """Return grounded fixture outputs while recording the actual provider input payload."""
        self.calls.append(copy.deepcopy(payload))
        content = payload["input"][0]["content"]
        material = json.loads(content if isinstance(content, str) else content[0]["text"])
        self.assertEqual(payload["instructions"], SELF_CHECK + DRAFTER)
        result = {"profile": self.w.profile(), "notes": []} if material["context"]["task"]["kind"] == "profile" else self.payload()["result"]
        text = json.dumps({"result": result})
        return {"id": "response-" + str(len(self.calls)), "status": "completed", "output": [
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}]}

    def test_scanned_resume_reaches_the_single_agent_once(self):
        """The agent receives the page once, identified by source ID and page."""
        self.scanned_resume()
        original = self.w.docs.asset(self.resume["source_path"]).read_bytes()
        with patch.object(self.engine.runner, "_request", side_effect=self.respond):
            result = self.engine.workflow.prepare(self.task["id"], "Use the uploaded resume.")
        self.assertEqual(result["status"], "draft_ready")
        self.assertEqual(len(self.calls), 1)
        for prompt in (DRAFTER,):
            content = next(call for call in self.calls if call["instructions"] == SELF_CHECK + prompt)["input"][0]["content"]
            images = [part for part in content if part["type"] == "input_image"]
            self.assertEqual(len(images), 1)
            self.assertEqual(base64.b64decode(images[0]["image_url"].split(",", 1)[1]), PNG)
            self.assertIn({"type": "input_text", "text": "resume:page:1"}, content)
        self.assertEqual(self.w.docs.asset(self.resume["source_path"]).read_bytes(), original)

    def test_text_and_block_only_sources_do_not_send_content_images(self):
        """Text remains sufficient even when extracted blocks are the only readable source."""
        for text in ("Alex Test", ""):
            with self.subTest(text=text):
                record = self.w.docs.get(self.resume["id"])
                record["text"] = text
                self.w.docs._save(record)
                task = self.w.chat.enqueue({"kind": "application", "target_id": self.job["id"]})
                self.calls = []
                with patch.object(self.engine.runner, "_request", side_effect=self.respond):
                    self.engine.workflow.prepare(task["id"], "Read the text.")
                draft = next(call for call in self.calls if call["instructions"] == SELF_CHECK + DRAFTER)
                self.assertIsInstance(draft["input"][0]["content"], str)

    def test_profile_extraction_reads_scanned_uploaded_resume(self):
        """Profile drafts use the same scanned source path as application reviews."""
        self.scanned_resume()
        task = self.w.chat.enqueue({"kind": "profile", "target_id": self.resume["id"]})
        original = self.w.profile()
        with patch.object(self.engine.runner, "_request", side_effect=self.respond):
            result = self.engine.workflow.prepare(task["id"], "Read my uploaded resume.")
        self.assertEqual(result["status"], "draft_ready")
        self.assertEqual([call["instructions"] for call in self.calls], [SELF_CHECK + DRAFTER])
        for call in self.calls:
            image = next(part for part in call["input"][0]["content"] if part["type"] == "input_image")
            self.assertEqual(base64.b64decode(image["image_url"].split(",", 1)[1]), PNG)
        self.assertEqual(self.w.profile(), original)

    def test_missing_previews_block_before_any_model_request(self):
        """A file ID alone cannot silently pass through as readable resume evidence."""
        self.scanned_resume(image_paths=[])
        with patch.object(self.engine.runner, "_request", side_effect=self.respond):
            with self.assertRaisesRegex(ValueError, "previews are missing"):
                self.engine.workflow.prepare(self.task["id"], "Read the resume.")
        self.assertEqual(self.calls, [])

    def test_page_limit_blocks_before_any_model_request(self):
        """Scanned sources share the existing forty-page request limit."""
        self.scanned_resume(image_paths=self.resume["image_paths"] * 41)
        with patch.object(self.engine.runner, "_request", side_effect=self.respond):
            with self.assertRaisesRegex(ValueError, "Too many source pages"):
                self.engine.workflow.prepare(self.task["id"], "Read the resume.")
        self.assertEqual(self.calls, [])

    def test_byte_limit_blocks_before_any_model_request(self):
        """Large source previews share the existing twenty-megabyte request limit."""
        self.scanned_resume()
        self.w.docs.asset(self.resume["image_paths"][0]).write_bytes(PNG + b"x" * (20 * 1024 * 1024))
        with patch.object(self.engine.runner, "_request", side_effect=self.respond):
            with self.assertRaisesRegex(ValueError, "Too many source pages"):
                self.engine.workflow.prepare(self.task["id"], "Read the resume.")
        self.assertEqual(self.calls, [])
