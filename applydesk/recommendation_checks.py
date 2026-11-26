"""Bind recommendation screening to fresh scoped captures and explicit process rules."""

import hashlib
import re
from urllib.parse import parse_qs, urlparse

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


def process_issues(analysis, source, evidence_text=None, *, check_on_form=False):
    """Unknown process prerequisites pause; this does not claim to prove arbitrary rules."""
    issues = []
    for key in ("company", "title"):
        if normalized(analysis.get(key)) != normalized(source[key]):
            issues.append("The analyzed " + key + " does not match the captured recommendation.")
    for key in ("process_complete", "identity_consistent", "employment_type_consistent"):
        # A verified listing can lead to its form before account and form steps are complete.
        if key == "process_complete" and check_on_form and analysis.get(key) is False:
            continue
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
    """Require a source-observed employer link rather than navigating a platform feed."""
    target = source.get("employer_url")
    parsed = urlparse(target) if isinstance(target, str) else None
    if not parsed or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("No verified employer application link was captured. Review this recommendation manually.")
    if any(parsed.hostname == host or parsed.hostname.endswith("." + host) for host in PLATFORMS.values()):
        raise ValueError("The captured link still points to a recommendation platform. Use a verified employer link.")
    return target


def validate_native_page(source, page, *, agent_driven=False):
    """Check source identity before exploration; mere embedded content is not an action blocker."""
    from .recommendation_source import source_identity
    source_identity(source["platform"], page.get("url", ""), source["job_id"])
    content = normalized(page.get("text", ""))
    if not all(normalized(source[key]) in content for key in ("company", "title")):
        raise ValueError("The platform form does not identify the captured company and job title.")
    structural = {"Embedded frames require manual inspection.", "Shadow DOM controls require manual inspection."}
    warnings = [warning for warning in page.get("warnings", []) if not (agent_driven and warning in structural)]
    if warnings:
        raise ValueError("The platform form requires manual inspection: " + "; ".join(warnings))
    return page


def retain_employer_link(prior, fresh):
    """An omitted fresh link does not erase a destination observed on the exact same listing."""
    from .recommendation_source import validate_saved
    if fresh.get("employer_url") or not prior.get("employer_url"):
        return fresh
    if validate_saved(prior) != validate_saved(fresh):
        raise ValueError("The prior employer link belongs to a different source listing.")
    if any(not normalized(prior.get(key)) for key in IDENTITY_FIELDS):
        return fresh
    if any(normalized(prior[key]) != normalized(fresh.get(key)) for key in IDENTITY_FIELDS):
        raise ValueError("The prior employer link belongs to a different or unidentified source listing.")
    target = employer_target(prior)
    proof = prior.get("employer_url_provenance") or {
        **{key: prior[key] for key in (*IDENTITY_FIELDS, "source_url", "captured_at", "description_sha256")},
        "url": target}
    if (not isinstance(proof, dict) or proof.get("url") != target or proof.get("source_url") != prior["source_url"]
            or any(normalized(proof.get(key)) != normalized(prior[key]) for key in IDENTITY_FIELDS)
            or not isinstance(proof.get("captured_at"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", str(proof.get("description_sha256", "")))):
        raise ValueError("The saved employer link has inconsistent capture provenance.")
    result = {**fresh, "employer_url": target, "employer_url_provenance": dict(proof)}
    identity = prior.get("employer_job_id")
    if isinstance(identity, str) and 1 <= len(identity) <= 200:
        result["employer_job_id"] = identity
    return result


def validate_employer_page(source, page):
    """A redirect must preserve an observed employer requisition ID and visible identity."""
    target = employer_target(source)
    if verified_authentication_entry(source, page, target):
        return page
    actual = page.get("url", "")
    infineon = same_infineon_job(target, actual)
    text = normalized(page.get("title", "") + "\n" + page.get("text", ""))
    company = normalized(source["company"])
    brand = infineon and company == "infineon technologies" and re.search(r"\binfineon\b", text)
    if normalized(source["title"]) not in text or (company not in text and not brand):
        raise ValueError("The employer page does not establish the recommendation's exact company and title.")
    parsed = urlparse(actual)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("The employer destination is not a verified HTTPS job page.")
    if canonical_url(actual) != canonical_url(target) and not infineon and not observed_application_frame(target, page) and not same_workday_job(target, actual):
        identity = source.get("employer_job_id")
        if not isinstance(identity, str) or not identity.strip() or len(identity) > 200:
            raise ValueError("The employer link redirected without a verified requisition identity. Review manually.")
        pattern = r"(?<![\w-])" + re.escape(identity) + r"(?![\w-])"
        if not re.search(pattern, target) or not re.search(pattern, actual + "\n" + page.get("text", "")):
            raise ValueError("The employer redirect did not preserve the observed requisition identity.")
    return page


def same_infineon_job(target, actual):
    """Infineon's observed Apply route moves its exact numeric job ID into the pid query."""
    original, current = urlparse(target), urlparse(actual)
    if any(url.scheme != "https" or url.hostname != "jobs.infineon.com" or url.username or url.password
           or url.port for url in (original, current)):
        return False
    posting = re.fullmatch(r"/careers/job/(\d{1,22})/?", original.path)
    if not posting or current.path != "/careers/apply":
        return False
    original_query, current_query = (parse_qs(url.query, keep_blank_values=True) for url in (original, current))
    if any(query.get("domain") not in (None, ["infineon.com"]) for query in (original_query, current_query)):
        return False
    identity = [posting.group(1)]
    return original_query.get("pid") in (None, identity) and current_query.get("pid") == identity


def same_successfactors_requisition(entry, meta):
    """Match the account form to the employer-issued requisition for the selected posting."""
    requisition = entry.get("requisition")
    return (entry.get("tenant") == meta.get("tenant") == "supermicro"
            and isinstance(requisition, str) and re.fullmatch(r"[0-9]+", requisition) is not None
            and meta.get("requisition") == requisition)


def verified_authentication_entry(source, page, target):
    """A browser-verified handoff identifies only an account screen, never a ready application."""
    entry = page.get("authentication_entry") or {}
    meta = page.get("successfactors_auth") or {}
    actual = urlparse(page.get("url", ""))
    return bool(entry.get("provider") == "successfactors" and meta.get("kind") in {"login", "create_account"}
                and actual.scheme == "https" and actual.netloc == "career4.successfactors.com"
                and actual.path in {"/career", "/careers"} and meta.get("form_id")
                and same_successfactors_requisition(entry, meta)
                and canonical_url(entry.get("source_url", "")) == canonical_url(target)
                and all(normalized(entry.get(key)) == normalized(source[key]) for key in ("company", "title"))
                and any(field.get("type") == "password" and field.get("form_id") == meta["form_id"]
                        for field in page.get("fields", [])))


def same_workday_job(target, actual):
    """Workday's locale and apply steps retain the exact employer, career site and requisition."""
    original, current = urlparse(target), urlparse(actual)
    if original.scheme != "https" or current.scheme != "https" or original.hostname != current.hostname:
        return False
    if not re.fullmatch(r"[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com", original.hostname or ""):
        return False
    if any((url.username or url.password or url.port) for url in (original, current)):
        return False
    # Workday can omit or reformat the location segment when reopening the same requisition.
    pattern = (r"/(?:[a-z]{2}-[A-Z]{2}/)?([^/]+)/job/(?:[^/]+/)?([^/]+_(?:JR)?\d+(?:-\d+)?)"
               r"(?:/apply(?:/applyManually|/autofillWithResume|/useMyLastApplication)?)?/?")
    paths = [re.fullmatch(pattern, url.path) for url in (original, current)]
    return all(paths) and paths[0].groups() == paths[1].groups()


def same_workday_application(target, actual):
    """A Workday form identifies its tenant, careers site and requisition even after a notice detour."""
    return "apply" in urlparse(actual).path.split("/") and same_workday_job(target, actual)


def observed_application_frame(target, page):
    """Bind a browser-observed Greenhouse frame to its original employer requisition."""
    proof = page.get("application_frame")
    if not isinstance(proof, dict):
        return False
    parent, original = urlparse(proof.get("parent_url", "")), urlparse(target)
    frame = urlparse(page.get("url", ""))
    if parent.scheme != "https" or parent.username or parent.password or parent.port:
        return False
    if (parent.hostname or "").removeprefix("www.") != (original.hostname or "").removeprefix("www."):
        return False
    if parent.path != original.path or parent.query != original.query:
        return False
    identity = proof.get("job_id", "")
    if not isinstance(identity, str) or not re.fullmatch(r"\d+", identity):
        return False
    if parse_qs(parent.query).get("gh_jid") != [identity] or proof.get("frame_url") != page.get("url"):
        return False
    query = parse_qs(frame.query)
    return (frame.scheme == "https" and not frame.username and not frame.password and not frame.port
            and frame.hostname in {"job-boards.greenhouse.io", "boards.greenhouse.io"}
            and frame.path == "/embed/job_app" and query.get("token") == [identity]
            and len(query.get("for", [])) == 1)


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
