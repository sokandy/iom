import importlib
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import db as db_module

os.environ['USE_DB'] = '1'
import app


class WatchlistDbTests(unittest.TestCase):
    def setUp(self):
        self._old_sqlite_path = os.environ.get('SQLITE_PATH')
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / 'test_watchlist.db'
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

    def test_add_remove_and_list_watchlist(self):
        member_id = self.db.create_member('watchuser', 'Secret123!', email='w@example.com')
        item_id = self.db.create_item('Watch Item', 'desc', owner_id=member_id, starting_price=10.0)
        auction_id = self.db.create_auction(item_id, seller_id=member_id, starting_price=10.0, end_date=datetime.utcnow() + timedelta(days=1))

        self.assertTrue(self.db.add_watchlist(member_id, auction_id))
        self.assertFalse(self.db.add_watchlist(member_id, auction_id))
        self.assertTrue(self.db.is_watchlisted(member_id, auction_id))

        rows = self.db.get_watchlist_auctions(member_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], auction_id)

        self.assertTrue(self.db.remove_watchlist(member_id, auction_id))
        self.assertFalse(self.db.is_watchlisted(member_id, auction_id))


class WatchlistRouteTests(unittest.TestCase):
    def setUp(self):
        app.app.config['TESTING'] = True
        app.USE_DB = True
        self.client = app.app.test_client()
        with self.client.session_transaction() as sess:
            sess['u_name'] = 'tester'
            sess['user_id'] = 1

    @patch('db.add_watchlist', return_value=True)
    def test_watchlist_add_route(self, m_add):
        resp = self.client.post('/auction/7/watch', data={'action': 'add'})
        self.assertIn(resp.status_code, (301, 302))
        m_add.assert_called_once_with(1, 7)

    @patch('db.get_watchlist_auctions', return_value=[])
    def test_watchlist_page(self, m_get):
        resp = self.client.get('/watchlist')
        self.assertEqual(resp.status_code, 200)
        m_get.assert_called_once_with(1, limit=200)


if __name__ == '__main__':
    unittest.main()
