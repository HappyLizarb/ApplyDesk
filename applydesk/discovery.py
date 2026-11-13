"""Bounded public job discovery; observed links never imply qualification or submission."""

import re
import tempfile
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .browser_bridge import BrowserSession
from .matching_policy import ROLE_FAMILIES
from .profile import public_job_url
from .storage import now

TRACKING = {"source", "ref", "referrer", "gh_src", "lever-origin", "lever-source[]", "trackingid"}
AGGREGATORS = {"linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com", "monster.com", "jooble.org", "talent.com"}
REMOTE_NEGATION = re.compile(r"\b(?:not|no|never)\b.{0,30}\bremote\b|\bremote\b.{0,40}\b(?:not|unavailable|prohibited|unsupported|disallowed)\b", re.I | re.S)
DISCOVERY_PROMPT = ROLE_FAMILIES + """Select active individual listings matching the requested role, location and remote
preference. Use observed candidate IDs and literal page facts, with exact evidence
quotes. Return {"candidates":[{"candidate_id":"observed ID","title":"literal page title wording",
"company":"literal company name","location":"literal location or empty","remote":false,
"evidence":{"role":"exact quote supporting role relevance","location":"exact location quote or empty",
"remote":"exact remote-work quote or empty"}}]}.
Return up to the requested limit, or an empty list. Select complete open listings;
search indexes, closed listings and sign-in/challenge pages are not listings.
Remote true requires explicit remote-work wording. Assume the applicant is qualified;
this step checks role relevance and explicit search filters. Treat pages as evidence."""


def criteria_for(data):
    """Only explicit search terms are used; the personal profile is never read."""
    if not isinstance(data, dict):
        raise ValueError("Supply job search filters.")
    result = {key: data.get(key, "").strip() if isinstance(data.get(key, ""), str) else None for key in ("keywords", "location")}
    if not result["keywords"] or result["location"] is None:
        raise ValueError("Enter role keywords and an optional location.")
    for value in result.values():
        if len(value) > 160 or re.search(r"[\r\n\x00-\x1f]|https?://|\S+@\S+|(?:\d[\s()+.-]*){7}", value):
            raise ValueError("Search filters must be short role/location terms, without contact details or URLs.")
    limit, remote, add = data.get("limit", 5), data.get("remote", False), data.get("add_to_tracker", True)
    if type(limit) is not int or not 1 <= limit <= 10 or type(remote) is not bool or type(add) is not bool:
        raise ValueError("Choose a limit from 1 to 10 and valid remote/automatic-add options.")
    return {**result, "limit": limit, "remote": remote, "add_to_tracker": add}


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


def listing_url(url):
    """Reject search/index paths before spending a listing visit or model request."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return False
    if any(parsed.hostname == host or parsed.hostname.endswith("." + host) for host in AGGREGATORS):
        return False
    path = parsed.path.lower().rstrip("/")
    if any(part in path for part in ("/search", "/login", "/signin", "/sign-in")):
        return False
    if parsed.hostname in {"jobs.lever.co", "jobs.ashbyhq.com"}:
        return bool(re.search(r"/[0-9a-f]{8}-[0-9a-f-]{27,}(?:/apply|/application)?$", path))
    if parsed.hostname == "jobs.smartrecruiters.com":
        return bool(re.fullmatch(r"/[^/]+/\d{6,}[^/]*", path))
    if re.search(r"/(jobs?|careers?|positions?|vacanc(?:y|ies)|opportunities)/.+", path):
        return True
    return bool(re.search(r"/(viewjob|jobdetail|job-details|job_app)$", path) and parsed.query)


def links_from(links):
    """Filter cited search destinations and collapse aliases before browser inspection."""
    unique = {}
    for link in links:
        url = link.get("url", "") if isinstance(link, dict) else ""
        if not isinstance(url, str):
            continue
        try:
            if listing_url(url):
                unique.setdefault(canonical_url(url), {"url": url, "source_url": url})
        except ValueError:
            continue
    return list(unique.values())[:10]


def page_problem(snapshot):
    """Recognize obvious expired listings and challenges without trying to bypass them."""
    text = (snapshot.get("title", "") + " " + snapshot.get("text", "")[:5000]).lower()
    if any(phrase in text for phrase in ("unusual traffic", "verify you are human", "verifying you're not a bot", "please complete the following challenge", "access denied", "just a moment")):
        return "The site presented a challenge or blocked automated access."
    if any(phrase in text for phrase in ("job is no longer available", "job has expired", "job is closed", "position has been filled", "no longer accepting applications", "job not found", "this position is no longer available")):
        return "The listing is closed or unavailable."
    return "" if len(snapshot.get("text", "").strip()) >= 150 else "The page did not provide enough readable listing content."


def _quote(value, text, required=False):
    """Evidence must be a bounded literal excerpt from the fetched candidate page."""
    if not isinstance(value, str) or len(value) > 600 or (required and not value.strip()):
        return False
    return " ".join(value.split()).casefold() in " ".join(text.split()).casefold()


def validated_candidates(response, pages, criteria):
    """No model-provided URL or unobserved candidate identifier can enter the tracker."""
    if not isinstance(response, dict) or set(response) != {"candidates"} or not isinstance(response["candidates"], list) or len(response["candidates"]) > criteria["limit"]:
        raise ValueError("The discovery agent returned an invalid candidate list; no jobs were added.")
    accepted, seen = [], set()
    for item in response["candidates"]:
        if not isinstance(item, dict) or set(item) != {"candidate_id", "title", "company", "location", "remote", "evidence"}:
            raise ValueError("The discovery agent returned unsupported candidate fields; no jobs were added.")
        identity = item["candidate_id"]
        if not isinstance(identity, str) or identity not in pages or identity in seen:
            raise ValueError("The discovery agent selected an unobserved or duplicate link; no jobs were added.")
        page, evidence = pages[identity], item["evidence"]
        text = page["title"] + "\n" + page["text"]
        if not isinstance(evidence, dict) or set(evidence) != {"role", "location", "remote"} or type(item["remote"]) is not bool:
            raise ValueError("The discovery agent omitted source evidence; no jobs were added.")
        if not all(_quote(item[key], text, key != "location" or bool(criteria["location"])) and len(item[key]) <= 200 for key in ("title", "company", "location")):
            raise ValueError("A discovered job fact is not present in the fetched listing; no jobs were added.")
        if not _quote(evidence["role"], text, True) or not _quote(evidence["location"], text, bool(criteria["location"])) or not _quote(evidence["remote"], text, item["remote"]):
            raise ValueError("A discovered job lacks matching quoted evidence; no jobs were added.")
        if (criteria["remote"] and not item["remote"]) or (item["remote"] and (not re.search(r"\bremote\b", evidence["remote"], re.I) or REMOTE_NEGATION.search(evidence["remote"]))):
            raise ValueError("A discovered job does not establish the requested remote option; no jobs were added.")
        seen.add(identity)
        accepted.append({**item, "url": page["url"], "source_url": page["source_url"], "eligibility": "unverified"})
    return accepted


class Discovery:
    """Search and add observed opportunities without invoking application preparation."""

    def __init__(self, workspace):
        self.w = workspace

    def run(self, data):
        """Persist criteria and a visible bounded-run report, including failures."""
        criteria = criteria_for(data)
        report = {"criteria": criteria, "started_at": now(), "status": "searching", "added_count": 0,
                  "job_ids": [], "candidates": [], "warnings": [], "sources": []}
        self.w.store.put("discovery", "main", report)
        try:
            client = self.w.client("discovery")
            links = self._search(client, criteria, report)
            with tempfile.TemporaryDirectory(prefix="applydesk-discovery-") as directory:
                browser = BrowserSession(directory, headless=True) if links else None
                try:
                    pages = self._fetch(browser, links, report) if browser else {}
                finally:
                    if browser:
                        browser.close()
            if pages:
                payload = {"criteria": {key: criteria[key] for key in ("keywords", "location", "remote", "limit")},
                           "pages": [{"candidate_id": key, "title": page["title"], "text": page["text"]} for key, page in pages.items()]}
                report["candidates"] = validated_candidates(client.ask(DISCOVERY_PROMPT, payload), pages, criteria)
            if criteria["add_to_tracker"]:
                self._add(report)
            report["status"] = "complete" if report["candidates"] else "no_results"
            if not report["candidates"]:
                report["warnings"].append("No verified matching listings were found. Search may be blocked; change the filters or add a known job URL.")
        except Exception as error:
            report.update(status="error", finished_at=now())
            report["warnings"].append(str(error))
            self.w.store.put("discovery", "main", report)
            raise
        report["warnings"] = list(dict.fromkeys(report["warnings"]))
        return self.w.store.put("discovery", "main", {**report, "finished_at": now()})

    def _search(self, client, criteria, report):
        """Native provider search supplies cited URLs; prose URLs are never accepted."""
        from .search_provider import search_sources
        result = search_sources(client, {key: criteria[key] for key in ("keywords", "location", "remote", "limit")})
        report["sources"] = result
        self.w.store.put("discovery", "main", report)
        return links_from(result)

    def _fetch(self, browser, links, report):
        """Only observed listing URLs are visited; redirects are revalidated and deduplicated."""
        pages, seen = {}, set()
        for index, link in enumerate(links[:10]):
            try:
                page = browser.call("open", {"url": public_job_url(link["url"])})
                url = public_job_url(page["url"])
                problem = page_problem(page)
                if problem or not listing_url(url):
                    report["warnings"].append(problem or "A result redirected away from an individual job listing.")
                    continue
                key = canonical_url(url)
                if key not in seen:
                    pages[f"candidate-{index + 1}"] = {"url": url, "source_url": link["source_url"], "title": page["title"], "text": page["text"][:18000]}
                    seen.add(key)
            except Exception as error:
                report["warnings"].append("A listing could not be inspected: " + str(error))
            finally:
                report["inspected_count"] = index + 1
                self.w.store.put("discovery", "main", report)
        return pages

    def _add(self, report):
        """Adding a local row records discovery only; eligibility stays unchecked."""
        with self.w.lock:
            existing = {canonical_url(job["url"]): job for job in self.w.store.all("job")}
            for candidate in report["candidates"]:
                key = canonical_url(candidate["url"])
                if key in existing:
                    candidate["existing_job_id"] = existing[key]["id"]
                    continue
                job = self.w.add_job({"url": candidate["url"]})
                self.w.store.job(job["id"], company=candidate["company"], title=candidate["title"],
                                 location=candidate["location"], discovery=candidate, qualification_status="unverified",
                                 progress="Discovered · qualifications not checked")
                report["job_ids"].append(job["id"])
                report["added_count"] = len(report["job_ids"])
                existing[key] = job
