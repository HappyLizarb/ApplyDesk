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

    def test_screening_does_not_treat_another_browser_tab_as_posting_evidence(self):
        client = StubClient(dict(ANALYSIS, company="Example", title="Engineer", details="Current posting",
                                 country="US", category="", preferred=[], cover_letter_required=False))
        page = {"native": True, "text": "Current posting", "url": "https://example.com/job/1",
                "tabs": [{"label": "Applied to Engineer"}], "elements": [{"label": "Other job"}]}
        ai.analyze_job(client, PROFILE, page)
        self.assertEqual(client.calls[0][1]["page"], {"native": True, "url": page["url"], "text": "Current posting"})

    def test_document_id_is_repaired_once_or_the_posting_stays_in_review(self):
        """A résumé identifier cannot verify professional experience or cause a false eligibility skip."""
        analysis = dict(ANALYSIS, company="Employer", title="Engineer", details="Description",
                        country="US", category="Hardware", preferred=[], cover_letter_required=False)
        analysis["required"] = [dict(ANALYSIS["required"][0], evidence_ids=["resume-file-id"])]
        for replacement in (["work"], [], ["resume-file-id"]):
            client = Mock()
            client.ask.side_effect = [copy.deepcopy(analysis), {"citations": [replacement]}]
            if replacement == ["work"]:
                actual = ai.analyze_job(client, PROFILE, ANALYSIS["required"][0]["text"])
                self.assertTrue(ai.validate_eligibility(actual, PROFILE)["eligible"])
            else:
                with self.assertRaisesRegex(ValueError, "another review"):
                    ai.analyze_job(client, PROFILE, ANALYSIS["required"][0]["text"])
            self.assertEqual(client.ask.call_count, 2)

    def test_confirmed_work_evidence_passes(self):
        """Required professional experience can cite confirmed employment."""
        self.assertTrue(ai.validate_eligibility(ANALYSIS, PROFILE)["eligible"])

    def test_assumed_skills_and_experience_types_need_no_personal_evidence(self):
        """Unlisted skills and hands-on experience remain allowed without inventing profile facts."""
        profile = copy.deepcopy(PROFILE)
        for requirement in ("Python required", "Experience designing analog circuits required"):
            analysis = {"requirements_complete": True, "source_text": requirement,
                        "required": [{"text": requirement, "met": True, "evidence_ids": []}]}
            self.assertTrue(ai.validate_eligibility(analysis, profile)["eligible"])
        self.assertEqual(profile, PROFILE)

    def test_unmet_mandatory_requirements_never_pass_the_assumption(self):
        """An explicit failed match blocks preparation even when some supporting facts exist."""
        for requirement in ("3 years of professional Python experience", "US citizenship required",
                            "Active security clearance required"):
            for ids in ([], ["work"]):
                with self.subTest(requirement=requirement, ids=ids):
                    analysis = {"requirements_complete": True, "source_text": requirement,
                                "required": [{"text": requirement, "met": False, "evidence_ids": ids}]}
                    result = ai.validate_eligibility(analysis, PROFILE)
                    self.assertFalse(result["eligible"])
                    self.assertTrue(any("mandatory requirement" in reason for reason in result["reasons"]))

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

    def test_employers_future_vehicle_review_is_separate_from_current_driving_qualifications(self):
        """Screen the confirmed license independently of an employer's later review."""
        license_text = "Valid U.S. Driver’s License"
        condition = "as a condition of employment, must pass Example Engineering’s Motor Vehicle Records Review."
        license_row = {"text": license_text, "met": True, "evidence_ids": ["license"]}
        review_row = {"text": condition, "met": False, "evidence_ids": []}
        profile = {"confirmed": True, "facts": [{"id": "license", "text": license_text, "confirmed": True}]}
        analysis = {"company": "Example Engineering", "requirements_complete": True,
                    "source_text": license_text + " and " + condition, "required": [license_row, review_row]}
        self.assertTrue(ai.validate_eligibility(analysis, profile)["eligible"])
        self.assertFalse(review_row["met"])
        for current in (license_text, "Clean driving record required.", "Must have passed the review before applying."):
            unresolved = {"text": current, "met": False, "evidence_ids": []}
            with self.subTest(current=current):
                actual = ai.validate_eligibility({**analysis, "source_text": current + "\n" + condition,
                                                  "required": [unresolved, review_row]}, profile)
                self.assertFalse(actual["eligible"])
                self.assertEqual(len(actual["reasons"]), 1)
                self.assertIn(current, actual["reasons"][0])
        for observed in ("Unrelated listing.", condition[:-1] + " before applying.",
                         "To qualify before applying, " + condition):
            with self.subTest(observed=observed):
                actual = ai.validate_eligibility({**analysis, "source_text": observed,
                                                  "required": [dict(review_row, text=condition[:-1])]}, profile)
                self.assertFalse(actual["eligible"])
        mixed = {"text": analysis["source_text"], "met": False, "evidence_ids": []}
        self.assertFalse(ai.validate_eligibility({**analysis, "required": [mixed]}, profile)["eligible"])

    def test_complete_listing_can_have_no_explicit_requirements(self):
        """Job descriptions need no artificial no-qualifications sentence."""
        analysis = {"required": [], "requirements_complete": True,
                    "source_text": "Design analog circuits with a collaborative team."}
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













    def test_live_mismatch_cannot_be_overruled_by_model(self):
        """A sponsorship answer mismatch fails without consulting the model."""
        client = StubClient({"passed": True, "issues": []})
        expected = [{"id": "sponsor", "value": "No", "fact_ids": ["work"]}]
        result = ai.audit_fields(PROFILE,
                                {"fields": [{"id": "sponsor", "value": "Yes"}]}, expected)
        self.assertFalse(result["passed"])
        self.assertFalse(client.calls)

    def test_duplicate_live_field_ids_are_ambiguous(self):
        """Identical values do not make duplicate field identities safe."""
        field = {"id": "x", "value": "yes"}
        expected = [{**field, "fact_ids": ["work"]}]
        result = ai.audit_fields(PROFILE, {"fields": [field, field]}, expected)
        self.assertFalse(result["passed"])

    def test_matching_values_and_known_facts_pass_without_a_model_request(self):
        """Field verification is local; self-checking belongs in the agent prompt."""
        field = {"id": "x", "value": "yes"}
        result = ai.audit_fields(PROFILE, {"fields": [field]}, [{**field, "fact_ids": ["work"]}])
        self.assertTrue(result["passed"])
        self.assertEqual(result["issues"], [])




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
