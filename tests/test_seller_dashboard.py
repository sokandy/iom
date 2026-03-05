import importlib
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import db as db_module

os.environ['USE_DB'] = '1'
import app


class SellerDashboardTests(unittest.TestCase):
    def setUp(self):
        self._old_sqlite_path = os.environ.get('SQLITE_PATH')
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / 'test_seller_dashboard.db'
        os.environ['SQLITE_PATH'] = str(self.db_path)

        self.db = importlib.reload(db_module)
        self.db.bootstrap_sqlite_db(reset=True)

        app.app.config['TESTING'] = True
        app.USE_DB = True
        self.client = app.app.test_client()

    def tearDown(self):
        if self._old_sqlite_path is None:
            os.environ.pop('SQLITE_PATH', None)
        else:
            os.environ['SQLITE_PATH'] = self._old_sqlite_path
        importlib.reload(db_module)
        self._tmpdir.cleanup()

    def test_requires_login(self):
        resp = self.client.get('/seller/dashboard', follow_redirects=False)
        self.assertIn(resp.status_code, (301, 302))
        self.assertIn('/user_login', resp.headers.get('Location', ''))

    def test_lists_only_current_seller_auctions(self):
        seller_id = self.db.create_member('seller1', 'Secret123!', email='seller1@example.com')
        other_id = self.db.create_member('seller2', 'Secret123!', email='seller2@example.com')
        bidder_id = self.db.create_member('bidder1', 'Secret123!', email='bidder@example.com')

        seller_auction_id, _ = self.db.create_item_and_auction(
            'Seller Item A',
            'Owned by seller1',
            seller_id=seller_id,
            starting_price=50.0,
            end_date=datetime.utcnow() + timedelta(days=1),
        )
        other_auction_id, _ = self.db.create_item_and_auction(
            'Other Seller Item',
            'Owned by seller2',
            seller_id=other_id,
            starting_price=20.0,
            end_date=datetime.utcnow() + timedelta(days=1),
        )

        self.assertTrue(self.db.place_bid(seller_auction_id, bidder_id, 60.0))
        self.assertTrue(self.db.place_bid(seller_auction_id, bidder_id, 70.0))
        self.assertTrue(self.db.place_bid(other_auction_id, bidder_id, 30.0))

        with self.client.session_transaction() as sess:
            sess['u_name'] = 'seller1'
            sess['user_id'] = seller_id

        resp = self.client.get('/seller/dashboard')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('Seller Item A', html)
        self.assertNotIn('Other Seller Item', html)
        self.assertIn('HK$70.00', html)
        self.assertIn('>2<', html)


if __name__ == '__main__':
    unittest.main()
