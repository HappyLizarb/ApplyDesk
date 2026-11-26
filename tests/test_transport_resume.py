"""Transient provider failures preserve the application ledger and skip finished jobs."""
from unittest.mock import patch
from test_application_automation import AutomationTests
from applydesk.chatgpt_transport import AccountError


class TransportResumeTests(AutomationTests):
    def test_transient_failure_resumes_only_unprocessed_jobs(self):
        """A later tool call resumes its original allowance without a second submission."""
        self.intent['limit'] = 2
        original = self.engine.applications._job
        def fail_second(job):
            if job['id'] == self.jobs[1]['id']:
                raise AccountError(503)
            return original(job)
        with patch.object(self.engine.applications, '_job', side_effect=fail_second):
            with self.assertRaises(AccountError):
                self.run_applications()
        resumed = self.run_applications()
        self.assertEqual(resumed['submitted_count'], 2)
        self.assertEqual(len(self.w.store.all('application_run')), 1)
        self.assertEqual(sum(browser.methods.count('submit') for browser in self.browsers), 2)

    def test_changed_profile_still_blocks_transport_resume(self):
        """Retry permission never bypasses a changed applicant profile."""
        with patch.object(self.engine.applications, '_job', side_effect=AccountError(503)):
            with self.assertRaises(AccountError):
                self.run_applications()
        self.w.save_profile(self.w.profile())
        with self.assertRaisesRegex(InterruptedError, 'Profile changed'):
            self.run_applications()
        self.assertFalse(self.w.store.all('submission_attempt'))
