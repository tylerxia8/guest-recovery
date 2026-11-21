import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import maintenance_bridge
from research import analyze
from test_issue_workflow import snapshot


class MaintenanceBridgeTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(app, 'DATABASE', str(Path(directory) / 'test.sqlite3')))
        app.initialize()

    def test_sends_reviewed_summary_and_refreshes_ticket(self):
        source = snapshot('Water is pooling under the kitchen sink.')
        source['conversations'][0].update(property_ids=['11111111-1111-4111-8111-111111111111'],
                                          conversation_id='thread', source_messages_fingerprint='a' * 64)
        issue = analyze(source)['issues'][0]
        ticket = {'issue_id': issue['id'], 'ticket_id': 'ticket', 'ticket_number': 7,
                  'status': 'ASSIGNMENT_PENDING', 'priority': 'ROUTINE', 'updated_at': app.now()}
        with app.connect() as db, patch.object(maintenance_bridge, 'call') as call:
            call.side_effect = [ticket, ticket | {'status': 'COMPLETED'}]
            db.execute('INSERT INTO research_snapshot VALUES (1, ?)', (json.dumps(source),))
            maintenance_bridge.attach(db, issue)
            data = {'issue_id': issue['id'], 'fingerprint': issue['maintenance']['fingerprint'],
                    'description': 'Inspect the sink drain for a leak.'}
            with self.assertRaisesRegex(ValueError, 'changed'):
                app.mutate(db, '/api/maintenance', data | {'fingerprint': 'stale'})
            call.assert_not_called()
            self.assertEqual(app.mutate(db, '/api/maintenance', data)['ticket_number'], 7)
            self.assertEqual(call.call_args.args, ('/requests', {
                'issue_id': 'sample:maintenance', 'external_property_id': source['conversations'][0]['property_ids'][0],
                'reservation_id': 'sample', 'conversation_id': 'thread',
                'description': data['description'], 'source_fingerprint': 'a' * 64,
            }))
            self.assertEqual(app.mutate(db, '/api/maintenance/status', {'issue_id': issue['id']})['status'], 'COMPLETED')
            call.assert_called_with('/requests/sample%3Amaintenance')
            maintenance_bridge.attach(db, issue)
            self.assertEqual(issue['maintenance']['ticket']['status'], 'COMPLETED')
