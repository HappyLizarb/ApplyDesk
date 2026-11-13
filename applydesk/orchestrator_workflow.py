"""Enforce independent draft and review requests through the existing validated task API."""

import base64
import json
from pathlib import Path

from .agent_contract import object_fields, string, strings
from .chatgpt_inference import output_text
from .orchestrator_prompts import AUDITOR, CONTRACT, DRAFTER, REVIEWER


class DraftWorkflow:
    """The master can delegate work but cannot produce or impersonate reviewer results."""

    def __init__(self, engine):
        self.engine, self.w = engine, engine.w

    def context(self, task_id):
        """Check the stop boundary and current facts under the same lock used for writes."""
        with self.w.lock:
            self.engine.check_active()
            return self.w.chat.get(task_id)

    def prepare(self, task_id, instruction):
        """Run one draft and both required reviews; never automatically retry failed evidence."""
        string(instruction, 5000, empty=True)
        context = self.context(task_id)
        if context["task"]["status"] == "needs_input":
            raise ValueError("This task has a blocker. Ask the user to correct it and retry explicitly.")
        try:
            if context["next_action"] == "draft":
                context = self._draft(context, instruction)
            if context["task"]["status"] == "needs_input":
                return {"status": "needs_input", "issues": context["task"].get("issues", [])}
            roles = ["application_auditor"] if context["task"]["kind"] == "profile" else ["document_reviewer", "application_auditor"]
            for role in roles:
                report = context["task"]["reviews"].get(role, {})
                if not report.get("passed") or not report.get("identity_verified"):
                    self._review(task_id, role)
            final = self.context(task_id)
            return {"task_id": task_id, "status": final["task"]["status"], "draft_only": True,
                    "issues": [issue for review in final["task"]["reviews"].values() for issue in review["issues"]]}
        except InterruptedError:
            raise
        except Exception as exc:
            current = self.context(task_id)
            with self.w.lock:
                self.engine.check_active()
                self.w.chat.block({"task_id": task_id, "version": current["version"], "issues": [str(exc)[:1000]]})
            raise

    def _draft(self, context, instruction):
        """Only a fresh Application Agent sees the drafting contract and master's direction."""
        content = json.dumps({"context": context, "master_instruction": instruction, "contract": CONTRACT})
        images = self._source_images(context)
        if images:
            content = [{"type": "input_text", "text": content}, *images]
        inputs = [{"role": "user", "content": content}]
        identity, response = self.engine.run_agent("application", DRAFTER, inputs, context)
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

    def _review(self, task_id, role):
        """Fresh independent calls cannot receive each other's findings or master conversation."""
        context = self.context(task_id)
        material = {"inputs": context["inputs"], "review": context["review"], "task_kind": context["task"]["kind"]}
        content = [{"type": "input_text", "text": json.dumps(material)}]
        if role == "document_reviewer":
            content.extend(self._images(context))
        else:
            content.extend(self._source_images(context))
        identity, response = self.engine.run_agent(role, REVIEWER if role == "document_reviewer" else AUDITOR,
                                                    [{"role": "user", "content": content}], context)
        verdict = object_fields(json.loads(output_text(response)), ["passed", "issues", "checked_items"])
        with self.w.lock:
            self.engine.check_active()
            self.w.chat.review({"task_id": task_id, "version": context["version"], "agent_id": identity,
                                "role": role, **verdict}, execution=identity)

    def _images(self, context):
        """Send the exact current page bytes; excessive or missing previews block the review."""
        documents = context["review"]["documents"]
        template = context["inputs"].get("template")
        expected = context["review"]["expected_checks"]["document_reviewer"]
        if template and any(item.startswith("template:") for item in expected):
            documents = [*documents, template]
        content = self._page_images(documents, template["id"] if template else "")
        if expected and not content:
            raise ValueError("Document page previews are missing.")
        return content

    def _source_images(self, context):
        """Give the drafter and auditor scanned source pages when no readable text exists."""
        inputs = context["inputs"]
        documents = ([inputs["source"]] if inputs.get("source") else []) + inputs.get("resumes", [])
        scanned = [document for document in documents if not document.get("text", "").strip()
                   and not any(block.get("text", "").strip() for block in document.get("blocks", []))]
        if any(not document.get("image_paths") for document in scanned):
            raise ValueError("Source document page previews are missing.")
        return self._page_images(scanned)

    def _page_images(self, documents, template_id=""):
        """Read bounded page bytes identically for source reading and independent layout review."""
        content, size = [], 0
        for document in documents:
            for index, path in enumerate(document.get("image_paths", [])):
                raw = self.w.docs.asset(path).read_bytes()
                size += len(raw)
                if size > 20 * 1024 * 1024 or len(content) >= 80:
                    raise ValueError("Too many review pages for one request. Review the document manually.")
                label = "template" if document["id"] == template_id else document["id"]
                content.append({"type": "input_text", "text": f"{label}:page:{index + 1}"})
                mime = "image/jpeg" if Path(path).suffix.lower() in {".jpg", ".jpeg"} else "image/png"
                content.append({"type": "input_image", "image_url": f"data:{mime};base64," + base64.b64encode(raw).decode(), "detail": "high"})
        return content
