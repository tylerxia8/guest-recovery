import json
import unittest
from contextlib import nullcontext
from http.cookies import SimpleCookie
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import operations_login
from auth import AuthError


class OperationsLoginTests(unittest.TestCase):
    def setUp(self):
        self.issuer = 'https://turnover.example'
        self.workspace, self.account, self.user = (str(uuid4()) for _ in range(3))
        self.config = (self.issuer, self.workspace, self.account, self.user)
        location, cookie = operations_login.start(self.issuer)
        self.parameters = parse_qs(urlsplit(location).query)
        self.headers = {'Cookie': cookie.split(';', 1)[0]}
        self.query = {'state': self.parameters['state'], 'code': ['A' * 43]}

    def test_wrong_browser_state_blocks_redemption(self):
        with patch.object(operations_login, 'build_opener') as opener:
            with self.assertRaises(AuthError):
                operations_login.finish(MagicMock(), self.config, self.headers, {**self.query, 'state': ['B' * 43]})
            opener.assert_not_called()

    def test_wrong_workspace_does_not_access_existing_guest_data(self):
        connect = MagicMock()
        with patch.object(operations_login, 'build_opener') as opener:
            response = MagicMock()
            response.read.return_value = json.dumps({'workspace_id': str(uuid4()), 'account_id': self.account}).encode()
            opener.return_value.open.return_value = nullcontext(response)
            with self.assertRaisesRegex(AuthError, 'not linked'):
                operations_login.finish(connect, self.config, self.headers, self.query)
            connect.assert_not_called()

    def test_exact_mapping_uses_existing_local_user(self):
        db = MagicMock()
        with patch.object(operations_login, 'build_opener') as opener, patch.object(operations_login, 'new_session') as session:
            response = MagicMock()
            response.read.return_value = json.dumps({'workspace_id': self.workspace, 'account_id': self.account}).encode()
            opener.return_value.open.return_value = nullcontext(response)
            result = operations_login.finish(lambda: nullcontext(db), self.config, self.headers, self.query)
            self.assertIs(result, session.return_value)
            self.assertEqual(db.execute.call_args.args[1], (self.user,))
            request = opener.return_value.open.call_args.args[0]
            verifier = SimpleCookie(self.headers['Cookie'])[operations_login.FLOW_COOKIE].value.split('.')[1]
            self.assertEqual(json.loads(request.data), {'client': 'guest-recovery', 'code': 'A' * 43, 'verifier': verifier})

    def test_shared_read_is_bound_to_the_local_session(self):
        import hashlib

        token = 'fixture-local-session'
        access = 'ops_' + 'C' * 43
        session_hash = hashlib.sha256(token.encode()).hexdigest()
        response = MagicMock()
        response.read.side_effect = [
            json.dumps({
                'workspace_id': self.workspace, 'account_id': self.account,
                'read_token': access, 'read_token_expires_in': 900,
            }).encode(),
            b'{"reservation":{"hospitable_id":"booking"}}',
        ]
        with patch.object(operations_login, 'build_opener') as opener, patch.object(operations_login, 'new_session', return_value=token):
            opener.return_value.open.return_value = nullcontext(response)
            operations_login.finish(lambda: nullcontext(MagicMock()), self.config, self.headers, self.query)
            self.addCleanup(operations_login.forget_read_access, session_hash)
            query = {'property_external_id': ['house'], 'reservation_external_id': ['booking']}
            with self.assertRaisesRegex(AuthError, 'Sign in with Turnover'):
                operations_login.stay_context(self.issuer, 'another-session', query)
            result = operations_login.stay_context(self.issuer, session_hash, query)
            self.assertEqual(result['reservation']['hospitable_id'], 'booking')
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header('Authorization'), 'Bearer ' + access)
