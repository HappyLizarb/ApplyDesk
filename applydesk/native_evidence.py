"""Keep visible application evidence separate from model-proposed actions."""

import hashlib
import json
import math
import re

from .intelligence import _evidence_errors, _facts
from . import application_terms
from .native_application_io import _picker

BINDING = re.compile(r"\b(arbitration|class.action|waiv(?:e|er)|terms (?:of (?:use|service)|and conditions)|background (?:check|screening)|electronic signature)\b", re.I)
INPUT_ROLES = {"AXTextField", "AXTextArea", "AXComboBox", "AXIncrementor", "AXCheckBox", "AXRadioButton", "AXPopUpButton"}
AGREEMENT_TOPICS = (r"terms (?:of (?:use|service)|and conditions)", r"privacy", r"arbitration",
                    r"class.action", r"waiv(?:e|er)|release.{0,30}liability", r"background|screening",
                    r"interpol", r"restricted.part", r"reference check", r"credit check",
                    r"recording|transcription", r"marketing", r"electronic signature")


def page_text(page):
    """Include visible native values without exposing secure fields or screenshot bytes."""
    if page.get("native_modal"):
        return "A native dialog is open."
    parts = [page.get("title", ""), page.get("text", "")]
    for item in page.get("elements", []):
        if not item.get("secure") and (item.get("web_content", True) or item.get("parent_dialog_id")):
            parts.extend(str(item.get(key, "")) for key in ("label", "value"))
    return "\n".join(parts)


def quoted(quote, text):
    """Whitespace differences in native accessibility text do not change the observed words."""
    return isinstance(quote, str) and bool(quote.strip()) and " ".join(quote.split()) in " ".join(text.split())


def public_page(page):
    """Pass only the current viewport to the model; images use the model's image channel."""
    if page.get("native_modal"):
        picker = _picker(page, set())
        return {"native_modal": True, "snapshot_id": page.get("snapshot_id"),
                "text": ("Use upload with this file picker ID." if picker else
                         "A native dialog is open. Press Escape to return to the form and inspect it again."),
                "dialogs": [{"id": picker["id"], "kind": "file_picker"}] if picker else [], "elements": []}
    result = {key: page[key] for key in ("native", "url", "title", "text", "elements_truncated",
                                         "text_truncated", "screenshot_needed", "reading_notice") if key in page}
    fields = ("id", "role", "label", "value", "url", "enabled", "focused", "selected", "required", "secure",
              "expanded", "clipped", "text_incomplete", "parent_dialog_id")
    result["elements"] = [{key: item[key] for key in fields if key in item}
                          for item in page.get("elements", []) if item.get("web_content", True)]
    if page.get("screenshot"):
        result["image_size"] = {key: page["screenshot"].get(key) for key in ("width", "height", "scale")}
    return result


def observation(page):
    """Store bounded visible evidence for self-review across scrolled and multistep forms."""
    return {"snapshot_id": page.get("snapshot_id"), "url": page.get("url"),
            "text": page_text(page)[:24000], "controls": [
                {key: item.get(key) for key in ("id", "role", "label", "value", "secure")}
                for item in page.get("elements", []) if not page.get("native_modal")
                and item.get("role") in INPUT_ROLES and item.get("web_content", True)]}


def evidence_digest(value):
    """Bind a review to its exact profile, selected files and observed window snapshot."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def review_context(evidence, current=None):
    """Carry prior evidence forward once, omitting values already available in the current viewport."""
    lines, controls = {}, {}
    for view in evidence:
        for line in view.get("text", "").splitlines():
            if line.strip():
                lines[line] = None
        for control in view.get("controls", []):
            controls[control["id"]] = control
    if current:
        for line in current.get("text", "").splitlines():
            lines.pop(line, None)
        for control in current.get("controls", []):
            controls.pop(control["id"], None)
    return {"observed_text": "\n".join(lines)[-60000:], "latest_controls": list(controls.values())}


def agreement_facts(profile, job):
    """An approval must have a recorded human source and name this exact saved application."""
    prefix = f"Application {(job or {}).get('id')}: "
    return {key: fact for key, fact in _facts(profile).items() if fact.get("kind") == "application_agreement"
              and fact["text"].startswith(prefix) and any(s.get("role") == "user" for s in fact.get("sources", []))}


def validate_agreement(profile, job, control, ids, quote, page):
    """Accept required employer terms directly and use specific approvals for additional permissions."""
    if (control.get("role") not in {"AXCheckBox", "AXRadioButton"} or control.get("enabled") is not True
            or not control.get("web_content", True)):
        raise ValueError("Choose the visible agreement checkbox or radio button itself.")
    if application_terms.visible_terms(page, control):
        return
    grants = agreement_facts(profile, job)
    if not ids:
        raise ValueError("Show the agreement wording next to its checkbox and inspect it again.")
    if _evidence_errors(ids, grants) or not quoted(quote, page_text(page)):
        raise ValueError("This agreement needs the user's recorded approval for this posting.")
    wording = str(control.get("label", "")) + " " + quote
    topics = [topic for topic in AGREEMENT_TOPICS if re.search(topic, wording, re.I)]
    approved = " ".join(grants[key]["text"].split(": ", 1)[-1] for key in ids)
    if not topics or any(not re.search(topic, approved, re.I) for topic in topics):
        raise ValueError("The recorded approval does not cover the visible agreement.")


def _public_job_search(job, element, operation):
    """Search a visible jobs field with the saved title, employer, or single published requisition."""
    text = operation.get("text")
    if (not isinstance(text, str) or not text.strip() or operation.get("point") or element.get("clipped")
            or not element.get("web_content") or not element.get("enabled")
            or element.get("role") not in {"AXTextField", "AXSearchField", "AXComboBox"}
            or not re.search(r"\b(search|keywords?)\b", str(element.get("label", "")), re.I)):
        return False
    if text in {job.get("title"), job.get("company")}:
        return True
    source = job.get("public_source") or {}
    identifiers = re.findall(r"\brequisition\s+(?:id|number|no\.?)\s*[:#]?\s*([a-z]{0,6}-?\d{3,}(?:-\d+)?)\b",
                             source.get("description") or "", re.I)
    return source.get("complete") is True and {item.casefold() for item in identifiers} == {text.casefold()}


def _agreement_control(page, operation, element):
    """Bind a visual click to one observed checkbox, including its identity for the native hit test."""
    if operation["action"] != "click":
        raise ValueError("Choose the observed agreement control.")
    if operation.get("point") is None:
        return element
    point = native_point(page, operation["point"])
    matches = []
    for field in page.get("elements", []):
        if (field.get("role") not in {"AXCheckBox", "AXRadioButton"} or not field.get("id")
                or not field.get("web_content", True) or not field.get("enabled") or field.get("clipped")):
            continue
        box = field.get("bounds") or {}
        if (all(type(box.get(k)) in (int, float) and math.isfinite(box[k]) for k in ("x", "y", "width", "height"))
                and box["x"] <= point["x"] < box["x"] + box["width"] and box["y"] <= point["y"] < box["y"] + box["height"]):
            matches.append(field)
    if len(matches) != 1 or operation.get("element_id") not in (None, matches[0]["id"]):
        raise ValueError("Choose the observed agreement control.")
    return matches[0]


def validate_operation(profile, page, operation, job=None):
    """Allow native exploration while keeping personal answers grounded and secrets private."""
    if not isinstance(operation, dict) or operation.get("action") not in {"click", "type", "key", "scroll"}:
        raise ValueError("Choose a native click, type, key or scroll action.")
    if operation.get("action") == "key" and str(operation.get("key")).casefold() in {"cmd+t", "cmd+w", "cmd+l"}:
        raise ValueError("Stay in the current application. ApplyDesk manages its browser tabs.")
    element = next((item for item in page.get("elements", []) if item.get("id") == operation.get("element_id")), {})
    if page.get("native_modal") and not (operation.get("action") == "key" and operation.get("key") == "escape"):
        raise ValueError("Use the approved document upload action for a native file picker, or Escape to cancel.")
    if element.get("secure"):
        raise ValueError("Use authenticate for secure password fields.")
    if operation.get("point") is not None and not page.get("screenshot"):
        raise ValueError("Observe a screenshot before choosing a coordinate.")
    intent = operation.get("intent", "navigation")
    if intent == "agreement":
        element = _agreement_control(page, operation, element)
        validate_agreement(profile, job, element, operation.get("fact_ids"), operation.get("agreement_quote"), page)
        return {**operation, "element_id": element["id"]}
    elif (operation["action"] == "click" and element.get("role") in {"AXCheckBox", "AXRadioButton"}
          and BINDING.search(str(element.get("label", "")))):
        raise ValueError("Use the agreement action for this checkbox.")
    elif intent == "privacy":
        validate_consent(page, operation.get("consent"))
    elif intent == "navigation" and operation.get("action") == "type" and job:
        if _public_job_search(job, element, operation):
            return operation
        if _evidence_errors(operation.get("fact_ids"), _facts(profile)):
            raise ValueError("Use the saved job title, employer or published requisition in the employer search field.")
    elif operation.get("action") == "type" or intent == "answer":
        errors = _evidence_errors(operation.get("fact_ids"), _facts(profile))
        if errors:
            raise ValueError("Personal answers need confirmed profile facts: " + "; ".join(errors))
    elif intent != "navigation":
        raise ValueError("Identify this action as navigation, a personal answer or ordinary privacy processing.")
    return operation


def validate_consent(page, consent):
    """Ordinary application privacy processing does not authorize contracts or extra screening."""
    if not isinstance(consent, dict) or consent.get("kind") not in {"application_privacy", "cookie_reject"}:
        raise ValueError("Identify the observed ordinary privacy choice.")
    quote = consent.get("quote")
    if not quoted(quote, page_text(page)) or BINDING.search(quote):
        raise ValueError("Quote the visible notice for this privacy choice. Use the agreement action for binding terms.")


def native_point(page, point):
    """Convert a point in the supplied image to native window points, including Retina scale."""
    image = page.get("screenshot") or {}
    if not isinstance(point, dict) or set(point) != {"x", "y"}:
        raise ValueError("A screenshot target needs image x and y coordinates.")
    x, y, scale = point["x"], point["y"], image.get("scale", 0)
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in (x, y, scale)) or scale <= 0:
        raise ValueError("The screenshot target or scale is invalid.")
    if not 0 <= x < image.get("width", 0) or not 0 <= y < image.get("height", 0):
        raise ValueError("The screenshot target is outside the observed window.")
    return {"x": x / scale, "y": y / scale}
