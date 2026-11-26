"""Distinguish a visible field rejection from an uncertain delivery or network error."""

import re

from .native_evidence import page_text, quoted

RECEIVED = re.compile(r"\b(?:application (?:was |has been )?(?:received|submitted)|already applied|thank you for applying|received your application)\b", re.I)


def rejected_field(before, after, result, final_id):
    """Identify a field diagnostic without treating it as proof of non-delivery."""
    name = result.get("quote")
    if result.get("status") != "field_error" or not isinstance(name, str) or not re.fullmatch(
            r"(?:Error:\s*)?(?:select at least one (?:value|option)|this field is required|please (?:select|enter) a value)[.!]?", name, re.I):
        return None
    binding = ("pid", "window_id", "active_tab_id", "generation", "url")
    if (any(before.get(key) is None or before[key] != after.get(key) for key in binding)
            or not before["url"].startswith("https://") or not after.get("snapshot_id")
            or before.get("snapshot_id") == after["snapshot_id"] or after.get("native_modal") or after.get("loading")
            or RECEIVED.search(page_text(after)) or quoted(name, page_text(before))):
        return None
    elements = [item for item in after.get("elements", []) if item.get("web_content", True)]
    errors = [item for item in elements if item.get("role") in {"AXStaticText", "AXHeading"}
              and name in (item.get("label"), item.get("value"))]
    field = next((item for item in elements if item.get("id") == result.get("field_id") and item.get("enabled")
                  and item.get("role") in {"AXCheckBox", "AXComboBox", "AXPopUpButton", "AXTextField"}
                  and item.get("value") in (None, "", False, 0) and item.get("required")), None)
    final = any(item.get("id") == final_id and item.get("enabled") and item.get("role") in {"AXButton", "AXLink"} for item in elements)
    if field and errors and final:
        return {"error": name, "field": str(field.get("label", ""))[:160], "url": before["url"],
                "snapshot_id": after["snapshot_id"]}
    return None
