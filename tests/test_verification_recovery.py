"""Verification recovery cannot erase an attempt or retry an uncertain submission."""
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from applydesk.application_submission import attempt_key, renew_verification_attempt
from applydesk.application_verification import pending, refill_application, send_code, verify_application
from applydesk.storage import Store


class VerificationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.store = Store(self.root.name)
        self.addCleanup(self.store.close)
        self.job = self.store.add_job('https://employer.example/job/1')
        self.job.update(last_snapshot='new-audit', attachments=[])
        self.attempt = {'id': 'original', 'run_id': 'run', 'job_id': self.job['id'], 'status': 'verification_required',
                        'receipt': {'confirmed': False, 'verification_required': True, 'issue': 'Incorrect security code'}}
        self.store.put('submission_attempt', attempt_key(self.job), self.attempt)
        self.workspace = SimpleNamespace(store=self.store, lock=threading.RLock(), profile=lambda: {'confirmed': True})
        self.engine = SimpleNamespace(w=self.workspace, check_active=Mock(), active={'message': {'role': 'user', 'text': 'refill'}})

    def test_refill_keeps_original_identity_and_archives_rejected_receipt(self):
        run = {'id': 'run', 'profile_revision': 2}
        saved = renew_verification_attempt(self.engine, self.job, run, self.attempt, lambda: None)
        self.assertEqual(saved['id'], 'original')
        self.assertEqual(saved['status'], 'submitting')
        self.assertEqual(len(self.store.all('submission_attempt')), 1)
        self.assertEqual(saved['verification_history'][0]['issue'], 'Incorrect security code')
        with self.assertRaisesRegex(ValueError, 'code rejection'):
            renew_verification_attempt(self.engine, self.job, run, self.attempt, lambda: None)

    def test_unknown_or_confirmed_outcomes_never_allow_recovery(self):
        for status in ('submission_unknown', 'submitted', 'submitting'):
            self.store.put('submission_attempt', attempt_key(self.job), {**self.attempt, 'status': status})
            with self.assertRaisesRegex(ValueError, 'awaiting email verification'):
                pending(self.engine, self.job['id'])
        self.store.put('submission_attempt', attempt_key(self.job), self.attempt)
        self.engine.active['auto_restart'] = True
        with self.assertRaisesRegex(ValueError, 'new user message'):
            pending(self.engine, self.job['id'])

    def test_native_recovery_hands_off_without_browser_actions_or_ledger_writes(self):
        """Both operations return the pending receipt, also after restart without a retained session."""
        self.workspace.profile = lambda: {'confirmed': True, 'revision': 2}
        self.attempt['profile_revision'] = 2
        self.store.put('submission_attempt', attempt_key(self.job), self.attempt)
        self.store.put('application_run', 'run', {'id': 'run', 'results': []})
        self.engine.active['message']['text'] = 'Refill the application, then enter ABC12345.'
        native = SimpleNamespace(native=True)
        session = SimpleNamespace(manager=native, call=Mock(), park=Mock())
        self.workspace.application_browser = native
        job_before = self.store.get('job', self.job['id'])
        for sessions in ({self.job['id']: session}, {}):
            self.workspace.sessions = sessions
            for operation, args in ((verify_application, ('ABC12345',)), (refill_application, ())):
                with self.subTest(retained=bool(sessions), operation=operation.__name__):
                    result = operation(self.engine, self.job['id'], *args)
                    self.assertEqual(result['status'], 'needs_review')
                    self.assertEqual(result['receipt'], self.attempt['receipt'])
                    self.assertIn('employer’s submission confirmation', result['issues'][0])
        session.call.assert_not_called()
        session.park.assert_not_called()
        self.assertEqual(self.store.get('job', self.job['id']), job_before)
        self.assertEqual(self.store.all('submission_attempt'), [self.attempt])
        self.assertEqual(self.store.all('application_run'), [{'id': 'run', 'results': []}])

    def test_retained_legacy_session_still_reaches_its_verification_audit(self):
        """A native default manager does not displace the capability of an already-retained session."""
        self.workspace.profile = lambda: {'confirmed': True, 'revision': 2}
        self.attempt['profile_revision'] = 2
        self.store.put('submission_attempt', attempt_key(self.job), self.attempt)
        self.engine.active['message']['text'] = 'Use ABC12345'
        session = SimpleNamespace(manager=SimpleNamespace(native=False), call=Mock(return_value={}))
        self.workspace.sessions = {self.job['id']: session}
        self.workspace.application_browser = SimpleNamespace(native=True)
        with patch('applydesk.application_verification.verification_audit', side_effect=ValueError('expected legacy audit')):
            with self.assertRaisesRegex(ValueError, 'expected legacy audit'):
                verify_application(self.engine, self.job['id'], 'ABC12345')
        session.call.assert_called_once_with('snapshot')
        self.assertEqual(self.store.all('submission_attempt'), [self.attempt])

    def test_interrupted_code_submission_is_uncertain_and_remains_single_attempt(self):
        session = Mock()
        session.call.side_effect = InterruptedError('Stopped after send')
        with patch('applydesk.application_verification.save_result') as saved:
            with self.assertRaises(InterruptedError):
                send_code(self.engine, self.job, self.attempt, session, {})
            self.assertEqual(saved.call_args.args[-1]['status'], 'submission_unknown')
        self.assertEqual(self.store.get('submission_attempt', attempt_key(self.job))['status'], 'submission_unknown')
        self.assertEqual(len(self.store.all('submission_attempt')), 1)
        session.park.assert_called_once()

    def test_profile_change_requires_new_eligibility_review_before_verification(self):
        self.workspace.profile = lambda: {'confirmed': True, 'revision': 2}
        self.engine.active['message']['text'] = 'Use code 12345678'
        self.attempt['profile_revision'] = 1
        self.store.put('submission_attempt', attempt_key(self.job), self.attempt)
        with self.assertRaisesRegex(ValueError, 'profile changed'):
            verify_application(self.engine, self.job['id'], '12345678')
