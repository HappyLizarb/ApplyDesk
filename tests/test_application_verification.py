"""Email verification is Needs input, while its original attempt remains protected from retries."""

import tempfile
import unittest
from types import SimpleNamespace

from applydesk.application_submission import attempt_key, finish_attempt
from applydesk.storage import Store


class VerificationTests(unittest.TestCase):
    def test_verification_is_not_a_submission_or_an_erased_attempt(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            self.addCleanup(store.close)
            job = store.add_job('https://employer.example/job/123')
            attempt = {"id": "attempt-1", "job_id": job["id"], "status": "submitting"}
            receipt = {"confirmed": False, "verification_required": True, "issue": "An email code is required.", "receipt": ""}
            result = finish_attempt(SimpleNamespace(store=store), job, attempt, 'verification_required', receipt)
            self.assertEqual(result['status'], 'needs_review')
            self.assertEqual(store.get('job', job['id'])['progress'], 'An email code is required.')
            self.assertEqual(store.get('submission_attempt', attempt_key(job))['id'], 'attempt-1')
            self.assertEqual(store.get('submission_attempt', attempt_key(job))['status'], 'verification_required')
