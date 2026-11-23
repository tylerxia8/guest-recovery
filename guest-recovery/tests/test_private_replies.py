import sqlite3
import unittest
from contextlib import closing
from uuid import uuid4

from auth import AuthError
from private_replies import observe_messages, save


class PrivateReplyTests(unittest.TestCase):
    def test_replies_are_scoped_to_verified_workspace_and_message_version(self):
        with closing(sqlite3.connect(':memory:')) as first, closing(sqlite3.connect(':memory:')) as second:
            first.row_factory = second.row_factory = sqlite3.Row
            stay = {'property_external_id': str(uuid4()), 'reservation_external_id': str(uuid4())}
            message = {'id': 'fixture-message', 'body': 'Fixture question', 'sent_at': '2026-11-23T17:00:00Z'}
            with first:
                observed = observe_messages(first, stay, [message])[message['id']]
            details = {
                **stay, 'message_id': message['id'], 'fingerprint': observed['fingerprint'],
                'version': str(observed['version']), 'body': 'Fixture answer',
            }
            with second, self.assertRaisesRegex(AuthError, 'Reload the conversation'):
                save(second, details)
            with first:
                save(first, details)
            with first:
                save(first, details)
            with first, self.assertRaisesRegex(AuthError, 'draft changed'):
                save(first, {**details, 'body': 'Different draft from an old tab'})
            with first:
                updated = observe_messages(first, stay, [{**message, 'body': 'Edited question'}])[message['id']]
            self.assertTrue(updated['stale'])
            self.assertEqual(updated['body'], 'Fixture answer')
            with first, self.assertRaisesRegex(AuthError, 'Reload the conversation'):
                save(first, details)
