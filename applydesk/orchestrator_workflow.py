"""Prepare drafts with the single agent and the existing validated task API."""

import base64
import json
from pathlib import Path

from .auto_start import usage_limited

from .agent_contract import object_fields, string, strings
from .chatgpt_inference import output_text
from .orchestrator_prompts import CONTRACT, DRAFTER


class DraftWorkflow:
    """One agent prepares the task; local validation runs before saving its draft."""

    def __init__(self, engine):
        self.engine, self.w = engine, engine.w

    def context(self, task_id):
        """Check the stop boundary and current facts under the same lock used for writes."""
        with self.w.lock:
            self.engine.check_active()
            return self.w.chat.get(task_id)

    def prepare(self, task_id, instruction):
        """Run one draft request with self-check instructions, without reviewer requests."""
        string(instruction, 5000, empty=True)
        context = self.context(task_id)
        if context["task"]["status"] == "needs_input":
            raise ValueError("This task has a blocker. Ask the user to correct it and retry explicitly.")
        try:
            if context["next_action"] == "draft":
                context = self._draft(context, instruction)
            if context["task"]["status"] == "needs_input":
                return {"status": "needs_input", "issues": context["task"].get("issues", [])}
            final = self.context(task_id)
            return {"task_id": task_id, "status": final["task"]["status"], "draft_only": True,
                    "issues": final["task"].get("issues", [])}
        except InterruptedError:
            raise
        except Exception as exc:
            if usage_limited(exc):
                raise
            with self.w.lock:
                self.engine.check_active()
                if self.w.chat.get(task_id)["version"] == context["version"]:
                    self.w.chat.block({"task_id": task_id, "version": context["version"], "issues": [str(exc)[:1000]]})
            raise

    def _draft(self, context, instruction):
        """Use the same agent settings for the task contract and source material."""
        material = {**context, "inputs": {**context["inputs"], "template": None}}
        content = json.dumps({"context": material, "instruction": instruction, "contract": CONTRACT})
        images = self._source_images(context)
        if images:
            content = [{"type": "input_text", "text": content}, *images]
        inputs = [{"role": "user", "content": content}]
        identity, response = self.engine.run_agent("master", DRAFTER, inputs, context)
        result = json.loads(output_text(response))
        if isinstance(result, dict) and set(result) == {"blocked"}:
            with self.w.lock:
                self.engine.check_active()
                self.w.chat.block({"task_id": context["task"]["id"], "version": context["version"], "issues": strings(result["blocked"])})
            return self.context(context["task"]["id"])
        object_fields(result, ["result"])
        with self.w.lock:
            self.engine.check_active()
            return self.w.chat.submit({"task_id": context["task"]["id"], "version": context["version"],
                                      "agent_id": identity, "result": result["result"]}, execution=identity)

    def _source_images(self, context):
        """Give the agent scanned source pages when no readable text exists."""
        inputs = context["inputs"]
        documents = ([inputs["source"]] if inputs.get("source") else []) + inputs.get("resumes", [])
        scanned = [document for document in documents if not document.get("text", "").strip()
                   and not any(block.get("text", "").strip() for block in document.get("blocks", []))]
        if any(not document.get("image_paths") for document in scanned):
            raise ValueError("Source document page previews are missing.")
        return self._page_images(scanned)

    def _page_images(self, documents):
        """Read bounded source page bytes without changing uploaded documents."""
        content, size = [], 0
        for document in documents:
            for index, path in enumerate(document.get("image_paths", [])):
                raw = self.w.docs.asset(path).read_bytes()
                size += len(raw)
                if size > 20 * 1024 * 1024 or len(content) >= 80:
                    raise ValueError("Too many source pages for one request. Read the document manually.")
                label = document["id"]
                content.append({"type": "input_text", "text": f"{label}:page:{index + 1}"})
                mime = "image/jpeg" if Path(path).suffix.lower() in {".jpg", ".jpeg"} else "image/png"
                content.append({"type": "input_image", "image_url": f"data:{mime};base64," + base64.b64encode(raw).decode(), "detail": "high"})
        return content
