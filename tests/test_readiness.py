"""Regression tests for deterministic final-readiness evidence."""

import copy
import unittest

from applydesk.readiness import verify_readiness


def valid_case():
    """A single real final form has a fresh audit and the exact current resume."""
    digest = "a" * 64
    job = {"id": "job", "url": "https://example.org/jobs/engineer", "company": "Example Co",
           "title": "Software Engineer", "resume_id": "resume-v1", "cover_id": None,
           "profile_revision": 4, "status": "preparing", "cover_state": "not_checked"}
    profile = {"revision": 4, "confirmed": True, "transcript_id": None}
    fields = [{"field_id": "name", "form_id": "application", "context_id": "application",
               "type": "text", "label": "Full name", "value": "Test User", "required": True, "supported": True},
              {"field_id": "resume", "form_id": "application", "context_id": "application",
               "type": "file", "label": "Resume", "required": True,
               "files": [{"name": "resume.pdf", "size": 50, "sha256": digest}]}]
    button = {"field_id": "submit", "form_id": "application", "context_id": "application",
              "text": "Submit application", "context_text": "Full name Resume Submit application",
              "action": "final_or_ambiguous", "disabled": False}
    snapshot = {"snapshot_id": "final-snapshot", "title": "Example Co careers", "text": "Software Engineer — Example Co",
                "fields": fields, "buttons": [button], "guard": {"manual": False, "mutations_blocked": True, "blocked_requests": []},
                "audit": {"passed": True, "snapshot_id": "final-snapshot", "field_ids": ["name"]}, "warnings": []}
    ledger = [{"field_id": "resume", "document_id": "resume-v1", "kind": "resume", "sha256": digest, "expected_sha256": digest}]
    return job, copy.deepcopy(job), profile, snapshot, ledger


class ReadinessTests(unittest.TestCase):
    """Readiness must fail closed for stale identity, document, form, or audit evidence."""

    def test_complete_evidence_is_ready_and_cover_is_unnecessary(self):
        result = verify_readiness(*valid_case())
        self.assertEqual(result, {"ready": True, "issues": [], "cover_state": "not_required"})

    def test_generic_finish_apply_and_submit_never_prove_final(self):
        for label in ("Finish", "Apply now", "Submit", "Confirm", "Continue"):
            with self.subTest(label=label):
                case = valid_case()
                case[3]["buttons"][0]["text"] = label
                result = verify_readiness(*case)
                self.assertFalse(result["ready"])
                self.assertEqual(result["cover_state"], "not_checked")

    def test_multiple_submission_boundaries_are_ambiguous(self):
        case = valid_case()
        case[3]["buttons"].append({"text": "Finish", "action": "final_or_ambiguous"})
        self.assertFalse(verify_readiness(*case)["ready"])

    def test_profile_and_selected_document_revisions_cannot_change(self):
        for target, key, value in ((2, "revision", 5), (1, "resume_id", "resume-v2"), (1, "cover_id", "cover-v2"), (1, "url", "https://example.org/other")):
            with self.subTest(key=key):
                case = valid_case()
                case[target][key] = value
                self.assertFalse(verify_readiness(*case)["ready"])

    def test_audit_is_bound_to_current_snapshot_and_fields(self):
        for key, value in (("snapshot_id", "old-snapshot"), ("field_ids", []), ("passed", False)):
            with self.subTest(key=key):
                case = valid_case()
                case[3]["audit"][key] = value
                self.assertFalse(verify_readiness(*case)["ready"])

    def test_expected_company_and_title_must_be_visible(self):
        case = valid_case()
        case[3]["text"] = "Software Engineer — Another Company"
        case[3]["title"] = "Another Company"
        self.assertFalse(verify_readiness(*case)["ready"])

    def test_fields_must_belong_to_actual_final_form(self):
        case = valid_case()
        case[3]["fields"][0]["form_id"] = "newsletter"
        self.assertFalse(verify_readiness(*case)["ready"])

    def test_spa_requires_matching_section_and_direct_job_context(self):
        case = valid_case()
        case[3]["buttons"][0]["form_id"] = None
        for field in case[3]["fields"]:
            field["form_id"] = None
        self.assertFalse(verify_readiness(*case)["ready"])
        case[3]["buttons"][0]["context_text"] = "Software Engineer — Example Co Resume Submit application"
        self.assertTrue(verify_readiness(*case)["ready"])

    def test_hidden_prior_page_upload_requires_manual_verification(self):
        case = valid_case()
        case[3]["fields"] = case[3]["fields"][:1]
        result = verify_readiness(*case)
        self.assertFalse(result["ready"])
        self.assertTrue(any("earlier upload" in issue for issue in result["issues"]))

    def test_source_hash_live_hash_and_document_id_must_all_match(self):
        for key, value in (("expected_sha256", "b" * 64), ("document_id", "resume-v0"), ("sha256", "wrong")):
            with self.subTest(key=key):
                case = valid_case()
                case[4][0][key] = value
                self.assertFalse(verify_readiness(*case)["ready"])
        case = valid_case()
        case[3]["fields"][1]["files"][0]["sha256"] = "c" * 64
        self.assertFalse(verify_readiness(*case)["ready"])

    def test_optional_stale_attachment_still_requires_review(self):
        case = valid_case()
        case[3]["fields"].append({"field_id": "cover", "form_id": "application", "type": "file",
                                  "label": "Cover letter", "required": False, "files": [{"sha256": "b" * 64}]})
        self.assertFalse(verify_readiness(*case)["ready"])

    def test_optional_empty_cover_field_can_be_unnecessary(self):
        case = valid_case()
        case[3]["fields"].append({"field_id": "cover", "form_id": "application", "type": "file",
                                  "label": "Cover letter (optional)", "required": False, "files": []})
        self.assertEqual(verify_readiness(*case)["cover_state"], "not_required")

    def test_required_cover_cannot_be_replaced_by_a_slash(self):
        case = valid_case()
        case[3]["fields"].append({"field_id": "cover", "form_id": "application", "type": "file",
                                  "label": "Cover letter", "required": True, "files": []})
        result = verify_readiness(*case)
        self.assertFalse(result["ready"])
        self.assertEqual(result["cover_state"], "required")

    def test_unlocated_cover_mention_is_not_marked_unnecessary(self):
        case = valid_case()
        case[3]["text"] += " Please upload a cover letter."
        result = verify_readiness(*case)
        self.assertFalse(result["ready"])
        self.assertEqual(result["cover_state"], "not_checked")

    def test_required_radio_group_uses_audited_selected_member(self):
        case = valid_case()
        for identity, checked in (("yes", False), ("no", True)):
            case[3]["fields"].append({"field_id": identity, "form_id": "application", "group_id": "authorization",
                                      "name": "sponsor", "type": "radio", "required": True, "checked": checked, "supported": True})
        case[3]["audit"]["field_ids"].append("no")
        self.assertTrue(verify_readiness(*case)["ready"])
        case[3]["fields"][-1]["checked"] = False
        self.assertFalse(verify_readiness(*case)["ready"])
        for field in case[3]["fields"][-2:]:
            field["checked"] = True
        self.assertFalse(verify_readiness(*case)["ready"])
        case[3]["fields"][-2]["checked"] = False
        case[3]["fields"][-1]["name"] = ""
        self.assertFalse(verify_readiness(*case)["ready"])

    def test_required_cover_text_blocks_even_with_a_previous_letter(self):
        case = valid_case()
        case[0]["cover_id"] = case[1]["cover_id"] = "cover-v1"
        case[3]["fields"].append({"field_id": "cover", "form_id": "application", "type": "textarea",
                                  "label": "Cover letter", "required": True, "value": "Verified document text", "supported": True})
        case[3]["audit"]["field_ids"].append("cover")
        result = verify_readiness(*case)
        self.assertFalse(result["ready"])
        self.assertTrue(any("requires a cover letter" in issue for issue in result["issues"]))
        self.assertEqual(result["cover_state"], "required")

    def test_optional_prefilled_cover_text_needs_review(self):
        case = valid_case()
        case[3]["fields"].append({"field_id": "cover", "form_id": "application", "type": "textarea",
                                  "label": "Cover letter", "required": False, "value": "Old letter", "supported": True})
        case[3]["audit"]["field_ids"].append("cover")
        result = verify_readiness(*case)
        self.assertFalse(result["ready"])
        self.assertEqual(result["cover_state"], "not_checked")

    def test_guard_failures_and_unknown_frames_prevent_readiness(self):
        for key, value in (("manual", True), ("mutations_blocked", False), ("blocked_requests", [{"method": "POST"}])):
            with self.subTest(key=key):
                case = valid_case()
                case[3]["guard"][key] = value
                self.assertFalse(verify_readiness(*case)["ready"])
        case = valid_case()
        case[3]["warnings"] = ["Embedded frames require manual inspection."]
        self.assertFalse(verify_readiness(*case)["ready"])

    def test_normal_browser_still_requires_fresh_action_and_field_audits(self):
        case = valid_case()
        case[3]["guard"] = {"manual": False, "mutations_blocked": False, "submission_guarded": False,
                            "page_networking": "normal", "action_audited": True, "blocked_requests": []}
        self.assertTrue(verify_readiness(*case)["ready"])
        for changes in ({"action_audited": False}, {"page_networking": "unknown"}, {"manual": True},
                        {"blocked_requests": [{"reason": "Private network URL"}]}):
            changed = copy.deepcopy(case)
            changed[3]["guard"].update(changes)
            self.assertFalse(verify_readiness(*changed)["ready"])
        for missing in ("audit", "snapshot_id"):
            changed = copy.deepcopy(case)
            changed[3].pop(missing)
            self.assertFalse(verify_readiness(*changed)["ready"])


if __name__ == "__main__":
    unittest.main()
