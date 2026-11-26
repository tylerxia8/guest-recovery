import datetime as dt
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from auth import AuthError
from guest_app import connect, initialize
from private_supply import approve
from private_triage import case_actions, render_actions


class SharedTriageTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 11, 26, 14, tzinfo=dt.UTC)
        self.fields = {'property_external_id': str(uuid4()), 'reservation_external_id': str(uuid4())}
        self.data = {
            'effective_check_in': (self.now - dt.timedelta(days=1)).isoformat(),
            'effective_check_out': (self.now + dt.timedelta(days=1)).isoformat(),
            'property_name': 'Fixture house',
        }

    def actions(self, *texts):
        message_data = [
            {'id': str(index), 'sent_at': (self.now - dt.timedelta(minutes=10-index)).isoformat(), 'body': text}
            for index, text in enumerate(texts)
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.sqlite3'
            initialize(path)
            with connect(path) as db:
                return case_actions(db, self.fields, {**self.data, 'messages': message_data}, self.now)

    def test_selects_a_catalog_product_and_pack_count_for_active_supply_case(self):
        actions = self.actions('We are out of toilet paper. Can we have two rolls of toilet paper?')
        markup = render_actions(actions['0'])
        self.assertIn('Suggested supplies', markup)
        self.assertIn('https://www.amazon.com/dp/B011DX5G92', markup)
        self.assertIn('1 pack', markup)
        self.assertIn('Confirm stock', markup)

    def test_resolved_supply_report_does_not_suggest_an_order(self):
        actions = self.actions('We are out of toilet paper.', 'Never mind, we found the toilet paper.')
        self.assertNotIn('Suggested supplies', render_actions(actions.get('0', {})))

    def test_safety_guidance_takes_priority_and_never_assumes_a_local_utility(self):
        actions = self.actions('We smell gas at the property. We also need toilet paper.')
        markup = render_actions(actions['0'], cleaning_access=True, repair_access=True)
        self.assertIn('Safety report', markup)
        self.assertIn('call local emergency services', markup)
        self.assertNotIn('Piedmont', markup)
        self.assertNotIn('amazon.com', markup)

    def test_repair_and_cleaning_links_require_the_matching_access(self):
        actions = self.actions('The bathroom sink is not working.', 'The bedding is dirty and needs replacing.')
        self.assertIn('Maintenance intake', render_actions(actions['0'], repair_access=True))
        self.assertNotIn('Maintenance intake', render_actions(actions['0']))
        self.assertIn('Request a cleaning touch-up', render_actions(actions['1'], cleaning_access=True))


    def test_manager_can_approve_only_the_current_proposal_for_its_stay(self):
        now = dt.datetime.now(dt.UTC)
        data = {
            **self.data,
            'effective_check_in': (now - dt.timedelta(days=1)).isoformat(),
            'effective_check_out': (now + dt.timedelta(days=1)).isoformat(),
            'messages': [{'id': 'first', 'sent_at': now.isoformat(), 'body': 'We are out of toilet paper.'}],
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.sqlite3'
            initialize(path)
            with connect(path) as db:
                original = case_actions(db, self.fields, data, now)['first']['proposal']
            fields = {**self.fields, 'approved': 'yes', 'fingerprint': original['fingerprint']}
            with connect(path) as db, self.assertRaises(AuthError):
                approve(db, {**fields, 'reservation_external_id': str(uuid4())})
            with connect(path) as db:
                approve(db, fields)
            with connect(path) as db:
                current = case_actions(db, self.fields, data, now)['first']
            self.assertIn('Approved for manual ordering', render_actions(current, csrf='token', fields=self.fields))
            changed = {**data, 'messages': [{**data['messages'][0], 'body': 'We are out of paper towels.'}]}
            with connect(path) as db:
                proposed = case_actions(db, self.fields, changed, now)['first']
            self.assertEqual(proposed['proposal']['state'], 'pending')
            self.assertTrue(proposed['proposal']['prior_approval'])
            with connect(path) as db, self.assertRaisesRegex(AuthError, 'latest supply proposal'):
                approve(db, fields)
