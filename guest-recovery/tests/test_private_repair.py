import unittest
from uuid import uuid4

from auth import AuthError
from private_repair import repair_payload


class PrivateRepairTests(unittest.TestCase):
    def test_retry_preserves_issue_identity_and_booking(self):
        fields = {
            'property_external_id': str(uuid4()),
            'reservation_external_id': str(uuid4()),
            'description': '  Bathroom sink is dripping.  ',
        }
        first = repair_payload(fields)
        self.assertEqual(first, repair_payload(fields))
        self.assertEqual(first['description'], 'Bathroom sink is dripping.')
        self.assertEqual(first['reservation_id'], fields['reservation_external_id'])
        self.assertNotEqual(first['issue_id'], repair_payload({**fields, 'description': 'Different issue'})['issue_id'])
        with self.assertRaises(AuthError):
            repair_payload({**fields, 'property_external_id': 'invalid'})
