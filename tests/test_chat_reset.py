"""Clearing the single conversation must not restore late messages or delete saved work."""

import threading
from unittest.mock import patch

from chat_helpers import ChatFixture
from applydesk.chat_history import ChatHistory


class ChatResetTests(ChatFixture):
    """Use synthetic account metadata and interrupted model responses only."""

    def setUp(self):
        """Keep all account, chat, and document records inside the temporary fixture."""
        super().setUp()
        self.engine = self.w.orchestrator
        self.w.store.put('chatgpt', 'accounts', {'active': 'test', 'accounts': [
            {'id': 'test', 'label': 'Test', 'connected': True, 'client_id': 'oaiapp_test', 'subject': 'test'}]})
        self.w.store.put('chatgpt_models', 'test', [{'id': 'test-model', 'name': 'Test', 'thinking': []}])

    def test_reset_preserves_saved_records_and_pauses_the_queue(self):
        """Only conversation messages disappear; saved profile, jobs, documents, and drafts remain."""
        before = {kind: self.w.store.all(kind) for kind in ['profile', 'job', 'chat_task', 'library', 'chatgpt']}
        documents = self.w.docs.manifest.copy()
        self.engine.history.add('user', 'Old question')
        self.engine.history.add('assistant', 'Old reply')
        self.engine.send({'message': 'Queued question'})
        self.assertEqual(self.engine.reset({}), {'cleared': True, 'paused': True})
        self.assertEqual(self.engine.history.read(), [])
        self.assertEqual(ChatHistory(self.w).read(), [])
        self.assertIsNone(self.engine._next())
        for kind, records in before.items():
            self.assertEqual(self.w.store.all(kind), records)
        self.assertEqual(self.w.docs.manifest, documents)
        self.assertTrue(self.w.store.get('agent_control', 'main')['paused'])

    def test_old_queued_work_cannot_start_after_reset(self):
        """A work item claimed before reset cannot recreate the deleted user or assistant turn."""
        self.engine.send({'message': 'Old request'})
        old = self.engine._next()
        self.engine.reset({})
        self.engine.control({'paused': False})
        with patch.object(self.engine, '_master') as master:
            self.engine._perform(old)
        master.assert_not_called()
        self.assertEqual(self.engine.history.read(), [])

    def test_late_provider_reply_cannot_resurrect_history(self):
        """Reset during an active response cancels its result before any chat or draft commit."""
        entered, release = threading.Event(), threading.Event()
        def delayed(*args):
            entered.set()
            release.wait(3)
            return {'id': 'late', 'status': 'completed', 'output': [
                {'type': 'message', 'content': [{'type': 'output_text', 'text': 'Old response'}]}]}
        self.engine.send({'message': 'Old request'})
        work = self.engine._next()
        with patch.object(self.engine.runner, '_request', side_effect=delayed):
            worker = threading.Thread(target=self.engine._perform, args=(work,))
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                self.engine.reset({})
            finally:
                release.set()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.engine.history.read(), [])
        self.assertEqual(self.engine.state()['runs'], [])
        self.assertEqual(self.engine.error, '')

    def test_fresh_message_has_no_prior_conversation_context(self):
        """Sending after reset queues a fresh turn while the worker remains explicitly paused."""
        old = self.engine.history.add('assistant', 'Private old context')
        self.engine.reset({})
        self.engine.history.finish(old, 'Late update')
        fresh = self.engine.send({'message': 'New conversation'})
        self.assertEqual(self.engine.history.context(fresh['message_id']), [])
        self.assertEqual(len(self.engine.history.read()), 1)
        self.assertTrue(self.engine.paused)
