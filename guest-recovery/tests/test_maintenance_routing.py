import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import guest_app
from maintenance_bridge import repair_note, synchronize_pass


class MaintenanceRoutingTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(guest_app, 'DATABASE', str(Path(directory) / 'test.sqlite3')))
        guest_app.initialize()
        self.now = dt.datetime.now(dt.UTC)
        self.property_id = '11111111-1111-4111-8111-111111111111'
        self.settings = {
            'configured': True, 'mode': 'workspace', 'properties': [{'external_property_id': self.property_id}],
            'organization_id': 'workspace', 'expires_at': 'expiry', 'url': 'https://maintenance.example',
        }
        self.issue_id = 'reservation:maintenance'

    def insert_messages(self, *bodies):
        messages = [
            {'id': str(index), 'body': body, 'sender_type': 'guest',
             'created_at': (self.now - dt.timedelta(minutes=5-index)).isoformat()}
            for index, body in enumerate(bodies)
        ]
        conversation = {
            'reservation_id': 'reservation', 'conversation_id': 'conversation', 'property': 'Synthetic property',
            'property_ids': [self.property_id], 'messages': messages,
            'check_in': (self.now-dt.timedelta(days=1)).isoformat(),
            'check_out': (self.now+dt.timedelta(days=1)).isoformat(),
        }
        snapshot = {'synced_at': self.now.isoformat(), 'conversations': [conversation],
                    'active_reservation_ids': ['reservation'], 'property_count': 1,
                    'nontext_message_count': 0, 'scope': 'Test'}
        with guest_app.connect() as db:
            db.execute('INSERT OR REPLACE INTO research_snapshot VALUES (1, ?)', (json.dumps(snapshot),))

    def test_creates_one_repair_and_appends_labeled_followup_once(self):
        self.insert_messages('The kitchen sink is broken.', 'The kitchen sink still is not working.')
        ticket = {'issue_id': self.issue_id, 'ticket_id': '22222222-2222-4222-8222-222222222222',
                  'ticket_number': 3, 'status': 'ASSIGNMENT_PENDING', 'priority': 'ROUTINE',
                  'source_fingerprint': 'a' * 64, 'updated_at': self.now.isoformat()}
        with patch('maintenance_bridge.configuration', return_value=self.settings), \
                patch('maintenance_bridge.perform_change', return_value=ticket) as create, \
                patch('maintenance_bridge._forward_message', return_value=ticket) as append:
            for _ in range(3):
                synchronize_pass(guest_app.connect, self.now.isoformat())
        create.assert_called_once()
        self.assertEqual(create.call_args.args[0]['payload']['description'], 'The kitchen sink is broken.')
        append.assert_called_once()
        self.assertEqual(append.call_args.args[1:], ('1', 'The kitchen sink still is not working.'))
        with guest_app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM maintenance_links').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM maintenance_synced_messages').fetchone()[0], 2)

    def test_blocks_demo_and_unmapped_properties(self):
        self.insert_messages('The kitchen sink is broken.')
        for settings in (self.settings | {'mode': 'demo'}, self.settings | {'properties': []}):
            with patch('maintenance_bridge.configuration', return_value=settings), \
                    patch('maintenance_bridge.perform_change') as create:
                synchronize_pass(guest_app.connect, self.now.isoformat())
                create.assert_not_called()

    def test_repairs_exclude_credentials_and_emergencies(self):
        message = {'sender_type': 'guest', 'body': 'The sink is broken. The entry code is 1234.'}
        self.assertEqual(repair_note(message), 'The sink is broken.')
        self.assertEqual(repair_note(message | {'body': 'The stove is broken and I smell gas.'}), '')
