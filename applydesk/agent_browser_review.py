"""Bind the agent's final self-check to observed controls, confirmed facts and uploaded bytes."""

import hashlib
import json
import re

from .application_terms import covers, unresolved_declarations
from .intelligence import _evidence_errors, _facts
from .readiness import _version_issues

AUTH = re.compile(r"\b(sign[ -]?in|log[ -]?in|create (?:an? )?account|register|password)\b", re.I)
DECLARATION = re.compile(r"\b(i (agree|accept|certify|attest|authorize|acknowledge|consent)|by (submitting|applying|clicking|continuing|proceeding|sending).{0,240}(agree|accept|certify|attest|authorize|acknowledge|consent)|electronic signature)\b", re.I | re.S)


def validate_review(workspace, job, profile, page, ledger, review):
    """Read-only review summaries are valid evidence; nonexistent editable fields are never invented."""
    if not isinstance(review, dict) or not isinstance(page, dict) or not isinstance(page.get("elements"), list):
        raise ValueError("The final review or observed page is malformed.")
    if not isinstance(review.get("final_element_id"), str):
        raise ValueError("The final review must identify an observed control.")
    current = workspace.store.get("job", job["id"]) or {}
    issues = _version_issues(job, current, profile)
    issues.extend(_page_issues(page, review))
    elements = page.get("elements", [])
    ids = [item.get("id") for item in elements]
    if any(not isinstance(identity, str) or not identity for identity in ids) or len(ids) != len(set(ids)):
        issues.append("The observed page contains missing or duplicate element identities.")
    indexed = {item.get("id"): item for item in elements}
    final = indexed.get(review.get("final_element_id"), {})
    issues.extend(_final_issues(job, page, review, final))
    issues.extend(_declaration_issues(page, final))
    issues.extend(_answer_issues(profile, indexed, review, final))
    issues.extend(_summary_issues(profile, page, review))
    issues.extend(_upload_issues(workspace, current, profile, page, ledger, review, final))
    if issues:
        raise ValueError("; ".join(dict.fromkeys(issues)))
    binding = {"page": page["snapshot_id"], "profile_revision": profile["revision"],
               "review": review, "uploads": ledger}
    digest = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
    return {"passed": True, "snapshot_id": page["snapshot_id"], "profile_revision": profile["revision"],
            "review_digest": digest, "final_element_id": final["id"],
            "field_ids": [answer["element_id"] for answer in review["answers"]],
            "summary_quotes": [item["quote"] for item in review["summaries"]],
            "resume_quote": review["resume_quote"]}


def _page_issues(page, review):
    """A complete observation and explicit self-check must precede the irreversible action."""
    issues = []
    if not page.get("snapshot_id") or not isinstance(page.get("text"), str):
        issues.append("The final page observation is missing.")
    if page.get("text_truncated") or page.get("elements_truncated"):
        issues.append("The final page observation is incomplete.")
    if page.get("loading"):
        issues.append("The final page is still loading.")
    if review.get("required_complete") is not True:
        issues.append("The agent has not confirmed all required application answers.")
    return issues


def _final_issues(job, page, review, final):
    """Allow the chosen Submit only in demonstrated application context, never authentication."""
    issues = []
    if not final or final.get("disabled") or final.get("visible") is not True:
        issues.append("The selected final control is missing, hidden or disabled.")
    button = final.get("tag") == "button" or (final.get("tag") == "input" and final.get("type") in {"button", "submit"})
    if not button and final.get("role") != "button":
        issues.append("The final application control is not an observed button.")
    if not final.get("form_id") and not final.get("context"):
        issues.append("The final control has no observed application context.")
    if final.get("authentication_form") or AUTH.search(final.get("label", "")):
        issues.append("An account or sign-in control cannot submit a job application.")
    relevant_ids = {item["id"] for item in page.get("elements", [])
                    if not final.get("form_id") or item.get("form_id") == final["form_id"]}
    if any(item.get("invalid") and item.get("id") in relevant_ids for item in page.get("errors", [])):
        issues.append("The final application contains an invalid answer.")
    for key in ("company", "title"):
        quote, expected = review.get(key + "_quote"), job.get(key)
        if not _quoted(quote, page) or not isinstance(expected, str) or not expected.strip():
            issues.append(f"The final page does not quote the expected {key}.")
        elif not re.search(r"(?<!\w)" + re.escape(expected.casefold()) + r"(?!\w)", quote.casefold()):
            issues.append(f"The final {key} quote identifies a different application.")
    return issues


def _declaration_issues(page, final):
    """Substantive visible assent remains gated; standalone footer links and notices are not assent."""
    quotes = unresolved_declarations(page.get("text", ""), DECLARATION)
    for element in page.get("elements", []):
        if final.get("form_id") and element.get("form_id") != final["form_id"]:
            continue
        if element.get("type") == "checkbox" and element.get("required") and re.search(
                r"\b(terms|agreement|consent|certification|signature)\b", element.get("label", ""), re.I):
            if not covers(element["label"]):
                quotes.append(element["label"])
    return [f"The application contains a declaration or agreement requiring your review: {quote}" for quote in dict.fromkeys(quotes)]


def _quoted(quote, page):
    """Quotes are exact visible text, not the model's reconstruction of the page."""
    return isinstance(quote, str) and bool(quote.strip()) and quote in page.get("text", "")


def _answer_issues(profile, elements, review, final):
    """Every current required or populated answer must match the exact live readback."""
    answers, facts, issues, audited = review.get("answers"), _facts(profile), [], set()
    if not isinstance(answers, list):
        return ["The final answer self-check must be a list."]
    for answer in answers:
        if not isinstance(answer, dict):
            issues.append("A reviewed answer is malformed.")
            continue
        identity = answer.get("element_id")
        if not isinstance(identity, str):
            issues.append("A reviewed answer has no observed control identity.")
            continue
        element = elements.get(identity)
        if identity in audited or not element or not _input(element):
            issues.append("A reviewed answer has an unknown or duplicate control.")
            continue
        audited.add(identity)
        if "value" not in answer or json.dumps(answer["value"], sort_keys=True) != json.dumps(_value(element), sort_keys=True):
            issues.append(f"The live answer for {element.get('label') or identity} differs from its self-check.")
        label = element.get("label", "")
        if not (element.get("required") and _value(element) is True and covers(label)):
            issues.extend(_evidence_errors(answer.get("fact_ids"), facts))
        if final.get("form_id") and element.get("form_id") != final["form_id"]:
            issues.append("A reviewed answer is outside the final application form.")
    relevant = {identity: element for identity, element in elements.items()
                if not final.get("form_id") or element.get("form_id") == final["form_id"]}
    return issues + _missing_answers(relevant, audited)


def _missing_answers(relevant, audited):
    """Only the chosen application's active fields participate in completeness checks."""
    issues = []
    for identity, element in relevant.items():
        if not _input(element) or element.get("type") == "file":
            continue
        value = _value(element)
        required = element.get("required") and not element.get("disabled") and "radio" not in (element.get("type"), element.get("role"))
        if (required or value not in (None, "", False, [])) and identity not in audited:
            issues.append("A required or populated live answer is missing from the self-check.")
        if required and value in (None, "", False, []):
            issues.append("A required application answer is empty.")
    issues.extend(_radio_issues(relevant, audited))
    return issues


def _input(element):
    """Exclude navigation controls and options while retaining native and accessible inputs."""
    return ((element.get("tag") in {"input", "select", "textarea"}
             and element.get("type") not in {"submit", "button", "reset", "hidden", "image"})
            or element.get("role") in {"textbox", "combobox", "checkbox", "radio", "listbox"})


def _value(element):
    """Checkbox/radio evidence represents the selection rather than its static HTML value."""
    if element.get("type") in {"checkbox", "radio"} or element.get("role") in {"checkbox", "radio"}:
        return element.get("checked") in (True, "true")
    return element.get("value")


def _radio_issues(elements, audited):
    """Required groups need one selected, reviewed member, not every unselected option."""
    groups, issues = {}, []
    for element in elements.values():
        if not element.get("disabled") and (element.get("type") == "radio" or element.get("role") == "radio"):
            group = (element.get("form_id"), element.get("group_id"), element.get("name"))
            groups.setdefault(group, []).append(element)
    for group, options in groups.items():
        selected = [item for item in options if _value(item)]
        if not any(group[1:]) or len(selected) > 1 or (any(item.get("required") for item in options) and not selected):
            issues.append("A radio group has missing or ambiguous selections.")
        if any(item["id"] not in audited for item in selected):
            issues.append("A selected radio answer is missing from the self-check.")
    return issues


def _summary_issues(profile, page, review):
    """Read-only summary evidence retains its actual quote and confirmed supporting fact IDs."""
    summaries, facts, issues = review.get("summaries"), _facts(profile), []
    if not isinstance(summaries, list):
        return ["The final summary self-check must be a list."]
    if not summaries and not review.get("answers"):
        issues.append("No personal application answers or review summaries were checked.")
    for summary in summaries:
        if not isinstance(summary, dict) or not _quoted(summary.get("quote"), page):
            issues.append("A review summary does not quote the current visible page.")
            continue
        issues.extend(_evidence_errors(summary.get("fact_ids"), facts))
    return issues


def _upload_issues(workspace, job, profile, page, ledger, review, final):
    """Historical byte readback plus a current filename proves a retained upload on read-only review."""
    issues, recorded = [], set()
    if not isinstance(ledger, list) or not ledger:
        return ["The selected resume has no verified upload."]
    expected = {"resume": job.get("resume_id"), "transcript": profile.get("transcript_id")}
    for upload in ledger:
        identity = upload.get("element_id")
        if not identity or identity in recorded:
            issues.append("The upload ledger contains missing or duplicate control identities.")
        recorded.add(identity)
        issues.extend(_verify_upload(workspace, upload, expected, page))
    resumes = [upload for upload in ledger if upload.get("kind") == "resume"]
    if len(resumes) != 1 or not _quoted(review.get("resume_quote"), page):
        issues.append("The current review does not visibly identify one verified resume.")
    elif resumes[0].get("filename", "") not in review["resume_quote"]:
        issues.append("The visible resume quote does not name the uploaded file.")
    for element in page.get("elements", []):
        if final.get("form_id") and element.get("form_id") != final["form_id"]:
            continue
        if element.get("type") == "file" and (element.get("required") or element.get("files")) and element["id"] not in recorded:
            issues.append("A current required or populated file control lacks a verified upload.")
    return issues


def _verify_upload(workspace, upload, expected, page):
    """Rehash the selected source without claiming that a review page exposes live File bytes."""
    identity, kind = upload.get("document_id"), upload.get("kind")
    if not identity or identity != expected.get(kind):
        return ["An uploaded attachment is not the currently selected document version."]
    try:
        record = workspace.docs.get(identity)
        source = workspace.docs.asset(record["source_path"])
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
    except (KeyError, ValueError, OSError):
        return ["The uploaded document source is no longer available."]
    issues, files = [], upload.get("files", [])
    if digest != record.get("source_sha256") or upload.get("sha256") != digest:
        issues.append("The selected document bytes changed after upload.")
    if upload.get("selected_file_verified") is not True or len(files) != 1 or files[0].get("sha256") != digest:
        issues.append("The upload lacks verified browser file-byte readback.")
    if upload.get("filename") != source.name or upload.get("size") != source.stat().st_size:
        issues.append("The upload metadata differs from the selected source file.")
    if not files or files[0].get("name") != source.name or files[0].get("size") != upload.get("size"):
        issues.append("The browser file readback differs from the source metadata.")
    if source.name not in page.get("text", ""):
        issues.append("An uploaded filename is absent from the current review.")
    current = next((item for item in page.get("elements", []) if item["id"] == upload.get("element_id")), None)
    if current and current.get("files") and current["files"] != files:
        issues.append("The current file control differs from the verified upload.")
    return issues
