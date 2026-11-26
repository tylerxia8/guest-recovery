import unittest

from auth import AuthError
from private_touchup import cleaning_payload


class PrivateTouchupTests(unittest.TestCase):
    def test_confirmed_entry_window_is_required_and_retries_are_stable(self):
        form = {
            'property_external_id': 'fixture-home',
            'reservation_external_id': 'fixture-booking',
            'release_at': '2026-11-23T12:00',
            'deadline_at': '2026-11-23T14:00',
            'scope': 'Replace bedding',
            'duration_minutes': '30',
        }
        with self.assertRaisesRegex(AuthError, 'guest agreed'):
            cleaning_payload(form)
        form['guest_access_confirmed'] = 'yes'
        first = cleaning_payload(form)
        self.assertEqual(first['release_at'], '2026-11-23T12:00:00-05:00')
        self.assertEqual(first, cleaning_payload(form))
        form['release_at'] = '2026-11-01T01:30'
        with self.assertRaises(AuthError):
            cleaning_payload(form)

    def test_stay_change_keeps_the_original_booking_and_uses_eastern_time(self):
        from private_touchup import stay_change_form, stay_change_payload

        fields = {
            'kind': 'late_checkout', 'property_external_id': 'fixture-home',
            'reservation_external_id': 'fixture-stay', 'target_at': '2026-11-23T11:00',
            'booked_check_in': '2026-11-22T21:00Z', 'booked_check_out': '2026-11-23T15:00Z',
        }
        payload = stay_change_payload(fields)
        self.assertEqual(payload['target_at'], '2026-11-23T11:00:00-05:00')
        self.assertEqual(payload['booked_check_out'], '2026-11-23T15:00:00+00:00')
        self.assertEqual(payload, stay_change_payload(fields))
        self.assertIn('action="/stay-change"', stay_change_form('csrf', {}, fields))
        self.assertEqual(stay_change_form('csrf', {}, {}), '')
        with self.assertRaises(AuthError):
            stay_change_payload({**fields, 'booked_check_out': '2026-11-23T15:00'})
