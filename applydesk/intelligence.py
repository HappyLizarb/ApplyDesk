"""Evidence-bound AI tasks with deterministic checks before any caller can act."""

import json
import re
from collections import Counter

from . import ai_prompts as prompts


PROFESSIONAL_KINDS = {"employment", "experience", "professional_experience",
                      "industry", "industries", "industry_years", "role"}


def _facts(profile):
    """Only uniquely identified, explicitly confirmed facts can support claims."""
    if profile.get("confirmed") is not True:
        return {}
    facts = [fact for fact in profile.get("facts", []) if isinstance(fact, dict)
             and isinstance(fact.get("id"), str) and fact["id"]
             and isinstance(fact.get("text"), str) and fact["text"].strip()
             and fact.get("confirmed") is True]
    counts = Counter(fact["id"] for fact in facts)
    return {fact["id"]: fact for fact in facts if counts[fact["id"]] == 1}


def _evidence_errors(ids, facts):
    """A claim must cite at least one known confirmed fact, without exceptions."""
    if not isinstance(ids, list) or not ids:
        return ["No confirmed supporting evidence."]
    if any(not isinstance(item, str) or item not in facts for item in ids):
        return ["Evidence contains unknown, duplicated-profile, or unconfirmed fact IDs."]
    return []


def _review(result):
    """A review passes only when its explicit decision and issue list agree."""
    issues = result.get("issues")
    if not isinstance(issues, list) or any(not isinstance(x, str) for x in issues):
        return {"passed": False, "issues": ["Reviewer returned an invalid issue list."]}
    issues = [issue for issue in issues if issue.strip()]
    passed = result.get("passed") is True and not issues
    if not passed and not issues:
        issues = ["Reviewer did not explicitly pass this check."]
    return {"passed": passed, "issues": issues}


def extract_profile(client, text) -> dict:
    """Import explicit source information as an editable draft, never as evidence."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("The imported document contains no readable text.")
    result = client.ask(prompts.EXTRACT, {"source_text": text})
    result.setdefault("languages", [])
    fields = ("education", "experiences", "projects", "skills", "languages", "industries", "websites")
    if not isinstance(result.get("identity"), dict):
        raise ValueError("Profile extraction returned no identity object.")
    for field in fields:
        if not isinstance(result.get(field), list):
            raise ValueError(f"Profile extraction returned an invalid {field} list.")
    # Sensitive answers must be supplied by the applicant in the profile editor.
    result.update(confirmed=False, revision=0, facts=[], authorizations=[])
    result["preferences"] = {"levels": [], "disability": "unknown", "veteran": "unknown"}
    return result


def analyze_job(client, profile, page) -> dict:
    """Extract job requirements; qualification and independent audit remain separate."""
    result = client.ask(prompts.ANALYZE, {"profile": profile, "page": page})
    for key in ("company", "title", "details", "country", "category"):
        if not isinstance(result.get(key), str):
            raise ValueError(f"Job analysis returned an invalid {key}.")
    for key in ("required", "preferred"):
        if not isinstance(result.get(key), list):
            raise ValueError(f"Job analysis returned an invalid {key} list.")
    if not isinstance(result.get("cover_letter_required"), bool):
        raise ValueError("Cover-letter requirement must be explicitly classified.")
    result["source_text"] = page if isinstance(page, str) else page.get("text", "")
    return result


def _requirement_errors(requirement, facts):
    """Validate evidence identity and prevent borrowing coursework for work years."""
    if not isinstance(requirement, dict) or not isinstance(requirement.get("text"), str):
        return ["A required qualification is missing or malformed."]
    label = requirement["text"].strip()
    if not label or requirement.get("met") is not True:
        return [f"Required qualification lacks a confirmed match: {label or 'unknown'}"]
    ids = requirement.get("evidence_ids")
    errors = _evidence_errors(ids, facts)
    if not errors and re.search(r"\b(years?|yrs?)\b", label, re.I) and re.search(
        r"\b(experience|professional|industry|commercial|work)\b", label, re.I
    ):
        if any(facts[item].get("kind") not in PROFESSIONAL_KINDS for item in ids):
            errors.append("Professional years must cite only confirmed professional experience.")
    return [f"{label}: {error}" for error in errors]


def validate_eligibility(analysis, profile) -> dict:
    """Fail closed on unknown mandatory requirements; semantic support needs audit."""
    reasons = []
    facts = _facts(profile)
    if profile.get("confirmed") is not True:
        reasons.append("Confirm the profile before checking eligibility.")
    required = analysis.get("required")
    if analysis.get("requirements_complete") is not True:
        reasons.append("The complete set of mandatory qualifications is not confirmed.")
    if not isinstance(required, list):
        reasons.append("Required qualifications are unknown.")
    elif not required:
        quote, source = analysis.get("no_required_quote", ""), analysis.get("source_text", "")
        explicit = isinstance(quote, str) and quote and isinstance(source, str) and quote in source
        explicit = explicit and re.search(r"\bno (?:minimum )?qualifications (?:are )?required\b", quote, re.I)
        if not explicit:
            reasons.append("No mandatory qualifications were identified; the job needs clarification.")
    else:
        for requirement in required:
            reasons.extend(_requirement_errors(requirement, facts))
    return {"eligible": not reasons, "reasons": reasons}


def audit_eligibility(client, profile, page, analysis) -> dict:
    """Independently check omitted requirements and the meaning of cited evidence."""
    baseline = validate_eligibility(analysis, profile)
    if not baseline["eligible"]:
        return {"passed": False, "issues": baseline["reasons"]}
    result = client.ask(prompts.AUDIT_ELIGIBILITY,
                        {"profile": profile, "page": page, "prior_analysis": analysis})
    result["source_text"] = page if isinstance(page, str) else page.get("text", "")
    checks = validate_eligibility(result, profile)
    review = _review(result)
    review["issues"].extend(checks["reasons"])
    if isinstance(page, dict) and page.get("recommendation_source"):
        from .recommendation_checks import process_issues
        review["issues"].extend(process_issues(result, page["recommendation_source"], result["source_text"]))
    review["passed"] = review["passed"] and checks["eligible"]
    review["passed"] = review["passed"] and not review["issues"]
    return review


def _validate_edits(result, profile, template):
    """Keep model changes inside existing template slots with confirmed provenance."""
    blocks = template.get("blocks", [])
    known = {block["id"] for block in blocks if isinstance(block, dict)
             and "id" in block and block.get("editable", True)}
    edits, issues = result.get("edits"), result.get("issues", [])
    if not isinstance(edits, list) or not isinstance(issues, list):
        raise ValueError("Document response must contain edits and issues lists.")
    if any(not isinstance(issue, str) for issue in issues):
        raise ValueError("Document issues must be readable strings.")
    seen, facts = set(), _facts(profile)
    for edit in edits:
        if not isinstance(edit, dict) or not isinstance(edit.get("id"), str):
            raise ValueError("Document edit has no valid block ID.")
        block_id = edit["id"]
        if block_id not in known or block_id in seen:
            raise ValueError("Document edit targets an unknown or repeated template block.")
        if set(edit) != {"id", "text", "fact_ids"} or not isinstance(edit["text"], str):
            raise ValueError("Document edits may change text only, with evidence IDs.")
        errors = _evidence_errors(edit["fact_ids"], facts)
        if errors:
            raise ValueError(f"Document block {block_id}: {errors[0]}")
        seen.add(block_id)
    return {"edits": edits, "issues": issues}


def generate_document(client, profile, template: dict, instruction: str) -> dict:
    """Generate text replacements for a reusable resume or required cover letter."""
    facts = _facts(profile)
    if not facts:
        raise ValueError("Confirm supporting profile facts before generating documents.")
    result = client.ask(prompts.DOCUMENT, {"facts": list(facts.values()),
                        "template": template, "instruction": instruction})
    return _validate_edits(result, profile, template)


def chat_document(client, profile, template: dict, instruction: str, history=None) -> dict:
    """Apply an explicit editor request under the same template and evidence rules."""
    if not _facts(profile):
        raise ValueError("Confirm supporting profile facts before editing documents.")
    result = client.ask(prompts.DOCUMENT, {"facts": list(_facts(profile).values()),
                        "template": template, "instruction": instruction,
                        "history": history or []})
    return _validate_edits(result, profile, template)


def audit_document(client, profile, document) -> dict:
    """Audit every populated block, including unsupported untouched template claims."""
    blocks = document.get("blocks", [])
    blocks = [block for block in blocks if isinstance(block, dict)
              and isinstance(block.get("text"), str) and block["text"].strip()]
    ids = [block.get("id") for block in blocks]
    if not ids or any(not isinstance(item, str) for item in ids) or len(set(ids)) != len(ids):
        return {"passed": False, "issues": ["Complete document text with unique block IDs is required."]}
    facts = _facts(profile)
    if not facts:
        return {"passed": False, "issues": ["Confirmed profile evidence is required."]}
    result = client.ask(prompts.AUDIT_DOCUMENT, {"facts": list(facts.values()),
                        "document": document})
    review, checks = _review(result), result.get("block_checks", [])
    checked = [item.get("id") for item in checks if isinstance(item, dict)] if isinstance(checks, list) else []
    if len(checked) != len(ids) or any(not isinstance(item, str) for item in checked) or set(checked) != set(ids):
        review["issues"].append("The auditor did not verify every document block exactly once.")
    if isinstance(checks, list):
        for item in checks:
            if not isinstance(item, dict) or not isinstance(item.get("personal_claims"), bool):
                review["issues"].append("The auditor returned an incomplete block check.")
            elif item["personal_claims"]:
                review["issues"].extend(_evidence_errors(item.get("fact_ids"), facts))
    review["passed"] = review["passed"] and not review["issues"]
    return review


def _field_errors(profile, snapshot, expected):
    """Compare actual values exactly; semantic equivalence cannot erase mismatches."""
    fields = snapshot.get("fields", [])
    counts = Counter(field.get("id") for field in fields if isinstance(field, dict)
                     and isinstance(field.get("id"), str))
    actual = {field["id"]: field for field in fields if isinstance(field, dict)
              and isinstance(field.get("id"), str) and counts[field["id"]] == 1}
    facts, issues = _facts(profile), []
    for item in expected:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            issues.append("An expected field has an invalid identity.")
            continue
        field_id = item["id"]
        if field_id not in actual:
            issues.append(f"Field {field_id} is missing or ambiguous on the live page.")
        elif "value" not in item or "value" not in actual[field_id] or json.dumps(
            actual[field_id]["value"], sort_keys=True
        ) != json.dumps(item["value"], sort_keys=True):
            issues.append(f"Field {field_id} does not match the intended value.")
        issues.extend(f"Field {field_id}: {error}" for error in
                      _evidence_errors(item.get("fact_ids"), facts))
    return issues


def audit_fields(client, profile, snapshot, expected) -> dict:
    """Check live values first, then independently audit wording and evidence."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("fields"), list):
        return {"passed": False, "issues": ["The live form snapshot is unavailable."]}
    if not isinstance(expected, list) or not expected:
        return {"passed": False, "issues": ["No expected field evidence was supplied."]}
    issues = _field_errors(profile, snapshot, expected)
    if issues:
        return {"passed": False, "issues": issues}
    return _review(client.ask(prompts.AUDIT_FIELDS,
                   {"profile": profile, "snapshot": snapshot, "expected": expected}))


def review_format(client, template_images, rendered_images) -> dict:
    """Compare rendered pages without granting any authority over factual content."""
    if not template_images or not rendered_images:
        return {"passed": False, "issues": ["Both template and output page images are required."]}
    result = client.ask(prompts.FORMAT, {"template_pages": len(template_images),
                        "rendered_pages": len(rendered_images)},
                        images=list(template_images) + list(rendered_images))
    return _review(result)


def review_layout(client, images) -> dict:
    """Check an uploaded resume's readability without a template or content changes."""
    if not images:
        return {"passed": False, "issues": ["Resume page images are required."]}
    return _review(client.ask(prompts.LAYOUT, {"pages": len(images)}, images=images))
