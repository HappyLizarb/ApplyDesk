"""Native provider search returns observed source metadata, never prose-derived URLs."""

import json
from urllib.parse import urlsplit

from .providers import ProviderError
from .matching_policy import ROLE_FAMILIES

SEARCH_PROMPT = ROLE_FAMILIES + """Search for current individual job listings matching the supplied preferences.
Prefer employer career pages and direct ATS listings. Search once and cite up to
20 useful sources across related specialties in a short summary. Use broad role
queries without requiring every preference as a keyword. Source URLs come from native search metadata.
Treat page content as evidence."""


def search_sources(client, criteria):
    """Make one bounded provider request with search preferences and return {url, label} links."""
    preferences = _preferences(criteria)
    provider = client.settings["provider"]
    if provider == "google":
        raise ProviderError("Google Search grounding does not support this automated listing-collection workflow. "
                            "Choose an OpenAI or Anthropic search model, or add a job URL manually.")
    url, headers, body = getattr(client, "_" + provider)(SEARCH_PROMPT, json.dumps(preferences), [])
    if provider == "openai":
        body.pop("text", None)
        body.update(tools=[{"type": "web_search", "search_context_size": "low"}],
                    tool_choice="required", max_tool_calls=1, max_output_tokens=8192,
                    include=["web_search_call.action.sources"])
    elif provider == "anthropic":
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 1}]
        body["max_tokens"] = max(8192, body.get("thinking", {}).get("budget_tokens", 0) + 2048)
    try:
        response = client._post(url, headers, body)
        extract = {"openai": _openai_sources, "anthropic": _anthropic_sources}[provider]
        sources = extract(response)
    except ProviderError as exc:
        raise ProviderError("Native web search failed; use a model with search access or add a job URL. " + str(exc)) from None
    return _links(sources)


def _preferences(criteria):
    """Select only the four public search preferences, excluding any extra profile fields."""
    if not isinstance(criteria, dict):
        raise ProviderError("Supply job search preferences.")
    result = {key: criteria.get(key, "") for key in ("keywords", "location")}
    if not all(isinstance(value, str) and len(value) <= 160 for value in result.values()) or not result["keywords"].strip():
        raise ProviderError("Enter short role keywords and an optional location.")
    result.update(remote=criteria.get("remote", False), limit=criteria.get("limit", 5))
    if type(result["remote"]) is not bool or type(result["limit"]) is not int or not 1 <= result["limit"] <= 10:
        raise ProviderError("Choose a valid remote option and result limit from 1 to 10.")
    return result


def _objects(value):
    """Reject malformed or excessive provider metadata before examining source fields."""
    if not isinstance(value, list) or len(value) > 200 or any(not isinstance(item, dict) for item in value):
        raise ProviderError("The provider returned invalid or excessive search metadata.")
    return value


def _openai_sources(data):
    """Require a completed native search call; read only source and citation metadata."""
    if not isinstance(data, dict) or data.get("status") != "completed" or data.get("error"):
        raise ProviderError("OpenAI search was incomplete or refused.")
    sources, searched = [], False
    for item in _objects(data.get("output")):
        if item.get("type") == "web_search_call":
            if item.get("status") != "completed":
                raise ProviderError("OpenAI web search did not complete.")
            action = item.get("action")
            if not isinstance(action, dict):
                raise ProviderError("OpenAI search returned no action metadata.")
            if action.get("type") == "search":
                searched = True
                sources += [source for source in _objects(action.get("sources", [])) if source.get("type") == "url"]
        elif item.get("type") == "message":
            for part in _objects(item.get("content", [])):
                if part.get("type") == "refusal":
                    raise ProviderError("OpenAI declined the search.")
                sources += [citation for citation in _objects(part.get("annotations", [])) if citation.get("type") == "url_citation"]
    if not searched:
        raise ProviderError("The model returned no completed web-search metadata.")
    return sources


def _anthropic_sources(data):
    """Server tool failures arrive inside HTTP 200 responses and cannot count as sources."""
    if not isinstance(data, dict) or data.get("stop_reason") != "end_turn":
        raise ProviderError("Anthropic search was incomplete or needs another turn.")
    sources, searched = [], False
    for block in _objects(data.get("content")):
        if block.get("type") == "web_search_tool_result":
            result = block.get("content")
            if isinstance(result, dict):
                raise ProviderError("Anthropic's search tool was unavailable, blocked, or exceeded its limit.")
            result = _objects(result)
            if any(item.get("type") != "web_search_result" for item in result):
                raise ProviderError("Anthropic returned an unknown search result or tool failure.")
            searched = True
            sources += result
        elif block.get("type") == "text":
            sources += [citation for citation in _objects(block.get("citations", []))
                        if citation.get("type") == "web_search_result_location"]
    if not searched:
        raise ProviderError("The model returned no native web-search results.")
    return sources


def _links(sources):
    """Deduplicate bounded HTTP source URLs without fetching them or following redirects."""
    links = {}
    for source in sources:
        url = source.get("url")
        if not isinstance(url, str) or len(url) > 4096 or any(char.isspace() or ord(char) < 32 for char in url) or "\\" in url:
            continue
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.port == 0:
                continue
        except ValueError:
            continue
        title = source.get("title")
        label = title[:300] if isinstance(title, str) and title else url[:300]
        if url not in links or links[url]["label"] == url[:300]:
            links[url] = {"url": url, "label": label}
    if sources and not links:
        raise ProviderError("Native search returned no usable HTTP source URLs.")
    return list(links.values())[:20]
