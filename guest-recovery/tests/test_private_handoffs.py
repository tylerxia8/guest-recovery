import unittest

import sqlite3
from auth import AuthError
from contextlib import closing
from private_handoffs import case_links, initialize, lookup, remember, render_status


class PrivateHandoffTest(unittest.TestCase):
    def test_saved_reference_links_only_to_the_original_stay(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.row_factory = sqlite3.Row
            initialize(db)
            remember(db, 'repair', 'repair&1', 'property', 'stay')
            remember(db, 'repair', 'repair&1', 'other', 'other')
            remember(db, 'cleaning', 'job-2', 'property', 'stay')
            self.assertIn('Check repair status', case_links(db, 'property', 'stay'))
            self.assertIn('repair%261', case_links(db, 'property', 'stay'))
            self.assertIn('Check cleaning status', case_links(db, 'property', 'stay'))
            self.assertEqual(case_links(db, 'property', 'other'), '')
            self.assertEqual(lookup(db, {'kind': ['repair'], 'request_id': ['repair&1']})['reservation_id'], 'stay')
            with self.assertRaises(AuthError):
                lookup(db, {'kind': ['cleaning'], 'request_id': ['repair&1']})

    def test_status_escapes_repair_question_and_keeps_intake_available(self):
        html = render_status(
            'repair',
            {'status': 'IN_PROGRESS', 'ticket_number': 10, 'next_question': {'field_key': 'room', 'body': '<Where?>'}},
            'csrf', {'external_property_id': 'home', 'reservation_id': 'stay', 'issue_id': 'repair'},
        )
        self.assertIn('In progress', html)
        self.assertIn('Ticket 10', html)
        self.assertIn('&lt;Where?&gt;', html)
        self.assertIn('action="/repair-answer"', html)
        self.assertIn('/messages?property_external_id=home&amp;reservation_external_id=stay', html)
