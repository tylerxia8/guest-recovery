import os
import unittest
from unittest.mock import patch

from stay_policy import request


class InternalStayPolicyTest(unittest.TestCase):
    def test_checkout_uses_schedule_instead_of_time_cutoff(self):
        stay = {
            'reservation_id': 'sample',
            'check_in': '2026-11-19T21:00:00+00:00',
            'check_out': '2026-11-21T15:00:00+00:00',
        }
        received = '2026-11-21T02:00:00+00:00'
        message = {'id': 'request', 'body': 'Can we have a late checkout?', 'created_at': received}
        with patch.dict(os.environ, {'INTERNAL_SCHEDULER_WORKSPACE_ID': 'sample'}):
            proposal = request(stay, message, received)
            self.assertTrue(proposal['can_request_scheduler'])
            self.assertEqual(proposal['target_at'], '2026-11-21T16:00:00+00:00')
            self.assertNotIn('8 p.m.', proposal['body'])
            longer = request(stay, {**message, 'body': 'Can we check out at 1 PM?'}, received)
            self.assertTrue(longer['can_request_scheduler'])
            self.assertEqual(longer['target_at'], '2026-11-21T18:00:00+00:00')
        with patch.dict(os.environ, {'INTERNAL_SCHEDULER_WORKSPACE_ID': ''}):
            self.assertEqual(request(stay, message, received)['reason'], 'after_cutoff')
