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
        self.assertIn('Review and approve', payload['text'])
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

    def test_manager_can_approve_selected_items_once(self):
        from manual_orders import approve
        with patch('manual_orders.send_email', return_value={'id': 'email-reference'}):
            send_ready(guest_app.connect, self.settings)
        with guest_app.connect() as db:
            notice = db.execute('SELECT * FROM manual_order_notices').fetchone()
            request = {'issue_id': notice['issue_id'], 'fingerprint': notice['fingerprint']}
            with self.assertRaisesRegex(ValueError, 'changed'):
                approve(db, {**request, 'fingerprint': 'old-quote'}, guest_app.now())
            self.assertEqual(approve(db, request, guest_app.now()), {'approved': True})
            self.assertEqual(approve(db, request, guest_app.now()), {'approved': True})
            self.assertEqual(db.execute("SELECT COUNT(*) FROM audit WHERE action = 'manual_amazon_order_approved'").fetchone()[0], 1)

    def test_manager_cannot_approve_after_delivery_details_change(self):
        from manual_orders import approve
        with patch('manual_orders.send_email', return_value={'id': 'email-reference'}):
            send_ready(guest_app.connect, self.settings)
        with guest_app.connect() as db:
            notice = db.execute('SELECT * FROM manual_order_notices').fetchone()
            db.execute("UPDATE fulfillment_profiles SET imported_address = '{}'")
            with self.assertRaisesRegex(ValueError, 'changed'):
                approve(db, {'issue_id': notice['issue_id'], 'fingerprint': notice['fingerprint']}, guest_app.now())

    def test_guest_acknowledgment_is_queued_once_without_promising_delivery(self):
        self.conversation['source_messages_fingerprint'] = 'a' * 64
        self.save_snapshot()
        with patch.dict('os.environ', {'HOSPITABLE_TOKEN': 'test-token'}), patch(
            'manual_orders.send_email', return_value={'id': 'email-reference'}
        ):
            send_ready(guest_app.connect, self.settings)
            send_ready(guest_app.connect, self.settings)
        with guest_app.connect() as db:
            sends = db.execute('SELECT state, body, context FROM guest_reply_sends').fetchall()
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0]['state'], 'queued')
        self.assertTrue(json.loads(sends[0]['context'])['automatic_supply'])
        self.assertIn('once we have confirmed details', sends[0]['body'])
        self.assertNotIn('Amazon', sends[0]['body'])

    def test_guest_acknowledgment_skips_conversations_staff_answered(self):
        self.conversation['source_messages_fingerprint'] = 'a' * 64
        self.conversation['messages'].append({
            'id': 'staff-reply', 'sender_type': 'host',
            'body': 'I am checking for spare supplies.', 'created_at': guest_app.now(),
        })
        self.save_snapshot()
        with patch.dict('os.environ', {'HOSPITABLE_TOKEN': 'test-token'}), patch(
            'manual_orders.send_email', return_value={'id': 'email-reference'}
        ):
            send_ready(guest_app.connect, self.settings)
        with guest_app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM guest_reply_sends').fetchone()[0], 0)
