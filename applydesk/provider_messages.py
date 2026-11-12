"""Translate the app's bounded Responses history without exposing native reasoning."""

import base64
import binascii
import json
import re
import uuid

from .providers import ProviderError


def tool_definitions(tools):
    """Flatten only the app's declared namespace into provider-native function names."""
    result, names = [], set()
    for group in tools or []:
        if group.get("type") == "namespace" and group.get("name") != "applydesk":
            raise ProviderError("Unsupported agent tool namespace.")
        entries = group.get("tools", []) if group.get("type") == "namespace" else [group]
        for tool in entries:
            name = tool.get("name", "")
            if tool.get("type") != "function" or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or name in names:
                raise ProviderError("Invalid agent tool declaration.")
            names.add(name)
            result.append({"name": name, "description": tool.get("description", ""), "schema": tool["parameters"]})
    return result


def content_blocks(content):
    """User content remains text or an inline image; unsupported input fails explicitly."""
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    if not isinstance(content, list) or any(not isinstance(item, dict) for item in content):
        raise ProviderError("Unsupported agent message content.")
    for item in content:
        if item.get("type") not in {"input_text", "output_text", "text", "input_image"}:
            raise ProviderError("Unsupported agent message content.")
    return content


def inline_image(url):
    """Accept local base64 image bytes only, never a remote retrieval URL."""
    if not isinstance(url, str) or len(url) > 8 * 1024 * 1024:
        raise ProviderError("Document image is too large for the selected provider.")
    match = re.fullmatch(r"data:(image/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/=]+)", url)
    if not match:
        raise ProviderError("The selected provider needs an inline document image.")
    mime, data = match.groups()
    try:
        raw = base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error):
        raise ProviderError("Invalid document image encoding.") from None
    if not raw or len(raw) > 5 * 1024 * 1024:
        raise ProviderError("Document image must be at most 5 MB.")
    return mime, data


def append_message(messages, role, parts, key="content"):
    """Adjacent same-role blocks form one turn, including parallel tool results."""
    if not parts:
        return
    if messages and messages[-1]["role"] == role:
        messages[-1][key].extend(parts)
    else:
        messages.append({"role": role, key: list(parts)})


def function_call(name, arguments, identity=None, native_id=None):
    """Normalize tool calls while keeping provider IDs available for exact replies."""
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or not isinstance(arguments, dict):
        raise ProviderError("The provider returned an invalid tool call.")
    if identity is not None and (not isinstance(identity, str) or not identity or len(identity) > 200):
        raise ProviderError("The provider returned an invalid tool identifier.")
    return {"type": "function_call", "namespace": "applydesk", "name": name,
            "call_id": identity or "local-call-" + uuid.uuid4().hex,
            "arguments": json.dumps(arguments, allow_nan=False), "native_id": native_id, "native_copy": True}


def normalized_response(provider, identity, native, output):
    """Opaque native blocks survive tool turns but never enter visible chat text."""
    if not output or not isinstance(identity, str) or not identity:
        raise ProviderError("The provider returned no complete text or tool calls.")
    identities = [item["call_id"] for item in output if item["type"] == "function_call"]
    if len(identities) != len(set(identities)):
        raise ProviderError("The provider returned duplicate tool identifiers.")
    marker = {"type": "provider_message", "provider": provider, "message": native}
    for item in output:
        item["native_copy"] = True
    return {"id": identity, "status": "completed", "output": [marker, *output]}


def text_output(text):
    """Visible output uses the same representation as the existing OpenAI runner."""
    if not isinstance(text, str):
        raise ProviderError("The provider returned invalid text.")
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}
