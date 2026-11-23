import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import guest_app
from cleaner_returns import queue_ready_visits, withdraw_changed_automatic_visit
from guest_replies import queue_safety_replies, queue_stay_reply


class GuestAutomationTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(guest_app, 'DATABASE', str(Path(directory) / 'test.sqlite3')))
        guest_app.initialize()
        self.now = dt.datetime(2026, 11, 21, 15, tzinfo=dt.UTC)
        self.timestamp = self.now.isoformat()
        self.workspace = '11111111-1111-4111-8111-111111111111'
        self.enterContext(patch.dict(os.environ, {'INTERNAL_SCHEDULER_WORKSPACE_ID': self.workspace}))
        self.message = {'id': 'first', 'body': 'Can we check out an hour late?',
                        'created_at': self.timestamp, 'sender_type': 'guest'}
        self.issue = {
            'id': 'reservation:instructions', 'category': 'Instructions',
            'reservation_id': 'reservation', 'property_ids': ['property'],
            'conversation_id': 'conversation', 'source_messages_fingerprint': 'c' * 64,
            'check_in': (self.now - dt.timedelta(days=1)).isoformat(),
            'check_out': (self.now + dt.timedelta(days=1)).isoformat(),
            'evidence': [self.message], 'messages': [self.message], 'lane': 'needs_attention',
        }

    def test_only_current_rule_confirmed_stay_reply_is_queued_once(self):
        plan = {'request_id': 'request', 'kind': 'late_checkout', 'reason': 'schedule_confirmation_required',
                'target_at': '2026-11-22T16:00:00+00:00',
                'current_checkout_at': '2026-11-22T15:00:00+00:00',
                'current_checkin_at': self.issue['check_in'], 'decision': 'pending', 'schedule_based': True}
        remote = {'request_id': 'request', 'decision': 'approved', 'target_at': plan['target_at']}
        row = {'id': 'request', 'scheduler_url': 'internal-scheduler:' + self.workspace,
               'state': 'approved', 'reservation_id': 'reservation', 'message_id': 'first',
               'plan': json.dumps(plan), 'remote': json.dumps(remote)}
        body = 'Your late checkout is confirmed for 11:00 AM Eastern Time.'
        prepared = {'sending_enabled': True, 'send': None, 'updated_at': None, 'safety': False,
                    'needs_manager': False, 'stay_policy_approvals': ['request'], 'suggested': body,
                    'body': body, 'source_fingerprint': 'fingerprint', 'version': 0,
                    'instruction_versions': [], 'instruction_context': 'answer'}
        with guest_app.connect() as db, patch('research.analyze', return_value={'issues': [self.issue]}), \
                patch('guest_replies.draft', return_value=prepared):
            db.execute('INSERT INTO research_snapshot VALUES (1, ?)', (json.dumps({'synced_at': self.timestamp}),))
            queue_stay_reply(db, row | {'state': 'pending'}, self.timestamp)
            self.assertIsNone(db.execute('SELECT id FROM guest_reply_sends').fetchone())
            queue_stay_reply(db, row, self.timestamp)
            queue_stay_reply(db, row, self.timestamp)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM guest_reply_sends').fetchone()[0], 1)
            queued = db.execute('SELECT body, context FROM guest_reply_sends').fetchone()
            self.assertEqual(queued['body'], body)
            self.assertTrue(json.loads(queued['context'])['automatic_stay'])

    def test_cleaning_requires_explicit_consent_and_queues_once(self):
        self.message['body'] = 'The sheets are dirty.'
        permission = {'id': 'permission', 'body': 'The cleaner can enter today from 1 PM to 3 PM.',
                      'sender_type': 'guest', 'created_at': (self.now + dt.timedelta(minutes=1)).isoformat()}
        self.issue.update(id='reservation:cleaning', category='Cleaning')
        snapshot = {'synced_at': self.timestamp}
        check_time = (self.now + dt.timedelta(minutes=2)).isoformat()
        with guest_app.connect() as db, patch('research.analyze', return_value={'issues': [self.issue]}), \
                patch('fulfillment.profile', return_value={'address': {'timezone': 'America/New_York'}}):
            db.execute('INSERT INTO research_snapshot VALUES (1, ?)', (json.dumps(snapshot),))
            queue_ready_visits(db, snapshot, check_time)
            self.assertIsNone(db.execute('SELECT id FROM cleaner_returns').fetchone())
            self.issue['messages'] = [self.message, permission]
            queue_ready_visits(db, snapshot, check_time)
            queue_ready_visits(db, snapshot, check_time)
            rows = db.execute('SELECT * FROM cleaner_returns').fetchall()
            self.assertEqual(len(rows), 1)
            plan = json.loads(rows[0]['body'])
            self.assertTrue(plan['automatic'])
            self.assertTrue(plan['request']['guest_access_confirmed'])
            self.issue['messages'].append({**permission, 'id': 'changed', 'body': 'No cleaner is needed.',
                                          'created_at': check_time})
            self.assertTrue(withdraw_changed_automatic_visit(db, rows[0], plan, check_time))

    def test_qualifying_safety_report_queues_without_approval(self):
        self.message['body'] = 'I smell gas in the kitchen.'
        conversation = {**self.issue, 'messages': [self.message]}
        alert = {'reservation_id': 'reservation', 'message_id': 'first'}
        with guest_app.connect() as db, patch.dict(os.environ, {'AUTOMATIC_SAFETY_REPLIES': '1'}), \
                patch('fulfillment.profile', return_value=None):
            for _ in range(2):
                queue_safety_replies(db, [alert], {'conversations': [conversation]}, self.timestamp)
            rows = db.execute('SELECT body, context FROM guest_reply_sends').fetchall()
            self.assertEqual(len(rows), 1)
            self.assertTrue(json.loads(rows[0]['context'])['automatic_safety'])
