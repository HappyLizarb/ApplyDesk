"""Behavior checks for evidence gates and provider transport without paid calls."""

import copy
import io
import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from applydesk import intelligence as ai
from applydesk.providers import MODEL_OPTIONS, ProviderClient, ProviderError, validate_settings


PROFILE = {"confirmed": True, "facts": [
    {"id": "work", "text": "Three years of professional Python work.",
     "kind": "employment", "confirmed": True},
    {"id": "class", "text": "Three years of Python coursework.",
     "kind": "education", "confirmed": True},
    {"id": "draft", "text": "Proposed skill", "kind": "skill", "confirmed": False},
]}
ANALYSIS = {"requirements_complete": True, "required": [
    {"text": "3 years of professional Python experience", "met": True,
     "evidence_ids": ["work"]}]}
TEMPLATE = {"blocks": [{"id": "p0", "text": "Example", "style": "Normal"}]}


class StubClient:
    """Record a model request and return a fresh, controlled response."""

    def __init__(self, response):
        """Keep the fixture independent from mutations performed by the caller."""
        self.response, self.calls = response, []

    def ask(self, system, payload, images=None):
        """Capture only test data; no network is involved."""
        self.calls.append((system, payload, images))
        return copy.deepcopy(self.response)


class EligibilityTests(unittest.TestCase):
    """Assumed qualifications do not create personal facts or waive source validation."""

    def test_confirmed_work_evidence_passes(self):
        """Required professional experience can cite confirmed employment."""
        self.assertTrue(ai.validate_eligibility(ANALYSIS, PROFILE)["eligible"])

    def test_assumed_qualifications_need_no_personal_evidence(self):
        """An unrecorded qualification passes without adding facts or asserting a supported match."""
        profile = copy.deepcopy(PROFILE)
        analysis = copy.deepcopy(ANALYSIS)
        analysis["required"][0].update(met=False, evidence_ids=[])
        self.assertTrue(ai.validate_eligibility(analysis, profile)["eligible"])
        self.assertFalse(analysis["required"][0]["met"])
        self.assertEqual(profile, PROFILE)

    def test_invented_and_unconfirmed_evidence_fail(self):
        """Assumed qualification uses empty IDs, never fabricated supporting facts."""
        for ids in (["unknown"], ["draft"], "work"):
            with self.subTest(ids=ids):
                analysis = copy.deepcopy(ANALYSIS)
                analysis["required"][0]["evidence_ids"] = ids
                self.assertFalse(ai.validate_eligibility(analysis, PROFILE)["eligible"])

    def test_missing_requirements_and_incomplete_extraction_fail(self):
        """Absent and incomplete requirement sets cannot silently pass."""
        for analysis in ({}, {"required": []}, {"required": ANALYSIS["required"]}):
            self.assertFalse(ai.validate_eligibility(analysis, PROFILE)["eligible"])

    def test_complete_listing_can_have_no_explicit_requirements(self):
        """Job descriptions need no artificial no-qualifications sentence."""
        analysis = {"required": [], "requirements_complete": True,
                    "source_text": "Edit reference works with a collaborative team."}
        self.assertTrue(ai.validate_eligibility(analysis, PROFILE)["eligible"])

    def test_requirement_must_be_observed_when_source_is_supplied(self):
        """The qualification assumption never permits invented listing requirements."""
        analysis = {**ANALYSIS, "source_text": "An unrelated description."}
        self.assertFalse(ai.validate_eligibility(analysis, PROFILE)["eligible"])

    def test_false_string_and_duplicate_fact_ids_fail(self):
        """Neither truthy strings nor conflicting evidence identities count."""
        analysis = copy.deepcopy(ANALYSIS)
        analysis["required"][0]["met"] = "true"
        self.assertFalse(ai.validate_eligibility(analysis, PROFILE)["eligible"])
        profile = copy.deepcopy(PROFILE)
        profile["facts"].append(copy.deepcopy(profile["facts"][0]))
        self.assertFalse(ai.validate_eligibility(ANALYSIS, profile)["eligible"])

    def test_auditor_cannot_rescue_a_failed_evidence_gate(self):
        """A cheap deterministic failure avoids another paid request."""
        client = StubClient({"passed": True, "issues": []})
        result = ai.audit_eligibility(client, PROFILE, "job", {})
        self.assertFalse(result["passed"])
        self.assertFalse(client.calls)

    def test_independent_auditor_honors_assumed_new_requirements(self):
        """An observed additional qualification does not demand new personal evidence."""
        result = copy.deepcopy(ANALYSIS)
        result.update(passed=True, issues=[])
        result["required"].append({"text": "Security clearance", "met": False,
                                   "evidence_ids": []})
        page = ANALYSIS["required"][0]["text"] + ". Security clearance"
        self.assertTrue(ai.audit_eligibility(StubClient(result), PROFILE, page, ANALYSIS)["passed"])


class IntelligenceTests(unittest.TestCase):
    """Imported facts, document changes and live fields preserve trust boundaries."""

    def test_import_is_always_an_unconfirmed_draft(self):
        """The model cannot mark facts or sensitive answers as user confirmed."""
        result = {key: [] for key in ("education", "experiences", "projects",
                                      "skills", "industries", "websites")}
        result.update(identity={}, confirmed=True, facts=PROFILE["facts"],
                      authorizations=[{"country": "US", "status": "authorized_no_sponsor"}])
        draft = ai.extract_profile(StubClient(result), "Explicit imported text")
        self.assertFalse(draft["confirmed"])
        self.assertEqual(draft["facts"], [])
        self.assertEqual(draft["authorizations"], [])
        self.assertEqual(draft["preferences"]["disability"], "unknown")

    def test_document_edits_keep_template_blocks_and_evidence(self):
        """A supported edit is returned unchanged for the document service."""
        edits = {"edits": [{"id": "p0", "text": "Professional Python experience",
                             "fact_ids": ["work"]}], "issues": []}
        result = ai.generate_document(StubClient(edits), PROFILE, TEMPLATE, "Category resume")
        self.assertEqual(result, edits)
        self.assertEqual(TEMPLATE["blocks"][0]["text"], "Example")

    def test_document_rejects_unknown_blocks_missing_evidence_and_styles(self):
        """Unsupported document output cannot mutate the template."""
        invalid = [
            {"id": "new", "text": "x", "fact_ids": ["work"]},
            {"id": "p0", "text": "x", "fact_ids": ["draft"]},
            {"id": "p0", "text": "x", "fact_ids": []},
            {"id": "p0", "text": "x", "fact_ids": ["work"], "style": "Title"},
        ]
        for edit in invalid:
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                ai.generate_document(StubClient({"edits": [edit]}), PROFILE, TEMPLATE, "Edit")

    def test_document_chat_uses_the_same_evidence_gate(self):
        """The sidebar cannot bypass the generator's provenance checks."""
        client = StubClient({"edits": [{"id": "p0", "text": "Invented", "fact_ids": []}]})
        with self.assertRaises(ValueError):
            ai.chat_document(client, PROFILE, TEMPLATE, "Add this claim", [])

    def test_protected_document_blocks_cannot_be_edited(self):
        """Linked or protected template content stays outside model edit authority."""
        template = {"blocks": [{"id": "p0", "text": "Name", "editable": False}]}
        client = StubClient({"edits": [{"id": "p0", "text": "Replacement", "fact_ids": ["work"]}]})
        with self.assertRaises(ValueError):
            ai.generate_document(client, PROFILE, template, "Replace the name")

    def test_document_audit_requires_coverage_of_untouched_blocks(self):
        """A generic pass cannot hide unreviewed template sample claims."""
        client = StubClient({"passed": True, "issues": [], "block_checks": []})
        self.assertFalse(ai.audit_document(client, PROFILE, TEMPLATE)["passed"])
        self.assertEqual(client.calls[0][1]["document"], TEMPLATE)

    def test_document_audit_rejects_unsupported_unchanged_claims(self):
        """Every personal-claim block needs confirmed supporting facts."""
        result = {"passed": True, "issues": [], "block_checks": [
            {"id": "p0", "personal_claims": True, "fact_ids": ["unknown"]}]}
        self.assertFalse(ai.audit_document(StubClient(result), PROFILE, TEMPLATE)["passed"])
        result["block_checks"][0]["fact_ids"] = ["work"]
        self.assertTrue(ai.audit_document(StubClient(result), PROFILE, TEMPLATE)["passed"])

    def test_live_mismatch_cannot_be_overruled_by_model(self):
        """A sponsorship answer mismatch fails without consulting the model."""
        client = StubClient({"passed": True, "issues": []})
        expected = [{"id": "sponsor", "value": "No", "fact_ids": ["work"]}]
        result = ai.audit_fields(client, PROFILE,
                                {"fields": [{"id": "sponsor", "value": "Yes"}]}, expected)
        self.assertFalse(result["passed"])
        self.assertFalse(client.calls)

    def test_duplicate_live_field_ids_are_ambiguous(self):
        """Identical values do not make duplicate field identities safe."""
        field = {"id": "x", "value": "yes"}
        expected = [{**field, "fact_ids": ["work"]}]
        result = ai.audit_fields(StubClient({}), PROFILE, {"fields": [field, field]}, expected)
        self.assertFalse(result["passed"])

    def test_semantic_auditor_can_reject_matching_values(self):
        """Readback agreement alone does not establish the right question meaning."""
        client = StubClient({"passed": False, "issues": ["Sponsorship was inferred."]})
        field = {"id": "x", "value": "yes"}
        result = ai.audit_fields(client, PROFILE, {"fields": [field]},
                                 [{**field, "fact_ids": ["work"]}])
        self.assertFalse(result["passed"])
        self.assertEqual(len(client.calls), 1)

    def test_visual_review_requires_images_and_rejects_inconsistent_pass(self):
        """An issue cannot disappear just because the model also returns passed."""
        client = StubClient({"passed": True, "issues": ["Clipped text"]})
        self.assertFalse(ai.review_format(client, [], [1])["passed"])
        self.assertFalse(client.calls)
        self.assertFalse(ai.review_format(client, [1], [2])["passed"])
        self.assertEqual(client.calls[0][2], [1, 2])


class ProviderTests(unittest.TestCase):
    """Exercise every supported preset without an API key or external request."""

    def test_supported_settings_and_request_shapes(self):
        """Each preset sends valid JSON to its provider with credentials in headers."""
        for provider, models in MODEL_OPTIONS.items():
            for model, efforts in models.items():
                for thinking in efforts:
                    settings = {"provider": provider, "model": model, "thinking": thinking}
                    client = ProviderClient(settings, "unit-test-key")
                    with self.subTest(settings=settings):
                        self.assertEqual(validate_settings(settings), settings)
                        url, headers, body = getattr(client, "_" + provider)("JSON", "{}", [])
                        self.assertNotIn("unit-test-key", url + json.dumps(body))
                        self.assertIn("unit-test-key", " ".join(headers.values()))
                        self.assertEqual(body.get("model", model), model)

    def test_unsupported_effort_fails_before_network(self):
        """Custom IDs cannot inherit unverified thinking controls from another model."""
        for settings in ({"provider": "openai", "model": "unknown", "thinking": "high"},
                         {"provider": "google", "model": "gemini-3.8-flash", "thinking": "none"}):
            with self.assertRaises(ProviderError):
                ProviderClient(settings, "test-key")

    def test_api_response_parsing_for_each_provider(self):
        """Provider response envelopes resolve to the same public JSON contract."""
        replies = {
            "openai": {"status": "completed", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": '{"passed":true}'}]}]},
            "anthropic": {"stop_reason": "end_turn", "content": [
                {"type": "text", "text": '{"passed":true}'}]},
            "google": {"candidates": [{"finishReason": "STOP", "content": {"parts": [
                {"text": '{"passed":true}'}]}}]},
        }
        for provider, reply in replies.items():
            client = ProviderClient({"provider": provider, "model": next(iter(MODEL_OPTIONS[provider]))}, "test")
            opener = Mock()
            opener.open.return_value = io.BytesIO(json.dumps(reply).encode())
            with patch("applydesk.providers.request.build_opener", return_value=opener):
                self.assertEqual(client.ask("Task", {"example": 1}), {"passed": True})
            self.assertEqual(opener.open.call_args.kwargs["timeout"], 60)

    def test_markdown_duplicates_and_nan_are_rejected(self):
        """Never repair or accept ambiguous structured decisions."""
        client = ProviderClient({"provider": "openai", "model": "gpt-5.4"}, "test")
        for text in ('```json\n{}\n```', '{"passed":true,"passed":false}', '{"x":NaN}', '[]'):
            reply = {"status": "completed", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": text}]}]}
            with patch.object(client, "_post", return_value=reply), self.assertRaises(ProviderError):
                client.ask("Task", {})

    def test_incomplete_response_and_http_errors_do_not_leak_secrets(self):
        """Token exhaustion and authentication errors fail without exposing data."""
        client = ProviderClient({"provider": "openai", "model": "gpt-5.4"}, "secret-test-key")
        with patch.object(client, "_post", return_value={"status": "incomplete"}):
            with self.assertRaises(ProviderError):
                client.ask("Task", {})
        opener = Mock()
        opener.open.side_effect = HTTPError("https://api.openai.com", 401, "secret-test-key", {}, None)
        with patch("applydesk.providers.request.build_opener", return_value=opener):
            with self.assertRaises(ProviderError) as caught:
                client.ask("Task", {"private": "Do not log"})
        self.assertIn("401", str(caught.exception))
        self.assertNotIn("secret-test-key", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
