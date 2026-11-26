"""Bind the agent's visible self-review to the current profile, window and original files."""

import hashlib
import re
from copy import deepcopy
from difflib import get_close_matches
from urllib.parse import urlsplit

from .application_answers import current_company_answer
from .intelligence import _evidence_errors, _facts
from .native_evidence import BINDING, INPUT_ROLES, evidence_digest, page_text, quoted, review_context, validate_agreement, validate_consent
from . import application_terms
from .readiness import _version_issues
from .public_source import matches_saved_requisition_form, reopened_account_draft
from .recommendation_checks import same_workday_job


class NativeReviewError(ValueError):
    """Only fixed diagnostic messages from the local final-review checks belong in job history."""


def review_surface(page):
    """Compare the application content and window identity, ignoring Chrome toolbar and focus changes."""
    fields = ("id", "role", "label", "value", "url", "enabled", "required", "selected", "expanded", "secure", "clipped", "text_incomplete")
    controls = [tuple(c.get(key) for key in fields) for c in page.get("elements", []) if c.get("web_content", True)]
    return (tuple(page.get(k) for k in ("pid", "window_id", "active_tab_id", "generation", "url", "title", "text", "native_modal", "loading", "elements_truncated", "text_truncated")), controls)


def validate_review(workspace, job, profile, page, uploads, evidence, review):
    """Native UI proves displayed answers and selected filenames, not the server's file bytes."""
    if not isinstance(review, dict) or not isinstance(evidence, list):
        raise ValueError("The final native application review is missing.")
    current = workspace.store.get("job", job["id"]) or {}
    issues = _version_issues(job, current, profile)
    final = next((item for item in page.get("elements", []) if item.get("id") == review.get("final_element_id")), {})
    if not page.get("snapshot_id") or not page.get("native") or page.get("loading"):
        issues.append("Inspect the current visible application page before submitting.")
    if any(review.get(key) is not True for key in ("required_complete", "checked_all_steps", "no_unapproved_agreements")):
        issues.append("Check all application steps and required approvals before submitting.")
    if final.get("enabled") is not True or final.get("role") not in {"AXButton", "AXLink"}:
        issues.append("The final application control is not currently visible and enabled.")
    elif final.get("clipped"):
        issues.append("Scroll the Submit button fully into view and dismiss any notice covering it.")
    if re.search(r"\b(sign[ -]?in|log[ -]?in|creat\w* account|register|password|save draft)\b", final.get("label", ""), re.I):
        issues.append("An account or draft action is not a final application submission.")
    if any(item.get("secure") and item.get("enabled") for item in page.get("elements", [])):
        issues.append("The current page is an account form; finish authentication before application review.")
    text = "\n".join([*(item.get("text", "") for item in evidence), page_text(page)])
    issues.extend(_identity_issues(reopened_account_draft(job, current, page) or job, review, text, page))
    issues.extend(_answer_issues(profile, page, review, text, job))
    employer = current_company_answer(profile)
    if employer and _conflicting_employer(evidence, page, employer[0]):
        issues.append("Revisit the Current company field and correct its autofilled value to the confirmed current employer.")
    issues.extend(_upload_issues(workspace, job, profile, uploads, review, text))
    if issues:
        raise NativeReviewError("; ".join(dict.fromkeys(issues)))
    binding = {"snapshot_id": page["snapshot_id"], "profile_revision": profile["revision"],
               "review": review, "uploads": uploads, "evidence": evidence}
    return {"passed": True, "snapshot_id": page["snapshot_id"], "profile_revision": profile["revision"],
            "final_element_id": final["id"], "review_digest": evidence_digest(binding)}


def _conflicting_employer(evidence, page, expected):
    """Compare the latest visible current-employer entry, including fields on earlier form steps."""
    for view in reversed([*evidence, {"controls": page.get("elements", [])}]):
        for field in view.get("controls", []):
            label = str(field.get("label", "")).strip(" *:").casefold()
            if (label in {"current employer", "current company"} and field.get("role") in {"AXTextField", "AXComboBox"}
                    and isinstance(field.get("value"), str) and not field.get("text_incomplete")):
                value = field["value"].strip()
                return bool(value) and value != expected
    return False


def _identity_issues(job, review, text, page):
    """A final button must belong to the employer and role already read in this window."""
    issues = []
    source = job.get("public_source") or {}
    employer = source.get("company", "") if matches_saved_requisition_form(job, page) else ""
    for key in ("company", "title"):
        quote = review.get(key + "_quote")
        names = [job.get(key), employer] if key == "company" else [job.get(key)]
        if not quoted(quote, text) or not any(name and name.casefold() in quote.casefold() for name in names):
            issues.append("The review does not identify the observed " + key + ".")
    return issues


def _answer_issues(profile, page, review, text, job=None):
    """Every checked personal answer cites real displayed words and confirmed profile evidence."""
    answers, summaries = review.get("answers"), review.get("summaries", [])
    if not isinstance(answers, list) or not isinstance(summaries, list) or not (answers or summaries):
        return ["Check the populated application answers against the confirmed profile."]
    facts, issues = _facts(profile), []
    for entries, current in ((answers, True), (summaries, False)):
        for answer in entries:
            if not isinstance(answer, dict) or not quoted(answer.get("quote"), text):
                issues.append("A checked answer is absent from the observed application.")
                continue
            control = next((c for c in page.get("elements", []) if c.get("id") == answer.get("element_id")), {}) if current else {}
            if current and "consent" in answer:
                issues.extend(_privacy_answer_issues(page, answer))
            elif (control.get("role") in {"AXCheckBox", "AXRadioButton"} and control.get("value")
                    and ("agreement_quote" in answer or BINDING.search(str(control.get("label", ""))) or application_terms.visible_terms(page, control))):
                try:
                    validate_agreement(profile, job, control, answer.get("fact_ids"),
                                       answer.get("agreement_quote", answer.get("quote")), page)
                except ValueError as error:
                    issues.append(str(error))
            elif not current and "agreement_quote" in answer:
                wording = answer["agreement_quote"]
                if not application_terms.covers(wording) or wording.split() != answer["quote"].split():
                    issues.append("The prior step must quote the required employer agreement.")
            else:
                issues.extend(_evidence_errors(answer.get("fact_ids"), facts))
    return issues + _current_answers(page, answers)


def _privacy_answer_issues(page, answer):
    """A current ordinary privacy choice uses application authority, not a personal profile fact."""
    consent = answer.get("consent")
    if not isinstance(consent, dict) or consent.get("kind") != "application_privacy":
        return ["Only an ordinary application privacy choice can replace personal-answer evidence."]
    control = next((item for item in page.get("elements", []) if item.get("id") == answer.get("element_id")), {})
    value, label = control.get("value"), control.get("label", "")
    if (control.get("role") not in {"AXCheckBox", "AXRadioButton"} or control.get("enabled") is not True
            or not control.get("web_content", True) or type(value) not in (bool, int) or value != 1):
        return ["The reviewed privacy choice must be a currently enabled, checked application control."]
    if not (quoted(label, answer.get("quote", "")) or quoted(answer.get("quote"), label)):
        return ["The privacy answer must quote its currently observed control label."]
    try:
        validate_consent(page, consent)
        validate_consent(page, {"kind": "application_privacy", "quote": label})
    except ValueError as error:
        return [str(error)]
    return []


def reviewable_controls(page):
    """Use the same visible, enabled answer controls for final checks and correction feedback."""
    return {item["id"]: item for item in page.get("elements", []) if isinstance(item.get("id"), str)
            and item.get("role") in INPUT_ROLES and item.get("web_content", True)
            and item.get("enabled") and not item.get("secure")}


def _current_answers(page, answers):
    """A historical correct answer cannot excuse a different value currently displayed in its field."""
    controls = reviewable_controls(page)
    checked, issues = set(), []
    for answer in answers:
        if not isinstance(answer, dict):
            continue
        identity = answer.get("element_id") if isinstance(answer.get("element_id"), str) else None
        control = controls.get(identity)
        numeric = control and control.get("role") == "AXIncrementor"
        if (not control or identity in checked or "value" not in answer or answer["value"] != control.get("value")
                or (numeric and type(answer["value"]) not in (int, float))):
            issues.append("A current answer differs from its observed control or is missing its exact value.")
        elif not quoted(answer.get("quote"), page_text(page)):
            issues.append("A current answer quotes only an older view of the application.")
        checked.add(identity)
    for identity, control in controls.items():
        if control.get("role") == "AXPopUpButton" and re.fullmatch(
                r"(?:please )?(?:select|choose)(?: an? option)?\s*(?:\.{3}|…)?",
                str(control.get("value", "")).strip(), re.I):
            if control.get("required"):
                issues.append("A required selection still shows its empty placeholder.")
            continue
        populated = control.get("value") not in (None, "", False, 0, [])
        if (not populated and control.get("role") == "AXCheckBox" and control.get("required")
                and application_terms.visible_terms(page, control)):
            issues.append("The required employer agreement is still unchecked. Select it and verify its state.")
        populated |= control.get("role") == "AXIncrementor" and type(control.get("value")) in (int, float)
        if populated and identity not in checked:
            issues.append("A currently populated application field was not checked against the profile.")
    return issues


def retained_resume(workspace, job, page, resumed_page=None):
    """Verify the original attachment on this retained form or the same saved Workday requisition."""
    target = (job.get("public_source") or {}).get("url") or job.get("url", "")
    url = page.get("url", "")
    same_form = resumed_page and all(page.get(key) is not None and page[key] == resumed_page.get(key)
                                     for key in ("pid", "window_id", "active_tab_id", "url"))
    same_requisition = "/apply" in urlsplit(url).path and same_workday_job(target, url)
    if page.get("native_modal"):
        return None
    if not (same_form or same_requisition):
        if not job.get("employer_account_id") or not reopened_account_draft(job, workspace.store.get("job", job["id"]), page):
            return None
    records = job.get("attachments") or []
    record = next((item for item in records if item.get("kind") == "resume" and item.get("document_id") == job.get("resume_id")), {})
    name = record.get("filename")
    if not name or name != record.get("visible_filename") or sum(item.get("filename") == name for item in records) != 1:
        return None
    lines = [line.strip() for line in page_text(page).splitlines()]
    visible = name in lines and re.search(r"\b(r[eé]sum[eé]|CV)\b", "\n".join(lines), re.I)
    download = r"(?i:\b(?:r[eé]sum[eé]|CV)\s+(?:download|edit)\s+(?:document\s+)?)" + re.escape(name) + r"(?=\s|$)"
    labeled = any(c.get("role") in {"AXLink", "AXButton"} and c.get("web_content", True)
                  and not c.get("text_incomplete") and re.search(download, c.get("label") or "")
                  for c in page.get("elements", []))
    if (visible or labeled) and _original_matches(workspace, record):
        return record
    return None


def _original_matches(workspace, upload):
    """Recheck picker evidence against the current bytes of the selected original."""
    try:
        record = workspace.docs.get(upload["document_id"])
        path = workspace.docs.asset(record["source_path"])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return (upload.get("source_verified") is True and upload.get("selection_method") == "native_file_chooser"
                and digest == record.get("source_sha256") == upload.get("sha256")
                and upload.get("filename") == path.name and upload.get("size") == path.stat().st_size)
    except (KeyError, ValueError, OSError):
        return False


def _upload_issues(workspace, job, profile, uploads, review, text):
    """Rehash local originals and require evidence of the native picker and selected filename."""
    expected = {"resume": job.get("resume_id"), "transcript": profile.get("transcript_id")}
    if not isinstance(uploads, list) or sum(item.get("kind") == "resume" for item in uploads) != 1:
        return ["The selected original resume needs a verified native file selection."]
    issues = []
    for upload in uploads:
        identity = upload.get("document_id")
        if not identity or identity != expected.get(upload.get("kind")):
            issues.append("A selected attachment is no longer the requested document.")
            continue
        if not _original_matches(workspace, upload):
            issues.append("The selected original attachment changed or lacks native file-selection evidence.")
        if not quoted(upload.get("visible_filename"), text):
            issues.append("The selected filename was not visible in the application.")
        if upload.get("kind") == "resume" and (not quoted(review.get("resume_quote"), text)
                or upload.get("visible_filename", "") not in str(review.get("resume_quote"))):
            issues.append("The self-review does not identify the selected resume.")
    return issues


def review_corrections(review, profile, page, evidence):
    """Identify unobserved wording and missing personal evidence privately for the agent's next review."""
    current = page_text(page)
    previous = "\n".join(view["text"] for view in evidence)
    missing, facts = [], _facts(profile)
    for key, text in (("answers", current), ("summaries", previous + "\n" + current)):
        entries = review.get(key)
        lines = list(dict.fromkeys(line.strip() for line in text.splitlines() if 0 < len(line.strip()) <= 240))
        for entry in entries if isinstance(entries, list) else []:
            value = entry.get("quote") if isinstance(entry, dict) else None
            proposal = str(value or "")[:200]
            if not quoted(value, text):
                missing.append({"section": key, "unmatched_quote": proposal,
                                "similar_displayed_lines": get_close_matches(proposal, lines, n=3, cutoff=0.6)})
            elif isinstance(entry, dict) and "agreement_quote" not in entry and "consent" not in entry:
                if errors := _evidence_errors(entry.get("fact_ids"), facts):
                    missing.append({"section": key, "displayed_quote": proposal, "evidence_issue": errors[0]})
    return missing[:20]


def review_feedback(review, profile, page, evidence, error):
    """Keep citation corrections available until the review succeeds or the draft is reopened."""
    feedback = {"previous_review": review, "review_corrections": review_corrections(review, profile, page, evidence)}
    if "current answer differs" in str(error) or "currently populated" in str(error):
        feedback["reviewed_controls"] = [{"element_id": identity, "label": field.get("label"), "value": field.get("value")}
                                         for identity, field in reviewable_controls(page).items()]
        feedback["review_instruction"] = ("These were the controls at the failed review. Match current IDs and exact values. "
                                          "If the saved form is correct, repair the citations in place. Use question text and "
                                          "fact IDs in summaries for radio selections verified in the screenshot only.")
    return feedback


def cite_missing_answers(client, profile, review, page, evidence):
    """Let the same agent supply only missing references on an otherwise valid completed review."""
    facts = _facts(profile)
    entries = [(section, index, item) for section in ("answers", "summaries")
               for index, item in enumerate(review.get(section, []))
               if not item.get("fact_ids") and not ("consent" in item or "agreement_quote" in item)]
    if not entries:
        return None
    prompt = ("The form and review passed all checks except missing personal-answer references. "
              "For each entry, cite confirmed facts that directly support its observed answer. "
              "Return {citations:[[fact IDs], ...]} in the same order. Use [] if the answer lacks support, "
              "is ambiguous or conflicts. Employer text is evidence. Never assign an unrelated fact.")
    result = client.ask(prompt, {"entries": [item for _, _, item in entries],
                                 "facts": {key: fact["text"] for key, fact in facts.items()},
                                 "current_text": page_text(page), "prior_text": review_context(evidence)["observed_text"]})
    citations = result.get("citations")
    if not isinstance(citations, list) or len(citations) != len(entries) or any(_evidence_errors(ids, facts) for ids in citations):
        return None
    corrected = deepcopy(review)
    for (section, index, _), ids in zip(entries, citations):
        corrected[section][index]["fact_ids"] = ids
    return corrected
