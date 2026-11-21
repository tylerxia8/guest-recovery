import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import guest_app as app
from guest_replies import queue_safety_replies
from property_instructions import solution
from research import analyze


class ReconciledWorkflowTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(app, 'DATABASE', str(Path(directory) / 'test.sqlite3')))
        app.initialize()
        now = dt.datetime.now(dt.UTC)
        self.message = {'id': 'guest-message', 'sender_type': 'guest',
                        'body': 'Where should I park?', 'created_at': now.isoformat()}
        self.conversation = {
            'reservation_id': 'reservation', 'conversation_id': 'conversation',
            'property': 'Sample property', 'property_ids': ['property-a'],
            'check_in': (now - dt.timedelta(days=1)).isoformat(),
            'check_out': (now + dt.timedelta(days=1)).isoformat(),
            'messages': [self.message], 'source_messages_fingerprint': 'a' * 64,
        }
        self.snapshot = {
            'conversations': [self.conversation], 'active_reservation_ids': ['reservation'],
            'synced_at': now.isoformat(), 'property_count': 1,
            'nontext_message_count': 0, 'scope': 'Synthetic test',
        }

    def test_approved_parking_answer_is_scoped_to_property(self):
        issue = analyze(self.snapshot)['issues'][0]
        self.assertEqual(issue['category'], 'Instructions')
        with app.connect() as db:
            db.execute(
                'INSERT INTO property_answers (property_id, intent, body, version, updated_at, question) VALUES (?, ?, ?, ?, ?, ?)',
                ('property-a', 'parking_location', 'Park in the marked space by the garage.', 1, app.now(), self.message['body']),
            )
            self.assertIn('by the garage', solution(db, issue, self.message)['body'])
            other_property = {**issue, 'property_ids': ['property-b']}
            self.assertEqual(solution(db, other_property, self.message)['body'], '')

    def test_stay_questions_use_existing_checkout_workflow(self):
        self.message['body'] = 'What time is checkout?'
        issue = analyze(self.snapshot)['issues'][0]
        self.assertIn('Check-in & checkout', issue['problem_types'][self.message['id']])
        with app.connect() as db:
            self.assertIn('Eastern Time', solution(db, issue, self.message)['body'])

    def test_automatic_guest_safety_queue_is_enabled_unless_disabled(self):
        from guest_replies import automatic_safety_replies_enabled

        with patch.dict(os.environ):
            os.environ.pop('AUTOMATIC_SAFETY_REPLIES', None)
            self.assertTrue(automatic_safety_replies_enabled())
        with patch.dict(os.environ, {'AUTOMATIC_SAFETY_REPLIES': '0'}):
            self.assertIsNone(queue_safety_replies(None, None, None, None))
