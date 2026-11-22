import datetime as dt
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import guest_app
from manual_orders import send_ready


class ManualOrderTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.object(guest_app, 'DATABASE', str(Path(directory) / 'test.sqlite3')))
        guest_app.initialize()
        now = dt.datetime.now(dt.UTC)
        self.conversation = {
            'reservation_id': 'reservation', 'conversation_id': 'conversation',
            'property': 'Sample property', 'property_ids': ['property-a'],
            'check_in': (now - dt.timedelta(days=1)).isoformat(),
            'check_out': (now + dt.timedelta(days=1)).isoformat(),
            'messages': [
                {'id': 'request-a', 'sender_type': 'guest', 'body': 'We ran out of toilet paper.', 'created_at': now.isoformat()},
                {'id': 'request-b', 'sender_type': 'guest', 'body': 'We also need trash bags.', 'created_at': now.isoformat()},
            ],
        }
        self.snapshot = {
            'conversations': [self.conversation], 'active_reservation_ids': ['reservation'],
            'synced_at': now.isoformat(), 'property_count': 1, 'nontext_message_count': 0, 'scope': 'Synthetic test',
        }
        self.settings = {'NOTIFICATION_FROM': 'sender@example.com', 'MANAGER_EMAIL': 'manager@example.com'}
        with guest_app.connect() as db:
            db.execute("INSERT INTO integration_state VALUES (1, 'connected', ?, ?, NULL)", (now.isoformat(), now.isoformat()))
            db.execute("INSERT INTO rental_units VALUES ('property-a', 'Sample property')")
            db.execute("INSERT INTO fulfillment_profiles VALUES ('property-a', ?, NULL, 1)", (json.dumps({
                'line1': '1 Fictional Drive', 'city': 'Charlotte', 'region': 'NC', 'postal_code': '28202',
                'country': 'US', 'timezone': 'America/New_York',
            }),))
        self.save_snapshot()

    def save_snapshot(self):
        with guest_app.connect() as db:
            db.execute('INSERT OR REPLACE INTO research_snapshot VALUES (1, ?)', (json.dumps(self.snapshot),))

    def test_current_request_emails_exact_products_once_without_quote(self):
        with patch('manual_orders.send_email', return_value={'id': 'email-reference'}) as mail:
            send_ready(guest_app.connect, self.settings)
            send_ready(guest_app.connect, self.settings)
        mail.assert_called_once()
        payload = mail.call_args.args[1]
        self.assertEqual(payload['to'], ['manager@example.com'])
        self.assertIn('Please place this order', payload['text'])
        self.assertIn('https://www.amazon.com/dp/B011DX5G92', payload['text'])
        self.assertIn('https://www.amazon.com/dp/B01DM5EZ50', payload['text'])
        self.assertIn('1 pack(s)', payload['text'])

    def test_incomplete_address_and_unconfirmed_email_do_not_trigger_duplicate_orders(self):
        with guest_app.connect() as db:
            db.execute("UPDATE fulfillment_profiles SET imported_address = '{}'")
        with patch('manual_orders.send_email', side_effect=TimeoutError) as mail:
            send_ready(guest_app.connect, self.settings)
            mail.assert_not_called()
            with guest_app.connect() as db:
                db.execute("UPDATE fulfillment_profiles SET imported_address = ?", (json.dumps({
                    'line1': '1 Fictional Drive', 'city': 'Charlotte', 'region': 'NC', 'postal_code': '28202',
                    'country': 'US', 'timezone': 'America/New_York',
                }),))
            send_ready(guest_app.connect, self.settings)
            send_ready(guest_app.connect, self.settings)
            mail.assert_called_once()
            with guest_app.connect() as db:
                self.assertEqual(db.execute('SELECT status FROM manual_order_notices').fetchone()['status'], 'unknown')

    def test_inactive_stay_does_not_email_manager(self):
        self.snapshot['active_reservation_ids'] = []
        self.save_snapshot()
        with patch('manual_orders.send_email') as mail:
            send_ready(guest_app.connect, self.settings)
        mail.assert_not_called()

    def test_quoted_proposals_at_both_prices_require_manual_checkout(self):
        from purchases import describe
        for cents in (2500, 2501):
            result = describe({'total_cents': cents, 'decision': 'pending', 'notice_status': 'delivered',
                               'email_id': 'email', 'notified_at': guest_app.now(), 'offer': None, 'email_payload': None})
            self.assertEqual(result['status'], 'Manager to place Amazon order')
            self.assertIsNone(result['eligible_at'])
