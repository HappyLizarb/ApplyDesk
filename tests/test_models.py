"""Model choices and catalog discovery are verified without keys or live requests."""

import io
import json
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

from applydesk.model_catalog import list_models
from applydesk.providers import MODEL_OPTIONS, ProviderClient, ProviderError, _NoRedirect, validate_settings


def payload(provider, model, thinking="default"):
    """Build a provider request without sending it."""
    client = ProviderClient({"provider": provider, "model": model, "thinking": thinking}, "fictional-key")
    return getattr(client, "_" + provider)("Return JSON", "{}", [])


class ModelSettingsTests(unittest.TestCase):
    """Presets get documented controls; custom names do not inherit those controls."""

    def test_default_omits_reasoning_overrides_for_every_model(self):
        for provider, models in MODEL_OPTIONS.items():
            for model in [*models, "custom-future-model"]:
                with self.subTest(provider=provider, model=model):
                    _, _, body = payload(provider, model)
                    self.assertNotIn("reasoning", body)
                    self.assertNotIn("thinking", body)
                    self.assertNotIn("output_config", body)
                    self.assertNotIn("thinkingConfig", body.get("generationConfig", {}))

    def test_old_saved_presets_keep_their_explicit_and_omitted_defaults(self):
        defaults = {("openai", "gpt-5.4"): "none", ("openai", "gpt-5.4-mini"): "none",
                    ("anthropic", "claude-sonnet-4-6"): "none", ("anthropic", "claude-haiku-4-5"): "none",
                    ("google", "gemini-3.8-flash"): "low"}
        for (provider, model), default in defaults.items():
            settings = {"provider": provider, "model": model}
            self.assertEqual(validate_settings(settings)["thinking"], default)
            for thinking in (default, "low", "medium", "high"):
                explicit = {**settings, "thinking": thinking}
                self.assertEqual(validate_settings(explicit), explicit)

    def test_new_presets_use_their_documented_parameter_names(self):
        self.assertEqual(payload("openai", "gpt-6.1-sol", "max")[2]["reasoning"], {"effort": "max"})
        for model in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"):
            body = payload("anthropic", model, "xhigh")[2]
            self.assertEqual(body["output_config"], {"effort": "xhigh"})
            self.assertNotIn("thinking", body)
        sonnet = payload("anthropic", "claude-sonnet-4-6", "max")[2]
        self.assertEqual(sonnet["thinking"], {"type": "adaptive"})
        haiku = payload("anthropic", "claude-haiku-4-5", "medium")[2]
        self.assertEqual(haiku["thinking"], {"type": "enabled", "budget_tokens": 4096})
        config = payload("google", "gemini-3.6-flash", "minimal")[2]["generationConfig"]
        self.assertEqual(config["thinkingConfig"], {"thinkingLevel": "minimal"})

    def test_unknown_models_accept_only_default_and_preserve_the_id(self):
        for provider in MODEL_OPTIONS:
            settings = {"provider": provider, "model": "my-future-model"}
            self.assertEqual(validate_settings(settings), {**settings, "thinking": "default"})
            for thinking in ("none", "low", "high", "max", None, {}):
                with self.subTest(provider=provider, thinking=thinking), self.assertRaises(ProviderError):
                    validate_settings({**settings, "thinking": thinking})
        for model in ("gpt-6.1-sol", "gpt-6-astra"):
            with self.assertRaises(ProviderError):
                payload("openai", model, "none")

    def test_model_identifiers_cannot_change_request_destinations(self):
        invalid = ["", None, 12, "../model", "https://evil.test/model", "model?key=secret", "model#suffix",
                   "model/other", "models/../model", "model%2Fother", "model\nHeader", " model", "模型", "a" * 201]
        for provider in MODEL_OPTIONS:
            for model in invalid:
                with self.subTest(provider=provider, model=model), self.assertRaises(ProviderError):
                    validate_settings({"provider": provider, "model": model})
        with self.assertRaises(ProviderError):
            payload("google", "model:delete")
        self.assertEqual(payload("openai", "ft:gpt-example:org:custom")[2]["model"], "ft:gpt-example:org:custom")
        url, _, _ = payload("google", "models/gemini-3.8-flash")
        self.assertEqual(url, "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent")

    def test_invalid_providers_and_keys_fail_before_network(self):
        with patch("applydesk.providers.request.build_opener") as opener:
            for provider in ("other", {}, None):
                with self.assertRaises(ProviderError):
                    list_models(provider, "key")
            for key in ("", "key\r\nheader", "key\x00", "é", "a" * 1001):
                with self.assertRaises(ProviderError):
                    list_models("openai", key)
            opener.assert_not_called()


class ModelCatalogTests(unittest.TestCase):
    """Catalogs are bounded GET requests, with independent pagination per provider."""

    def opener(self, *responses):
        """The fake opener returns JSON byte streams and records request metadata."""
        opener = Mock()
        opener.open.side_effect = [io.BytesIO(json.dumps(response).encode()) for response in responses]
        return opener

    def test_openai_listing_is_get_only_sorted_and_keeps_key_in_headers(self):
        opener = self.opener({"data": [{"id": "z-model"}, {"id": "a-model"}, {"id": "z-model"}]})
        with patch("applydesk.providers.request.build_opener", return_value=opener):
            rows = list_models("openai", "fictional-key")
        self.assertEqual(rows, [{"id": "a-model", "name": "a-model"}, {"id": "z-model", "name": "z-model"}])
        req = opener.open.call_args.args[0]
        self.assertEqual(req.full_url, "https://api.openai.com/v1/models")
        self.assertEqual(req.method, "GET")
        self.assertIsNone(req.data)
        self.assertEqual(req.get_header("Authorization"), "Bearer fictional-key")
        self.assertLessEqual(opener.open.call_args.kwargs["timeout"], 15)

    def test_anthropic_pagination_uses_encoded_after_id(self):
        opener = self.opener({"data": [{"id": "claude-example", "display_name": "Example"}],
                              "has_more": True, "last_id": "opaque&cursor"},
                             {"data": [{"id": "claude-next"}], "has_more": False})
        with patch("applydesk.providers.request.build_opener", return_value=opener):
            rows = list_models("anthropic", "fictional-key")
        req = opener.open.call_args.args[0]
        self.assertEqual(parse_qs(urlsplit(req.full_url).query), {"limit": ["100"], "after_id": ["opaque&cursor"]})
        self.assertEqual(req.get_header("X-api-key"), "fictional-key")
        self.assertEqual(req.get_header("Anthropic-version"), "2023-06-01")
        self.assertEqual(rows[0], {"id": "claude-example", "name": "Example"})

    def test_google_filters_generation_method_and_preserves_encoded_cursor(self):
        model = {"name": "models/gemini-example", "displayName": "Example", "supportedGenerationMethods": ["generateContent"]}
        opener = self.opener({"models": [model, {"name": "models/embedding", "supportedGenerationMethods": ["embedContent"]}],
                              "nextPageToken": "token&url=https://evil.test"}, {"models": [], "nextPageToken": ""})
        with patch("applydesk.providers.request.build_opener", return_value=opener):
            rows = list_models("google", "fictional-key")
        req = opener.open.call_args.args[0]
        parsed = urlsplit(req.full_url)
        self.assertEqual(parsed.netloc, "generativelanguage.googleapis.com")
        self.assertEqual(parse_qs(parsed.query), {"pageSize": ["100"], "pageToken": ["token&url=https://evil.test"]})
        self.assertNotIn("fictional-key", req.full_url)
        self.assertEqual(rows, [{"id": "gemini-example", "name": "Example"}])

    def test_page_and_model_limits_never_return_a_silently_incomplete_catalog(self):
        first = {"data": [{"id": "one"}], "has_more": True, "last_id": "one"}
        with patch("applydesk.model_catalog.MAX_PAGES", 1), patch("applydesk.model_catalog._request_json", return_value=first):
            with self.assertRaisesRegex(ProviderError, "page limit"):
                list_models("anthropic", "fictional-key")
        with patch("applydesk.model_catalog.MAX_MODELS", 1), patch("applydesk.model_catalog._request_json", return_value={"data": [{"id": "one"}, {"id": "two"}]}):
            with self.assertRaisesRegex(ProviderError, "exceeded the limit"):
                list_models("openai", "fictional-key")
        with patch("applydesk.model_catalog._request_json", return_value=first) as request:
            with self.assertRaisesRegex(ProviderError, "repeated"):
                list_models("anthropic", "fictional-key")
            self.assertEqual(request.call_count, 2)

    def test_malformed_catalogs_and_cursors_fail_safely(self):
        for provider, response in (("openai", {"data": "wrong"}), ("openai", {"data": [None]}),
                                   ("openai", {"data": [{"id": "../../wrong"}]}),
                                   ("anthropic", {"data": [], "has_more": True, "last_id": {}}),
                                   ("anthropic", {"data": [], "has_more": "false"}),
                                   ("google", {"models": [], "nextPageToken": "x" * 4097})):
            with self.subTest(provider=provider, response=response):
                with patch("applydesk.model_catalog._request_json", return_value=response), self.assertRaises(ProviderError):
                    list_models(provider, "fictional-key")

    def test_http_network_and_redirect_errors_do_not_expose_private_details(self):
        for error in (HTTPError("https://evil.test/fictional-key", 401, "private-body", {}, None),
                      HTTPError("https://evil.test/fictional-key", 302, "private-body", {}, None),
                      URLError("fictional-key private-body")):
            opener = Mock()
            opener.open.side_effect = error
            with patch("applydesk.providers.request.build_opener", return_value=opener):
                with self.assertRaises(ProviderError) as caught:
                    list_models("openai", "fictional-key")
            self.assertNotIn("fictional-key", str(caught.exception))
            self.assertNotIn("private-body", str(caught.exception))
            self.assertNotIn("evil.test", str(caught.exception))
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.test"))

    def test_response_size_and_elapsed_time_are_bounded(self):
        opener = self.opener({"data": [{"id": "one"}]})
        with patch("applydesk.model_catalog.MAX_PAGE_BYTES", 5), patch("applydesk.providers.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(ProviderError, "size limit"):
                list_models("openai", "fictional-key")
        with patch("applydesk.model_catalog.time.monotonic", side_effect=[0, 61]):
            with patch("applydesk.model_catalog._request_json") as request, self.assertRaisesRegex(ProviderError, "timed out"):
                list_models("openai", "fictional-key")
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
