"""Native generateContent mapping preserves function IDs and opaque thought signatures."""

import json
import uuid
from copy import deepcopy

from .provider_messages import append_message, content_blocks, function_call, inline_image
from .provider_messages import normalized_response, text_output, tool_definitions
from .providers import ProviderError, validate_model_id


def request_body(payload):
    """Use JSON-schema declarations and provider-native thinking levels."""
    validate_model_id("google", payload["model"])
    body = {"systemInstruction": {"parts": [{"text": payload["instructions"]}]},
            "contents": messages(payload["input"]), "generationConfig": {"maxOutputTokens": 16384}}
    effort = payload.get("reasoning", {}).get("effort", "default")
    if effort != "default":
        if effort not in {"minimal", "low", "medium", "high"}:
            raise ProviderError("Unsupported Google thinking strength.")
        body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": effort.upper()}
    tools = tool_definitions(payload.get("tools"))
    if tools:
        body["tools"] = [{"functionDeclarations": [{"name": tool["name"], "description": tool["description"],
                         "parametersJsonSchema": tool["schema"]} for tool in tools]}]
        body["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
    return body


def messages(history):
    """Reconstruct each native model turn exactly before adding function responses."""
    result, calls = [], {item["call_id"]: item for item in history if item.get("type") == "function_call"}
    for item in history:
        kind = item.get("type")
        if kind == "provider_message":
            if item.get("provider") != "google":
                raise ProviderError("The provider changed during this tool conversation.")
            result.append(deepcopy(item["message"]))
        elif item.get("native_copy"):
            continue
        elif kind == "function_call_output":
            append_message(result, "user", [_tool_result(item, calls)], "parts")
        elif kind == "function_call":
            part = {"functionCall": {"name": item["name"], "args": json.loads(item["arguments"])}}
            append_message(result, "model", [part], "parts")
        elif item.get("role") in {"user", "assistant"}:
            role = "model" if item["role"] == "assistant" else "user"
            append_message(result, role, [_content(block) for block in content_blocks(item.get("content", ""))], "parts")
        else:
            raise ProviderError("Unsupported agent conversation item.")
    return result


def _tool_result(item, calls):
    """Match parallel function results by ID, including providers that omit call IDs."""
    call = calls.get(item["call_id"])
    if not call:
        raise ProviderError("A tool result has no matching provider call.")
    value = json.loads(item["output"])
    response = {"name": call["name"], "response": value if isinstance(value, dict) else {"result": value}}
    if call.get("native_id"):
        response["id"] = call["native_id"]
    return {"functionResponse": response}


def _content(block):
    """Inline document screenshots retain their original encoding and MIME type."""
    if block["type"] == "input_image":
        mime, data = inline_image(block.get("image_url"))
        return {"inlineData": {"mimeType": mime, "data": data}}
    if not isinstance(block.get("text"), str):
        raise ProviderError("Invalid agent message text.")
    return {"text": block["text"]}


def parse_response(value):
    """Only one fully completed, unblocked candidate can produce application actions."""
    candidates = value.get("candidates", [])
    if value.get("promptFeedback", {}).get("blockReason") or not isinstance(candidates, list) or len(candidates) != 1:
        raise ProviderError("Google blocked the request or returned no single completed candidate.")
    candidate = candidates[0]
    if not isinstance(candidate, dict) or candidate.get("finishReason") != "STOP":
        raise ProviderError("Google did not complete the response. It may be blocked or exceed the output limit.")
    native = candidate.get("content", {})
    if not isinstance(native, dict) or native.get("role", "model") != "model" or not isinstance(native.get("parts"), list):
        raise ProviderError("Google returned invalid response content.")
    output = _outputs(native["parts"])
    if sum(item["type"] == "function_call" for item in output) > 8:
        raise ProviderError("Google returned too many simultaneous tool calls.")
    native = {**deepcopy(native), "role": "model"}
    return normalized_response("google", value.get("responseId") or "google-" + uuid.uuid4().hex, native, output)


def _outputs(parts):
    """Thought parts are retained privately and excluded from displayed answer text."""
    output = []
    for part in parts:
        if not isinstance(part, dict):
            raise ProviderError("Google returned invalid response content.")
        if part.get("thought"):
            continue
        if "functionCall" in part:
            call = part["functionCall"]
            if not isinstance(call, dict):
                raise ProviderError("Google returned an invalid function call.")
            output.append(function_call(call.get("name"), call.get("args", {}), call.get("id"), call.get("id")))
        elif isinstance(part.get("text"), str) and part["text"]:
            output.append(text_output(part["text"]))
        elif not set(part) <= {"thoughtSignature", "text"}:
            raise ProviderError("Google returned an unsupported response part.")
    return output
