"""Evidence-bound AI tasks with deterministic checks before any caller can act."""

import re
from collections import Counter

from . import ai_prompts as prompts
from .immigration_eligibility import TOPIC, check_immigration

# Match the complete future review clause, including when it follows a separate license requirement.
FUTURE_VEHICLE_REVIEW = re.compile(
    r"(?:^|(?<=[.!?])\s+|\blicen[cs]e\s+and\s+)"
    r"(as a condition of employment,\s+must pass [\w&.-]+(?:[ -][\w&.-]+){0,3}['’]s motor vehicle records? review)"
    r"(?=\.(?:\s|$)|[ \t]*$)", re.I | re.M)


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


def analyze_job(client, profile, page) -> dict:
    """Extract job requirements once before local validation."""
    evidence = page
    if isinstance(page, dict) and page.get("native"):
        # Screening uses the employer's posting. Other Chrome tabs can belong to unrelated applications.
        keys = ("native", "url", "title", "text", "text_truncated", "public_source", "recommendation_source")
        evidence = {key: page[key] for key in keys if key in page}
    result = client.ask(prompts.ANALYZE, {"profile": profile, "page": evidence})
    for key in ("company", "title", "details", "country", "category"):
        if not isinstance(result.get(key), str):
            raise ValueError(f"Job analysis returned an invalid {key}.")
    for key in ("required", "preferred"):
        if not isinstance(result.get(key), list):
            raise ValueError(f"Job analysis returned an invalid {key} list.")
    if not isinstance(result.get("cover_letter_required"), bool):
        raise ValueError("Cover-letter requirement must be explicitly classified.")
    _repair_citations(client, profile, result["required"])
    result["source_text"] = page if isinstance(page, str) else page.get("text", "")
    return result


def _repair_citations(client, profile, requirements):
    """Give the agent one chance to replace invalid references without changing its eligibility decision."""
    facts = _facts(profile)
    invalid = [row for row in requirements if isinstance(row, dict) and row.get("met") is True
               and row.get("evidence_ids") and _evidence_errors(row["evidence_ids"], facts)]
    if not invalid:
        return
    prompt = prompts.BASE + prompts.QUALIFICATIONS + """
For each quoted requirement, return its directly supporting confirmed profile fact IDs.
Return {citations:[[id,...],...]}, in order. Use [] when the facts do not support it.
Check the kind of experience and any minimum years. A document ID is not a profile fact.
"""
    answer = client.ask(prompt, {"requirements": [row.get("text") for row in invalid],
                                 "facts": list(facts.values())}).get("citations")
    if not isinstance(answer, list) or len(answer) != len(invalid) or any(_evidence_errors(ids, facts) for ids in answer):
        raise ValueError("The agent could not verify its profile citations. This posting needs another review.")
    for row, ids in zip(invalid, answer):
        row["evidence_ids"] = ids


def _requirement_errors(requirement, facts, source):
    """Reject unmet requirements without turning assumed skills into personal facts."""
    if not isinstance(requirement, dict) or not isinstance(requirement.get("text"), str):
        return ["A required qualification is missing or malformed."]
    label = requirement["text"].strip()
    if not label or type(requirement.get("met")) is not bool:
        return ["A required qualification has missing text or an invalid match flag."]
    if not requirement["met"]:
        if not any(match[1] == label.removesuffix(".") for match in FUTURE_VEHICLE_REVIEW.finditer(source)):
            return [f"{label}: A mandatory requirement is unmet or needs confirmation."]
    if not source and TOPIC.search(label):
        return ["An eligibility condition needs its observed source text before it can be checked."]
    if source and label not in source:
        return ["A required qualification does not quote the observed description."]
    ids = requirement.get("evidence_ids")
    if not isinstance(ids, list):
        return ["Qualification evidence IDs must be a list; use an empty list for assumed qualifications."]
    errors = _evidence_errors(ids, facts) if ids else []
    return [f"{label}: {error}" for error in errors]


def validate_eligibility(analysis, profile) -> dict:
    """Honor supported matches while checking complete requirements and supplied evidence."""
    reasons = []
    facts = _facts(profile)
    if not isinstance(analysis, dict):
        return {"eligible": False, "reasons": ["Job analysis is missing or malformed."]}
    if profile.get("confirmed") is not True:
        reasons.append("Confirm the profile before checking eligibility.")
    required = analysis.get("required")
    if analysis.get("requirements_complete") is not True:
        reasons.append("The complete set of mandatory qualifications is not confirmed.")
    if not isinstance(required, list):
        reasons.append("Required qualifications are unknown.")
    else:
        source = analysis.get("source_text", "")
        if not isinstance(source, str):
            reasons.append("The captured job description must be text.")
            source = ""
        for requirement in required:
            reasons.extend(_requirement_errors(requirement, facts, source))
    immigration = check_immigration(profile, analysis.get("source_text", ""), analysis.get("country", ""), required if isinstance(required, list) else [])
    return {"eligible": not reasons and immigration["eligible"], "reasons": reasons + immigration["reasons"],
            "needs_input": immigration["needs_input"]}
