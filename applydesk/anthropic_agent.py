"""Native Messages API mapping, including unchanged signed thinking between tool calls."""

import json
from copy import deepcopy

from .provider_messages import append_message, content_blocks, function_call, inline_image
from .provider_messages import normalized_response, text_output, tool_definitions
from .providers import ProviderError, validate_model_id


def request_body(payload):
    """Use Anthropic tool schemas and adaptive thinking only for explicit supported effort."""
    body = {"model": validate_model_id("anthropic", payload["model"]), "max_tokens": 16384,
            "system": payload["instructions"], "messages": messages(payload["input"])}
    effort = payload.get("reasoning", {}).get("effort", "default")
    if effort != "default":
        if effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ProviderError("Unsupported Anthropic thinking strength.")
        body.update(thinking={"type": "adaptive"}, output_config={"effort": effort})
    tools = tool_definitions(payload.get("tools"))
    if tools:
        body["tools"] = [{"name": tool["name"], "description": tool["description"], "input_schema": tool["schema"]} for tool in tools]
        body["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
    return body


def messages(history):
    """Preserve signed native turns and follow each tool call with its matching result."""
    result = []
    for item in history:
        kind = item.get("type")
        if kind == "provider_message":
            if item.get("provider") != "anthropic":
                raise ProviderError("The provider changed during this tool conversation.")
            result.append(deepcopy(item["message"]))
        elif item.get("native_copy"):
            continue
        elif kind == "function_call_output":
            append_message(result, "user", [{"type": "tool_result", "tool_use_id": item["call_id"], "content": item["output"]}])
        elif kind == "function_call":
            append_message(result, "assistant", [{"type": "tool_use", "id": item["call_id"], "name": item["name"], "input": json.loads(item["arguments"])}])
        elif item.get("role") in {"user", "assistant"}:
            append_message(result, item["role"], [_content(block) for block in content_blocks(item.get("content", ""))])
        else:
            raise ProviderError("Unsupported agent conversation item.")
    return result


def _content(block):
    """Document screenshots keep their original MIME type and image bytes."""
    if block["type"] == "input_image":
        mime, data = inline_image(block.get("image_url"))
        return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}
    if not isinstance(block.get("text"), str):
        raise ProviderError("Invalid agent message text.")
    return {"type": "text", "text": block["text"]}


def parse_response(value):
    """Reject refusals, token limits, and incomplete tools before the app can act."""
    reason, blocks = value.get("stop_reason"), value.get("content")
    if reason not in {"end_turn", "tool_use"} or not isinstance(blocks, list):
        raise ProviderError("Anthropic did not complete the response. It may be blocked or exceed the output limit.")
    output = []
    for block in blocks:
        if not isinstance(block, dict):
            raise ProviderError("Anthropic returned invalid response content.")
        kind = block.get("type")
        if kind == "text" and block.get("text"):
            output.append(text_output(block["text"]))
        elif kind == "tool_use":
            if not isinstance(block.get("id"), str) or not block["id"]:
                raise ProviderError("Anthropic returned a tool call without an identifier.")
            output.append(function_call(block.get("name"), block.get("input"), block.get("id")))
        elif kind not in {"text", "thinking", "redacted_thinking"}:
            raise ProviderError("Anthropic returned an unsupported response block.")
    calls = [item for item in output if item["type"] == "function_call"]
    if bool(calls) != (reason == "tool_use") or len(calls) > 8:
        raise ProviderError("Anthropic returned an incomplete tool response.")
    native = {"role": "assistant", "content": deepcopy(blocks)}
    return normalized_response("anthropic", value.get("id"), native, output)
