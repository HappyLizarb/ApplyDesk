"""Keep public job captures tied to the observed employer, role and destination."""

import hashlib
import re
from urllib.parse import urlsplit

from .discovery import canonical_url, page_problem
from .recommendation_checks import normalized


def validate_public_source(source):
    """Validate stored capture provenance before using it to prepare an application."""
    fields = ("url", "company", "title", "description", "description_sha256", "captured_at")
    if not isinstance(source, dict) or source.get("complete") is not True:
        raise ValueError("Read the complete public job listing before preparing it.")
    if any(not isinstance(source.get(key), str) or not source[key].strip() for key in fields):
        raise ValueError("The public listing is missing its company, role or source capture.")
    parsed = urlsplit(source["url"])
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("The public listing needs its observed HTTP or HTTPS URL.")
    digest = hashlib.sha256(source["description"].encode()).hexdigest()
    if len(source["description"]) > 120000 or digest != source["description_sha256"]:
        raise ValueError("The saved public description changed. Read the listing again.")
    return source


def validate_public_page(source, page, listing=True):
    """Read fresh requirements and establish the same employer and role before form entry."""
    validate_public_source(source)
    actual = page.get("url", "")
    parsed = urlsplit(actual)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("The job page did not open a public HTTP or HTTPS destination.")
    text = normalized(page.get("title", "") + "\n" + page.get("text", ""))
    if any(not re.search(r"(?<!\w)" + re.escape(normalized(source[key])) + r"(?!\w)", text) for key in ("company", "title")):
        raise ValueError("The page does not identify the saved company and job title.")
    if listing:
        if canonical_url(actual) != canonical_url(source["url"]):
            raise ValueError("The public listing moved to a different URL. Read that listing before preparing it.")
        problem = page_problem(page)
        if problem or page.get("text_truncated") or page.get("truncated"):
            raise ValueError(problem or "Read the complete job description before preparing it.")
    return {**page, "public_source": source}


def validate_public_analysis(source, analysis):
    """Keep model interpretation from replacing the captured employer or role."""
    validate_public_source(source)
    for key in ("company", "title"):
        if normalized(analysis.get(key)) != normalized(source[key]):
            raise ValueError("The analyzed " + key + " does not match the saved public listing.")
