"""Observed application navigation preserves job identity and replans stale forms."""
import unittest
from unittest.mock import Mock
from applydesk.recommendation_checks import same_workday_job
from applydesk.application_steps import fill_current_form, enter_current_form
from applydesk.browser_bridge import BrowserError
from applydesk.immigration_eligibility import eligibility_quotes


class PendingNavigationTests(unittest.TestCase):
    def test_workday_application_steps_keep_exact_employer_site_and_requisition(self):
        original = 'https://example.wd5.myworkdayjobs.com/Careers/job/Example-City/Engineer_JR123'
        self.assertTrue(same_workday_job(original, original + '/apply'))
        self.assertTrue(same_workday_job(original, original.replace('/Careers/', '/en-US/Careers/') + '/apply/applyManually'))
        for changed in [original.replace('JR123', 'JR124'), original.replace('example.', 'other.'), original.replace('/Careers/', '/Other/'), original + '/unknown']:
            self.assertFalse(same_workday_job(original, changed))

    def test_stale_fill_replans_and_does_not_replay_old_answers(self):
        session = Mock()
        session.call.side_effect = [BrowserError('The form changed. Inspect it again before filling.'), {'snapshot_id': 'new', 'fields': []}]
        result = fill_current_form(session, {'snapshot_id': 'old'})
        self.assertEqual(result['intervention'], 'stale_before_fill')
        self.assertEqual(session.call.call_count, 2)
        session.call.assert_called_with('snapshot')

    def test_entry_refresh_is_bounded_and_other_errors_are_not_retried(self):
        session = Mock()
        session.call.side_effect = BrowserError('Blocked network request')
        with self.assertRaisesRegex(BrowserError, 'Blocked network'):
            enter_current_form(session, {'snapshot_id': 'old'}, {'company': 'Example', 'title': 'Engineer'})
        self.assertEqual(session.call.call_count, 1)

    def test_employer_license_notice_does_not_remove_personal_eligibility_rules(self):
        source = 'In some instances, if applicable, U.S. export control laws require that Infineon obtain a U.S. government export license prior to releasing technologies to certain persons.\nUS citizenship required.'
        self.assertEqual(eligibility_quotes(source), ['US citizenship required.'])
