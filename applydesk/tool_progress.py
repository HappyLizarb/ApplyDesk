"""Reuse page reads and detect tool sequences that return no new information."""

import json
from urllib.parse import urldefrag

from .auto_start import usage_limited


class ToolProgress:
    """Track progress within one chat turn, without limiting useful research steps."""

    def __init__(self, call):
        self.call = call
        self.pages, self.seen = {}, set()
        self.repeats = 0

    def execute(self, item):
        """Repeated reads reuse the exact capture; changed results count as progress."""
        key = self.key(item)
        cached = item.get("name") in {"read_page", "web_search", "find_jobs"}
        if cached and key in self.pages:
            result = self.pages[key]
        else:
            try:
                result = self.call(item)
            except InterruptedError:
                raise
            except Exception as exc:
                if usage_limited(exc):
                    raise
                result = {"error": str(exc)[:1000]}
            if cached:
                self.pages[key] = result
            elif item.get("name") == "refresh_page":
                self.pages[self.key({**item, "name": "read_page"})] = result
        evidence = (key, json.dumps(result, sort_keys=True, ensure_ascii=False))
        self.repeats = self.repeats + 1 if evidence in self.seen else 0
        self.seen.add(evidence)
        if self.repeats and isinstance(result, dict):
            return {**result, "progress_note": "This operation returned the same information. Use a different query or source, or report the blocker."}
        return result

    def key(self, item):
        """Ignore argument ordering and URL fragments when identifying identical reads."""
        try:
            arguments = json.loads(item.get("arguments", "{}"))
            if item.get("name") == "read_page" and isinstance(arguments.get("url"), str):
                arguments["url"] = urldefrag(arguments["url"])[0]
            if item.get("name") in {"web_search", "find_jobs"} and isinstance(arguments.get("query"), str):
                arguments["query"] = " ".join(arguments["query"].split())
        except (ValueError, AttributeError, TypeError):
            arguments = item.get("arguments")
        return json.dumps([item.get("namespace", "applydesk"), item.get("name"), arguments], sort_keys=True)
