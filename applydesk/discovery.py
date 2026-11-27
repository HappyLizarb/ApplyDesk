"""Shared URL normalization and visible listing checks for public job research."""

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunparse

TRACKING = {"source", "ref", "referrer", "gh_src", "lever-origin", "lever-source[]", "trackingid"}


PLATFORM_HOSTS = {"linkedin.com", "joinhandshake.com"}
DEFAULT_SITES = ("myworkdayjobs.com", "greenhouse.io", "lever.co", "ashbyhq.com",
                 "smartrecruiters.com", "icims.com", "jobvite.com", "successfactors.com")


def canonical_url(url):
    """Deduplicate tracking aliases while preserving query parameters that identify jobs."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    host = "job-boards.greenhouse.io" if host == "boards.greenhouse.io" else host
    path = parsed.path.rstrip("/")
    if host in {"jobs.lever.co", "jobs.ashbyhq.com"}:
        path = re.sub(r"/(apply|application)$", "", path)
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in TRACKING
             and not (key == "gh_jid" and path.endswith("/" + value))]
    netloc = host + (":" + str(parsed.port) if parsed.port and parsed.port not in {80, 443} else "")
    return urlunparse((parsed.scheme.lower(), netloc, path, "", urlencode(sorted(query)), ""))


def page_problem(snapshot):
    """Recognize obvious expired listings and challenges without trying to bypass them."""
    text = (snapshot.get("title", "") + " " + snapshot.get("text", "")[:5000]).lower()
    if any(phrase in text for phrase in ("unusual traffic", "verify you are human", "verifying you're not a bot", "please complete the following challenge", "access denied", "just a moment")):
        return "The site presented a challenge or blocked automated access."
    if any(phrase in text for phrase in ("job is no longer available", "job has expired", "job is closed", "position has been filled", "no longer accepting applications", "job not found", "this position is no longer available")):
        return "The listing is closed or unavailable."
    return "" if len(snapshot.get("text", "").strip()) >= 150 else "The page did not provide enough readable listing content."


def google_xray_query(query):
    """Honor an explicit employer site; otherwise search a broad set of public recruiting systems."""
    if not isinstance(query, str) or not query.strip() or len(query) > 1000:
        raise ValueError("Enter a short role and location query for public employer listings.")
    query = " ".join(query.split())
    sites = re.findall(r"(?<![-\w])site:([^\s)]+)", query, re.I)
    if any(_platform(site.strip('"').split('/')[0].lstrip('*.')) for site in sites):
        raise ValueError("Search the employer's public careers site instead of LinkedIn or Handshake.")
    if not sites:
        query += " (" + " OR ".join("site:" + host for host in DEFAULT_SITES) + ")"
    return query + " -site:linkedin.com -site:joinhandshake.com"


def require_public_listing(url):
    """Retired platform sessions cannot be reopened through the public reader or application path."""
    parsed = urlsplit(url)
    if _platform(parsed.hostname or ""):
        raise ValueError("Find and read this job on its public employer careers site; job-platform connections are no longer used.")
    return url


def _platform(host):
    """Compare complete host boundaries so unrelated employer domains remain available."""
    normalized = host.lower().rstrip(".")
    return any(normalized == platform or normalized.endswith("." + platform) for platform in PLATFORM_HOSTS)
