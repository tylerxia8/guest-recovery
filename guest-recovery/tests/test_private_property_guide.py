import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from auth import AuthError
from guest_app import connect, initialize
from private_property_guide import approve, guides
from private_replies import observe_messages


class SharedPropertyGuideTests(unittest.TestCase):
    def test_approved_answer_stays_with_its_property_and_requires_fresh_source(self):
        message = {'id': 'parking-fixture', 'body': 'Can I park here?', 'sent_at': '2026-11-26T12:00:00Z'}
        home, other_home, booking = (str(uuid4()) for _ in range(3))
        fields = {'property_external_id': home, 'reservation_external_id': booking}
        with TemporaryDirectory() as directory:
            database = Path(directory) / 'fixture.sqlite3'
            initialize(database)
            with connect(database) as db:
                observed = observe_messages(db, fields, [message])['parking-fixture']
                self.assertEqual(guides(db, home, message)[0]['body'], '')
            approval = {
                **fields, 'approved': 'yes', 'fingerprint': observed['fingerprint'], 'message_id': message['id'],
                'version': '0', 'intent': 'parking_location', 'body': 'Please use the marked driveway space.',
            }
            with connect(database) as db, self.assertRaisesRegex(AuthError, 'Reload the conversation'):
                approve(db, {**approval, 'property_external_id': other_home})
            with connect(database) as db, self.assertRaisesRegex(AuthError, 'general property facts'):
                approve(db, {**approval, 'body': 'Use door code 1234.'})
            with connect(database) as db:
                approve(db, approval)
            with connect(database) as db:
                self.assertEqual(guides(db, home, message)[0]['body'], approval['body'])
                self.assertEqual(guides(db, other_home, message)[0]['body'], '')
                self.assertEqual(guides(db, home, {**message, 'body': 'What is the Wi-Fi password?'}), [])
            with connect(database) as db, self.assertRaisesRegex(AuthError, 'changed this instruction'):
                approve(db, approval)
