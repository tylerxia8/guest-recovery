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
