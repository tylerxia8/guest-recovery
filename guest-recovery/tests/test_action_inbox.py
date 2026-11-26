import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import nullcontext
from unittest.mock import Mock, patch
from uuid import uuid4

from action_inbox import items
from auth import AuthError
from guest_app import initialize
from private_replies import initialize as initialize_drafts
from private_supply import initialize as initialize_supply
from workspace_database import provision


class ActionInboxTests(unittest.TestCase):
    def test_only_verified_workspace_rows_are_returned(self):
        workspace, owner, other_workspace, other_owner, prop, booking = (str(uuid4()) for _ in range(6))
        with tempfile.TemporaryDirectory() as directory:
            path, _ = provision(directory, workspace, owner, initialize)
            provision(directory, other_workspace, other_owner, initialize)
            with sqlite3.connect(path) as db:
                initialize_drafts(db)
                initialize_supply(db)
                now = time.time()
                db.execute(
                    "INSERT INTO private_supply_proposals VALUES (?, ?, ?, ?, ?, ?, 1, 'pending', 0)",
                    (prop, booking, 'Fictional Lodge', 'fixture-plan', now, now + 86400),
                )
                db.execute(
                    "INSERT INTO private_reply_drafts VALUES (?, ?, ?, ?, ?, ?)",
                    (prop, booking, 'message', 'fingerprint', 1, 'private draft text'),
                )
                db.execute("""
                    INSERT INTO purchase_requests
                        (id,issue_id,property,summary,total_cents,gift_cents,decision,notice_status,created_at,updated_at)
                    VALUES ('fixture-order','fixture-issue','Fictional Lodge','private order detail',
                            1500,0,'pending','pending','2026-11-24','2026-11-24')
                """)
            lookup = Mock()
            lookup.read.return_value = json.dumps({
                'workspace_id': workspace, 'workspace_owner_id': owner, 'account_id': owner,
            }).encode()
            with patch('action_inbox.build_opener') as opener:
                opener.return_value.open.return_value = nullcontext(lookup)
                auth = 'Bearer opq_' + 'a' * 43
                result = items(directory, 'https://cleaner.example', auth)
                self.assertEqual({row['source'] for row in result}, {'guest-replies', 'supplies'})
                self.assertNotIn('private draft text', repr(result))
                self.assertNotIn('private order detail', repr(result))
                pending = next(row for row in result if row['title'] == 'Guest supply request')
                self.assertIn(booking, pending['path'])
                with sqlite3.connect(path) as db:
                    db.execute("UPDATE private_supply_proposals SET state='approved'")
                self.assertFalse(any(row['title'] == 'Guest supply request'
                                     for row in items(directory, 'https://cleaner.example', auth)))
                lookup.read.return_value = json.dumps({
                    'workspace_id': other_workspace, 'workspace_owner_id': other_owner, 'account_id': other_owner,
                }).encode()
                self.assertEqual(items(directory, 'https://cleaner.example', auth), [])
                with self.assertRaises(AuthError):
                    items(directory, 'https://cleaner.example', 'Bearer opm_' + 'a' * 43)
