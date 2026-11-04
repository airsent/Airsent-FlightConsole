import unittest
from presence_auth import authenticate, issue_token, token_role
class PresenceAuthTests(unittest.TestCase):
    def test_role_verified_against_credentials(self):
        passwords={'operator':'op-test','viewer':'view-test'}
        self.assertEqual(authenticate('operator','op-test',passwords),'operator')
        self.assertEqual(authenticate('viewer','view-test',passwords),'viewer')
        self.assertIsNone(authenticate('operator','view-test',passwords))
    def test_signed_role_survives_refresh_and_rejects_tampering(self):
        token=issue_token('operator','test-secret',now=100)
        self.assertEqual(token_role(token,'test-secret',now=200),'operator')
        self.assertIsNone(token_role(token,'wrong-secret',now=200))
        self.assertIsNone(token_role(token+'x','test-secret',now=200))
        self.assertIsNone(token_role(token,'test-secret',now=3000000))
        self.assertIsNone(token_role('','test-secret'))
