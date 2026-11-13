"""Read public web pages and copy observed job details into the local tracker."""

import hashlib
import tempfile
import uuid
from urllib.parse import urlencode, urlsplit

from .agent_contract import string
from .application_session import ApplicationSession
from .discovery import canonical_url, page_problem
from .profile import public_job_url
from .storage import now

SEARCH_ENGINES = {"google": "https://www.google.com/search?", "bing": "https://www.bing.com/search?",
                  "duckduckgo": "https://duckduckgo.com/?"}


class WebResearch:
    """Give each chat turn browser captures it can read and save in discovery order."""

    def __init__(self, engine, session_factory=ApplicationSession):
        self.engine, self.w = engine, engine.w
        self.session_factory = session_factory
        self.captures = {}
        self.owner = None

    def _binding(self):
        """Connect page results to the human request that opened them."""
        self.engine.check_active()
        message = (self.engine.active or {}).get("message")
        if not message or message.get("role") != "user":
            raise ValueError("Ask in chat which jobs or pages to research.")
        return (message["id"], self.engine.history.version, self.engine.history.since,
                self.engine.accounts.generation, self.engine.accounts.state()["active"])

    def search(self, query, engine):
        """Open a normal search-results page using job keywords chosen by the agent."""
        query = string(query, 1000)
        if engine not in SEARCH_ENGINES:
            raise ValueError("Choose Google, Bing, or DuckDuckGo, or open another search website with read_page.")
        result = self.read_page(SEARCH_ENGINES[engine] + urlencode({"q": query}))
        return {**result, "query": query, "search_engine": engine}

    def read_page(self, url):
        """Read rendered page text and links in an isolated, cancellable browser session."""
        owner = self._binding()
        target = public_job_url(string(url, 8000))
        with tempfile.TemporaryDirectory(prefix="applydesk-web-") as directory:
            session = self.session_factory(self.engine, directory, headless=True, research=True)
            try:
                page = session.call("open", {"url": target})
            finally:
                session.close()
        if self._binding() != owner:
            raise InterruptedError("The chat changed while the page was loading.")
        return self._capture(page, owner)

    def _capture(self, page, owner):
        """Number visible text lines so saving a description copies bytes instead of rewriting it."""
        url = public_job_url(page["url"])
        text = string(page.get("text", ""), 60000, empty=True)
        lines = text.splitlines()
        if self.owner != owner:
            self.captures.clear()
            self.owner = owner
        identity = uuid.uuid4().hex
        capture = {"id": identity, "url": url, "title": page.get("title", ""), "text": text,
                   "lines": lines, "captured_at": now(), "problem": page_problem(page),
                   "truncated": bool(page.get("text_truncated")), "owner": owner}
        self.captures[identity] = capture
        return {"capture_id": identity, "url": url, "title": capture["title"], "line_count": len(lines),
                "text": "\n".join(f"{index}: {line}" for index, line in enumerate(lines, 1)),
                "links": self._links(page), "truncated": capture["truncated"], "page_issue": capture["problem"]}

    def _links(self, page):
        """Skip malformed or non-web destinations without discarding readable page content."""
        links = []
        for row in page.get("links", []):
            value = row.get("href", row.get("url", "")) if isinstance(row, dict) else None
            if not isinstance(value, str):
                continue
            try:
                parsed = urlsplit(value)
                if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
                    links.append({"text": row.get("text", ""), "url": value})
            except ValueError:
                continue
        return links

    def save_job(self, data):
        """Copy the selected description span and observed identity into one tracker row."""
        owner = self._binding()
        capture = self.captures.get(data["capture_id"])
        if not capture or capture["owner"] != owner:
            raise ValueError("Read this job page again to use its current capture.")
        if capture["problem"] or capture["truncated"]:
            raise ValueError(capture["problem"] or "Open the individual listing to capture its complete description.")
        source = self._source(capture, data)
        with self.w.lock, self.w.store.lock:
            if self._binding() != owner:
                raise InterruptedError("The chat changed before the job was saved.")
            previous = self._duplicate(source["url"])
            if previous:
                return {"status": "excluded" if previous.get("removed_at") else "existing", "job_id": previous["id"]}
            job = self.w.store.add_job(source["url"])
            job = self.w.store.job(job["id"], company=source["company"], title=source["title"],
                                   details=source["description"], details_origin="captured", public_source=source,
                                   qualification_status="unverified", progress="Saved from the web")
            return {"status": "added", "job_id": job["id"], "company": job["company"], "title": job["title"]}

    def _source(self, capture, data):
        """Keep exact page wording for identity and the selected complete description."""
        first, last = data["first_line"], data["last_line"]
        if type(first) is not int or type(last) is not int or not 1 <= first <= last <= len(capture["lines"]):
            raise ValueError("Choose the first and last numbered lines of the complete job description.")
        company, title = string(data["company"], 500), string(data["title"], 500)
        visible = " ".join(capture["text"].split()).casefold()
        if any(" ".join(value.split()).casefold() not in visible for value in (company, title)):
            raise ValueError("Copy the company and role title as they appear on this page.")
        description = "\n".join(capture["lines"][first - 1:last]).strip()
        if len(description) < 150:
            raise ValueError("Select the complete job description, including its requirements.")
        return {"url": capture["url"], "company": company, "title": title, "description": description,
                "description_sha256": hashlib.sha256(description.encode()).hexdigest(),
                "captured_at": capture["captured_at"], "complete": True}

    def _duplicate(self, url):
        """Keep removed listings excluded and recognize connected-source employer links."""
        key = canonical_url(url)
        for job in self.w.store.all("job"):
            urls = [job["url"], (job.get("recommendation_source") or {}).get("employer_url"),
                    (job.get("public_source") or {}).get("url")]
            if any(canonical_url(value) == key for value in urls if value):
                return job
        return None
