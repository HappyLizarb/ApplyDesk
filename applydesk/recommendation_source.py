"""Validate bounded source captures independently from worker lifecycle and storage."""

import hashlib
import re
from urllib.parse import parse_qs, urlparse, urlsplit

from .storage import now

PLATFORMS = {"linkedin": "linkedin.com", "handshake": "joinhandshake.com"}
ACTIVE = {"connecting", "waiting_signin", "scanning"}


def source_identity(platform, url, identity):
    """Accept only observed source URL shapes whose numeric ID matches the record."""
    if not isinstance(platform, str) or platform not in PLATFORMS:
        raise ValueError("Choose LinkedIn or Handshake.")
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9]{1,22}", identity):
        raise ValueError("The source job has no valid stable identifier.")
    if not isinstance(url, str) or len(url) > 4096 or any(char.isspace() for char in url) or "\\" in url:
        raise ValueError("The source job URL is invalid.")
    parsed = urlsplit(url)
    host = "www.linkedin.com" if platform == "linkedin" else "app.joinhandshake.com"
    if parsed.scheme != "https" or parsed.hostname != host or parsed.port not in {None, 443} or parsed.username or parsed.password:
        raise ValueError("The job URL does not belong to the selected source.")
    pattern = r"/jobs/view/(?:[^/]+-)?([0-9]{1,22})/?" if platform == "linkedin" else r"/job-search/([0-9]{1,22})/?"
    match = re.fullmatch(pattern, parsed.path)
    observed = match.group(1) if match else None
    if platform == "linkedin" and parsed.path.rstrip("/") == "/jobs/search-results":
        values = parse_qs(parsed.query).get("currentJobId", [])
        observed = values[0] if len(values) == 1 else None
    if observed != identity:
        raise ValueError("The source URL and job identifier disagree.")
    path = f"/jobs/view/{identity}/" if platform == "linkedin" else f"/job-search/{identity}"
    return "https://" + host + path


def normalized(value):
    """Compare visible identities without weakening punctuation or wording."""
    return " ".join(value.split()).casefold() if isinstance(value, str) else ""


def validate_source(source):
    """Read a complete historical record and verify that its captured text is intact."""
    if not isinstance(source, dict) or source.get("complete") is not True:
        raise ValueError("Capture the complete selected job description before preparing this recommendation.")
    fields = ("platform", "job_id", "company", "title", "source_url", "description", "captured_at", "description_sha256")
    if any(not isinstance(source.get(key), str) or not source[key].strip() for key in fields):
        raise ValueError("The recommendation capture is missing source identity or provenance.")
    issues = source.get("issues", [])
    if not isinstance(issues, list) or any(not isinstance(item, str) for item in issues) or any(item.strip() for item in issues):
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
    return source


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


class Recommendations:
    """Expose stored records from earlier platform imports."""

    def __init__(self, workspace):
        self.w = workspace
        for job in self.w.store.all("job"):
            source = job.get("recommendation_source")
            if job["status"] == "new" and isinstance(source, dict) and source.get("complete") is False:
                self.w.store.job(job["id"], status="needs_review")
        for platform in PLATFORMS:
            report = self.w.store.get("recommendation", platform, {})
            if report.get("status") in ACTIVE:
                report.update(status="interrupted", message="The previous import stopped. Use public job search to find new listings.", finished_at=now())
                self.w.store.put("recommendation", platform, report)

    def state(self):
        """Display historic blocked captures truthfully without changing the saved operation records."""
        reports = {}
        for platform in sorted(PLATFORMS):
            report = dict(self.w.store.get("recommendation", platform, {}))
            if report.get("status") == "blocked" and report.get("message") == "Found 0 matching job(s).":
                issues = report.get("issues", [])
                issues = issues if isinstance(issues, list) else []
                report["message"] = next((item.strip() for item in issues if isinstance(item, str) and item.strip()),
                                         "The listings could not be read. Try the search again.")
            reports[platform] = report
        return reports

    def preferences(self):
        """Source selection is explicit and never inferred from past imports or profile facts."""
        preferences = {}
        for platform in sorted(PLATFORMS):
            saved = self.w.store.get("recommendation_preference", platform, {})
            preferences[platform] = {"enabled": isinstance(saved, dict) and saved.get("enabled") is True}
        return preferences
