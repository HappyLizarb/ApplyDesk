"""Workday account scope and explicit signup fields remain tied to the observed form."""
import unittest
from urllib.parse import quote
from applydesk.account_scope import sign_in_scope
from applydesk.application_steps import _auth_inputs, _auth_context


class WorkdayAuthTests(unittest.TestCase):
    def setUp(self):
        self.path = '/en-US/Careers/job/US/Engineer_JR123/apply/applyManually'
        self.origin = 'https://example.wd5.myworkdayjobs.com'
        self.action = self.origin + '/en-US/Careers/register?redirect=' + quote(self.path, safe='')
        self.button = {'action': 'create_account', 'auth_widget': 'workday', 'form_action': self.action,
                       'form_method': 'POST', 'form_id': 'form', 'auth_form': True, 'type': 'submit'}
        self.fields = [{'field_id': str(index), 'type': kind, 'label': label, 'form_id': 'form',
                        'form_action': self.action, 'form_method': 'POST', 'workday_consent': label == 'I agree'}
                       for index, (kind, label) in enumerate([('text', 'Email Address*'), ('password', 'Password*'),
                                                            ('password', 'Verify New Password*'), ('checkbox', 'I agree')])]
        self.page = {'url': self.origin + self.path, 'text': 'Example Create Account',
                     'workday_auth': {'root': '/en-US/Careers'}, 'fields': self.fields,
                     'buttons': [{'action': 'login', 'form_id': None}, self.button]}

    def test_header_login_does_not_make_signup_ambiguous(self):
        self.page['buttons'].append({**self.button, 'type': ''})
        button, fields, origin = _auth_context(self.page)
        self.assertEqual(button, self.button)
        answers, passwords = _auth_inputs(fields, 'create_account', 'fictional@example.test', {})
        self.assertEqual(len(passwords), 2)
        self.assertEqual(answers[-1]['value'], True)
        self.assertEqual(sign_in_scope({'company': 'Example'}, self.page, button), self.origin + '/en-US/Careers/')

    def test_other_site_cannot_receive_account(self):
        changed = {**self.button, 'form_action': self.action.replace('/Careers/register', '/Other/register')}
        with self.assertRaisesRegex(ValueError, 'destination changed'):
            sign_in_scope({'company': 'Example'}, self.page, changed)
