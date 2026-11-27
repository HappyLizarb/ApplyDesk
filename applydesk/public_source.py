"""Keep public job captures tied to the observed employer, role and destination."""

import hashlib
import re
from urllib.parse import parse_qs, urlsplit

from .discovery import canonical_url, page_problem
from .native_page import incomplete_capture_message
from .recommendation_source import normalized, same_workday_application, same_workday_job, validate_source


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
    # Workday removes the job heading on its signup screen but retains the verified requisition in the URL.
    if (not listing and page.get("resumed_form") and page.get("native") and text
            and same_workday_application(source["url"], actual)
            and any(c.get("secure") and c.get("enabled") and c.get("web_content", True) for c in page.get("elements", []))):
        return {**page, "public_source": source}
    if any(not re.search(r"(?<!\w)" + re.escape(normalized(source[key])) + r"(?!\w)", text) for key in ("company", "title")):
        raise ValueError("The page does not identify the saved company and job title.")
    if listing:
        if canonical_url(actual) != canonical_url(source["url"]):
            raise ValueError("The public listing moved to a different URL. Read that listing before preparing it.")
        problem = page_problem(page)
        if problem or page.get("text_truncated") or page.get("truncated"):
            raise ValueError(problem or incomplete_capture_message(page, "Read the complete job description before preparing it."))
    return {**page, "public_source": source}


def title_requisition(source, title):
    """Identify a form tab when its visible job title and requisition match the captured listing."""
    role = normalized(source.get("title"))
    if not role:
        return ""
    match = re.search(r"(?<!\w)" + re.escape(role) + r"\s*\(([a-z0-9-]{2,40})\)$", normalized(title))
    if not match:
        return ""
    identifier = match.group(1)
    listed = r"\b(?:job\s+)?req(?:uisition)?\.?(?:\s+(?:id|number|no\.?))?\s*[:#]?\s*"
    return identifier if re.search(listed + re.escape(identifier) + r"(?![\w-])", source.get("description", ""), re.I) else ""


def saved_form_tab(job, page, owned):
    """Identify one inactive Chrome tab to inspect before granting access to its form."""
    if page.get("native_modal") or not (job.get("attachments") and job.get("account_origin")):
        return None
    source = job.get("public_source") or {}
    matches = [tab["id"] for tab in page.get("tabs", []) if tab.get("id") not in owned
               and title_requisition(source, tab.get("label"))]
    return matches[0] if len(matches) == 1 and matches[0] != page.get("active_tab_id") else None


def matches_saved_requisition_form(job, page):
    """Identify a restored employer form using the exact saved URL, account origin and visible requisition."""
    url = page.get("document_url", "")
    if not url or url != job.get("live_url") or not job.get("attachments"):
        return False
    parsed = urlsplit(url)
    if parsed.scheme != "https" or job.get("account_origin") != f"https://{parsed.netloc}":
        return False
    identifiers = [value for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
                   if re.fullmatch(r"(?:career_)?(?:job_)?(?:req|requisition)_?id", key, re.I) for value in values]
    source = job.get("public_source") or {}
    try:
        validate_public_source(source)
        # The active tab still identifies the requisition when its employer header has scrolled away.
        active = page.get("active_tab_id")
        title = next((t.get("label") for t in page.get("tabs", []) if active and t.get("id") == active), None)
        from_title = title_requisition(source, title or page.get("title"))
        if from_title:
            return not identifiers or [from_title] == [value.lower() for value in identifiers]
        if not identifiers:
            return False
        validate_public_page(source, {**page, "url": url}, listing=False)
    except ValueError:
        return False
    if len(identifiers) != 1 or not re.fullmatch(r"[A-Za-z0-9-]{2,40}", identifiers[0]):
        return False
    requisition = r"(?<![\w-])" + re.escape(identifiers[0]) + r"(?![\w-])"
    listed = r"\b(?:job\s+)?(?:req(?:uisition)?\.?)(?:\s+(?:ID|number|no\.?))?\s*[:#]?\s*" + requisition
    visible = page.get("title", "") + "\n" + page.get("text", "")
    return bool(re.search(listed, source["description"], re.I) and re.search(requisition, visible, re.I))


def validate_public_analysis(source, analysis):
    """Keep model interpretation from replacing the captured employer or role."""
    validate_public_source(source)
    for key in ("company", "title"):
        if normalized(analysis.get(key)) != normalized(source[key]):
            raise ValueError("The analyzed " + key + " does not match the saved public listing.")


def reopened_account_draft(job, stored, page):
    """Recover the form recorded during this run after the applicant signed back into the same requisition."""
    if not stored or page.get("native_modal") or page.get("reading_notice"):
        return None
    keys = ("id", "employer_account_id", "account_origin", "resume_id", "public_source", "attachments")
    if any(not job.get(key) or job[key] != stored.get(key) for key in keys):
        return None
    rebound = {**job, "live_url": stored.get("live_url")}
    return rebound if matches_saved_requisition_form(rebound, page) else None


def validate_alternate_listing(saved, source, page_text):
    """Bind an observed Workday listing to the original employer, title and exact requisition."""
    validate_source(saved)
    if any(normalized(saved[key]) != normalized(source[key]) for key in ("company", "title")):
        raise ValueError("The employer and title must match the saved job.")
    url = urlsplit(source["url"])
    if not same_workday_job(source["url"], source["url"]) or "apply" in url.path.split("/"):
        raise ValueError("Read the employer’s Workday job description to verify the alternate link.")
    # A Workday tenant can identify the original employer by its full name or initials.
    words = re.findall(r"[a-z0-9]+", saved["company"].lower())
    tenant = url.hostname.split(".")[0]
    original = urlsplit(saved.get("employer_url") or "")
    names = {"".join(words), "".join(word[0] for word in words)}
    requisition = url.path.rstrip("/").rsplit("_", 1)[-1]
    exact = r"(?<![a-zA-Z0-9])" + re.escape(requisition) + r"(?![a-zA-Z0-9])"
    known = (saved.get("employer_job_id") == requisition or
             (requisition.startswith("JR") and re.search(exact, saved["description"]))
             or requisition in original.path.strip("/").split("/"))
    if tenant not in names or not known or not re.search(exact, page_text):
        raise ValueError("The employer tenant and requisition must match the saved job and the visible listing.")
