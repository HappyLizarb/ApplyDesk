"""Bind recommendation screening to fresh scoped captures and explicit process rules."""

import hashlib
import re
from urllib.parse import urlparse

from .discovery import canonical_url, page_problem

PLATFORMS = {"linkedin": "linkedin.com", "handshake": "joinhandshake.com"}
IDENTITY_FIELDS = ("platform", "job_id", "company", "title")


def normalized(value):
    """Compare visible identities without weakening punctuation or wording."""
    return " ".join(value.split()).casefold() if isinstance(value, str) else ""


def validate_source(source, expected=None, require_complete=True):
    """Only the controlled reader may attest completeness; hashes detect later edits."""
    if not isinstance(source, dict) or (require_complete and source.get("complete") is not True):
        raise ValueError("Capture the complete selected job description before preparing this recommendation.")
    fields = (*IDENTITY_FIELDS, "source_url", "description", "captured_at", "description_sha256")
    optional = {"company", "title", "description"} if not require_complete else set()
    if any(not isinstance(source.get(key), str) or (key not in optional and not source[key].strip()) for key in fields):
        raise ValueError("The recommendation capture is missing source identity or provenance.")
    issues = source.get("issues", [])
    if not isinstance(issues, list) or any(not isinstance(item, str) for item in issues) or (require_complete and any(item.strip() for item in issues)):
        raise ValueError("The recommendation capture has unresolved source issues. Review it manually.")
    parsed = urlparse(source["source_url"])
    domain = PLATFORMS.get(source["platform"])
    if not domain or parsed.scheme != "https" or parsed.username or parsed.password or not parsed.hostname:
        raise ValueError("The recommendation source must be an authenticated platform job page.")
    if parsed.hostname != domain and not parsed.hostname.endswith("." + domain):
        raise ValueError("The recommendation URL does not match its platform.")
    if len(source["description"]) > 120000:
        raise ValueError("The complete description exceeds the supported capture size; review it manually.")
    digest = hashlib.sha256(source["description"].encode()).hexdigest()
    if digest != source["description_sha256"]:
        raise ValueError("The recommendation description changed after capture. Read the job again.")
    known = [key for key in IDENTITY_FIELDS if key in {"platform", "job_id"} or normalized((expected or {}).get(key))]
    if expected and any(normalized(source[key]) != normalized(expected.get(key)) for key in known):
        raise ValueError("The fresh recommendation is a different job. Review its identity before continuing.")
    return source


def scoped_page(source, expected):
    """Expose only a freshly captured selected job, never the surrounding recommendation feed."""
    validate_source(source, expected)
    return {"url": source["source_url"], "title": source["company"] + " — " + source["title"],
            "text": source["description"], "fields": [], "buttons": [], "links": [],
            "recommendation_source": source}


def process_issues(analysis, source, evidence_text=None):
    """Unknown process prerequisites pause; this does not claim to prove arbitrary rules."""
    issues = []
    for key in ("company", "title"):
        if normalized(analysis.get(key)) != normalized(source[key]):
            issues.append("The analyzed " + key + " does not match the captured recommendation.")
    for key in ("process_complete", "identity_consistent", "employment_type_consistent"):
        if analysis.get(key) is not True:
            issues.append("Recommendation " + key.replace("_", " ") + " is not established.")
    consistency = analysis.get("consistency_issues")
    if not isinstance(consistency, list) or any(not isinstance(item, str) for item in consistency):
        issues.append("The recommendation consistency review is missing or malformed.")
    else:
        issues.extend(item for item in consistency if item.strip())
    rules = analysis.get("process_restrictions")
    if not isinstance(rules, list) or len(rules) > 100:
        return issues + ["Application process restrictions are unknown."]
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("kind") not in {"application_limit", "availability_statement", "other"}:
            issues.append("An application process restriction is malformed.")
            continue
        quote, text = rule.get("quote"), rule.get("text")
        if not isinstance(quote, str) or not quote.strip() or quote not in (evidence_text or source["description"]) or not isinstance(text, str) or not text.strip():
            issues.append("An application process restriction has no exact supporting source quote.")
        else:
            issues.append("Manual verification required for application prerequisite: " + text[:500])
    return list(dict.fromkeys(issues))


def employer_target(source):
    """Require a fresh observed employer link rather than navigating a platform feed."""
    target = source.get("employer_url")
    parsed = urlparse(target) if isinstance(target, str) else None
    if not parsed or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("No verified employer application link was captured. Review this recommendation manually.")
    if any(parsed.hostname == host or parsed.hostname.endswith("." + host) for host in PLATFORMS.values()):
        raise ValueError("The captured link still points to a recommendation platform. Use a verified employer link.")
    return target


def validate_native_page(source, page):
    """An in-platform form must retain the captured listing identity and visible employer/role."""
    from .recommendation_source import source_identity
    source_identity(source["platform"], page.get("url", ""), source["job_id"])
    content = normalized(page.get("text", ""))
    if not all(normalized(source[key]) in content for key in ("company", "title")):
        raise ValueError("The platform form does not identify the captured company and job title.")
    if page.get("warnings"):
        raise ValueError("The platform form requires manual inspection: " + "; ".join(page["warnings"]))
    return page


def validate_employer_page(source, page):
    """A redirect must preserve an observed employer requisition ID and visible identity."""
    target = employer_target(source)
    text = normalized(page.get("title", "") + "\n" + page.get("text", ""))
    if any(normalized(source[key]) not in text for key in ("company", "title")):
        raise ValueError("The employer page does not establish the recommendation's exact company and title.")
    actual = page.get("url", "")
    parsed = urlparse(actual)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("The employer destination is not a verified HTTPS job page.")
    if canonical_url(actual) != canonical_url(target):
        identity = source.get("employer_job_id")
        if not isinstance(identity, str) or not identity.strip() or len(identity) > 200:
            raise ValueError("The employer link redirected without a verified requisition identity. Review manually.")
        pattern = r"(?<![\w-])" + re.escape(identity) + r"(?![\w-])"
        if not re.search(pattern, target) or not re.search(pattern, actual + "\n" + page.get("text", "")):
            raise ValueError("The employer redirect did not preserve the observed requisition identity.")
    return page


def employer_evidence(source, page):
    """Screen the union of source and employer requirements without changing capture provenance."""
    validate_employer_page(source, page)
    problem = page_problem(page)
    if problem:
        raise ValueError(problem + " The employer requirements need manual review.")
    combined = source["description"] + "\n\nEmployer job page:\n" + page.get("title", "") + "\n" + page.get("text", "")
    if len(combined) > 120000 or page.get("text_truncated") or page.get("truncated"):
        raise ValueError("The complete employer description could not be included. Review its requirements manually.")
    return {**page, "text": combined, "recommendation_source": source}
