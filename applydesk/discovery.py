"""Shared URL normalization and visible listing checks for public job research."""

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

TRACKING = {"source", "ref", "referrer", "gh_src", "lever-origin", "lever-source[]", "trackingid"}


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
