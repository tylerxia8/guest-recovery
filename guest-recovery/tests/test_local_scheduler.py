import os
import unittest
from unittest.mock import patch

import cleaner_returns
import local_scheduler


class InternalSchedulerTest(unittest.TestCase):
    def test_internal_mode_uses_no_http_or_token(self):
        workspace = '11111111-1111-4111-8111-111111111111'
        with patch.dict(os.environ, {'INTERNAL_SCHEDULER_WORKSPACE_ID': workspace,
                                     'CLEANER_SCHEDULER_TOKEN': ''}):
            self.assertEqual(cleaner_returns.configuration()['workspace_id'], workspace)
            self.assertTrue(cleaner_returns.configuration()['stay_configured'])
            with patch.object(local_scheduler, 'request', return_value={'status': 'needs_manager'}) as call, \
                 patch('urllib.request.build_opener') as opener:
                result = cleaner_returns.request(local_scheduler.origin(workspace), 'GET', '/v1/cleaner-returns/sample')
                self.assertEqual(result['status'], 'needs_manager')
                call.assert_called_once_with(workspace, 'GET', '/v1/cleaner-returns/sample', None)
                with self.assertRaisesRegex(ValueError, 'connection changed'):
                    cleaner_returns.request(local_scheduler.origin('another-workspace'), 'GET', '/v1/cleaner-returns/sample')
                opener.assert_not_called()
            with self.assertRaisesRegex(ValueError, 'uses the internal scheduler'):
                cleaner_returns.prepare_configuration({})
