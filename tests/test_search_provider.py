"""Native search tests use controlled tool metadata, never live provider calls."""

import copy
import json
import unittest
from unittest.mock import patch

from applydesk.providers import ProviderClient, ProviderError
from applydesk.search_provider import search_sources

CRITERIA = {"keywords": "software internship", "location": "Boston", "remote": False, "limit": 5}
URL = "https://jobs.example.test/jobs/123"
REPLIES = {
    "openai": {"status": "completed", "output": [{"type": "web_search_call", "status": "completed",
                  "action": {"type": "search", "sources": [{"type": "url", "url": URL}]}}]},
    "anthropic": {"stop_reason": "end_turn", "content": [{"type": "web_search_tool_result",
                     "content": [{"type": "web_search_result", "url": URL, "title": "Software internship"}]}]},
}


def client(provider):
    """Known fixture models exercise each adapter with an inert synthetic key."""
    model = {"openai": "gpt-5.4", "anthropic": "claude-haiku-4-5", "google": "gemini-3.8-flash"}[provider]
    return ProviderClient({"provider": provider, "model": model, "thinking": "high"}, "fictional-key")


class NativeSearchTests(unittest.TestCase):
    """Only native search metadata can supply discovery links."""

    def test_each_provider_makes_one_request_and_returns_observed_sources(self):
        for provider, response in REPLIES.items():
            instance = client(provider)
            with self.subTest(provider=provider), patch.object(instance, "_post", return_value=response) as post:
                links = search_sources(instance, CRITERIA)
            post.assert_called_once()
            self.assertEqual(links[0]["url"], URL)
            self.assertEqual(set(links[0]), {"url", "label"})

    def test_search_enables_native_tools_without_json_only_output(self):
        for provider in REPLIES:
            instance = client(provider)
            with patch.object(instance, "_post", return_value=REPLIES[provider]) as post:
                search_sources(instance, CRITERIA)
            body = post.call_args.args[2]
            if provider == "openai":
                self.assertEqual(body["tools"][0]["type"], "web_search")
                self.assertEqual(body["include"], ["web_search_call.action.sources"])
                self.assertEqual(body["max_tool_calls"], 1)
                self.assertEqual(body["max_output_tokens"], 8192)
                self.assertEqual(body["tool_choice"], "required")
                self.assertNotIn("text", body)
            elif provider == "anthropic":
                self.assertEqual(body["tools"], [{"type": "web_search_20250305", "name": "web_search", "max_uses": 1}])
                self.assertGreater(body["max_tokens"], body["thinking"]["budget_tokens"])
                self.assertEqual(body["max_tokens"], 10240)

    def test_extra_profile_and_credential_fields_never_enter_search_payload(self):
        criteria = {**CRITERIA, "profile": {"name": "Private Name"}, "password": "private-password", "add_to_tracker": True}
        instance = client("openai")
        with patch.object(instance, "_post", return_value=REPLIES["openai"]) as post:
            search_sources(instance, criteria)
        body = post.call_args.args[2]
        self.assertEqual(json.loads(body["input"][0]["content"][0]["text"]), CRITERIA)
        self.assertNotIn("Private Name", json.dumps(body))
        self.assertNotIn("private-password", json.dumps(body))

    def test_openai_citations_add_titles_but_prose_urls_are_ignored(self):
        response = copy.deepcopy(REPLIES["openai"])
        response["output"].append({"type": "message", "content": [{"type": "output_text", "text": "https://invented.test/jobs/4",
                                    "annotations": [{"type": "url_citation", "url": URL, "title": "Observed title"}]}]})
        instance = client("openai")
        with patch.object(instance, "_post", return_value=response):
            self.assertEqual(search_sources(instance, CRITERIA), [{"url": URL, "label": "Observed title"}])

    def test_anthropic_citations_are_native_metadata_but_text_is_not(self):
        response = copy.deepcopy(REPLIES["anthropic"])
        response["content"].append({"type": "text", "text": "https://invented.test/jobs/4", "citations": [
            {"type": "web_search_result_location", "url": URL + "-other", "title": "Other observed result"}]})
        instance = client("anthropic")
        with patch.object(instance, "_post", return_value=response):
            links = search_sources(instance, CRITERIA)
        self.assertEqual([link["url"] for link in links], [URL, URL + "-other"])

    def test_google_search_is_blocked_before_building_or_billing_any_request(self):
        instance = client("google")
        with patch.object(instance, "_google") as builder, patch.object(instance, "_post") as post:
            with self.assertRaisesRegex(ProviderError, "Choose an OpenAI or Anthropic search model"):
                search_sources(instance, CRITERIA)
        builder.assert_not_called()
        post.assert_not_called()

    def test_prose_without_completed_native_metadata_never_supplies_links(self):
        replies = {"openai": {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": URL}]}]},
                   "anthropic": {"stop_reason": "end_turn", "content": [{"type": "text", "text": URL}]}}
        for provider, response in replies.items():
            instance = client(provider)
            with self.subTest(provider=provider), patch.object(instance, "_post", return_value=response) as post:
                with self.assertRaisesRegex(ProviderError, "Native web search failed"):
                    search_sources(instance, CRITERIA)
            post.assert_called_once()

    def test_native_search_errors_and_partial_responses_fail(self):
        replies = [("openai", {"status": "incomplete"}),
                   ("openai", {"status": "completed", "output": [{"type": "web_search_call", "status": "failed"}]}),
                   ("anthropic", {"stop_reason": "pause_turn", "content": []}),
                   ("anthropic", {"stop_reason": "end_turn", "content": [{"type": "web_search_tool_result", "content": {
                       "type": "web_search_tool_result_error", "error_code": "unavailable"}}]})]
        for provider, response in replies:
            instance = client(provider)
            with self.subTest(provider=provider, response=response), patch.object(instance, "_post", return_value=response):
                with self.assertRaises(ProviderError):
                    search_sources(instance, CRITERIA)

    def test_empty_completed_searches_return_empty_results(self):
        replies = copy.deepcopy(REPLIES)
        replies["openai"]["output"][0]["action"]["sources"] = []
        replies["anthropic"]["content"][0]["content"] = []
        for provider, response in replies.items():
            instance = client(provider)
            with patch.object(instance, "_post", return_value=response):
                self.assertEqual(search_sources(instance, CRITERIA), [])

    def test_output_links_are_bounded_and_unsafe_urls_are_not_returned(self):
        response = copy.deepcopy(REPLIES["openai"])
        response["output"][0]["action"]["sources"] = [{"type": "url", "url": URL + str(index), "title": "x" * 500} for index in range(25)]
        response["output"][0]["action"]["sources"] += [{"type": "url", "url": value} for value in (
            "javascript:alert(1)", "file:///private/data", "https://user:secret@host.test/jobs/1", "https://host.test:bad/jobs/1")]
        instance = client("openai")
        with patch.object(instance, "_post", return_value=response):
            links = search_sources(instance, CRITERIA)
        self.assertEqual(len(links), 20)
        self.assertTrue(all(link["url"].startswith(URL) and len(link["label"]) <= 300 for link in links))

    def test_invalid_preferences_fail_without_a_request(self):
        instance = client("openai")
        for criteria in ({}, {**CRITERIA, "keywords": ["role"]}, {**CRITERIA, "limit": True}, {**CRITERIA, "remote": "yes"}):
            with patch.object(instance, "_post") as post, self.assertRaises(ProviderError):
                search_sources(instance, criteria)
            post.assert_not_called()

    def test_provider_rejection_has_no_retry_or_ungrounded_fallback(self):
        instance = client("openai")
        with patch.object(instance, "_post", side_effect=ProviderError("openai HTTP 400: Check model settings.")) as post:
            with self.assertRaisesRegex(ProviderError, "model with search access"):
                search_sources(instance, CRITERIA)
        post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
