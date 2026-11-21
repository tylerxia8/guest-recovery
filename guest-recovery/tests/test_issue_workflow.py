import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from issue_workflow import attach, save
from research import analyze


def snapshot(*texts, upcoming=False):
    now = dt.datetime.now(dt.timezone.utc)
    return {
        'conversations': [{
            'reservation_id': 'sample', 'property': 'Sample property',
            'check_in': (now + dt.timedelta(days=1 if upcoming else -1)).isoformat(),
            'check_out': (now + dt.timedelta(days=2)).isoformat(),
            'messages': [
                {'id': str(index), 'sender_type': 'guest', 'body': text,
                 'created_at': (now + dt.timedelta(seconds=index)).isoformat()}
                for index, text in enumerate(texts)
            ],
        }],
        'active_reservation_ids': [] if upcoming else ['sample'],
        'upcoming_reservation_ids': ['sample'] if upcoming else [],
        'synced_at': now.isoformat(), 'scope': 'Synthetic test',
        'property_count': 1, 'nontext_message_count': 0,
    }


class IssueWorkflowTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(app, 'DATABASE', str(Path(directory) / 'test.sqlite3')))
        app.initialize()

    def test_mock_categories(self):
        examples = {
            'Timing': "Could we check out at noon on Sunday?",
            'Maintenance': "Water is pooling under the kitchen sink when we use it.",
            'Supplies': "Is there a remote for the downstairs living room TV?",
            'Safety': "The back door won't latch, so we can't secure the house.",
            'Cleaning': "The kitchen trash was full when we arrived. Can someone pick it up?",
            'Instructions': "Which of the two parking spaces is ours?",
            'Other': "Can we add one overnight guest to the reservation?",
        }
        for expected, text in examples.items():
            with self.subTest(text=text):
                self.assertEqual([issue['category'] for issue in analyze(snapshot(text))['issues']], [expected])

    def test_upcoming_timing_request_is_visible_and_notes_are_saved(self):
        source = snapshot('Can we check in around 1?', upcoming=True)
        issue = analyze(source)['issues'][0]
        self.assertEqual(issue['lane'], 'needs_attention')
        with app.connect() as db:
            db.execute('INSERT INTO research_snapshot VALUES (1, ?)', (json.dumps(source),))
            attach(db, issue)
            request = {'issue_id': issue['id'], 'version': 0, 'fingerprint': issue['workflow']['fingerprint'],
                       'decision': 'approved', 'note': '1 PM Eastern; cleaner confirmed.'}
            self.assertEqual(save(db, request, app.now()), {'version': 1})
            attach(db, issue)
            self.assertEqual(issue['workflow']['decision'], 'approved')
            self.assertEqual(issue['workflow']['note'], request['note'])
            with self.assertRaisesRegex(ValueError, 'issue changed'):
                save(db, request, app.now())

    def test_safety_requires_acknowledgment_and_reopens_with_new_evidence(self):
        source = snapshot('The carbon monoxide alarm is sounding upstairs.')
        with app.connect() as db:
            db.execute('INSERT INTO research_snapshot VALUES (1, ?)', (json.dumps(source),))
            issue = analyze(source)['issues'][0]
            attach(db, issue)
            request = {'issue_id': issue['id'], 'version': 0, 'fingerprint': issue['workflow']['fingerprint'],
                       'decision': 'resolved', 'note': 'Manager follow-up'}
            with self.assertRaisesRegex(ValueError, 'Acknowledge'):
                save(db, request, app.now())
            save(db, request | {'decision': 'acknowledged'}, app.now())
            save(db, request | {'version': 1}, app.now())
            updated = snapshot('The carbon monoxide alarm is sounding upstairs.',
                               'The carbon monoxide alarm started again.')
            db.execute('UPDATE research_snapshot SET body = ? WHERE id = 1', (json.dumps(updated),))
            issue = analyze(updated)['issues'][0]
            attach(db, issue)
            self.assertTrue(issue['workflow']['stale'])
            with self.assertRaisesRegex(ValueError, 'Acknowledge'):
                save(db, request | {'version': 2, 'fingerprint': issue['workflow']['fingerprint']}, app.now())
