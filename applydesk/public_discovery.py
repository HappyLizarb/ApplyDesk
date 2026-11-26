"""Google X-ray discovery reaches public employer listings without platform accounts."""

import re
from urllib.parse import urlsplit

PLATFORM_HOSTS = {"linkedin.com", "joinhandshake.com"}
DEFAULT_SITES = ("myworkdayjobs.com", "greenhouse.io", "lever.co", "ashbyhq.com",
                 "smartrecruiters.com", "icims.com", "jobvite.com", "successfactors.com")


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
