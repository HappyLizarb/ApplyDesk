"""Use the single configured agent for sequential application steps."""

import json

from .agent_context import digest
from .chatgpt_inference import output_text

STEP_BOUNDARY = """Return the JSON requested for this step. Use the user's request for preferences
and confirmations. ApplyDesk controls authorized jobs, browser actions and submission counts.
Work only on the assigned job. ApplyDesk handles the order and outcomes of other jobs in the request.
Assess eligibility from the job requirements and applicant facts.

When filling forms, use standard defaults for routine choices that the applicant left open.
Use the confirmed given name for a preferred first name when no separate preference is saved.
If a contact method is required and no preference is saved, choose email when offered and on file.
Choose equivalent wording from a menu, format an existing value for the form, or calculate a
duration from applicable, non-overlapping saved dates. Cite the underlying facts for these answers.
Use only applicant information supplied by ApplyDesk. An omitted fact does not establish a negative
answer. Ask for a required personal fact only when it cannot be derived from the supplied information.

"""

class ApplicationClient:
    """Task-specific JSON requests share the chat agent's model and thinking setting."""

    def __init__(self, engine, role, job_id):
        self.engine, self.job_id = engine, job_id

    def ask(self, prompt, data, images=None):
        """Only validated JSON leaves this adapter; no API keys or arbitrary tools are exposed."""
        message = (self.engine.active or {}).get("message", {})
        if message.get("role") == "user":
            data = {**data, "current_user_request": message["text"]}
        content = [{"type": "input_text", "text": json.dumps(data)}]
        for image in images or []:
            content.append({"type": "input_image", "image_url": "data:" + image["mime_type"] + ";base64," + image["data"], "detail": "high"})
        if len(content) > 81 or sum(len(item.get("image_url", "")) for item in content) > 28 * 1024 * 1024:
            raise ValueError("Too many document pages for one request.")
        context = {"task": {"id": "application:" + self.job_id}, "version": digest(data)}
        return self._result(prompt, [{"role": "user", "content": content}], context)

    def _result(self, prompt, inputs, context):
        """Let the same agent repair malformed JSON once before reporting a protocol failure."""
        for attempt in range(2):
            _, response = self.engine.run_agent("master", STEP_BOUNDARY + prompt, inputs, context)
            text = output_text(response)
            try:
                result = json.loads(text)
                if not isinstance(result, dict):
                    raise ValueError("The result must be one JSON object.")
                return result
            except ValueError as error:
                if attempt:
                    raise ValueError("The application agent returned invalid JSON after one repair attempt.") from error
                inputs = [*inputs, {"role": "assistant", "content": text},
                          {"role": "user", "content": "Repair only the JSON syntax of your response. Return one valid JSON object, preserving the evidence and requested schema. Error: " + str(error)}]
