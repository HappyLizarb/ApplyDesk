"""Reuse the connected ChatGPT account for the existing evidence and review pipeline."""

import json

from .agent_context import digest
from .chatgpt_inference import output_text

ROLES = {"application": "application", "formatting": "document_reviewer", "auditor": "application_auditor"}


class ApplicationClient:
    """One fresh, independently recorded request per application or reviewer operation."""

    def __init__(self, engine, role, job_id):
        self.engine, self.role, self.job_id = engine, ROLES[role], job_id

    def ask(self, prompt, data, images=None):
        """Only validated JSON leaves this adapter; no API keys or arbitrary tools are exposed."""
        content = [{"type": "input_text", "text": json.dumps(data)}]
        for image in images or []:
            content.append({"type": "input_image", "image_url": "data:" + image["mime_type"] + ";base64," + image["data"], "detail": "high"})
        if len(content) > 81 or sum(len(item.get("image_url", "")) for item in content) > 28 * 1024 * 1024:
            raise ValueError("Too many document pages for an independent review.")
        context = {"task": {"id": "application:" + self.job_id}, "version": digest(data)}
        _, response = self.engine.run_agent(self.role, prompt, [{"role": "user", "content": content}], context)
        result = json.loads(output_text(response))
        if not isinstance(result, dict):
            raise ValueError("The application agent did not return a complete JSON object.")
        return result
