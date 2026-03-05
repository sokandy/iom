import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import db as db_module

os.environ['USE_DB'] = '1'
import app


class AdminAuditLogDbTests(unittest.TestCase):
    def setUp(self):
        self._old_sqlite_path = os.environ.get('SQLITE_PATH')
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / 'test_iom.db'
        os.environ['SQLITE_PATH'] = str(self.db_path)
        self.db = importlib.reload(db_module)
        self.db.bootstrap_sqlite_db(reset=True)

    def tearDown(self):
        if self._old_sqlite_path is None:
            os.environ.pop('SQLITE_PATH', None)
        else:
            os.environ['SQLITE_PATH'] = self._old_sqlite_path
        importlib.reload(db_module)
        self._tmpdir.cleanup()

    def test_log_and_read_recent_audit_rows(self):
        row_id = self.db.log_admin_action(
            admin_username='admin',
            action='grant_admin',
            target='member:2',
            result='success',
            detail='mid=2',
            ip_address='127.0.0.1',
        )
        self.assertGreater(row_id, 0)

        rows = self.db.get_recent_admin_audit_logs(limit=10)
        self.assertTrue(rows)
        self.assertEqual(rows[0]['action'], 'grant_admin')
        self.assertEqual(rows[0]['result'], 'success')


class AdminAuditRouteTests(unittest.TestCase):
    def setUp(self):
        app.app.config['TESTING'] = True
        app.USE_DB = True
        self.client = app.app.test_client()
        with self.client.session_transaction() as sess:
            sess['u_name'] = 'admin'

    @patch('app.user_is_admin', return_value=True)
    @patch('app._user_dict_from_session', return_value={'username': 'admin', 'm_is_admin': True})
    @patch('db.set_member_admin', return_value=True)
    @patch('db.log_admin_action')
    def test_admin_grant_writes_audit_log(self, m_log, m_set, m_user, m_is_admin):
        resp = self.client.post('/admin/grant', data={'member': '1'})
        self.assertIn(resp.status_code, (301, 302))
        self.assertTrue(m_log.called)


if __name__ == '__main__':
    unittest.main()
