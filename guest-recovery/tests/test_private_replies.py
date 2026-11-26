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

    def test_send_only_reads_an_approved_current_saved_draft(self):
        from private_replies import form, record_outcome, send_form, send_payload

        with closing(sqlite3.connect(':memory:')) as db:
            db.row_factory = sqlite3.Row
            stay = {'property_external_id': str(uuid4()), 'reservation_external_id': str(uuid4())}
            message = {'id': 'fixture-message', 'body': 'Fixture question', 'sent_at': '2026-11-23T17:00:00Z'}
            with db:
                observed = observe_messages(db, stay, [message])[message['id']]
            thread = 'a' * 64
            fields = {
                **stay, 'message_id': message['id'], 'fingerprint': observed['fingerprint'],
                'version': '0', 'body': '<fixture answer>', 'thread_fingerprint': thread,
            }
            self.assertEqual(send_form('fixture', stay, message['id'], observed, thread), '')
            with db:
                save(db, fields)
            with self.assertRaisesRegex(AuthError, 'Approve'):
                send_payload(db, fields)
            approved = {**fields, 'approved': 'yes', 'version': '1', 'body': 'Unsaved replacement'}
            self.assertEqual(send_payload(db, approved)['body'], '<fixture answer>')
            with self.assertRaisesRegex(AuthError, 'Reload'):
                send_payload(db, {**approved, 'version': '0'})
            with db:
                latest = observe_messages(db, stay, [message])[message['id']]
            markup = send_form('fixture', stay, message['id'], latest, thread)
            self.assertIn('Send through Hospitable', markup)
            self.assertIn('&lt;fixture answer&gt;', markup)
            self.assertNotIn('<fixture answer>', markup)
            with db:
                record_outcome(db, approved, 'unknown')
                record_outcome(db, approved, 'accepted')
                record_outcome(db, approved, 'unknown')
                accepted = observe_messages(db, stay, [message])[message['id']]
            self.assertIn('Hospitable accepted', form('fixture', stay, message['id'], accepted))
            self.assertEqual(send_form('fixture', stay, message['id'], accepted, thread), '')
            with db:
                observe_messages(db, stay, [{**message, 'body': 'Corrected question'}])
            with self.assertRaisesRegex(AuthError, 'Reload'):
                send_payload(db, approved)
