"""Short interest drafting is limited to observed employer-specific questions."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from applydesk.application_interest import interest_answers


class InterestAnswerTests(unittest.TestCase):
    def test_cached_answer_is_reused_only_for_same_source_and_profile(self):
        record = {'id': 'job', 'company': 'Example', 'title': 'Engineer', 'analysis': {'source_text': 'Hardware testing role.'}}
        store = Mock()
        store.get.side_effect = lambda *_: record
        store.job.side_effect = lambda _identity, **values: record.update(values)
        workspace = SimpleNamespace(store=store)
        profile = {'revision': 1, 'facts': [{'id': 'industries.0', 'kind': 'industries', 'text': 'Hardware', 'confirmed': True}]}
        page = {'fields': [{'type': 'textarea', 'label': 'Why Example?'}]}
        client = Mock()
        client.ask.return_value = {'answer': 'I am interested in the opportunity to work on hardware testing.'}
        first = interest_answers(workspace, lambda _: client, record, profile, page)
        self.assertEqual(first, interest_answers(workspace, lambda _: client, record, profile, page))
        self.assertEqual(client.ask.call_count, 1)
        interest_answers(workspace, lambda _: client, record, {**profile, 'revision': 2}, page)
        self.assertEqual(client.ask.call_count, 2)
        self.assertEqual(first['Why Example?']['fact_ids'], ['industries.0'])

    def test_factual_and_legal_questions_are_never_drafted(self):
        client = Mock()
        page = {'fields': [{'type': 'textarea', 'label': 'How many professional years do you have?'}]}
        result = interest_answers(Mock(), lambda _: client, {'company': 'Example'}, {}, page)
        self.assertEqual(result, {})
        client.ask.assert_not_called()
