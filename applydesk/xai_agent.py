"""Grok Responses adapter; only ApplyDesk tools are exposed to the single agent."""

import json
from copy import deepcopy

from .provider_messages import function_call, normalized_response, text_output, tool_definitions
from .providers import ProviderError, validate_model_id


def request_body(payload):
    """Keep system instructions, tool history, and encrypted reasoning without remote storage."""
    body = {"model": validate_model_id("xai", payload["model"]), "stream": False, "store": False,
            "input": [{"role": "system", "content": payload["instructions"]}, *messages(payload["input"])],
            "include": ["reasoning.encrypted_content"]}
    if payload.get("reasoning"):
        body["reasoning"] = deepcopy(payload["reasoning"])
    tools = tool_definitions(payload.get("tools"))
    if tools:
        body.update(tools=[{"type": "function", "name": tool["name"], "description": tool["description"],
                            "parameters": tool["schema"]} for tool in tools], parallel_tool_calls=False)
    return body


def messages(history):
    """Reuse exact native response items and omit the app's duplicate normalized messages."""
    result = []
    for item in history:
        if item.get("type") == "provider_message":
            if item.get("provider") != "xai":
                raise ProviderError("The provider changed during this tool conversation.")
            result.extend(deepcopy(item["message"]))
        elif item.get("native_copy"):
            continue
        elif item.get("type") == "function_call_output" or item.get("role") in {"user", "assistant"}:
            result.append(deepcopy(item))
        else:
            raise ProviderError("Unsupported Grok conversation item.")
    return result


def parse_response(value):
    """Incomplete responses and refusals cannot authorize a tool action or save a draft."""
    blocks = value.get("output")
    if value.get("status") != "completed" or not isinstance(blocks, list):
        raise ProviderError("Grok did not complete the response. Check model access and usage limits.")
    output = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("status", "completed") != "completed":
            raise ProviderError("Grok returned incomplete response content.")
        kind = block.get("type")
        if kind == "function_call":
            if not isinstance(block.get("call_id"), str) or not block["call_id"]:
                raise ProviderError("Grok returned a tool call without its identifier.")
            try:
                arguments = json.loads(block["arguments"])
            except (ValueError, KeyError, TypeError):
                raise ProviderError("Grok returned invalid tool arguments.") from None
            output.append(function_call(block.get("name"), arguments, block.get("call_id")))
        elif kind == "message":
            if not isinstance(block.get("content"), list):
                raise ProviderError("Grok returned invalid message content.")
            for content in block.get("content", []):
                if not isinstance(content, dict) or content.get("type") != "output_text":
                    raise ProviderError("Grok refused or returned unsupported content.")
                output.append(text_output(content.get("text")))
        elif kind != "reasoning":
            raise ProviderError("Grok returned an unsupported response item.")
    return normalized_response("xai", value.get("id"), deepcopy(blocks), output)
