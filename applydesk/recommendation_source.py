"""Validate bounded source captures independently from worker lifecycle and storage."""

from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import re
from urllib.parse import parse_qs, urlsplit

from .storage import now

PLATFORMS = {"linkedin", "handshake"}


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

def employer_url(value):
    """Validate link structure locally; the application browser rechecks public DNS."""
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or len(value) > 4096 or any(char.isspace() for char in value) or "\\" in value:
        raise ValueError("The employer link is invalid.")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.port == 0:
        raise ValueError("The employer link must be a public website URL.")
    host = parsed.hostname
    if host == "localhost" or host.endswith((".localhost", ".local", ".linkedin.com", ".joinhandshake.com")) or host in {"linkedin.com", "joinhandshake.com"}:
        raise ValueError("No external employer link was established.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return value
    if not address.is_global:
        raise ValueError("The employer link points to a private network.")
    return value

def normalize_job(platform, item):
    """Retain bounded job facts only; private feed snapshots cannot enter a report."""
    if not isinstance(item, dict):
        raise ValueError("The source returned an invalid job record.")
    item = {**{key: "" for key in ("title", "company", "location", "text")}, **item}
    url = source_identity(platform, item.get("url"), item.get("id"))
    for key, limit in (("title", 500), ("company", 500), ("location", 500), ("text", 50000)):
        if not isinstance(item.get(key), str) or len(item[key]) > limit:
            raise ValueError("The source returned invalid or excessive job text.")
    issues = item.get("issues")
    if type(item.get("complete")) is not bool or not isinstance(issues, list) or len(issues) > 20 or any(not isinstance(value, str) or len(value) > 600 for value in issues):
        raise ValueError("The source returned invalid completeness evidence.")
    issues = issues.copy()
    if not item["title"].strip() or not item["company"].strip() or len(item["text"].strip()) < 150:
        issues.append("The listing description or identity is incomplete.")
    try:
        employer = employer_url(item.get("employer_url"))
    except ValueError as error:
        employer = ""
        issues.append(str(error))
    item = {**item, **{key: item[key].strip() for key in ("company", "title", "location", "text")}}
    description = f"Company: {item['company']}\nTitle: {item['title']}\nLocation: {item['location']}\n\n{item['text']}"
    result = {"platform": platform, "source_url": url, "job_id": item["id"],
              **{key: item[key].strip() for key in ("company", "title", "location")}, "description": description,
              "captured_at": now(), "complete": item["complete"] and not issues,
              "description_sha256": hashlib.sha256(description.encode()).hexdigest(), "employer_url": employer,
              "issues": list(dict.fromkeys(issues))}
    if item.get("employer_job_id") is not None:
        if not isinstance(item["employer_job_id"], str) or not 1 <= len(item["employer_job_id"]) <= 200:
            raise ValueError("The employer job identifier is invalid.")
        result["employer_job_id"] = item["employer_job_id"]
    return result

def known_identity_matches(prior, fresh):
    """Unknown prior facts may be filled; known company/title wording cannot silently change."""
    return all(isinstance(prior.get(key), str) and (not prior[key].strip()
               or " ".join(prior[key].split()).casefold() == " ".join(fresh[key].split()).casefold())
               for key in ("company", "title"))

def validate_saved(source):
    """Stored captures must retain their identity, timestamp, and exact content digest."""
    if not isinstance(source, dict):
        raise ValueError("The saved recommendation source is invalid.")
    description = source.get("description")
    if not isinstance(description, str) or len(description) > 52000 or hashlib.sha256(description.encode()).hexdigest() != source.get("description_sha256"):
        raise ValueError("The saved recommendation description changed. Import the listing again.")
    try:
        captured = datetime.fromisoformat(source["captured_at"])
        if captured.tzinfo is None or captured > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError("The recommendation capture timestamp is invalid.") from None
    if any(not isinstance(source.get(key), str) or len(source[key]) > 500 for key in ("company", "title", "location")):
        raise ValueError("The saved recommendation identity is invalid.")
    prefix = f"Company: {source['company']}\nTitle: {source['title']}\nLocation: {source['location']}\n\n"
    if not description.startswith(prefix) or type(source.get("complete")) is not bool or not isinstance(source.get("issues"), list):
        raise ValueError("The saved recommendation mapping is invalid.")
    employer_url(source.get("employer_url"))
    return source_identity(source.get("platform"), source.get("source_url"), source.get("job_id"))

