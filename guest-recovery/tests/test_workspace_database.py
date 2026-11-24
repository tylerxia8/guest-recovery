import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from guest_app import initialize
from workspace_database import provision


class WorkspaceDatabaseTests(unittest.TestCase):
    def test_independent_accounts_get_private_databases(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace_a, workspace_b, account_a, account_b = (str(uuid4()) for _ in range(4))
            path_a, owner_a = provision(directory, workspace_a, account_a, initialize)
            path_b, owner_b = provision(directory, workspace_b, account_b, initialize)
            self.assertNotEqual(path_a, path_b)
            self.assertEqual((owner_a['id'], owner_b['id']), (account_a, account_b))
            with sqlite3.connect(path_a) as connection:
                connection.execute("INSERT INTO research_snapshot VALUES (1, '{\"case\":\"fixture A\"}')")
            with sqlite3.connect(path_b) as connection:
                self.assertIsNone(connection.execute('SELECT body FROM research_snapshot').fetchone())
                self.assertEqual(connection.execute('SELECT id FROM users').fetchall(), [(account_b,)])
            self.assertEqual(provision(directory, workspace_a, account_a, initialize)[0], path_a)
            self.assertEqual(path_a.stat().st_mode & 0o777, 0o600)
            with self.assertRaisesRegex(ValueError, 'different account'):
                provision(directory, workspace_a, account_b, initialize)

    def test_invited_manager_uses_the_existing_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, owner, manager = (str(uuid4()) for _ in range(3))
            path, _ = provision(directory, workspace, owner, initialize)
            shared_path, member = provision(directory, workspace, manager, initialize, owner_id=owner)
            self.assertEqual(shared_path, path)
            self.assertEqual(member['id'], manager)
            with sqlite3.connect(path) as db:
                self.assertEqual({row[0] for row in db.execute('SELECT id FROM users')}, {owner, manager})

    def test_refuses_existing_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, account = (str(uuid4()) for _ in range(2))
            destination = Path(directory) / 'other.sqlite3'
            (Path(directory) / (workspace + '.sqlite3')).symlink_to(destination)
            with self.assertRaisesRegex(ValueError, 'must be private'):
                provision(directory, workspace, account, initialize)
            self.assertFalse(destination.exists())

    def test_verified_callback_provisions_and_workspace_switch_cannot_reuse_cookie(self):
        import os
        from unittest.mock import Mock, patch

        from guest_app import Handler

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {'OPERATIONS_TURNOVER_ORIGIN': 'https://turnover.example'}
        ):
            handler = object.__new__(Handler)
            handler.command = 'GET'
            handler.path = '/auth/operations/callback'
            handler.headers = {}
            handler.respond = Mock()
            workspace, account = (str(uuid4()) for _ in range(2))
            with patch('operations_login.redeem', return_value=(
                {'workspace_id': workspace, 'account_id': account, 'workspace_owner_id': account,
                 'membership_token': 'opm_' + 'a' * 43}, 'fixture-login',
            )):
                handler.private_workspace(directory, 'https://recovery.example', {})
            headers = handler.respond.call_args.args[3]
            cookies = [value.split(';', 1)[0] for name, value in headers if name == 'Set-Cookie']
            handler.path = '/'
            handler.headers = {'Cookie': '; '.join(cookies)}
            membership = patch('operations_login.verify_membership', return_value=True)
            access = membership.start()
            self.addCleanup(membership.stop)
            with patch('operations_login.stays', return_value={
                'stays': [{'property_name': '<b>Fixture House</b>', 'checkin_at': 'Arrival', 'checkout_at': 'Departure'}],
            }) as read_stays:
                handler.private_workspace(directory, 'https://recovery.example', {})
                read_stays.assert_called_once()
            self.assertEqual(handler.respond.call_args.args[0], 200)
            self.assertIn(b'&lt;b&gt;Fixture House&lt;/b&gt;', handler.respond.call_args.args[1])

            handler.path = '/messages'
            query = {'property_external_id': [str(uuid4())], 'reservation_external_id': [str(uuid4())]}
            with patch('operations_login.guest_messages', return_value={
                'messages': [{'sent_at': 'Fixture date', 'body': '<script>Fixture guest text</script>'}],
            }) as read_messages:
                handler.private_workspace(directory, 'https://recovery.example', query)
                read_messages.assert_called_once()
            self.assertIn(b'&lt;script&gt;Fixture guest text&lt;/script&gt;', handler.respond.call_args.args[1])
            access.return_value = False
            handler.private_workspace(directory, 'https://recovery.example', query)
            self.assertEqual(handler.respond.call_args.args[0], 303)
            access.return_value = True

            handler.path = '/'
            other_workspace, other_account = (str(uuid4()) for _ in range(2))
            provision(directory, other_workspace, other_account, initialize)
            handler.headers = {'Cookie': '; '.join(cookies).replace(workspace, other_workspace)}
            handler.private_workspace(directory, 'https://recovery.example', {})
            self.assertEqual(handler.respond.call_args.args[0], 303)
            self.assertEqual(handler.respond.call_args.args[3]['Location'], '/auth/operations/start')
